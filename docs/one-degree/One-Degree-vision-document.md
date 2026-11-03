# The Resource Engine: Hackathon Vision Document

## Summary

One Degree is a nonprofit that helps low-income individuals and families find free and low-cost services near them. Its main product is 1degree.org, a free directory of more than 30,000 nonprofit and government resources, and it also builds tools for government agencies, healthcare organizations and community-based organizations that refer people to services. Each listing in the 1degree.org directory describes a service: what it offers (food, shelter, health care, job training, legal aid, benefits enrollment), who is eligible, where it is, when it is open, which languages it supports, how accessible it is and how to contact it. The directory matters because people can't use help they don't know about. One Degree points out that more than 100 million people in the U.S. face economic insecurity, yet about $80 billion in safety net programs goes unused every year, largely because the systems for finding help are outdated 

The Resource Engine is a new resource data platform for One Degree. It ingests community resource data from more than one source in different formats, keeps the raw data as received, converts it to Open Referral HSDS, and serves it through an API. An AI maintenance layer then checks stored records against each organization's public web presence and flags records that are wrong, incomplete or unreachable, so staff review and fix flagged records instead of researching every record by hand.

It is intended to replace One Degree's existing resource service (ODRS) over time. The hackathon builds the foundation and as much of the rest as time allows, in the priority order below. It does not sync back into ODRS.

## Background

The problem. One Degree and 211 resource directories connect people to more than 30,000 local services. A directory starts going out of date the day it is published. Specialists keep listings accurate by researching each organization and calling it, which is too slow to keep up with change.

How maintenance works today. One Degree calls it resource refresh. Paid staff and a volunteer team open a record in an editing tool that sits on top of ODRS and check the organization's website (which might also be a Facebook page) for accessibility, languages, eligibility requirements and hours. If the website doesn't have the right data, they call. Steffi described the editing tool as workable but not good.

The current stack. ODRS is old. It is a set of services, data stores and an index that hold resource data in One Degree's own format and serve it to the front end through APIs. A RAG process also consumes the data. One Degree plans to rebuild it and has pushed the full rebuild to next September, so the hackathon is a chance to prototype the foundation now.

The 211 project. One Degree has an upcoming project with 211. 211 data arrives through the NDP API in a format different from One Degree's. One Degree has production access to that endpoint for its partner but has not started ingesting. Once the integration is live, updates arrive every morning. Steffi expects 211's NDP data to remain the source of truth until One Degree's maintenance layer is good enough to replace the legacy tooling 211 uses to maintain it, which she expects to take a year or more and to be hard.

The standard. Open Referral publishes the Human Services Data Specification (HSDS) for exactly this data. Neither One Degree nor 211 uses it today. One Degree wants the Resource Engine to adopt it.

