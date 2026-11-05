import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
from ndp_ingestion.api import ApiConfig, ApiSource, HttpFailure
from ndp_ingestion.core import LocalRawStore, RawPayload, ingest
from ndp_ingestion.s3 import S3RawStore


def page(skip, ids, total):
    return json.dumps({'input': {'skip': skip, 'countAvailable': total, 'countReturned': len(ids)},
                       'data': [{'id': i, 'services': [{'id': i + '-svc'}]} for i in ids]}).encode()


class ApiTests(unittest.TestCase):
    def run_source(self, source):
        with tempfile.TemporaryDirectory() as root:
            rows=[]
            summary=ingest(source, LocalRawStore(root), rows.append)
            archived=list(Path(root).rglob('*.json'))
            return summary, rows, archived

    def test_all_pages_same_contract_as_local(self):
        calls=[]
        def transport(url, headers, timeout):
            q=parse_qs(urlsplit(url).query); calls.append((q, headers, timeout))
            skip=int(q['skip'][0]); return page(skip, ['a','b'] if skip==0 else ['c'], 3)
        s, rows, _=self.run_source(ApiSource(ApiConfig('https://example.invalid/export', page_size=2,
            headers={'X-Test': 'token'}, since='2025-01-01', since_param='updated'), transport))
        self.assertEqual(s.status, 'completed'); self.assertEqual(len(rows), 3)
        self.assertEqual([x[0]['skip'][0] for x in calls], ['0','2'])
        self.assertEqual(calls[0][0]['updated'], ['2025-01-01'])
        self.assertEqual(calls[0][1]['X-Test'], 'token')
        self.assertEqual(calls[0][2], 30)
        self.assertEqual(rows[0].payload['services'][0]['id'], 'a-svc')

    def test_server_caps_page_size(self):
        calls=[]
        def get(url, *args):
            n=int(parse_qs(urlsplit(url).query)['skip'][0]); calls.append(n)
            return page(n, [str(n)], 3)
        s, rows, _=self.run_source(ApiSource(ApiConfig('https://example.invalid', page_size=100), get))
        self.assertEqual((s.status, calls), ('completed', [0,1,2]))

    def test_retry_429_and_timeout(self):
        get=Mock(side_effect=[HttpFailure(429, '2'), TimeoutError(), page(0,['a'],1)])
        sleep=Mock()
        s, _, _=self.run_source(ApiSource(ApiConfig('https://example.invalid'),get,sleep))
        self.assertEqual(s.status,'completed'); self.assertEqual(get.call_count,3)
        self.assertEqual([x.args[0] for x in sleep.call_args_list],[2,2])

    def test_401_no_retry_and_no_secret(self):
        get=Mock(side_effect=HttpFailure(401)); sleep=Mock()
        s, _, _=self.run_source(ApiSource(ApiConfig('https://example.invalid', headers={'Authorization':'secret'}),get,sleep))
        self.assertEqual(s.status,'incomplete'); self.assertEqual(get.call_count,1)
        sleep.assert_not_called(); self.assertNotIn('secret',json.dumps(s.to_dict()))

    def test_retry_exhaustion(self):
        get=Mock(side_effect=ConnectionError('sensitive URL'))
        s, _, _=self.run_source(ApiSource(ApiConfig('https://example.invalid',retries=1),get,lambda _:None))
        self.assertEqual((s.status,get.call_count),('incomplete',2))
        self.assertNotIn('sensitive',json.dumps(s.to_dict()))

    def test_bad_pagination_is_incomplete(self):
        for bodies in ([page(0,[],2)], [page(0,['a'],3),page(0,['b'],3)],
                       [page(0,['a'],3),page(1,['b'],4)],
                       [page(0,['a'],3),page(1,['a'],3)], [b'not json']):
            with self.subTest(bodies=bodies):
                s, _, _=self.run_source(ApiSource(ApiConfig('https://example.invalid'),Mock(side_effect=bodies)))
                self.assertEqual(s.status,'incomplete')
                self.assertGreater(s.payloads_archived,0)

    def test_max_pages_is_not_success(self):
        s, _, _=self.run_source(ApiSource(ApiConfig('https://example.invalid',max_pages=1),lambda *a:page(0,['a'],2)))
        self.assertEqual(s.status,'incomplete')

    def test_empty_dataset(self):
        s, rows, _=self.run_source(ApiSource(ApiConfig('https://example.invalid'),lambda *a:page(0,[],0)))
        self.assertEqual((s.status,len(rows)),('completed',0))

    def test_bad_configuration(self):
        for kwargs in ({'endpoint':'http://example.invalid'}, {'endpoint':'https://user:pass@example.invalid'},
                       {'endpoint':'https://example.invalid?key=secret'}, {'since':'yesterday'},
                       {'query':{'skip': 5}}, {'page_size':0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ApiConfig(**({'endpoint':'https://example.invalid'} | kwargs))

    def test_s3_exact_bytes_metadata_version(self):
        client=Mock();client.put_object.return_value={'VersionId':'v/1'}
        ref=S3RawStore('team-bucket','raw',client).put(RawPayload(b' {"data": []}\n','2026-11-03T00:00:00Z'),'ndp','run',0)
        args=client.put_object.call_args.kwargs
        self.assertEqual(args['Body'],b' {"data": []}\n')
        self.assertEqual(args['Metadata']['source-name'],'ndp')
        self.assertEqual(ref,'s3://team-bucket/raw/ndp/run/000000.json?versionId=v%2F1')

    def test_s3_failure_prevents_handoff(self):
        client=Mock();client.put_object.side_effect=RuntimeError('AWS secret')
        callback=Mock()
        source=ApiSource(ApiConfig('https://example.invalid'),lambda *a:page(0,['a'],1))
        s=ingest(source,S3RawStore('bucket',client=client),callback)
        self.assertEqual(s.status,'incomplete');callback.assert_not_called()

if __name__=='__main__':unittest.main()
