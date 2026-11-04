## 4. HSDS rules

### 4.1 Version

1. Pin one tagged release of github.com/openreferral/specification and validate against its schema files.
2. Use v3.2.3. The published HSDS documentation I read tracks 3.2.x, and its API reference links to the 3.2 OpenAPI file. The repository also has tags for v3.3 and v3.3.1, and I did not review what changed.
3. The schema files in the repository are authoritative. The field names in this document came from the documentation pages and should be checked against the pinned schema files.

### 4.2 Boundary rule

Any data that leaves a component as shared data is valid HSDS JSON for the pinned version. That covers API responses, exports, files handed to another team or system, and messages between services that carry resource records. Internal storage can differ.

### 4.3 Required fields

These are the required properties in the pages I read. A record missing any of them fails validation.

| Object | Required properties |
|--------|--------------------|
| organization | id, name, description |
| service | id, name, status |
| location | id, location_type |
| address | id, address_1, city, state_province, postal_code, country, address_type |
| phone | id, number |
| service_at_location | id |
| schedule | id |
| metadata | id, last_action_date, last_action_type, field_name, previous_value, replacement_value, updated_by |
| attribute | id |
| url | id, url |

Other objects (language, accessibility, service_area, required_document, cost_option) require only id.

Allowed values worth knowing: service.status is active, inactive, defunct or temporarily closed. location_type is physical, postal or virtual. address_type is physical, postal or virtual.

### 4.4 Identifiers and empty values

1. Every id is a UUID.
2. Generate ids deterministically (for example UUIDv5 from the source name plus the native id) so re-ingest updates records instead of duplicating them (R1.6). Keep a map from HSDS id to source record.
3. Omit a property that has no value. The HSDS mapping guidance prefers omitting a property to sending null or an empty string, unless the property is required.

### 4.5 Extensions

1. HSDS allows extra properties when no HSDS property fits, and says not to use extras where an HSDS term would do. The HSDS JSON schemas accept unknown properties by default, so extensions do not fail validation. Document every extension.
2. We recommend putting all Resource Engine extras in one property per record, named x_resource_engine, so they stay easy to find and strip:

```json
{
  "id": "…",
  "name": "…",
  "x_resource_engine": {
    "source": { "system": "odrs", "record_id": "22560", "fetched_at": "2026-10-20T15:04:05Z" },
    "last_check": { "category": "discrepancy", "checked_at": "2026-10-20T16:00:00Z" },
    "evidence": [
      { "field": "eligibility_description", "url": "https://example.org/apply", "snippet": "…", "model": "…" }
    ]
  }
}
```

3. Keep extensions few and documented. Teams decide which ones they need.

### 4.6 Change history

Use the HSDS metadata object for every applied change. It records the resource, the field, the previous value, the replacement value, who changed it, when, and the kind of action. Evidence, proposals that were not applied, and model logs do not belong in metadata. They belong in the findings store.

### 4.7 Validation

1. Validate against the pinned JSON Schema files with a JSON Schema library, in three places: the transformer output before storage, API responses in tests, and any export before it leaves the system.
2. A record that fails is quarantined with the validation error (R1.5).
3. The hosted HSDS validator from Open Referral UK exists. The older openreferral/hsds-validator repository checks data packages, so I would not rely on it for 3.x JSON without testing it. I did not test either.

### 4.8 Schedules

1. HSDS schedules use RFC 5545 recurrence rules. Weekly opening hours use freq WEEKLY, byday (MO, TU and so on), opens_at and closes_at.
2. opens_at and closes_at use HH:MM and should include a UTC offset, such as 09:00-08:00. The ODRS sample has local Pacific times with no offset, and the right offset changes with daylight saving. Pick a rule, document it, and test it.
3. A lunch closure is two intervals, not one. The ODRS data keeps it in free text ("Closed daily 12:00 PM - 1:00 PM"). Turning that text into two schedule entries is a good job for the model.
4. Free-text schedule notes can go in the schedule notes or description properties.

### 4.9 Taxonomy

