"""Web research tools for the agents (design §8). One implementation, two hosts:

- AgentCore Gateway Lambda target  -> backend/lambdas/web_tools/handler.py
- AGENT_MODE=inprocess without a Gateway -> agents/local_tools.py

Tools: fetch_page, check_site, geocode, places_near, pdf_text, browse.
Standard library + boto3 only (Lambda runtime), Playwright optional for browse.

WEBSITE_SOURCE=sample serves seeded pages from
s3://$RE_S3_BUCKET/samples/websites/<domain>/<path>.json  ({"status", "text"|"html", "final_url"?})
so the evaluation set (design §13) runs without touching real sites. The agent
can't tell which source is active.
"""

from __future__ import annotations

import hashlib
import html.parser
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone

USER_AGENT = "ResourceEngineVerifier/1.0 (+https://www.1degree.org; community resource verification)"
MAX_BYTES = 2_000_000
CACHE_TTL = 24 * 3600
_SUSPICIOUS = re.compile(r"ignore (all |any )?(previous|prior) instructions|you are now|system prompt|"
                         r"mark (this|the record) (as )?verified", re.I)
_PARKED = re.compile(r"domain (is|may be) for sale|buy this domain|this domain is parked|parked free|"
                     r"domain has expired|godaddy\.com/domainsearch|sedo\.com|hugedomains|dan\.com", re.I)
_last_hit: dict[str, float] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _region() -> str:
    return os.getenv("AWS_REGION", "us-west-2")


def _bucket() -> str:
    return os.getenv("RE_S3_BUCKET", "team20-s3-test-bucket")


def _s3():
    import boto3
    return boto3.client("s3", region_name=_region())


