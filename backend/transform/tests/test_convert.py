import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from hsds_transform import convert_files, schema
from hsds_transform.mapper import is_uuid

FIXTURES = Path(__file__).resolve().parents[2] / "ingestion" / "tests" / "fixtures"
NDP = [FIXTURES / "national-data-platform-sample-1.json", FIXTURES / "national-data-platform-sample-2.json"]
ODRS = FIXTURES / "BayesExport_20250729.json"

FLAT_HEADER = ["Agency Name", "Program Name", "Description", "Address", "City", "State", "Zip", "Phone",
               "Website", "Languages", "Hours"]
FLAT_ROW = ["Helping Hands", "Food Pantry", "Free groceries", "12 Main St", "Oakland", "CA", "94601",
            "(510) 555-0100 ext 4", "www.helpinghands.org", "English; Spanish", "Mon-Fri 9am-5pm"]


def write_xlsx(path: Path, rows: list[list[str]]) -> None:
    def cell(ref, value):
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    sheet_rows = "".join(
        f'<row r="{i + 1}">' + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(row)) + "</row>"
        for i, row in enumerate(rows))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
                                       "</sheets></workbook>")
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                    "</Relationships>")
        zf.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet_rows}</sheetData></worksheet>")


def write_docx(path: Path, rows: list[list[str]]) -> None:
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{v}</w:t></w:r></w:p></w:tc>" for v in row)
                   + "</w:tr>" for row in rows)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", f"<w:document {w}><w:body><w:tbl>{body}</w:tbl></w:body></w:document>")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, paths, **kw):
        out = self.dir / "out"
        ds, issues, report = convert_files(paths, out, **kw)
        return ds, issues, report, out

    def assert_complete_output(self, out: Path, na="NA"):
        """Every HSDS table exists with every HSDS column; missing values are NA."""
        for name, tdef in schema.tables().items():
            with (out / "tables" / tdef.path).open(newline="") as fh:
                reader = csv.DictReader(fh)
                self.assertEqual(reader.fieldnames, tdef.column_names, name)
                for row in reader:
                    self.assertTrue(all(v != "" for v in row.values()) if na else True, name)
                    self.assertTrue(is_uuid(row["id"]), (name, row["id"]))
        tables = json.loads((out / "hsds_tables.json").read_text())
        self.assertEqual(set(tables), set(schema.tables()))
        for name, rows in tables.items():
            for row in rows:
                self.assertEqual(list(row), schema.table(name).column_names)


class TestSources(Base):
    def test_ndp_samples(self):
        ds, issues, report, out = self.convert(NDP, source_name="ndp")
        self.assertEqual(report["rows_per_table"]["organization"], 20)
        self.assertEqual(report["rows_per_table"]["service"], 36)
        self.assertFalse(ds.errors)
        self.assert_complete_output(out)
        org = next(iter(ds.rows["organization"].values()))
        self.assertEqual(org["name"], "Pacifica School District (PSD)")
        # every service points at an organization that exists
        for svc in ds.rows["service"].values():
            self.assertIn(svc["organization_id"], ds.rows["organization"])
            self.assertIn(svc["status"], ("active", "inactive", "defunct", "temporarily closed"))
        # explicit servicesAtLocations are not duplicated by locationIds
        pairs = [(s["service_id"], s["location_id"]) for s in ds.rows["service_at_location"].values()]
        self.assertEqual(len(pairs), len(set(pairs)))
        # no dangling references
        self.assertFalse([i for i in issues if "not in this dataset" in i["problem"]])
        nested = json.loads((out / "services.json").read_text())
        self.assertEqual(len(nested), 36)
        self.assertEqual(list(nested[0]), list(schema.table("service").property_order))
        self.assertIsInstance(nested[0]["organization"], dict)

    def test_odrs_sample(self):
        ds, issues, report, out = self.convert([ODRS], source_name="odrs")
        self.assertEqual(report["rows_per_table"]["service"], 50)
        self.assert_complete_output(out)
        svc = next(s for s in ds.rows["service"].values() if s["name"].startswith("Get emergency financial"))
        self.assertEqual(svc["assured_date"], "2025-06-13")
        self.assertIn("Oakland residents", svc["eligibility_description"])
        # organization description is not in the source: NA, reported by validation
        self.assertTrue(any(i["table"] == "organization" and i["column"] == "description" for i in issues))
        # weekly location schedules become RFC 5545 style rows
        days = {s.get("byday") for s in ds.rows["schedule"].values() if s.get("opens_at")}
        self.assertIn("MO", days)

    def test_ids_are_deterministic(self):
        a, *_ = convert_files(NDP, None, source_name="ndp")
        b, *_ = convert_files(NDP, None, source_name="ndp")
        self.assertEqual(set(a.rows["service"]), set(b.rows["service"]))
        c, *_ = convert_files(NDP, None, source_name="other")
        self.assertFalse(set(a.rows["service"]) & set(c.rows["service"]))


