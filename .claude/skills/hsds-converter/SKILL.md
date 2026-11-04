---
name: hsds-converter
description: Map, convert, validate or review service-directory data against Open Referral HSDS v3.2.3 in BEVIJEN. Use when working on backend/transform (hsds_transform), writing or reviewing a field mapping or mapping file, adding a source format or alias, checking whether output is valid HSDS, answering questions about HSDS objects, fields, required fields, enums or relationships, or assessing an HSDS version upgrade (v3.3+).
---

# HSDS converter (BEVIJEN)

The target is **HSDS v3.2.3**. Do not use fields from v3.3 or later (`schedule.group`,
`service.operating_hours`, `service.events`) unless the user explicitly decides to upgrade.

## Reference documents (read only what the task needs)

All paths are relative to the repository root.

| Need | Read |
|---|---|
| Overview, source priority, core model | `docs/hsds/README.md` |
| Fields of one object (type, format, required, enum, description) | `docs/hsds/objects/<object>.md` |
| Which objects exist, CSV file names, required fields | `docs/hsds/objects/README.md` |
| Foreign keys, nesting, cardinality | `docs/hsds/relationships.md` |
| Mapping rules, ids, formats, schedules, taxonomies, empty values, checklist | `docs/hsds/conversion-guide.md` |
| Online docs vs local schema vs v3.3.x | `docs/hsds/version-comparison.md` |
| How the converter works, CLI, `--na`, mapping files | `backend/transform/README.md` |
| Project decisions (open questions 4.8 schedules, 5.1.1 organization description) | `docs/one-degree/` |

Source of truth, in order: the schema JSON in `backend/transform/hsds_transform/schema/`
(and the pages generated from it in `docs/hsds/objects/`), then HSDS conformance rules, then HSDS
guidance, then project decisions. Do not take field lists from the online schema-reference
page: it hides some foreign-key columns.

## Rules that are easy to get wrong

- Check the field's **description**, not only its name, before mapping to it. For example,
  `service.url` is the service's own web page, and `attribute.link_type` is a codelist value,
  not a table name.
- **Never invent values**, required ones included. Leave the field missing and let validation
  report it. Example gaps: ODRS `organization.description`, `service.status`.
- Every `id` is a UUID. Keep source UUIDs. Otherwise use deterministic UUIDv5 so reruns
  give the same ids. Source system ids go in `organization_identifier`,
  `location.external_identifier` or `organization.tax_id`, not in `id`.
- Service ↔ location is always through `service_at_location`.
- Enums: `service.status` ∈ {active, inactive, defunct, temporarily closed};
  `location_type`/`address_type` ∈ {physical, postal, virtual}; `freq` ∈ {WEEKLY, MONTHLY};
  `address.country` is ISO 3166-1 alpha-2.
- Schedules: one row per pattern and time range. `valid_to` (data validity) is not `until`
  (the service ends). No UTC offset is added: that rule is still an open project decision.
  Do not pick one without the user.
- Numbers (`latitude`, `minimum_age`, `amount`, ...) are JSON numbers. Strict HSDS JSON leaves out
  empty properties. The converter's default `"NA"` marker is a project requirement, so use
  `--na omit` for conformant JSON.
- Tabular file names come from each schema's `path` (`url.csv`, `service_capacity.csv`,
  `units.csv`). Known issue: `hsds_transform/schema.py` `_EXTRA_PATHS` writes `urls.csv`
  and `service_capacities.csv`.

## Workflow for a new source or mapping change

1. Inspect sample records. Decide the root object (usually `service` or `organization`).
2. For each source field, find the HSDS field in `docs/hsds/objects/`. If none fits, leave it
   unmapped (it is reported) or propose a documented extra property. Ask before adding one.
3. Prefer a mapping file (`python3 -m hsds_transform suggest-mapping`) for one layout. Add an
   alias to `hsds_transform/vocab.py` only when the name is generic and safe across sources.
4. Run the conversion and the tests from `backend/transform/`:
   `python3 -m unittest discover -s tests -v` and
   `python3 -m hsds_transform convert <file> -o work/<name> --source <name>`.
5. Check `report.json` (`unmapped_source_fields`), `report_validation.csv` and
   `report_conflicts.csv`. Explain each remaining required-field gap. Do not fill it in.

## Keeping the reference current

- `docs/hsds/objects/` and `docs/hsds/relationships.md` are generated. Run
  `python3 docs/hsds/tools/generate_reference.py` after any schema change. Never edit them by hand.
- To upgrade HSDS: replace the vendored schema from the new tag (update `SOURCE.txt`), update
  `HSDS_VERSION` in `schema.py`, regenerate the docs, update `version-comparison.md` and this skill,
  then rerun the tests.