class _Text(html.parser.HTMLParser):
    """HTML -> readable text; scripts/styles dropped; links collected."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            self._href = dict(attrs).get("href")
        if tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        self.parts.append(data)
        if self._href and data.strip():
            self.links.append({"text": data.strip()[:80], "href": self._href})


def html_to_text(body: str, base_url: str = "") -> tuple[str, str, list[dict]]:
    p = _Text()
    p.feed(body)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    links = []
    for link in p.links[:200]:
        href = urllib.parse.urljoin(base_url, link["href"] or "")
        if href.startswith("http"):
            links.append({"text": link["text"], "url": href})
    return text, p.title.strip(), links


def _rate_limit(host: str, min_gap: float = 1.0) -> None:
    with _lock:
        wait = _last_hit.get(host, 0) + min_gap - time.time()
        _last_hit[host] = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def _robots_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/robots.txt", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as r:
            rp.parse(r.read(200_000).decode("utf-8", "replace").splitlines())
    except Exception:
        return True  # no robots.txt -> allowed
    return rp.can_fetch(USER_AGENT, url)


def _sample_page(url: str) -> dict:
    parts = urllib.parse.urlsplit(url)
    domain = (parts.hostname or "").removeprefix("www.")
    path = parts.path.strip("/").replace("/", "__") or "index"
    key = f"samples/websites/{domain}/{path}.json"
    try:
        obj = json.loads(_s3().get_object(Bucket=_bucket(), Key=key)["Body"].read())
    except Exception:
        return {"url": url, "status": 404, "error": "not found", "text": ""}
    body = obj.get("html") or ""
    text, title, links = html_to_text(body, url) if body else (obj.get("text", ""), obj.get("title", ""), [])
    return {"url": url, "final_url": obj.get("final_url", url), "status": obj.get("status", 200), "title": title,
            "text": text, "links": links, "tls_valid_until": obj.get("tls_valid_until"),
            "dns_ok": obj.get("dns_ok", True), "parked": obj.get("parked", False)}


def fetch_page(url: str, max_chars: int = 15000) -> dict:
    """Fetch a page as text. Returns {url, final_url, status, title, text, links, content_hash, fetched_at, cached}."""
    if not re.match(r"^https?://", url or ""):
        url = f"http://{url}"
    if os.getenv("WEBSITE_SOURCE", "sample") == "sample":
        page = _sample_page(url)
    else:
        cache_key = f"webcache/{hashlib.sha256(url.encode()).hexdigest()}.json"
        try:
            cached = json.loads(_s3().get_object(Bucket=_bucket(), Key=cache_key)["Body"].read())
            if time.time() - cached.get("_ts", 0) < CACHE_TTL:
                cached["cached"] = True
                return cached
        except Exception:
            pass
        if not _robots_ok(url):
            return {"url": url, "status": None, "error": "robots.txt disallows fetching", "fetched_at": _now()}
        _rate_limit(urllib.parse.urlsplit(url).hostname or "")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read(MAX_BYTES)
                ctype = r.headers.get("Content-Type", "")
                page = {"url": url, "final_url": r.geturl(), "status": r.status, "content_type": ctype}
        except urllib.error.HTTPError as exc:
            return {"url": url, "status": exc.code, "error": f"HTTP {exc.code}", "fetched_at": _now()}
        except Exception as exc:
            return {"url": url, "status": None, "error": type(exc).__name__, "fetched_at": _now()}
        if "pdf" in page.get("content_type", ""):
            page.update(text="", note="PDF: call pdf_text(url)")
        else:
            text, title, links = html_to_text(raw.decode("utf-8", "replace"), page["final_url"])
            page.update(text=text, title=title, links=links)
        page["_ts"] = time.time()
        try:
            _s3().put_object(Bucket=_bucket(), Key=cache_key, Body=json.dumps(page).encode(),
                             ContentType="application/json", ServerSideEncryption="AES256")
        except Exception:
            pass
    text = page.get("text") or ""
    page["content_hash"] = hashlib.sha256(text.encode()).hexdigest()
    page["suspicious_instructions"] = bool(_SUSPICIOUS.search(text))
    page["text"] = text[:max_chars]
    page["links"] = (page.get("links") or [])[:60]
    page.setdefault("fetched_at", _now())
    page.setdefault("cached", False)
    page.pop("_ts", None)
    return page


def check_site(url: str) -> dict:
    """DNS, HTTP status chain, TLS expiry, parked-domain heuristics -> 'actively bad' signals."""
    if not re.match(r"^https?://", url or ""):
        url = f"http://{url}"
    host = urllib.parse.urlsplit(url).hostname or ""
    if os.getenv("WEBSITE_SOURCE", "sample") == "sample":
        p = _sample_page(url)
        signals = []
        if not p.get("dns_ok", True):
            signals.append("dns_failed")
        if p.get("status") in (404, 410):
            signals.append(f"http_{p['status']}")
        if p.get("parked") or _PARKED.search(p.get("text", "")):
            signals.append("parked_domain")
        if p.get("tls_valid_until") and p["tls_valid_until"] < _now()[:10]:
            signals.append("tls_expired")
        return {"url": url, "reachable": p.get("status") == 200, "http": p.get("status"),
                "chain": [{"url": url, "status": p.get("status")}], "tls_valid_until": p.get("tls_valid_until"),
                "actively_bad_signals": signals, "checked_at": _now()}

    out: dict = {"url": url, "checked_at": _now(), "actively_bad_signals": []}
    try:
        socket.getaddrinfo(host, None)
        out["dns_ok"] = True
    except socket.gaierror:
        out.update(dns_ok=False, reachable=False)
        out["actively_bad_signals"].append("dns_failed")
        return out

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    chain, current, body = [], url, ""
    opener = urllib.request.build_opener(_NoRedirect())
    for _ in range(6):
        try:
            with opener.open(urllib.request.Request(current, headers={"User-Agent": USER_AGENT}), timeout=12) as r:
                chain.append({"url": current, "status": r.status})
                body = r.read(200_000).decode("utf-8", "replace")
                break
        except urllib.error.HTTPError as exc:
            chain.append({"url": current, "status": exc.code})
            loc = exc.headers.get("Location")
            if exc.code in (301, 302, 303, 307, 308) and loc:
                current = urllib.parse.urljoin(current, loc)
                continue
            break
        except Exception as exc:
            chain.append({"url": current, "status": None, "error": type(exc).__name__})
            break
    final = chain[-1] if chain else {}
    out.update(chain=chain, http=final.get("status"), final_url=final.get("url"), reachable=final.get("status") == 200)
    if final.get("status") in (404, 410):
        out["actively_bad_signals"].append(f"http_{final['status']}")
    if body and _PARKED.search(body):
        out["actively_bad_signals"].append("parked_domain")
    tls_host = urllib.parse.urlsplit(final.get("url") or url).hostname or host
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((tls_host, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=tls_host) as tls:
                not_after = tls.getpeercert()["notAfter"]
        out["tls_valid_until"] = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").strftime("%Y-%m-%d")
    except ssl.SSLCertVerificationError as exc:
        out["tls_error"] = "certificate_invalid"
        if "expired" in str(exc).lower():
            out["actively_bad_signals"].append("tls_expired")
    except Exception as exc:
        out["tls_error"] = type(exc).__name__
    return out


def _places():
    import boto3
    return boto3.client("geo-places", region_name=_region())


def geocode(address: str) -> dict:
    """Amazon Location (geo-places Geocode): validate an address / ZIP."""
    resp = _places().geocode(QueryText=address[:200], MaxResults=1, Filter={"IncludeCountries": ["USA"]})
    items = resp.get("ResultItems") or []
    if not items:
        return {"query": address, "found": False}
    it = items[0]
    a = it.get("Address") or {}
    lon, lat = (it.get("Position") or [None, None])[:2]
    return {"query": address, "found": True, "label": a.get("Label"), "address_1": " ".join(
        x for x in (a.get("AddressNumber"), a.get("Street")) if x) or None, "city": a.get("Locality"),
        "state_province": (a.get("Region") or {}).get("Code"), "postal_code": a.get("PostalCode"),
        "county": (a.get("SubRegion") or {}).get("Name"), "latitude": lat, "longitude": lon,
        "match_score": (it.get("MatchScores") or {}).get("Overall")}


def places_near(zip_code: str, query: str, max_results: int = 10) -> dict:
    """Amazon Location (geo-places SearchText) around a ZIP: candidate places for discovery."""
    center = geocode(f"{zip_code}, USA")
    if not center.get("found"):
        return {"zip": zip_code, "query": query, "places": [], "error": "ZIP not found"}
    resp = _places().search_text(QueryText=query[:200], BiasPosition=[center["longitude"], center["latitude"]],
                                 MaxResults=min(max_results, 20), AdditionalFeatures=["Contact"])
    places = []
    for it in resp.get("ResultItems") or []:
        a = it.get("Address") or {}
        contacts = it.get("Contacts") or {}
        places.append({"name": it.get("Title"), "address": a.get("Label"), "postal_code": a.get("PostalCode"),
                       "phones": [p.get("Value") for p in contacts.get("Phones") or []][:3],
                       "websites": [w.get("Value") for w in contacts.get("Websites") or []][:3],
                       "categories": [c.get("Name") for c in it.get("Categories") or []][:5],
                       "position": it.get("Position"), "distance_m": it.get("Distance"),
                       "source": "Amazon Location Service"})
    return {"zip": zip_code, "query": query, "places": places}


def pdf_text(url: str, max_chars: int = 20000) -> dict:
    """Download a PDF (<= 10 MB) and extract its text with Amazon Textract (async, via S3)."""
    import boto3
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read(10_000_001)
    if len(body) > 10_000_000:
        return {"url": url, "error": "PDF larger than 10 MB"}
    key = f"webcache/pdf/{hashlib.sha256(url.encode()).hexdigest()}.pdf"
    _s3().put_object(Bucket=_bucket(), Key=key, Body=body, ContentType="application/pdf", ServerSideEncryption="AES256")
    tx = boto3.client("textract", region_name=_region())
    job = tx.start_document_text_detection(DocumentLocation={"S3Object": {"Bucket": _bucket(), "Name": key}})["JobId"]
    deadline = time.time() + 120
    lines: list[str] = []
    while time.time() < deadline:
        resp = tx.get_document_text_detection(JobId=job)
        if resp["JobStatus"] == "SUCCEEDED":
            token = None
            while True:
                lines += [b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"]
                token = resp.get("NextToken")
                if not token:
                    break
                resp = tx.get_document_text_detection(JobId=job, NextToken=token)
            text = "\n".join(lines)
            return {"url": url, "text": text[:max_chars], "pages": resp.get("DocumentMetadata", {}).get("Pages"),
                    "s3_key": key, "suspicious_instructions": bool(_SUSPICIOUS.search(text)), "fetched_at": _now()}
        if resp["JobStatus"] == "FAILED":
            return {"url": url, "error": "Textract failed"}
        time.sleep(2)
    return {"url": url, "error": "Textract timed out"}


def browse(url: str, goal: str = "", evidence_key: str | None = None) -> dict:
    """AgentCore Browser for JS-heavy pages / Facebook; screenshot saved to S3 evidence/.
    Falls back to fetch_page when Playwright isn't installed."""
    try:
        from bedrock_agentcore.tools.browser_client import browser_session
        from playwright.sync_api import sync_playwright
    except ImportError:
        page = fetch_page(url)
        page["note"] = "browser unavailable here; plain fetch used"
        return page
    with browser_session(_region()) as client:
        ws_url, headers = client.generate_ws_headers()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(ws_url, headers=headers)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            text = page.inner_text("body")[:15000]
            out = {"url": url, "final_url": page.url, "title": page.title(), "text": text, "goal": goal,
                   "suspicious_instructions": bool(_SUSPICIOUS.search(text)), "fetched_at": _now()}
            if evidence_key:
                _s3().put_object(Bucket=_bucket(), Key=evidence_key, Body=page.screenshot(full_page=False),
                                 ContentType="image/png", ServerSideEncryption="AES256")
                out["screenshot_s3_key"] = evidence_key
            browser.close()
    return out


TOOLS = {"fetch_page": fetch_page, "check_site": check_site, "geocode": geocode, "places_near": places_near,
         "pdf_text": pdf_text, "browse": browse}