class TestFormats(Base):
    def check_flat(self, path: Path):
        ds, issues, report, out = self.convert([path], source_name="test")
        self.assertFalse(ds.errors, ds.errors)
        self.assert_complete_output(out)
        svc = next(iter(ds.rows["service"].values()))
        self.assertEqual(svc["name"], "Food Pantry")
        org = ds.rows["organization"][svc["organization_id"]]
        self.assertEqual(org["name"], "Helping Hands")
        addr = next(iter(ds.rows["address"].values()))
        self.assertEqual((addr["address_1"], addr["city"], addr["postal_code"]), ("12 Main St", "Oakland", "94601"))
        phone = next(iter(ds.rows["phone"].values()))
        self.assertEqual((phone["number"], phone["extension"]), ("(510) 555-0100", 4))
        self.assertEqual(sorted(l["name"] for l in ds.rows["language"].values()), ["English", "Spanish"])
        sched = next(iter(ds.rows["schedule"].values()))
        self.assertEqual((sched["byday"], sched["opens_at"], sched["closes_at"]), ("MO,TU,WE,TH,FR", "09:00", "17:00"))
        self.assertEqual(svc["url"], "http://www.helpinghands.org")
        with (out / "tables" / "services.csv").open() as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual(row["fees_description"], "NA")
        return ds

    def test_csv(self):
        p = self.dir / "in.csv"
        with p.open("w", newline="") as fh:
            csv.writer(fh).writerows([FLAT_HEADER, FLAT_ROW])
        self.check_flat(p)

    def test_tsv(self):
        p = self.dir / "in.tsv"
        p.write_text("\t".join(FLAT_HEADER) + "\n" + "\t".join(FLAT_ROW) + "\n")
        self.check_flat(p)

    def test_xlsx(self):
        p = self.dir / "in.xlsx"
        write_xlsx(p, [FLAT_HEADER, FLAT_ROW])
        self.check_flat(p)

    def test_docx_table(self):
        p = self.dir / "in.docx"
        write_docx(p, [FLAT_HEADER, FLAT_ROW])
        self.check_flat(p)

    def test_html_table(self):
        p = self.dir / "in.html"
        cells = lambda tag, row: "<tr>" + "".join(f"<{tag}>{v}</{tag}>" for v in row) + "</tr>"
        p.write_text(f"<html><body><table>{cells('th', FLAT_HEADER)}{cells('td', FLAT_ROW)}</table></body></html>")
        self.check_flat(p)

    def test_json_flat(self):
        p = self.dir / "in.json"
        p.write_text(json.dumps({"results": [dict(zip(FLAT_HEADER, FLAT_ROW))], "count": 1}))
        self.check_flat(p)

    def test_text_key_value(self):
        p = self.dir / "in.txt"
        p.write_text("\n".join(f"{h}: {v}" for h, v in zip(FLAT_HEADER, FLAT_ROW)) + "\n")
        self.check_flat(p)

    def test_unknown_extension_is_sniffed(self):
        p = self.dir / "export.dat"
        with p.open("w", newline="") as fh:
            csv.writer(fh).writerows([FLAT_HEADER, FLAT_ROW])
        self.check_flat(p)

    def test_xml_nested(self):
        p = self.dir / "in.xml"
        p.write_text("""<?xml version="1.0"?>
<directory><organizations>
  <organization id="org-1"><name>Helping Hands</name><description>Community nonprofit</description>
    <services><service id="svc-1"><name>Food Pantry</name><status>Active</status>
      <phones><phone><number>510-555-0100</number><type>Main</type></phone></phones>
    </service></services>
  </organization>
  <organization id="org-2"><name>Second Org</name><description>Other</description></organization>
</organizations></directory>""")
        ds, issues, report, out = self.convert([p], source_name="xml")
        self.assert_complete_output(out)
        self.assertEqual(report["rows_per_table"]["organization"], 2)
        svc = next(iter(ds.rows["service"].values()))
        self.assertEqual(svc["status"], "active")
        self.assertEqual(svc["organization_id"], ds.rows["organization"][svc["organization_id"]]["id"])
        phone = next(iter(ds.rows["phone"].values()))
        self.assertEqual((phone["type"], phone["service_id"]), ("voice", svc["id"]))

    def test_jsonl_ingestion_envelopes(self):
        p = self.dir / "envelopes.jsonl"
        payload = json.loads(NDP[0].read_text())["data"][0]
        p.write_text(json.dumps({"contract_version": "1", "payload": payload}) + "\n")
        ds, *_ = self.convert([p], source_name="ndp")
        self.assertEqual(len(ds.rows["organization"]), 1)