HSDS does not define a service taxonomy. It provides taxonomy, taxonomy_term and attribute objects so you can attach one. One Degree's tags, categories and areas need a taxonomy defined by the team (for example, "One Degree") with terms linked to services through attribute records. Eligibility-style flags from ODRS (homeless, veteran, youth and similar) use the same mechanism.

### 5.1 ODRS (One Degree's legacy system)

#### 5.1.1 The organization description gap

HSDS requires organization.description, and the ODRS API does not return one. A record cannot be stored as valid without it. Options, in order of preference:

1. Have the AI maintenance layer write a short description from the organization's website, and mark it as AI-generated in x_resource_engine.
2. Quarantine the organization until a description exists.
3. Use a plain fallback such as the organization name with a note that the description is not available, and flag the record in the "missing information" category.

Teams decide which to use and say so in the README.

#### 5.1.3 Handling the sample file

1. The sample file has no attachment download URLs. If you pull fresh data from the ODRS API, check attachment URLs for embedded credentials and strip them before the data goes in any repository.
2. The sample records are real public directory entries and are fine to load in the sandbox. Teams work from the sample file and mocks. Craig's offer of a staging API key is optional.

### 5.2 211 NDP

1. The real NDP format is not HSDS and is not available to teams. Craig will build a mock API in the same format with fake records, host it, and give teams the URL. [CONFIRM the timing and the auth the mock expects.]
2. Until the mock exists, build the second adapter against the adapter contract in 5.4 and use a stub.
3. NDP updates will arrive daily in production (Priority 4).

### 5.3 Spreadsheets (stretch)

1. Two layouts that look nothing alike are the goal. Examples from One Degree: a county list of about 500 resources, and a food pantry list where each row's details sit in one cell.
2. Column mapping is a natural job for the model, with a person confirming the mapping once per layout. PDFs and Google Docs are not a focus. Textract is available if a team wants to try PDFs.
3. Steffi will send sanitized examples. Teams can also generate synthetic spreadsheets in the same shapes.

### 5.4 Adapter contract

Each source implements the same interface so adding a source is configuration plus one module:

1. name: a stable source identifier used in ids and in x_resource_engine.source.
2. fetch(since): yields raw payloads. Each payload is written to the raw store untouched before anything else happens.
3. map(payload): yields HSDS objects (organization, service, location, address, phone, schedule and the rest) for one source record.
4. native_id(payload): returns the source's record id, used for deterministic HSDS ids.
5. Errors: an adapter reports records it cannot map instead of raising for the whole batch.

## 6. API

The API serves HSDS. The HSDS API reference defines the standard endpoints, and it is the model to follow.

| Endpoint | HSDS API reference status | Purpose |
|----------|--------------------------|---------|
| GET / | Required | Information about the API and its relationship to HSDS |
| GET /services | Required | Paginated list of services; supports page, per_page, modified_after, organization_id, taxonomy_term_id, taxonomy_id, minimal and full. The search parameter is Priority 5 and is backed by the OpenSearch index |
| GET /services/{id} | Required | One service, fully nested |
| GET /organizations and /organizations/{id} | Optional | |
| GET /service_at_locations and /service_at_locations/{id} | Optional | |
| GET /taxonomies, /taxonomy_terms and their {id} forms | Optional | |
| POST on services, organizations and the others | Optional | Not needed for the MVP |

Rules:

1. List responses are paginated with the HSDS Page schema: total_items, total_pages, page_number, size, first_page, last_page and empty, with the records in a contents array.
2. Query parameters combine with AND.
3. The metadata property is optional on each object. Include it where it helps, and not in large lists by default.
4. Bearer token auth on every endpoint. 
5. A key may be scoped to a single source, so a caller sees only that source's records. Craig described this.
6. The canonical OpenAPI file is at raw.githubusercontent.com/openreferral/specification/3.2/schema/openapi.json. FastAPI generates an OpenAPI document, so teams can compare their output with the canonical one.
7. The API is REST.


## 7. AI maintenance