Other inputs. Partners also send data as spreadsheets (for example, 500 resources from the County of San Mateo, or a food pantry list with each record's details packed into a single cell), PDFs and Google Docs.

## Users

| User | Description | What they need |
|------|-------------|----------------|
| Resource specialist | Paid One Degree staff and volunteers who verify and edit records | A short list of records that need attention, with the evidence, and an easy way to fix them |
| Engineer at One Degree | Builds the front end, the RAG process and future features on resource data | One consistent, documented data format and an API, whatever the original source |
| Data partner | A county, 211 or another organization that supplies resource data in its own format | A way to add their data without custom work in the core system |
| Navigator and help seeker | People who search and use the resource data through One Degree's products | Accurate listings (not in scope for the hackathon, but the reason for the work) |

## Architecture at a glance

```
Sources                    The Resource Engine
-------                    -------------------
ODRS API (One Degree)  \
211 NDP API (mock)      >-- ingest adapters --> raw store (as received)
Spreadsheets (stretch) /                              |
                                                      v
                                       transform + validate --> HSDS store --> API (HSDS JSON)
                                                      |                             |
                                                      v                             v
                                          AI maintenance layer              consumers
                                       (checks records against websites)
                                                      |
                                                      v
                                          review UI (flagged records, edits)
                                                      |
                                                      v
                                         updates written back to HSDS store
```

Steffi's diagram shows One Degree's front end connected to both the new HSDS store and ODRS, the old (legacy) system that stores resource data "in a specific way". The front end is out of scope for the hackathon, but they do want a way for specialists to review data that needs human validation. ODRS is the system the new store replaces over time. The diagram also puts deduplication of organizations in the ingest pipeline.

## Priorities

If time runs out, stop at the lowest item that is finished. Teams are expected to be ambitious. 

Steffi numbered these items in her "workshop hackathon" diagram: 1 ingest and transform to HSDS, 2 AI maintenance, 3 regular updates from the 211 NDP API (reconciliation), 4 REST APIs, 5 search by API. The agreed final order in the meeting was 1, 2, 4, 3, 5, which gives the order below. The diagram confirms the mapping.

| Priority | Capability | Status |
|----------|-----------|--------|
| 1 | Ingest from two sources, store raw data, convert to HSDS and store it | MVP |
| 2 | AI maintenance: check records against websites, flag problems, review UI, human editing | MVP |
| 3 | REST API to list records and get a record by ID, with bearer token auth | MVP |
| 4 | Handle recurring updates from a source without losing manual or AI-driven changes (reconciliation) | Design for it, implement if time allows |
| 5 | Search through the API, backed by an index (OpenSearch). Semantic search is the nice-to-have on top | Stretch |

Steffi and Craig both said they would be very happy if items 1 through 5 were done.

## Requirements

### Priority 1: Ingest and HSDS store

1. R1.1 The system ingests from two sources: a mock of the 211 NDP API format and various spreadsheets they receive from partners. Both are supplied by One Degree (see Dependencies).
2. R1.2 Sources are configuration plus an adapter. Adding a third source does not require changes to the core pipeline.
3. R1.3 The system stores each payload as received, with the source name and fetch time.
4. R1.4 The system converts every record to HSDS and stores it. Every stored record validates against the pinned HSDS schema.
5. R1.5 A record that cannot be converted to valid HSDS is quarantined with the reason. It is not dropped silently and not stored invalid.
6. R1.6 Re-running ingest on the same source updates existing records instead of creating duplicates.
7. R1.7 For each source, records received equal records stored plus records quarantined, and the system reports those counts.
8. R1.8 Stretch: ingest two spreadsheets with very different layouts. They receive content in all sorts of formats, including PDFs.

Deduplication across sources is hard and was named as a real need (the same food pantry from the API and from a county spreadsheet, with conflicting details). Steffi's diagram shows it as "Deduplication of orgs" inside the ingest pipeline box. Dealing with deduplication is a real need, but teams should consider it a stretch goal as part of the hackaton. Teams should keep a link from each HSDS record back to its source record so duplicates can be found later.

### Priority 2: AI maintenance

1. R2.1 The system checks a stored record against the organization's website content and compares the facts that specialists verify today: counties or area served, eligibility (residence, age, income), hours, languages, accessibility and contact details. 
2. R2.2 Each check assigns the record a category. Working categories, subject to Steffi's final list:
   - Verified: the record matches the source.
   - Discrepancy: the web shows something different or additional (for example, the record says four counties and the website says nine).
   - Actively bad: the website no longer works, the organization appears to have closed, or its certificate has expired. One Degree treats these as the most critical.
   - Check failed: the agent could not access the site or the check errored.
   - Missing information: the record lacks fields such as hours or eligibility, and the web may supply them.
3. R2.3 For every discrepancy, the system shows the current value, the proposed value, and the evidence (the source URL and the supporting text).
4. R2.4 For the hackathon, the organization's website is treated as more accurate than the stored record. One Degree knows nonprofit websites are sometimes wrong. Over time they expect to add annotations such as "confirmed by phone", so the design should not make that impossible.
5. R2.5 The system flags factual differences only. It does not flag wording or tone differences, because One Degree does not want staff reviewing changes of three words.
6. R2.6 The check runs on a recurring basis, not once. Each record shows when it was last checked and the result.
7. R2.7 A review UI lists everything that was ingested and everything that was flagged, filterable by category, with a verified marker.
8. R2.8 Staff can edit any field of a record directly. One Degree wants an editing experience that feels flexible, like a spreadsheet or Airtable, not a rigid page-by-page flow, and does not want heavy business-rule validation on edits for the MVP. The one check that stays is HSDS schema validation on save, so the API never serves an invalid record.
9. R2.9 The AI proposes changes. A person applies them. 
10. R2.10 Every applied change records who changed what, from what value, to what value, and when.

Content style rules (seventh-grade reading level, under four sentences, no marketing language) can be used as prompt guidance when the AI drafts text, but enforcing them is out of scope. Accuracy matters more than tone.

### Priority 3: API

1. R3.1 The API lists records and gets a record by ID. Steffi's diagram scopes this item as "get by ID, get by list".
2. R3.2 It uses bearer token authentication.
3. R3.3 It is a REST API and returns HSDS JSON.
4. R3.4 Nice to have: an API key limits a caller to the records from one source. 

Search is a separate item (Priority 5). In the meeting Craig said search could be keyword search, and Steffi pointed out that it needs an index, so her diagram lists search as its own item with the index underneath it.

### Priority 4: Reconciliation

Once a source delivers updates regularly, an update from the source can clash with a change made by the AI maintenance layer or by a person. Craig is unsure how to solve this and said the answer may need redoing once One Degree sees real data, so he does not want to prescribe it. For the hackathon:

1. R4.1 Treat ingest as repeatable, not one time.
2. R4.2 Keep enough history (source, time, who or what changed each field) to tell a source update from a manual or AI change.
3. R4.3 If time allows, detect a clash and surface it for a person to decide, instead of silently overwriting either side.

### Priority 5: Search

1. R5.1 The API searches records through an index (OpenSearch). Keyword search is the base case.
2. R5.2 Nice to have: semantic search on top of the index. Craig expects it to exist eventually.
3. R5.3 Search results are HSDS, and the index is rebuilt from the HSDS store, so it can be dropped and recreated.

## Not in scope for the MVP

| Item | Reason |
|------|--------|
| Syncing or writing back to ODRS, including its submission data model | The new store replaces ODRS. Treat it as a separate path |
| Enforcing content style rules | Accuracy first |
| Heavy validation on manual edits | Not wanted for the MVP. HSDS schema validation on save still applies |
| Integration with One Degree's authentication | Use whatever works |
| Real client data and production API keys | Teams use mocks, a sample file and synthetic data |
| Phone or email outreach to organizations | Not discussed |
| Multilingual listings | Not discussed |

## Journeys

Journey 1, a platform engineer adds a source. The engineer points the system at the ODRS format and the NDP mock. The system stores the raw payloads, converts the records to HSDS, validates them, and reports how many records were stored and how many were quarantined and why.

Journey 2, a specialist works the flagged list. The specialist opens the review UI, filters to actively bad records, and sees a nonprofit whose website is gone. They mark the record, edit the details, and the change is saved with their name and the time. Then they work through discrepancies, each shown with the old value, the new value and the evidence.

Journey 3, an application reads the data. A consumer calls the API with a bearer token, lists records and fetches one by ID. If Priority 5 is done, the consumer also searches. Every response is HSDS.

Journey 4, a daily update arrives (Priority 4). A source change touches a field a specialist edited yesterday. The system shows the clash and asks a person to choose.

## Success measures

For the hackathon:

1. Every record in the HSDS store, and every record the API returns, validates against the pinned HSDS schema.
2. For each source, records received equal records stored plus records quarantined.
3. The system can add or describe a third source by writing an adapter and configuration only.
4. The AI maintenance layer can demonstrate catching and surfacing discrepancies to a specialist. 
5. A specialist can take a flagged record to a saved fix in a few steps. The demo shows the steps and the time.

## Constraints and assumptions

1. All data the system shares, serves or exchanges follows HSDS closely. Internal storage can be flexible (see the Technical Document).
2. Teams do not receive production API keys. Mocks are safer than vending keys that One Degree does not administer.
3. HIPAA and SOC 2 apply to One Degree as an organization but not to resource data, and the hackathon does not touch the parts of the platform they cover.
4. The hackathon runs for 3 days in an AWS Workshop Studio account with Bedrock and Textract already provisioned. 
5. Each team saves its work to its own GitHub repository and shares it afterward.
6. Assumption: the NDP mock reflects the real NDP format closely enough that an adapter built against it needs little rework.
7. Assumption: a one-time ingest is acceptable for the MVP, with repeatable ingest as a design goal (Priority 4).
8. Where a decision is not critical, such as the language, the database, how to fill gaps in source data, the development teams decide and document their choice in their README.

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| The model reports wrong or invented facts | Staff lose trust in the flags | Require evidence for every proposed change and keep a person in the loop |
| Organization websites are wrong or out of date | A correct record gets flagged | Treat web findings as proposals, and keep room for later human annotations |
| Live sites, once added, block automated access or render content with scripts | Many checks fail | Record these as "check failed" and count them in the results. The sample content can simulate these failures |
| Source data lacks fields HSDS requires (for example, organization description) | Records fail validation | Teams choose how to fill or quarantine, and document it; see the Technical Document |
| Flexible editing produces records that fail HSDS | The API serves invalid data | Validate on save |
| Hours are converted incorrectly (HSDS wants a time zone offset) | Wrong hours shown to people | Test conversions on the sample data, including daylight saving transitions |
| Reconciliation has no agreed rule | Wasted effort | Design for it and defer the rule |

## Dependencies and owners

| Item | Owner | Status |
|------|-------|--------|
| Mock 211 NDP API, hosted where teams can reach it, using the real NDP format with fake records | Craig | Planned. Craig will build it with AI help and host it |
| Final category list for AI maintenance results | Steffi | To send |
| Example spreadsheets for the stretch goal | Steffi | To send (sanitized) |
| Sample website content for the maintenance checks, including seeded differences and failures | Teams, with Steffi's categories as a guide | Teams build it |

## Glossary

1. ODRS: One Degree Resource Service, One Degree's old (legacy) system that stores and serves its resource data. The new platform is informally "ODRS 2.0" and replaces it over time.
2. Opportunity: One Degree's word for a resource or program that an organization offers. It maps to an HSDS service.
3. NDP: the 211 API that supplies 211 resource data. I do not know what the acronym stands for.
4. HSDS: Open Referral's Human Services Data Specification, an exchange format for human services data.
5. Resource refresh: One Degree's name for the maintenance process that verifies and updates records.
6. Raw store: the as-received copy of source data, kept alongside the HSDS version.