class TestOptions(Base):
    def flat_csv(self, header=FLAT_HEADER, row=FLAT_ROW):
        p = self.dir / "in.csv"
        with p.open("w", newline="") as fh:
            csv.writer(fh).writerows([header, row])
        return p

    def test_na_omit_and_null(self):
        p = self.flat_csv()
        _, _, _, out = self.convert([p], na="omit")
        svc = json.loads((out / "services.json").read_text())[0]
        self.assertNotIn("fees_description", svc)
        self.assertNotIn("NA", json.dumps(svc))
        _, _, _, out = self.convert([p], na="null")
        svc = json.loads((out / "services.json").read_text())[0]
        self.assertIsNone(svc["fees_description"])

    def test_mapping_file_overrides(self):
        p = self.flat_csv(["Record", "Thing", "Notes", "Group"], ["A-1", "Shelter", "drop me", "Housing"])
        mapping = {"root": "service", "id": "Record",
                   "fields": {"Thing": "service.name", "Notes": None, "Group": "taxonomy:topics"}}
        ds, _, report, _ = self.convert([p], mapping=mapping)
        svc = next(iter(ds.rows["service"].values()))
        self.assertEqual(svc["name"], "Shelter")
        self.assertEqual(ds.source_map[0]["native_id"], "A-1")
        self.assertEqual(next(iter(ds.rows["taxonomy_term"].values()))["taxonomy"], "topics")
        self.assertFalse(ds.unmapped)

    def test_unmapped_values_are_reported_not_dropped(self):
        p = self.flat_csv(["Program Name", "Favourite Colour"], ["Food Pantry", "blue"])
        ds, _, report, out = self.convert([p])
        self.assertEqual(report["unmapped_source_fields"], {"Favourite Colour": 1})
        self.assertIn("blue", (out / "report_unmapped_fields.csv").read_text())

    def test_unreadable_file_is_reported(self):
        p = self.dir / "bad.json"
        p.write_text("{not json")
        ds, _, report, _ = self.convert([p])
        self.assertEqual(len(ds.errors), 1)


if __name__ == "__main__":
    unittest.main()