### 7.1 Flow

1. The scheduler selects records due for a check.
2. The worker gets the organization's website content through a website source component (section 7.3). In the MVP that content is sample data.
3. The model extracts the facts staff verify: counties or area served, eligibility (residence, age, income), hours, languages, accessibility and contact details.
4. The worker compares the extracted facts with the stored record and assigns a category (R2.2).
5. For each discrepancy, the worker stores the current value, the proposed value, the source URL and the supporting text in the findings store.
6. The review UI shows flagged records. A person applies, edits or dismisses each proposal.
7. An applied change updates the HSDS record, adds a metadata entry, and validates the result.

### 7.2 Guidance

1. Models: use Anthropic models through Bedrock. Check which models are enabled in the Workshop Studio account at the start, and record the model ID with each finding.
2. Evidence: a proposed value without supporting text is not a finding. Flag it as low confidence or drop it.
3. Materiality: compare facts, not prose. A change in wording is not a discrepancy (R2.5).
4. The website is treated as more accurate than the record in the MVP (R2.4).
5. Failures are findings. A site that does not load, a certificate that has expired, or a page the agent cannot read gets the actively bad or check failed category, not a silent skip.
6. Drafting text: if the model writes proposed text, ask for the One Degree style (seventh-grade reading level, under four sentences, no marketing language). That is prompt guidance, and enforcing it is out of scope.
7. Good fetch behavior for live sites: send an honest user agent, respect robots.txt and rate limit requests per site. This applies once a live source is switched on.
8. Log model inputs, outputs and model ID for each finding. This is operational data, not HSDS.
9. Cost: cache fetched pages, skip unchanged pages (compare a hash), and avoid re-sending a whole site on every check.

### 7.3 Website content

The MVP may optionally fetch live organization websites. Build the fetching code as a replaceable component so a real site can be added later by configuration:

1. A website source interface: given a URL, return the page content, the HTTP status and any error.
2. A sample implementation that reads the sample content. This is the default.
3. A live implementation that makes real HTTP requests, with an honest user agent, robots.txt handling and per-site rate limits. It is built behind the same interface and turned on by configuration. Teams do not need to finish it for the MVP.
4. Nothing outside the website source component should know which implementation is running.

### 7.4 Evaluation

1. Build a small test set with known truth. Seed discrepancies on purpose: change the number of counties, remove an eligibility rule, shut a site down.
2. Report how many seeded discrepancies were caught and how many flags were wrong, by category and by field type.
3. One Degree will set the target thresholds.


## 8. Review UI and editing

1. Lists: everything ingested, everything flagged, filterable by category and by source.
2. Record view: the stored value, the proposed value and the evidence side by side, with apply, edit and dismiss.
3. Editing: any field can be edited directly in a flexible, spreadsheet-like experience. One Degree dropped the idea of strict page-by-page validation for the MVP.
4. HSDS still applies. Allow free editing of text, but run schema validation on save. If an edit would produce a record that fails validation, block it with a clear message, or hold the record out of anything the API serves until it is fixed. Teams choose which.
5. Every saved edit writes a metadata entry with the editor's name.
6. Authentication: anything that works. The review UI does not have to integrate with One Degree's auth.


## 10. Handoff

Each team's repository should include:

1. A README with how to run it locally, how to run the tests, the pinned HSDS version, and the decisions it made (for example, how it handled the organization description gap and schedule offsets).
2. A Dockerfile for each service and a Compose file or equivalent.
3. Infrastructure as code (CloudFormation or Terraform) for anything deployed to AWS.
4. A GitHub Actions workflow for build, test and deploy.
5. A list of documented HSDS extensions.
6. A list of the AWS services used.

## 11. Testing guidance

1. Schema validation tests for every HSDS object an adapter produces.
2. Adapter tests using the ODRS sample, including the quirks in 5.1.2.
3. A count test: received equals stored plus quarantined (R1.7).
4. API tests that validate responses against the schema, including a paginated list.
5. A seeded evaluation for the maintenance layer (7.4).

