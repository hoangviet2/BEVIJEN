# HSDS version comparison

Checked on 2026-11-03. Sources compared:

| Source | What it is |
|---|---|
| **Online reference** | <https://docs.openreferral.org/en/latest/hsds/schema_reference.html> (the "latest" build on Read the Docs) |
| **Local schema** | `backend/transform/hsds_transform/schema/` in this repo (24 object schemas + `tabular.json`) |
| **Upstream tags** | `openreferral/specification` on GitHub at v3.0, v3.0.1, v3.1, v3.1.4, v3.2, v3.2.2, v3.2.3, v3.3, v3.3.1 |

## Summary

| Question | Answer |
|---|---|
| Which version is the local schema? | **v3.2.3**, exactly. All 24 object schemas are byte-identical to the v3.2.3 tag, and `tabular.json` is byte-identical to `schema/compiled/tabular.json` at that tag. |
| Which version does the online page show? | **v3.1 to v3.2.3 content** (the object schemas did not change between those tags). It does **not** show v3.3. |
| Why does the online page say "3.0.1"? | The page title comes from `release = '3.0.1'` in the docs' `conf.py`. That value was never updated until v3.3, so every 3.x build up to v3.2.3 shows "3.0.1". It is not the schema version. |
| Latest upstream release | **v3.3.1**. Its schema is identical to v3.3. v3.3.1 only fixed version labels in the docs. |
| Target for BEVIJEN | **v3.2.3**. It matches the local schema, the converter (`HSDS_VERSION = "3.2.3"` in `schema.py`) and the version pinned in the One Degree technical document. |

## Online reference vs local v3.2.3

The field tables on the online page were parsed and compared with the compiled
`service_with_definitions.json` (the file the page is generated from) and with the local schemas.

**Same in both:** all 24 objects, every field name, type, format, required flag and title.

**Differences, none of them a version difference:**

| Difference | Cause |
|---|---|
| The online page does not list `service.organization_id`, `service.program_id`, `service_at_location.location_id`, `service_capacity.unit_id` and `attribute.taxonomy_term_id` | The page's `jsonschema` directive collapses nested objects with `:collapse: organization,program,location,unit,taxonomy_term,...`, and the collapse matches by prefix. That also hides the foreign key that starts with the same name. The fields **are** in the v3.2.3 schema and are valid HSDS. |
| Nested fields show as `array[object]` or `object` online but as `array`/`$ref` in the schema files | Rendering only. |
| Description text differs slightly (backticks, curly quotes, "See attribute" link text) | Markdown rendering only. |
| `service_capacity.unit` has a different title | Rendering of `$ref` (no title in the schema). |

Use the local schema (or the generated pages in [`objects/`](objects/README.md)) as the
source of truth for field lists. The online page is a good place to read descriptions and
the JSON examples.

## v3.2.3 → v3.3 / v3.3.1 (not adopted)

From a diff of the schema files and the upstream changelog. These are all backwards
compatible. v3.2.3 data is valid v3.3 data.

| Object | Change in v3.3 | Effect on BEVIJEN |
|---|---|---|
| `schedule` | New optional `group` (string label that groups schedules, e.g. `regular-hours`, `holiday-schedule`) | Not produced. Add it if v3.3 is adopted. |
| `service` | New optional `operating_hours` (array of `schedule`) | Not produced. In v3.2.3, all service hours go in `schedules`. |
| `service` | New optional `events` (array of `schedule`) | Not produced. |
| `service` | `schedules` **deprecated** in favor of `operating_hours` and `events` | Still the correct field in v3.2.3. |
| OpenAPI (`openapi.json`) | Updated for the new fields | The converter does not serve an API. |

No other object schemas changed. `location.schedules` and `service_at_location.schedules`
are unchanged in v3.3.

Earlier v3.2.x patch releases (v3.2.1 to v3.2.3) changed only `openapi.json`, not the object
schemas.

## Finding in the converter (not a version difference)

The v3.2.3 `datapackage.json` and object schemas give these tabular file names:

| Object | Official v3.2.3 `path` | Name written by `hsds_transform/schema.py` (`_EXTRA_PATHS`) |
|---|---|---|
| `url` | `url.csv` | `urls.csv` |
| `service_capacity` | `service_capacity.csv` | `service_capacities.csv` |
| `unit` | `units.csv` | `units.csv` (matches) |

`schema.py` uses `_EXTRA_PATHS` because the compiled `tabular.json` has only 21 objects.
Each object schema has its own `path` property, so `schema.get("path")` gives the official
name. Two of the three hard-coded names are wrong. This is a conformance issue for the
tabular output and is still open.

## How to repeat this check

1. Download the tag: `https://codeload.github.com/openreferral/specification/tar.gz/refs/tags/v3.2.3`.
2. `diff -rq` its `schema/` against `backend/transform/hsds_transform/schema/`. Expect only
   the local `LICENSE`, `SOURCE.txt` and `tabular.json` (a copy of `schema/compiled/tabular.json`),
   plus upstream `codelists/`, `compiled/`, `simple/` and `openapi.json`, which are not vendored.
3. Read the upstream `docs/hsds/changelog.md` for any newer tag.
