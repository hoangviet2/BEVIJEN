# One Degree NDP ingestion

Ingestion only. Reads source-native organization bundles, archives raw bytes before
parsing, and hands records to the HSDS transformation owner. No HSDS mapping,
resource database, AI/Bedrock calls, queue, or independent AWS infrastructure.

## Status and integration boundary

- Phase 1 verified against both supplied samples: 20 organizations, 36 nested
  services, zero errors. Original source files are not copied into this package.
- Phase 2 implemented against mocked HTTP and S3. **No live API or AWS connectivity
  has been tested.** `sample-offset` is a provisional GET/skip/take profile, inferred
  from the samples; it is not a verified NDP operation contract.
- Repository integration: inspected hoangviet2/BEVIJEN main, feature/hsds, and
  feature/update at commit 5972e15. All contain only empty ai/backend/frontend
  placeholder Markdown files. This package lives under backend/ingestion; no
  application interface or IaC existed to extend. The callback and AWS configuration
  still require agreement with the HSDS and infrastructure owners.
- Before live use, obtain the authorized Export API operation URL, API version,
  exact method, auth header/token lifecycle, pagination/count semantics, rate
  limits, stable ordering/snapshot behavior, incremental filter semantics and
  inactive/deletion handling. Confirm the actual response matches these samples.
  If it differs, replace ApiSource's profile; preserve the core handoff contract.
- Official references checked November 3, 2026:
  https://register.211.org/Home/FAQs (Export API for organization datasets)
  https://apiportal.211.org/get-started-overview (authorized access/developer docs)
  https://apiportal.211.org/apis (operation details not retrievable in this session).

## Run local ingestion

Python 3.11+; local operation and unit tests need only the standard library.
Run from this directory (no installation required):

```sh
python3 -m unittest discover -s tests -v
python3 -m ndp_ingestion --raw-dir work/raw --output work/envelopes.jsonl local \
  '/absolute/path/national-data-platform-sample-1.json' \
  '/absolute/path/national-data-platform-sample-2.json'
```

Create `work/` first if using an output path there (`mkdir -p work`). JSONL output
is development output, not HSDS and not a resource database. Output paths use
exclusive create: choose a new filename on each replay. Omit `--output` to stream
envelopes to stdout. Summaries go to stderr. Exit 0 means all received records
were handed off (or identical duplicates skipped), 1 means incomplete/record
errors, and 2 means configuration/output initialization failed.

Raw payload files preserve exact bytes, including whitespace. A sidecar contains
retrieval timestamp, source name, content type and SHA-256. Raw files are archived
under source/run UUID/page, so repeat runs retain separate evidence.

## Transformation interface

Use `ingest(source, raw_store, handoff, source_name='ndp')` from Python. `handoff`
is a synchronous callable receiving a `RecordEnvelope`. It must return only once
the receiver has accepted the record, and raise on failure. CLI integration:

```sh
python3 -m ndp_ingestion --handoff team_package.transform:accept local /path/page.json
```

Only load trusted local callback modules. No actual team callback was supplied.
The transformation owner must agree to this contract before integration.

Fields: `contract_version`, `source_name`, `original_record_id`,
`logical_record_key`, `ingested_at` (UTC retrieval time), `raw_storage_reference`,
`record_pointer` (`/data/N` within the archived page), `source_updated_at` (verbatim
`meta.lastUpdated` when available), and `payload` (complete unmodified parsed
organization with nested services, locations and unknown fields).

Logical keys are compact JSON pairs `[source_name, original_record_id]`, independent
of page, run and timestamp. Keep source_name stable; default `ndp`. This dataset's
organization IDs already include their data-owner prefix. Confirm global native ID
uniqueness before adding other NDP subscriptions.

Identical records within a run are handed off once. Conflicting versions with the
same ID produce an error rather than silently choosing a winner. Across runs,
records are emitted again with the same keys. **The receiver must upsert/idempotently
accept by that key**; ingestion alone cannot guarantee duplicate-free database
writes. Callback failure after a partial commit is safe only with that property.
No manual/AI fields are merged or overwritten by this package.

Raw-to-transform data is an internal source-native interface. It is not a public
HSDS exchange. Agree this boundary with the team because the background document's
blanket HSDS boundary rule conflicts with separately owned ingestion/transformation.

## Direct API configuration

No endpoint or credentials are embedded. The client never falls back to files.
Provide configuration through the approved deployment environment:

