"""CLI emits source-native envelopes for development or calls a team callback."""
import argparse
from dataclasses import asdict
import importlib
import json
import logging
import os
import sys

from .core import FileSource, LocalRawStore, ingest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-name', default='ndp')
    parser.add_argument('--raw-dir', default='work/raw')
    parser.add_argument('--s3-bucket', default=os.getenv('NDP_RAW_BUCKET'))
    parser.add_argument('--s3-prefix', default=os.getenv('NDP_RAW_PREFIX', 'ingestion/raw'))
    parser.add_argument('--handoff', help='Trusted local module:function; must accept RecordEnvelope and be idempotent')
    parser.add_argument('--output', help='Development JSONL output (exclusive create); otherwise envelopes go to stdout')
    sub = parser.add_subparsers(dest='mode', required=True)
    local = sub.add_parser('local'); local.add_argument('files', nargs='+')
    api = sub.add_parser('api')
    api.add_argument('--profile', choices=['sample-offset'], required=True, help='Unverified GET skip/take profile; confirm with API owner')
    api.add_argument('--since', help='Pass through using documented NDP_SINCE_PARAM; no automatic watermark advancement')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    stream = None
    try:
        if args.handoff and args.output:
            raise ValueError('Choose callback or development output')
        if args.mode == 'local':
            source = FileSource(args.files)
        else:
            from .api import ApiConfig, ApiSource
            auth_header = os.getenv('NDP_AUTH_HEADER')
            auth_value = os.getenv('NDP_AUTH_VALUE')
            if bool(auth_header) != bool(auth_value):
                raise ValueError('Authentication header name and value must be configured together')
            query = json.loads(os.getenv('NDP_QUERY_JSON', '{}'))
            if not isinstance(query, dict):
                raise ValueError('NDP_QUERY_JSON must be an object')
            source = ApiSource(ApiConfig(
                endpoint=os.environ['NDP_API_ENDPOINT'], headers={auth_header: auth_value} if auth_header else {},
                query=query, page_size=int(os.getenv('NDP_PAGE_SIZE', '100')),
                skip_param=os.getenv('NDP_SKIP_PARAM', 'skip'), take_param=os.getenv('NDP_TAKE_PARAM', 'take'),
                since_param=os.getenv('NDP_SINCE_PARAM'), since=args.since,
                timeout=float(os.getenv('NDP_TIMEOUT', '30')), retries=int(os.getenv('NDP_RETRIES', '3')),
                max_pages=int(os.getenv('NDP_MAX_PAGES', '10000')),
            ))
        if args.s3_bucket:
            from .s3 import S3RawStore
            store = S3RawStore(args.s3_bucket, args.s3_prefix)
        else:
            store = LocalRawStore(args.raw_dir)
        if args.handoff:
            module, function = args.handoff.split(':', 1)
            handoff = getattr(importlib.import_module(module), function)
            if not callable(handoff): raise ValueError('Handoff must be callable')
        else:
            stream = open(args.output, 'x', encoding='utf-8') if args.output else sys.stdout
            def handoff(record):
                stream.write(json.dumps(asdict(record), ensure_ascii=False) + '\n')
                stream.flush()
        summary = ingest(source, store, handoff, args.source_name)
        print(json.dumps(summary.to_dict()), file=sys.stderr)
        return 0 if summary.status == 'completed' else 1
    except Exception as exc:
        print(json.dumps({'status': 'configuration_or_output_error', 'code': type(exc).__name__}), file=sys.stderr)
        return 2
    finally:
        if stream is not None and stream is not sys.stdout:
            stream.close()
