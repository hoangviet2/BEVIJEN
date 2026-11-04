"""Strands tools used when no AgentCore Gateway is configured (local dev, AGENT_MODE=inprocess).

Same names and arguments as the Gateway tools, so prompts and SKILL.md don't
change. Server-side tools call this server's /tools/* API (TOOLS_BASE_URL,
TOOLS_API_KEY); web tools run agents/tools/web_tools.py directly; web_search
uses the AgentCore Web Search connector through WEB_SEARCH_GATEWAY_ID if set.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from strands import tool

from tools import web_tools


def _api(method: str, path: str, body: dict | None = None, agent: str = "") -> dict:
    base = os.getenv("TOOLS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    req = urllib.request.Request(f"{base}/tools{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "x-api-key": os.getenv("TOOLS_API_KEY", ""),
                                          "x-re-agent": agent})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode("utf-8", "replace")[:1000]}


def build(agent_name: str) -> list:
    @tool
    def get_record(service_id: str) -> dict:
        """Load one HSDS service with its organization, locations, open findings and memory notes."""
        return _api("GET", f"/records/{service_id}", agent=agent_name)

    @tool
    def record_finding(service_id: str, category: str, field: str, current_value=None, proposed_value=None,
                       evidence: list | None = None, confidence: str = "medium", what_to_ask: list | None = None,
                       agent_session_id: str = "") -> dict:
        """Write one evidence-backed finding. category: discrepancy | missing_information | actively_bad |
        check_failed | needs_manual. evidence: [{url, snippet, fetched_at}]. confidence: high | medium | low."""
        return _api("POST", "/findings", {"service_id": service_id, "category": category, "field": field,
                                          "current_value": current_value, "proposed_value": proposed_value,
                                          "evidence": evidence or [], "confidence": confidence,
                                          "what_to_ask": what_to_ask or [], "agent_session_id": agent_session_id,
                                          "model_id": os.getenv("BEDROCK_MODEL_ID", "")}, agent_name)

    @tool
    def flag_for_specialist(service_id: str, reason: str, what_to_ask: list) -> dict:
        """Ask a volunteer to check something by phone; what_to_ask = 1-4 short questions."""
        return _api("POST", "/flags", {"service_id": service_id, "reason": reason, "what_to_ask": what_to_ask,
                                       "model_id": os.getenv("BEDROCK_MODEL_ID", "")}, agent_name)

    @tool
    def parse_schedule(text: str) -> dict:
        """Convert free-text opening hours into HSDS schedule rows (freq, byday, opens_at, closes_at)."""
        return _api("POST", "/schedules/parse", {"text": text}, agent_name)

    @tool
    def match_existing(name: str = "", phone: str = "", address: str = "", domain: str = "", zip: str = "") -> dict:
        """Find existing records that may be the same facility. Returns matches with score and strength."""
        return _api("POST", "/records/match", {"name": name, "phone": phone, "address": address, "domain": domain,
                                               "zip": zip}, agent_name)

    @tool
    def create_record(run_id: str, hsds: dict, evidence: list) -> dict:
        """Create a NEW HSDS 3.2.3 service record (with nested organization and locations). Discovery only."""
        return _api("POST", "/records", {"run_id": run_id, "hsds": hsds, "evidence": evidence}, agent_name)

    @tool
    def fetch_page(url: str) -> dict:
        """Fetch a web page as text with its links, HTTP status and content hash."""
        return web_tools.fetch_page(url)

    @tool
    def check_site(url: str) -> dict:
        """Check DNS, HTTP status chain, TLS certificate and parked-domain signs for a website."""
        return web_tools.check_site(url)

    @tool
    def browse(url: str, goal: str = "") -> dict:
        """Open a JavaScript-heavy page (or Facebook) in a real browser and return its text."""
        return web_tools.browse(url, goal)

    @tool
    def geocode(address: str) -> dict:
        """Validate an address or ZIP with Amazon Location; returns normalized address and lat/long."""
        return web_tools.geocode(address)

    @tool
    def places_near(zip: str, query: str) -> dict:
        """Find places matching a query near a ZIP code (Amazon Location)."""
        return web_tools.places_near(zip, query)

    @tool
    def pdf_text(url: str) -> dict:
        """Extract the text of a PDF (flyer, schedule) with Amazon Textract."""
        return web_tools.pdf_text(url)

    @tool
    def web_search(query: str, max_results: int = 10) -> dict:
        """Search the web. Returns results with url, title, text snippet and published date."""
        gw = os.getenv("WEB_SEARCH_GATEWAY_ID")
        if not gw:
            return {"error": "web_search is not configured (set WEB_SEARCH_GATEWAY_ID or GATEWAY_URL)"}
        from bedrock_agentcore.tools import WebSearchClient
        client = WebSearchClient(region=os.getenv("AWS_REGION", "us-west-2"), gateway_id=gw)
        resp = client.search(query[:200], max_results=max_results)
        return {"results": [{"url": r.url, "title": r.title, "snippet": r.text[:500],
                             "published_date": r.published_date} for r in resp.results]}

    verifier = [get_record, check_site, fetch_page, browse, web_search, geocode, pdf_text, parse_schedule,
                record_finding, flag_for_specialist]
    discovery = [web_search, places_near, geocode, fetch_page, browse, check_site, pdf_text, parse_schedule,
                 match_existing, create_record, record_finding, flag_for_specialist]
    return verifier if agent_name == "verifier-agent" else discovery
