# HSDS transform

Converts a file of any common format into Open Referral HSDS **v3.2.3** (the version
pinned in the technical document). Every HSDS table and every HSDS field is written.
When the source has no value for a field, the field is `NA`.

The table and field list is read from the official schema files, vendored unmodified
in `hsds_transform/schema/` from openreferral/specification tag v3.2.3. It is not
typed in by hand. That gives 24 tables: the 21 in the official tabular package plus
`url`, `unit` and `service_capacity`.

Python 3.11+. The standard library is enough for every format except PDF, legacy
`.xls` and YAML, which use optional packages.

## Quick start

Run these from this directory. No installation is needed.

```sh
python3 -m unittest discover -s tests -v

# Convert one or more files (formats can be mixed)
python3 -m hsds_transform convert path/to/file.xlsx -o work/hsds --source county_list

# Convert the supplied samples
python3 -m hsds_transform convert ../ingestion/tests/fixtures/national-data-platform-sample-*.json \
  -o work/ndp --source ndp
python3 -m hsds_transform convert ../ingestion/tests/fixtures/BayesExport_20250729.json \
  -o work/odrs --source odrs

python3 -m hsds_transform fields    # list every HSDS table and field
python3 -m hsds_transform formats   # list supported extensions
```

`--source` is a stable name for the data source. It is part of every generated id, so
always use the same name for the same source. Exit code 0 means every file and record
converted. Exit code 1 means a file or record could not be read; the details are in
`report.json` under `record_errors`.

## Supported inputs

| Format | Extensions | How records are found |
|---|---|---|
| JSON | .json .geojson | Top-level list, or the largest list of objects in a wrapper (`data`, `opportunities`, ...) |
| JSON Lines | .jsonl .ndjson | One record per line. Ingestion envelopes are unwrapped to their `payload` |
| CSV / TSV | .csv .tsv .tab .psv | One row per record. The delimiter is sniffed |
| Excel | .xlsx .xlsm (.xls with `xlrd`) | Every sheet, or only `--sheet NAME`. The first row is the header |
| OpenDocument | .ods | Every table |
| XML | .xml | Repeated elements. Attributes and nested elements are kept |
| HTML | .html .htm | Every `<table>`. A page without tables is read as text |
| Word | .docx | Tables. A document without tables is read as text |
| Text | .txt .md | Blocks of `Key: value` lines separated by blank lines |
| PDF | .pdf (needs `pypdf`) | Extracted text, read as `Key: value` blocks. Scanned PDFs need OCR first |
| YAML | .yaml .yml (needs `pyyaml`) | Same as JSON |

Files with any other extension are identified by their content.

## Output folder

| Path | Contents |
|---|---|
| `tables/*.csv` | One CSV per HSDS table, all 24 tables, every column, `NA` for missing values. This is the HSDS tabular format. `tables/datapackage.json` describes it |
| `services.json` | Each service as a fully nested HSDS object (organization, phones, schedules, service_at_locations → location → addresses, ...). Every schema property is present |
| `organizations.json` | Each organization as a nested HSDS object |
| `hsds_tables.json` | Every table as JSON rows with every column |
| `report.json` | Record counts, rows per table, the field mapping used, unmapped source fields and validation issue counts |
| `report_source_map.csv` | HSDS id → source file, record index, native id and path, for tracing and later deduplication |
| `report_unmapped_fields.csv` | Every source value with no HSDS field, with its path. Nothing is dropped silently |
| `report_validation.csv` | Each required field that is `NA`, wrong type or format, value outside an enum, or reference to a missing row |
| `report_conflicts.csv` | Two different source values for the same HSDS field. The first value is kept |

### The `NA` marker and strict HSDS JSON

`NA` is written as the string `"NA"`, as requested. Strict HSDS JSON Schema validation
rejects `"NA"` in typed fields such as `latitude` or `minimum_age`, and the HSDS mapping
guidance prefers leaving out empty properties. Use `--na` to choose:

| Option | CSV cell | JSON value |
|---|---|---|
| `--na NA` (default) | `NA` | `"NA"` |
| `--na null` | empty | `null` |
| `--na omit` | empty | property left out (the strict HSDS form to use for API responses and exports) |

## How fields are matched

Each source key is normalized (`yearIncorporated`, `Year Incorporated` and
`year-incorporated` all become `year_incorporated`). It is then matched in this order:

1. An entry in a mapping file, if one is given (see below).
2. A foreign key spelling such as `organization_id` or `idOrganization`. It is
   converted to the same deterministic UUID as the row it points to.
3. The exact HSDS field name or a known alias in `vocab.py` for the current table, such
   as `title` → `service.name`, `zip` → `address.postal_code` or `digits` → `phone.number`.
4. A value that belongs to a related table even though it sits on the current row, such
   as `Languages: "English, Spanish"`, `Hours`, `Phone`, `Fax`, `Address` or `Required documents`.
   A related row is created and linked.
