"""Read any supported file into a list of source records (dicts, possibly nested).

Structured formats (JSON, JSON Lines, CSV/TSV, Excel, XML, HTML tables, Word tables,
YAML) are read with the standard library. PDF, legacy .xls and YAML use optional
packages when present. Free text (TXT/MD/PDF/Word prose) is read as "Key: value"
blocks separated by blank lines.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


class UnsupportedFile(Exception):
    pass


Record = dict


def read_file(path: str | Path, sheet: str | None = None) -> list[Record]:
    path = Path(path)
    ext = path.suffix.lower()
    reader = _READERS.get(ext)
    if reader is None:
        reader = _sniff(path)
    if ext in (".xlsx", ".xlsm", ".xls"):
        return reader(path, sheet)
    return reader(path)


# --------------------------------------------------------------------------- JSON

def _pick_records(data) -> list[Record]:
    """Find the record list in a JSON document (the largest list of objects)."""
    if isinstance(data, list):
        if all(isinstance(x, dict) for x in data):
            return data
        return [{"value": x} for x in data]
    if not isinstance(data, dict):
        return [{"value": data}]
    best: list | None = None
    for value in data.values():
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            if best is None or len(value) > len(best):
                best = value
    if best is not None and len(data) <= 6:
        # A wrapper such as {"paging": ..., "data": [...]}
        return best
    return [data]


def read_json(path: Path) -> list[Record]:
    with path.open("rb") as fh:
        return _pick_records(json.loads(fh.read().decode("utf-8-sig")))


def read_jsonl(path: Path) -> list[Record]:
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            item = json.loads(line)
            # Ingestion envelopes carry the source record in "payload".
            if isinstance(item, dict) and "payload" in item and "contract_version" in item:
                item = item["payload"]
            out.extend(_pick_records(item) if not isinstance(item, dict) else [item])
    return out


# --------------------------------------------------------------------------- tables

def _rows_to_records(rows: list[list]) -> list[Record]:
    rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
    if not rows:
        return []
    header = [str(h).strip() if h is not None and str(h).strip() else f"column_{i + 1}"
              for i, h in enumerate(rows[0])]
    seen: dict[str, int] = {}
    for i, h in enumerate(header):  # de-duplicate header names
        if h in seen:
            seen[h] += 1
            header[i] = f"{h}_{seen[h]}"
        else:
            seen[h] = 1
    records = []
    for row in rows[1:]:
        rec = {}
        for i, h in enumerate(header):
            value = row[i] if i < len(row) else None
            rec[h] = value.strip() if isinstance(value, str) else value
        records.append(rec)
    return records


def read_delimited(path: Path, delimiter: str | None = None) -> list[Record]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:20000], delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = ","
    return _rows_to_records(list(csv.reader(io.StringIO(text), delimiter=delimiter)))


def read_tsv(path: Path) -> list[Record]:
    return read_delimited(path, "\t")


_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path: Path, sheet: str | None = None) -> list[Record]:
    """Minimal standard-library .xlsx reader. Reads every sheet unless one is named."""
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{_NS_MAIN}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS_MAIN}t")))
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        records: list[Record] = []
        for sh in wb.iter(f"{_NS_MAIN}sheet"):
            name = sh.get("name")
            if sheet and name != sheet:
                continue
            target = targets[sh.get(f"{_NS_REL}id")].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            root = ET.fromstring(zf.read(target))
            rows: list[list] = []
            for row in root.iter(f"{_NS_MAIN}row"):
                values: list = []
                for c in row.iter(f"{_NS_MAIN}c"):
                    idx = _col_index(c.get("r")) if c.get("r") else len(values)
                    while len(values) < idx:
                        values.append(None)
                    t = c.get("t")
                    v = c.find(f"{_NS_MAIN}v")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{_NS_MAIN}t"))
                    elif t == "b" and v is not None:
                        val = v.text == "1"
                    elif v is not None and v.text is not None:
                        num = float(v.text) if re.fullmatch(r"-?\d+(\.\d+)?([eE]-?\d+)?", v.text) else None
                        val = (int(num) if num is not None and num.is_integer() else num) if num is not None else v.text
                    else:
                        val = None
                    values.append(val)
                rows.append(values)
            sheet_records = _rows_to_records(rows)
            if sheet is None and len([s for s in wb.iter(f"{_NS_MAIN}sheet")]) > 1:
                for r in sheet_records:
                    r.setdefault("_sheet", name)
            records.extend(sheet_records)
        if sheet and not records and sheet not in [s.get("name") for s in wb.iter(f"{_NS_MAIN}sheet")]:
            raise UnsupportedFile(f"sheet {sheet!r} not found in {path.name}")
        return records


def read_xls(path: Path, sheet: str | None = None) -> list[Record]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise UnsupportedFile("legacy .xls needs the optional 'xlrd' package (pip install xlrd), "
                              "or save the file as .xlsx/.csv") from exc
    book = xlrd.open_workbook(str(path))
    records = []
    for sh in book.sheets():
        if sheet and sh.name != sheet:
            continue
        records.extend(_rows_to_records([sh.row_values(i) for i in range(sh.nrows)]))
    return records


def read_ods(path: Path) -> list[Record]:
    ns = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("content.xml"))
    records = []
    for tbl in root.iter(f"{{{ns['table']}}}table"):
        rows = []
        for row in tbl.iter(f"{{{ns['table']}}}table-row"):
            values = []
            for cell in row.iter(f"{{{ns['table']}}}table-cell"):
                repeat = int(cell.get(f"{{{ns['table']}}}number-columns-repeated", "1"))
                text = "\n".join("".join(p.itertext()) for p in cell.iter(f"{{{ns['text']}}}p"))
                values.extend([text] * min(repeat, 200))
            rows.append(values)
        records.extend(_rows_to_records(rows))
    return records


# --------------------------------------------------------------------------- XML

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _is_plural_of(parent: str, child: str) -> bool:
    p, c = parent.lower(), child.lower()
    return p in (c + "s", c + "es", c[:-1] + "ies" if c.endswith("y") else None, c + "_list", c + "list")


def _xml_to_value(el: ET.Element):
    children = list(el)
    if not children and not el.attrib:
        return (el.text or "").strip()
    tags = {_strip_ns(c.tag) for c in children}
    if not el.attrib and len(tags) == 1 and (len(children) > 1 or _is_plural_of(_strip_ns(el.tag), next(iter(tags)))):
        return [_xml_to_value(c) for c in children]  # <phones><phone/>...</phones> is a list
    out: dict = {_strip_ns(k): v for k, v in el.attrib.items()}
    for child in children:
        key = _strip_ns(child.tag)
        value = _xml_to_value(child)
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    text = (el.text or "").strip()
    if text and children:
        out["text"] = text
    elif text:
        out["value"] = text
    return out


def read_xml(path: Path) -> list[Record]:
    root = ET.parse(path).getroot()
    # Records are the repeated child elements, below at most a few wrapper elements.
    node = root
    for _ in range(4):
        kids = list(node)
        if not kids:
            break
        tags = [_strip_ns(k.tag) for k in kids]
        common = max(set(tags), key=tags.count)
        if tags.count(common) > 1:
            repeated = [k for k in kids if _strip_ns(k.tag) == common]
            return [v if isinstance(v, dict) else {"value": v} for v in map(_xml_to_value, repeated)]
        if len(kids) == 1 and len(list(kids[0])) > 0:
            node = kids[0]  # single wrapper element
            continue
        break
    value = _xml_to_value(node)
    return [value] if isinstance(value, dict) else [{"value": value}]


# --------------------------------------------------------------------------- HTML / Word

class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("br", "p", "div", "li") and self._cell is None:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self.tables:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif not self.tables or self._row is None:
            self.text.append(data)


def read_html(path: Path) -> list[Record]:
    parser = _TableParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    records = []
    for tbl in parser.tables:
        records.extend(_rows_to_records(tbl))
    if records:
        return records
    return parse_key_value_text("".join(parser.text))


_NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx(path: Path) -> list[Record]:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    records = []
    for tbl in root.iter(f"{_NS_W}tbl"):
        rows = []
        for tr in tbl.iter(f"{_NS_W}tr"):
            rows.append([" ".join("".join(t.text or "" for t in tc.iter(f"{_NS_W}t")).split())
                         for tc in tr.iter(f"{_NS_W}tc")])
        records.extend(_rows_to_records(rows))
    if records:
        return records
    paragraphs = ["".join(t.text or "" for t in p.iter(f"{_NS_W}t")) for p in root.iter(f"{_NS_W}p")]
    return parse_key_value_text("\n".join(paragraphs))


# --------------------------------------------------------------------------- text / PDF / YAML

_KV = re.compile(r"^\s*([A-Za-z][\w /&().#-]{0,60}?)\s*[:=]\s*(.*)$")


def parse_key_value_text(text: str) -> list[Record]:
    """Blocks of "Key: value" lines separated by blank lines become records."""
    records: list[Record] = []
    current: dict = {}
    last_key = None
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
            current, last_key = {}, None
            continue
        m = _KV.match(line)
        if m and not m.group(1).lower().startswith(("http", "www")):
            key, value = m.group(1).strip(), m.group(2).strip()
            if key in current:  # a repeated key starts a new record
                records.append(current)
                current = {}
            current[key] = value
            last_key = key
        elif last_key:
            current[last_key] = f"{current[last_key]} {line.strip()}".strip()
        else:
            current.setdefault("description", "")
            current["description"] = f"{current['description']} {line.strip()}".strip()
    if current:
        records.append(current)
    return records


def read_text(path: Path) -> list[Record]:
    return parse_key_value_text(path.read_text(encoding="utf-8-sig", errors="replace"))


def read_pdf(path: Path) -> list[Record]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise UnsupportedFile("PDF needs the optional 'pypdf' package (pip install pypdf)") from exc
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    records = parse_key_value_text(text)
    if not records:
        raise UnsupportedFile(f"{path.name}: no text could be extracted (scanned PDFs need OCR)")
    return records


def read_yaml(path: Path) -> list[Record]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise UnsupportedFile("YAML needs the optional 'pyyaml' package (pip install pyyaml)") from exc
    return _pick_records(yaml.safe_load(path.read_text(encoding="utf-8")))


def _sniff(path: Path):
    with path.open("rb") as fh:
        head = fh.read(4096)
    if head.startswith(b"PK"):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        if "xl/workbook.xml" in names:
            return read_xlsx
        if "word/document.xml" in names:
            return read_docx
        if "content.xml" in names:
            return read_ods
        raise UnsupportedFile(f"{path.name}: unrecognised zip-based file")
    if head.startswith(b"%PDF"):
        return read_pdf
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped[:1] in (b"{", b"["):
        return read_json
    if stripped[:1] == b"<":
        return read_html if b"<html" in head.lower() or b"<table" in head.lower() else read_xml
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFile(f"{path.name}: binary format not supported") from exc
    sample = head.decode("utf-8", errors="replace")
    if sample.count(",") + sample.count("\t") > sample.count(":") * 2:
        return read_delimited
    return read_text


_READERS = {
    ".json": read_json, ".geojson": read_json,
    ".jsonl": read_jsonl, ".ndjson": read_jsonl,
    ".csv": read_delimited, ".tsv": read_tsv, ".tab": read_tsv, ".psv": read_delimited,
    ".xlsx": read_xlsx, ".xlsm": read_xlsx, ".xls": read_xls, ".ods": lambda p: read_ods(p),
    ".xml": read_xml,
    ".html": read_html, ".htm": read_html,
    ".docx": read_docx,
    ".txt": read_text, ".md": read_text, ".text": read_text,
    ".pdf": read_pdf,
    ".yaml": read_yaml, ".yml": read_yaml,
}

SUPPORTED_EXTENSIONS = sorted(_READERS)
