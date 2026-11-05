# Validation — November 3, 2026

Python 3.14.5 on the supplied workspace.

- Phase 1: 6 initial unit tests passed before Phase 2 implementation.
- Latest suite: 28 tests passed (`python3 -m unittest discover -s tests -v`).
- Actual CLI run against both user-supplied files: exit 0, 2 payloads archived,
  20 organizations received, 20 handed off, 0 duplicates, 0 record errors.
- Verified all source-native fields equal the original parsed organization bundles.
- Verified archived bytes exactly equal both original files, including whitespace.
- Verified 20 unique logical keys and 36 preserved nested services.
- Mock API tests retrieve every page of a simulated export and exercise retries,
  invalid pagination and failure handling. Mock S3 tests verify bytes and metadata.
- Live NDP API: NOT tested; authorized operation details and access unavailable.
- Real S3/IAM: NOT tested; team bucket and execution role unavailable.
- Repository now contains all 3 JSON fixtures and 3 project Markdown files.
- feature/hsds has downstream storage/intake; the raw-to-HSDS callback remains
  required. No end-to-end integration with that service has been tested.
- ODRS sample CLI: 50 received/handed off, 0 errors, exit 0.
- Authorized ODRS staging: HTTPS verified with system CA bundle, filtered to three
  known fixture IDs with per_page=2; 2 pages archived, 3 handed off, 0 errors.
  Temporary live archive removed afterward because paging URLs echo the API key.
- Full ODRS catalog and production access remain untested.

Staff-supplied fixtures were uploaded by the user; no credentials are bundled. Development runtime artifacts were
written under the workspace work/ directory, outside the deliverable package.
