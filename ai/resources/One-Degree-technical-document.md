# The Resource Engine: Hackathon Technical Document

## 1. Context for builders

1. Several teams build in parallel. Each team saves its work to its own GitHub repository and shares it afterward.
2. The event runs in an AWS Workshop Studio account. Bedrock and Textract are provisioned in advance.
3. The hackathon runs 2 days.
4. No production data and no production API keys. Teams work from a sample file, a mock 211 NDP API and synthetic data.
5. One Degree wants to move the best parts into its own platform afterward, so keep the code readable and the infrastructure reproducible.

## 2. Decisions from One Degree

| Topic | Decision |
|-------|----------|
| Project name | The Resource Engine |
| Data standard | Open Referral HSDS for all data that is shared, served or exchanged. Internal storage is flexible |
| Language | Python with FastAPI is the default. Teams may choose another language and document why. One Degree runs Rails in production and would prefer APIs in Rails long term, but does not expect Rails here |
| Containers | Docker. Target deployment is ECS on Fargate |
| CI/CD | GitHub Actions |
| Infrastructure as code | No preference. CloudFormation or Terraform. One Degree uses Terraform for staging only |
| Search | OpenSearch |
| LLM access | Amazon Bedrock. Anthropic models preferred. |
| Region | us-west-2 or us-east-1 for the event. Craig thinks One Degree's production region is us-west-1 and will confirm. One Degree said not to constrain the build |
| API auth | Bearer token |
| API style | REST |
| HSDS version | v3.2.3 |
| Website content for maintenance checks | Sample data for the MVP, behind a replaceable component so real sites can be added by configuration (section 7.3) |
| Review UI auth | Anything that works |
| Compliance | HIPAA and SOC 2 apply to One Degree but not to resource data. The hackathon does not touch the covered parts |
| Prohibited services or libraries | None stated |

## 3. Architecture

### 3.1 Components

| Component | Responsibility | Suggested technology |
|-----------|---------------|----------------------|
| Source adapters | Pull or receive data from a source and map it to HSDS | Python modules behind one interface (section 5.4) |
| Scheduler | Runs ingest and maintenance checks on a recurring basis | EventBridge Scheduler, or a scheduler inside the app |
| Raw store | Keeps each payload exactly as received | Amazon S3, keyed by source and fetch time |
| Transformer and validator | Converts to HSDS and validates against the schema | Python with a JSON Schema library |
| HSDS store | Holds validated HSDS records and change history | Team's choice (section 3.2) |
| Search index | Keyword and semantic search (Priority 5, built from the HSDS store) | Amazon OpenSearch Service |
| API | Serves HSDS JSON | FastAPI in a container |
| AI maintenance worker | Fetches websites, extracts facts, compares, produces findings | Bedrock (Anthropic models), Textract for PDFs |
| Findings store | Holds findings, evidence and review state | Team's choice |
| Review UI | Lists, filters, shows diffs, edits records | Team's choice |

### 3.2 Storage guidance

