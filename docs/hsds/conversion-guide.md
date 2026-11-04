# Converting source data to HSDS v3.2.3

Rules for mapping any source record to HSDS v3.2.3. Each rule says where it comes from:

- **[spec]** normative HSDS: the JSON schemas and the Conformance page. Must be followed.
- **[guide]** HSDS guidance pages (Field Guidance, Identifiers, Mapping, Serialization). These
  are non-normative, but follow them unless there is a reason not to.
- **[BEVIJEN]** decisions for this project, implemented in `backend/transform/`
  (see [its README](../../backend/transform/README.md)).

Field-level details: [`objects/`](objects/README.md). Links between objects:
[relationships.md](relationships.md).

## 1. Conformance

1. **[spec]** JSON output must validate against the HSDS v3.2.3 JSON schemas.
2. **[spec]** Use an HSDS field whenever one fits. Use it with the meaning in its description,
   not just because the name matches.
3. **[spec]** Extra properties are allowed only when no HSDS field fits. The schemas accept
   unknown properties. **[guide]** Document every extra property.
4. **[spec]** A publication may use only a subset of fields. Non-required fields can be left out.

## 2. Required fields

Every required field across the 24 objects (the `id` of each object is also required):

| Object | Required besides `id` |
|---|---|
| `organization` | `name`, `description` |
| `service` | `name`, `status` |
| `location` | `location_type` |
| `address` | `address_1`, `city`, `state_province`, `postal_code`, `country`, `address_type` |
| `phone` | `number` |
| `program` | `name`, `description` |
| `taxonomy` | `name`, `description` |
| `taxonomy_term` | `name`, `description` |
| `organization_identifier` | `identifier_type`, `identifier` |
| `url` | `url` |
| `unit` | `name` |
| `service_capacity` | `unit`, `available`, `updated` |
| `metadata` | `last_action_date`, `last_action_type`, `field_name`, `previous_value`, `replacement_value`, `updated_by` |
| all others | only `id` |

**[BEVIJEN]** Never invent a value for a required field. If the source lacks it, leave it
missing (`NA`) and let validation report it (`report_validation.csv`). Known gaps:
ODRS `organization.description` and `service.status` (technical document 5.1.1).

## 3. Identifiers

1. **[spec]** Every `id` is a UUID (RFC 4122). Every `*_id` foreign key holds the `id` of the
   related row.
2. **[spec]** IDs are unique within their object type.
3. **[BEVIJEN]** Keep a source value that is already a UUID. Otherwise use
   UUIDv5(`source|table|native id`), so the same input always gives the same ids.
4. **[guide]** Source system ids, registry numbers and tax ids are not HSDS `id`s. Put them in
   `organization_identifier` (`identifier_scheme` from org-id.guide, e.g. `US-EIN`, plus
   `identifier` and the required `identifier_type`), in `location.external_identifier` and
   `external_identifier_type`, or in `organization.tax_id`. Prefer primary or legal identifiers,
   then secondary, then third-party. Leave out local ids when a better one exists.

## 4. Types, formats and allowed values

| Kind | Fields | Rule |
|---|---|---|
| UUID | all `id`, `*_id` (except `tax_id`, `external_identifier`) | RFC 4122 UUID |
| Email | `organization.email`, `service.email`, `service.assurer_email`, `contact.email` | `format: email` |
| URI | `organization.website`, `organization.uri`, `service.url`, `location.url`, `accessibility.url`, `url.url`, `schedule.schedule_link`, `required_document.uri`, `taxonomy.uri`, `taxonomy_term.term_uri`, `unit.uri` | `format: uri`, with a scheme (`https://...`) |
| Number | `location.latitude`, `location.longitude` (WGS84 decimal degrees), `service.minimum_age`, `service.maximum_age`, `cost_option.amount`, `organization.year_incorporated`, `phone.extension`, `service_capacity.available`, `service_capacity.maximum`, `schedule.timezone`, `schedule.interval`, `schedule.count` | JSON number, not a string |
| Date | `schedule.valid_from`, `valid_to`, `dtstart`, `until`; `cost_option.valid_from`, `valid_to`; `service.assured_date`; `metadata.last_action_date` | ISO 8601 `YYYY-MM-DD` (a plain string in JSON; typed `date` in the tabular package) |
| Date-time | `service.last_modified`, `service_capacity.updated` | ISO 8601 date-time with offset, `format: date-time` |
| Time | `schedule.opens_at`, `schedule.closes_at` | `HH:MM` with timezone: `Z` or an offset (`09:00-08:00`) |
| Country | `address.country` | ISO 3166-1 alpha-2 (`US`) |
| Enum | `service.status` | `active`, `inactive`, `defunct`, `temporarily closed` |
| Enum | `location.location_type`, `address.address_type` | `physical`, `postal`, `virtual` |
| Enum | `schedule.freq` | `WEEKLY`, `MONTHLY` |
| Enum | `schedule.wkst` | `MO` … `SU` |

