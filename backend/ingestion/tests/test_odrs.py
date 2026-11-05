import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from ndp_ingestion.core import FileSource, LocalRawStore, ingest
from ndp_ingestion.odrs import OdrsConfig, OdrsApiSource

FIXTURES = Path(__file__).parent / 'fixtures'


def page(n, ids, total, next_page=None):
    return json.dumps({'paging':{'current_page':n,'total_count':total,'total_pages':2 if total>1 else 1,'next_page':next_page},
                       'opportunities':[{'id':i,'title':'Service','organization':{'id':9},'updated_at':'2025-01-01'} for i in ids]}).encode()


class OdrsTests(unittest.TestCase):
    def run_source(self, source):
        with tempfile.TemporaryDirectory() as d:
            rows=[]; s=ingest(source,LocalRawStore(d),rows.append,'odrs')
            return s, rows

    def config(self, **kw):
        return OdrsConfig(endpoint='https://stage.invalid:3002/v1/opportunities',api_key='fake-test-key',**kw)

    def test_real_odrs_fixture(self):
        s, rows=self.run_source(FileSource([FIXTURES/'BayesExport_20250729.json'],'opportunities'))
        original=json.loads((FIXTURES/'BayesExport_20250729.json').read_bytes())['opportunities']
        self.assertEqual((s.status,s.records_received,s.records_handed_off),('completed',50,50))
        self.assertEqual([r.payload for r in rows],original)
        self.assertEqual(rows[0].record_pointer,'/opportunities/0')
        self.assertEqual(rows[0].source_updated_at,original[0]['updated_at'])

    def test_real_ndp_fixtures_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[];s=ingest(FileSource(sorted(FIXTURES.glob('national*.json'))),LocalRawStore(d),rows.append)
            self.assertEqual((s.status,len(rows)),('completed',20))
            self.assertEqual(sum(len(r.payload['services']) for r in rows),36)

    def test_follow_next_retains_configured_port_replaces_key(self):
        next_url='https://stage.invalid/v1/opportunities?page=2&per_page=1&api_key=untrusted'
        get=Mock(side_effect=[page(1,[1],2,next_url),page(2,[2],2)])
        s, rows=self.run_source(OdrsApiSource(self.config(page_size=1),get))
        self.assertEqual((s.status,len(rows)),('completed',2))
        url=get.call_args_list[1].args[0];p=urlsplit(url)
        self.assertEqual(p.port,3002)
        self.assertEqual(parse_qs(p.query)['api_key'],['fake-test-key'])
        self.assertNotIn('fake-test-key',repr(self.config()))

    def test_reject_bad_paging(self):
        for target in ['https://evil.invalid/v1/opportunities?page=2',
                       'http://stage.invalid/v1/opportunities?page=2',
                       'https://stage.invalid/other?page=2',
                       'https://stage.invalid/v1/opportunities?page=1',
                       'https://stage.invalid:444/v1/opportunities?page=2']:
            get=Mock(return_value=page(1,[1],2,target))
            s,_=self.run_source(OdrsApiSource(self.config(),get))
            self.assertEqual(s.status,'incomplete');self.assertEqual(get.call_count,1)

    def test_truncated_and_repeated_export(self):
        for bodies in [[page(1,[1],2)], [page(1,[1],2,'https://stage.invalid/v1/opportunities?page=2'),page(2,[1],2)]]:
            s,_=self.run_source(OdrsApiSource(self.config(),Mock(side_effect=bodies)))
            self.assertEqual(s.status,'incomplete')

    def test_missing_key(self):
        with self.assertRaises(ValueError):OdrsConfig(endpoint='https://stage.invalid')