1. HSDS governs data when it is shared or served, not how it is stored. The HSDS documentation says systems that publish HSDS are not expected to keep any particular internal schema.
2. Whatever a team stores, it must be able to round-trip to valid HSDS JSON.
3. [Open Referral publishes SQL and PostgreSQL schemas generated from the HSDS schemas](https://docs.openreferral.org/en/v3.3.1/hsds/database_schemas.html). A team that wants a relational store can start there.
4. Keep operational data (findings, evidence snippets, review state, model logs, user accounts) in separate tables or stores from the HSDS records. Do not mix it into the HSDS tables. Evidence and provenance that must travel with a record use the extension convention in section 4.5.
5. One Degree's current database is unknown. Teams choose their own.

### 3.3 Deployment

1. The deliverable is containers that One Degree can run on ECS Fargate.
2. A baseline that runs locally with Docker Compose is acceptable. Deploying to ECS in the Workshop Studio account is optional, and each team decides.
3. Teams may use other AWS services (Lambda, Step Functions, EventBridge, SQS) where they help. Document each one, because One Degree will have to operate it.


## 4.  ODRS (One Degree's legacy system)

Sample: BayesExport_20250729.json, the output of the One Degree API at data.1degree.org/v1/opportunities. It reports 127 opportunities across 3 pages of 50. 

Response shape:

1. A paging object with total_count, per_page, current_page, total_pages, previous_page, next_page, first_page and last_page. The adapter follows next_page.
2. An opportunities array. Each opportunity has id (integer), title, description, slugs, organization (id, name, website, no description), tags, categories, areas, schedule, properties (a flat map of flags and text), locations (each with address, coordinates, phones and a weekly schedule), phones, emails, attachments, access_instructions, rating fields, has_pending_submission, last_verified_at, updated_at and unavailable_at.

Mapping to HSDS. 

| ODRS field | HSDS target | Notes |
|-----------|-------------|-------|
| opportunity.id | service.id | Derive a UUID. Keep the native id in the id map and in x_resource_engine.source |
| title | service.name | |
| description | service.description | |
| organization | organization | name and website map directly. The source has no description, which HSDS requires. See 5.1.1 |
| last_verified_at | service.assured_date | |
| updated_at | service.last_modified | |
| unavailable_at | service.status | null means active. A date means the opportunity is no longer available; map to inactive or defunct. Teams decide and document it |
| properties.elig-description | service.eligibility_description | |
| properties.elig-age-or-over, elig-age-or-under | service.minimum_age, maximum_age | Values are strings in the source |
| properties.elig-documentation | required_document.document | One record per item. The source separates items with semicolons |
| properties.elig-*, community-*, citizenship-*, gender-* flags | attribute linked to a taxonomy_term | No structured HSDS field exists for these |
| properties.cost-free, cost-pay, cost-note | service.fees_description | cost_option is available for structured prices |
| properties.lang-* | language (name, code) | lang-all-languages-by-interpreter fits service.interpretation_services |
| properties.service-county-*, elig-city-resident, elig-county-resident | service_area.name | |
| properties.ada-accessible, ada-accessible-notes | accessibility on the location (description, details) | Source values include Yes and Unknown |
| properties.action-signup-url | service.url | |
| access_instructions | service.application_process, plus phone and url records | Combine the instruction text into readable steps |
| is_appointment, properties.time-appointment-required | application_process text, or an attribute | |
| tags, categories, areas | attribute linked to taxonomy_term | See 4.9 |
| phones | phone | number, extension, type voice, description from phone_type. Split "x 4" style extensions out of the number |
| emails | service.email, or a contact record | I have not checked the contact object's fields |
| locations | location, address, service_at_location | location_type physical; address_1 from address; address_2 from unit; state_province from state; postal_code from zip_code; two-letter country code US; latitude and longitude from lat and long |
| locations[].phones | phone with location_id | |
| locations[].schedule, opportunity schedule | schedule | See 4.8. Notes go to the notes property |
| available_on, expires_on | schedule.valid_from, valid_to | Null in the sample |
| attachments, images | not mapped | The sample has one attachment (record 6126) with no download URL. Keep attachments in the raw store if needed |
| rating, rating_tags, has_pending_submission, slug, all_slugs, region, translations | not mapped | HSDS leaves service feedback out of scope. Keep in the raw store |

## 5. Security and data handling

1. Use only the sample file, the mock NDP API, staging credentials One Degree issues for the event, and synthetic data. Do not use or ask for production API keys.
2. Do not commit secrets or the sample file's attachment URLs. Keep secrets in environment variables or AWS Secrets Manager.
3. Use least-privilege IAM roles for each component, encryption in transit, and encryption at rest for S3, the database and OpenSearch.
4. Resource data does not contain client or referral records. If a team finds personal client data anywhere, stop and tell One Degree.
5. HIPAA and SOC 2 do not apply to this build. Move to production under One Degree's own controls.
