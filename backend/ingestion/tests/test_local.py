import json
import tempfile
import unittest
from pathlib import Path
from ndp_ingestion.core import FileSource, LocalRawStore, ingest


class LocalTests(unittest.TestCase):
    def run_records(self, records, callback=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        body = b' { "data": ' + json.dumps(records).encode() + b' }\n'
        path = root / 'page.json'
        path.write_bytes(body)
        output = []
        summary = ingest(FileSource([path]), LocalRawStore(root / 'raw'), callback or output.append)
        return summary, output, root, body

    def test_preserves_bytes_and_nested_record(self):
        record = {'id': 'org-1', 'services': [{'id': 'service-1', 'custom': [None, 4]}], 'meta': {'lastUpdated': '2025-01-01'}}
        s, rows, root, body = self.run_records([record])
        self.assertEqual(s.status, 'completed')
        self.assertEqual(rows[0].payload, record)
        self.assertEqual(rows[0].source_updated_at, '2025-01-01')
        self.assertEqual(next((root / 'raw').rglob('000000.json')).read_bytes(), body)

    def test_duplicate_and_conflicting_records(self):
        s, rows, _, _ = self.run_records([{'id': '1'}, {'id': '1'}, {'id': '1', 'name': 'changed'}])
        self.assertEqual((s.records_received, s.records_handed_off, s.duplicates_skipped, s.record_errors), (3, 1, 1, 1))

    def test_bad_record_does_not_stop_batch(self):
        s, rows, _, _ = self.run_records([{}, None, {'id': 'ok'}])
        self.assertEqual((s.record_errors, len(rows)), (2, 1))

    def test_handoff_error_is_accounted(self):
        def fail(row): raise RuntimeError('secret must never reach summary')
        s, _, _, _ = self.run_records([{'id': '1'}], fail)
        self.assertEqual(s.record_errors, 1)
        self.assertNotIn('secret', json.dumps(s.to_dict()))

    def test_repeat_runs_keep_identity(self):
        _, a, _, _ = self.run_records([{'id': '1'}])
        _, b, _, _ = self.run_records([{'id': '1'}])
        self.assertEqual(a[0].logical_record_key, b[0].logical_record_key)

    def test_invalid_json_is_archived_and_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'bad.json'; p.write_bytes(b'broken')
            s = ingest(FileSource([p]), LocalRawStore(Path(t) / 'raw'), lambda _: None)
            self.assertEqual((s.status, s.payloads_archived), ('incomplete', 1))


if __name__ == '__main__': unittest.main()


class MultiFileTests(unittest.TestCase):
    def test_same_record_across_files_and_downstream_upsert_across_runs(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); paths=[]
            for index in range(2):
                p=root/f'{index}.json';p.write_text(json.dumps({'data':[{'id':'a','locations':[{'id':'loc'}]}]}));paths.append(p)
            state={}
            def upsert(row): state[row.logical_record_key]=row
            a=ingest(FileSource(paths),LocalRawStore(root/'raw'),upsert)
            b=ingest(FileSource(paths),LocalRawStore(root/'raw'),upsert)
            self.assertEqual((a.records_received,a.records_handed_off,a.duplicates_skipped),(2,1,1))
            self.assertEqual(b.status,'completed');self.assertEqual(len(state),1)

    def test_source_name_cannot_escape_archive(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                LocalRawStore(t).put(__import__('ndp_ingestion.core',fromlist=['RawPayload']).RawPayload(b'{}','now'),'..','run',0)