5. A table prefix (`org_website`, `site_city`) or a field that only one table has (`city`,
   `latitude`, `tax_id`).

Nested objects and lists under table-like keys (`phones`, `locations`, `addresses`,
`schedules`, `services`, ...) become linked child rows. A service and a location are
linked through `service_at_location`. Values under `categories`, `tags`, `taxonomy`
and similar keys become `taxonomy_term` rows, linked to their record by `attribute`
rows.

For a flat spreadsheet, one row normally produces a service plus its organization,
location, address, phones, schedules and languages. Rows with the same organization
name share one organization. Rows with the same address share one location.

### Mapping files for new layouts

The technical document suggests that a person confirms the column mapping once per
spreadsheet layout. To do that, generate a mapping file, review and edit it, and pass
it with `--mapping`:

```sh
python3 -m hsds_transform suggest-mapping county.xlsx > county.mapping.json
# edit: "Agency": "organization.name", "Topic": "taxonomy:topics", "Internal notes": null
python3 -m hsds_transform convert county.xlsx --mapping county.mapping.json -o work/county --source county
```

A mapping target can be `"table.field"`, `"taxonomy:<vocabulary>"` or `null` (ignore the
column). `"root"` sets which HSDS table each record is. `"id"` names the column that
holds a stable record id.

## Normalization rules

- **Ids** are UUIDs. A source UUID is kept. Otherwise the id is UUIDv5 of
  `source|table|native id`, so running the same data again produces the same ids
  (requirement R1.6). Records with no native id get an id from their organization name
  (organizations, taxonomies), from their address (implied locations), or from their
  file name and record position. Moving rows within a spreadsheet changes position-based
  ids, so set `"id"` in a mapping file when the source has an id column.
- **Schedules**: per-day fields (`monday_start`), lists of `{day, opensAt, closesAt}`,
  weekday columns (`Monday: 9am-5pm`) and simple text (`Mon-Fri 9-5; Sat 10-2`) become
  `freq=WEEKLY`, `byday`, and `opens_at`/`closes_at` in `HH:MM` format. Text that does not
  fully parse stays in `description` and no times are guessed. No UTC offset is added,
  because the sources give local time. Choosing an offset rule is still open (technical
  document 4.8).
- **Phones**: extensions such as `x 22` or `ext 4` are split out. `type` is mapped to the
  HSDS values (`main` → `voice`, `mobile` → `cell`, `tty` → `textphone`). A label that is
  not a type, such as "Main Office", goes to `description`.
- **Enums**: `status` (`open` → `active`, `closed` → `inactive`), `location_type`,
  `address_type`, `freq` and `wkst`.
- **Other values**: `country` becomes ISO alpha-2 (`United States` → `US`). US state names
  become codes. Dates and date-times become ISO 8601. A `www.` URL gets `http://`.
  Numbers are parsed from text.
- **Derived only from source data**: `location_type` is taken from the location's own
  address type. Nothing else is invented. Required fields the source does not have,
  such as ODRS `organization.description` and `service.status`, stay `NA` and are listed
  in `report_validation.csv`. How to fill them is a team decision (technical document
  5.1.1); this tool does not make it.
- A value that cannot be converted is kept as received and flagged by validation.
  Placeholder text (`""`, `N/A`, `none`, `null`, `unknown`) is treated as missing.

## Results on the supplied samples

| Sample | Records | HSDS rows | Validation issues |
|---|---|---|---|
| NDP samples 1+2 | 20 organizations | 20 organizations, 36 services, 23 locations, 46 addresses, 75 phones, 130 schedules, 45 taxonomy terms | Only fields the source lacks: 45 taxonomy_term.description, 4 address city/postal_code |
| ODRS BayesExport | 50 opportunities | 50 services, 41 organizations, 67 locations, 79 addresses, 260 schedules | Fields the source lacks: organization.description, service.status, address_type/country, location_type |

Fields with no HSDS equivalent, such as NDP `meta.idResource`, taxonomy level columns,
ODRS `properties.service-county-*` flags and ratings, are listed in
`report_unmapped_fields.csv`.

## Python use

```python
from hsds_transform import convert_files, convert_records

ds, issues, report = convert_files(["a.csv", "b.json"], "work/out", source_name="county")

# Records that are already parsed, e.g. ingestion envelope payloads
from hsds_transform.coerce import coerce_dataset
ds = convert_records([envelope.payload for envelope in envelopes], source_name="ndp")
coerce_dataset(ds)
```

## Limits

- Free-text documents (prose PDFs, Word letters, web pages) only convert when they hold
  `Key: value` lines or tables. Extracting records from prose needs the planned
  Bedrock model step.
- Mapping is based on rules. Unusual column names need an alias in `vocab.py` or a
  mapping file. Check `report.json` → `unmapped_source_fields` after a first run.
- Matching is limited to rows with the same native id, organization name or address
  within one run. Deduplication across sources is out of scope (stretch goal).
