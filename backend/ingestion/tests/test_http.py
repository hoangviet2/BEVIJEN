import io
import unittest
import urllib.error
from unittest.mock import Mock, patch
from ndp_ingestion.api import http_get, HttpFailure, NoRedirect


class HttpTests(unittest.TestCase):
    @patch('urllib.request.build_opener')
    def test_request_method_auth_timeout_and_exact_response_bytes(self, build):
        response=Mock();response.read.return_value=b' {"data": []}\n'
        build.return_value.open.return_value.__enter__.return_value=response
        self.assertEqual(http_get('https://example.invalid/export?skip=0',{'X-Key':'secret'},12),b' {"data": []}\n')
        request=build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual(request.get_header('X-key'),'secret')
        self.assertEqual(build.return_value.open.call_args.kwargs['timeout'],12)
        self.assertIsInstance(build.call_args.args[0],NoRedirect)

    @patch('urllib.request.build_opener')
    def test_http_error_sanitized(self, build):
        build.return_value.open.side_effect=urllib.error.HTTPError('https://secret.invalid',429,'secret',{'Retry-After':'5'},io.BytesIO(b'secret'))
        with self.assertRaises(HttpFailure) as caught:
            http_get('https://example.invalid',{},1)
        self.assertEqual(caught.exception.retry_after,'5')
        self.assertNotIn('secret',str(caught.exception))

    def test_redirect_does_not_forward_auth(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))