Each field's exact type and format are in its object page under [`objects/`](objects/README.md).

## 5. Text fields

1. **[guide]** `name` and `alternate_name`: plain text only, no markup.
2. **[guide]** `description` may hold HTML or Markdown. Consumers often take the first sentence
   as a short description, so put the key point first.
3. **[guide]** If the organization name is the only name available, it may also be used as the
   `service` or `location` name. Record that choice.

## 6. Structure: core objects and links

1. **[spec]** Core objects: `organization`, `service`, `location`, `service_at_location`.
2. A `service` belongs to an `organization` (`service.organization_id`).
3. A `service` is offered at a `location` **through `service_at_location`**, never by a direct
   service→location link. Use `service_at_location` for details that hold only at that place
   (phones, schedules, contacts, service areas).
4. `address`, `accessibility` belong to a `location`. A location can have several addresses
   (`physical`, `postal`).
5. `phone`, `contact`, `schedule`, `language` can belong to several kinds of parent. Set the
   foreign key for the parent they describe. See [relationships.md](relationships.md).
6. **[spec]** `attribute` links a `taxonomy_term` (or a free `value`) to any object through
   `link_id`, with `link_entity` naming the table. `taxonomy_term.taxonomy_id` points to its
   `taxonomy`. `parent_id` builds hierarchies.
7. **[spec]** `metadata` records one change per row and attaches to any object through
   `resource_id` and `resource_type`.

## 7. Schedules

1. **[spec]** Schedules are RFC 5545 RRULE fields: `freq`, `interval`, `byday`, `bymonthday`,
   `byweekno`, `byyearday`, `dtstart`, `until`, `count`, `wkst`.
2. **[guide]** One schedule row per distinct pattern. "Mon–Thu 9–12 and 15–17" is two rows
   (`freq=WEEKLY`, `byday=MO,TU,WE,TH`, one row per time range).
3. **[guide]** `valid_from` and `valid_to` give the period the data is believed correct.
   `until` means the service really ends. Do not confuse them.
4. **[guide]** For holiday or exceptional hours, end the regular schedule with `valid_to` and add
   new rows, or add extra rows for additional hours.
5. **[guide]** Open 24 hours: `opens_at=00:00`, `closes_at=23:59`.
6. **[guide]** `description` is a human summary of the same hours and adds nothing new.
   Exceptions go in `notes`.
7. **[BEVIJEN]** Times are written as `HH:MM` without an offset, because sources give local
   time. The offset rule (daylight saving) is still an open decision (technical document 4.8).
   Text that does not parse fully stays in `description`. Times are never guessed.
8. v3.2.3 has no `schedule.group`, `service.operating_hours` or `service.events`. Those are v3.3
   (see [version-comparison.md](version-comparison.md)). Use `service.schedules`.

## 8. Taxonomies

1. **[guide]** HSDS is not a taxonomy exchange format. Include only terms that are used.
2. **[guide]** Give `taxonomy.uri` when the taxonomy has a canonical URL. Include the terms
   as an embedded taxonomy only if the license allows.
3. `taxonomy_term.name` and `description` are required. **[BEVIJEN]** A source category with no
   description leaves `description` missing and reports it. Do not copy the name into it.

## 9. Empty values and serialization

1. **[guide]** In JSON, **leave out** a property that has no value (unless required) rather than
   writing `""` or `null`. In the tabular form, an empty cell or null is fine.
2. **[guide]** In the tabular form, a CSV with no rows may be left out.
3. **[spec]** The canonical serialization is JSON with related objects embedded
   ("dereferenced"). The tabular data package (one CSV per object + `datapackage.json`) is also
   supported. Use the official file names (`objects/README.md` lists them).
4. **[BEVIJEN]** By default the converter writes the string `"NA"` for every missing field and
   writes every table. That is a project requirement and **does not validate as strict HSDS
   JSON** for typed fields. For a conformant export or API response use `--na omit`.

## 10. Conversion checklist

For each source layout:

1. Identify the root object of each record (usually `service` or `organization`).
2. Map each source field to `object.field` using the object pages. Check the description, not
   only the name.
3. Split combined values into child rows: phones, addresses, schedules, languages, taxonomy terms.
4. Create `service_at_location` for every service–location pair.
5. Generate deterministic UUIDs and fill every foreign key.
6. Normalize formats and enums (section 4).
7. Leave required fields missing when the source lacks them, and report them. Never invent.
8. Record source fields with no HSDS home (unmapped report, or a documented extra property).
9. Validate: required fields, types and formats, enums, foreign keys that resolve, then the
   official JSON schema for JSON output.