| Variable | Meaning |
| --- | --- |
| NDP_API_ENDPOINT | Required authorized HTTPS operation URL, without query/auth credentials |
| NDP_AUTH_HEADER | Exact approved auth header name; optional only for an authorized mock that needs none |
| NDP_AUTH_VALUE | Full header value, including any required scheme; injected securely |
| NDP_QUERY_JSON | JSON object of documented filters; default `{}`; no credentials |
| NDP_PAGE_SIZE | Requested take; default 100, must meet actual server limits |
| NDP_SKIP_PARAM / NDP_TAKE_PARAM | Default skip/take; verify against actual docs |
| NDP_SINCE_PARAM | Documented incremental parameter; unset until confirmed |
| NDP_TIMEOUT | Per-request timeout seconds; default 30 |
| NDP_RETRIES | Additional attempts for transient failures; default 3 |
| NDP_MAX_PAGES | Safety limit; default 10000; hitting it marks run incomplete |
| NDP_RAW_BUCKET / NDP_RAW_PREFIX | Team-provisioned S3 destination / default ingestion/raw |
| AWS_REGION / AWS_DEFAULT_REGION | Team deployment region as needed by SDK |

```sh
# Only after approved environment configuration has been supplied:
python3 -m ndp_ingestion --handoff team_package.transform:accept api --profile sample-offset
# Only if incremental filtering semantics have been confirmed:
python3 -m ndp_ingestion --handoff team_package.transform:accept api \
  --profile sample-offset --since 2026-11-01
```

`--profile sample-offset` is deliberate acknowledgement of an unverified profile.
The profile requests pages from skip=0, advances by actual returned count (handles
server page-size caps), and stops at the advertised total. It rejects invalid
counts, stale offsets, repeated pages, premature empty pages and changing totals.
A capped/truncated run is never labeled complete. A stable count is not proof of a
consistent snapshot: confirm server ordering/snapshot semantics for live export.
HTTP redirects are refused so authentication is not forwarded to another endpoint.
Retries cover connection errors, timeouts, 408, 429 and selected 5xx responses, with
bounded exponential backoff and Retry-After handling. 401/403 are not retried.
Logs omit payloads, auth values, request URLs and arbitrary exception text.

## S3 and AWS handoff to IaC owner

Install optional AWS dependency in the team's environment: `python3 -m pip install '.[aws]'`.
Set NDP_RAW_BUCKET (or `--s3-bucket`) to switch either local or API acquisition to
S3. The S3 adapter stores the original response body and source/time/hash metadata;
its reference includes VersionId when returned. Successful HTTP response bodies,
including malformed JSON, are archived; transport/HTTP errors are reported, not
archived as resource pages. Only then does extraction/handoff proceed. Archive
failure prevents handoff for that payload.

Use the team execution role via boto3's standard credential chain. Do not put
static AWS keys in code or pass NDP API credentials as command-line arguments.
IAM role authentication covers AWS/S3, not NDP; NDP's approved authorization is
configured separately. SDK credentials may also come from an approved local AWS
profile/environment. The package does not create buckets, roles or secrets.

Have existing IaC supply:
- Bucket/prefix and region; role with s3:PutObject limited to that prefix.
- Bucket encryption, public-access block and retention/versioning policy.
- If SSE-KMS is configured, the necessary KMS permissions and key policy.
- Approved secret injection for the NDP header value, and HTTPS/S3 network access.
- Existing container/job entrypoint and recurring trigger (for example the team's
  scheduler). Use the return status to alert/retry incomplete runs.

No scheduler is provisioned here. Repeated invocation uses the same callable/CLI.
No durable checkpoint store is added. The orchestrator owns any watermark and
must advance it only after every page and downstream acknowledgement succeeds.
Use an agreed overlap window if incremental filters permit; never advance based
only on local wall-clock time. Full replay is the default. Missing fixture/API
records do not imply deletion. Quarantine/storage/reconciliation are downstream.

## Accounting and failure recovery

For every parsed page:
`records_received = records_handed_off + duplicates_skipped + record_errors`.
These are input organizations, not service/entity counts. Handoff is not storage.
The team's stored+quarantined invariant needs downstream acknowledgement.
Errors include a stage and controlled reason code; record errors include archive
reference and pointer for replay. Unreadable files, bad JSON, archive failures,
network/pagination failures mark a run incomplete; unknown missing record counts
are not guessed. Earlier handoffs can already have committed; replay idempotently.
For diagnosis, resolve a record error through its archived raw payload. This
package reports errors; it does not create a competing quarantine database.

## Tests

`python3 -m unittest discover -s tests -v` covers original-byte preservation,
nested fields, duplicate/conflicting IDs, replay/upsert contract, isolated record
and handoff failures, invalid JSON, multi-page HTTP, server page caps, incremental
query forwarding, auth headers, timeouts/retries, 401, malformed pagination,
empty datasets, max-page limits, S3 bytes/metadata/version references, S3 failure,
and HTTP redirect refusal. HTTP and S3 tests are mocked; they do not prove live
connectivity. See VALIDATION.md for actual supplied-file execution results.
