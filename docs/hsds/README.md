# HSDS reference for BEVIJEN

BEVIJEN converts service-directory data into the Open Referral **Human Services Data
Specification (HSDS) v3.2.3**. This folder is the reference for that conversion.

| Target version | **v3.2.3** ([tag](https://github.com/openreferral/specification/tree/v3.2.3)) |
|---|---|
| Schema in this repo | `backend/transform/hsds_transform/schema/`, an unmodified copy of the v3.2.3 tag |
| Online reference | <https://docs.openreferral.org/en/latest/hsds/schema_reference.html> (shows v3.2.x content; see [version-comparison.md](version-comparison.md)) |
| Converter | `backend/transform/` ([README](../../backend/transform/README.md)) |
| Latest upstream | v3.3.1 (not adopted; differences listed in [version-comparison.md](version-comparison.md)) |

## Contents

| Document | Use it for |
|---|---|
| [objects/README.md](objects/README.md) | Index of all 24 HSDS objects: tabular file name, field count, required fields |
| [objects/&lt;name&gt;.md](objects/README.md) | Every field of one object: title, type, format, required, core, allowed values, description, example, and its relationships |
| [relationships.md](relationships.md) | Every foreign key (tabular form), every nested property (JSON form), and an ER diagram of the core objects |
| [conversion-guide.md](conversion-guide.md) | Rules for mapping source data to HSDS: required fields, ids, formats, enums, schedules, taxonomies, empty values, plus a checklist |
| [version-comparison.md](version-comparison.md) | How the online docs, the local schema and upstream v3.3.x differ, and how to check again |
| [tools/generate_reference.py](tools/generate_reference.py) | Rebuilds `objects/` and `relationships.md` from the local schema |

`objects/` and `relationships.md` are **generated** from the vendored schema, so they always
match what the converter validates against. Edit the generator, not the output:

```sh
python3 docs/hsds/tools/generate_reference.py
```

## Which source wins

1. The v3.2.3 JSON schema files (`backend/transform/hsds_transform/schema/*.json`) and the
   generated pages in `objects/`.
2. The HSDS Conformance rules (summarized in [conversion-guide.md](conversion-guide.md) §1).
3. HSDS guidance pages (non-normative): Field Guidance, Identifiers, Mapping, Serialization.
4. BEVIJEN project decisions (`backend/transform/README.md`, `docs/one-degree/`).

The online page is useful for reading descriptions and JSON examples. Do not use it for
field lists: it hides some foreign-key fields (see [version-comparison.md](version-comparison.md)).

## The model in brief

- **Core objects:** `organization`, `service`, `location`, `service_at_location`.
- An organization provides services and has locations. A service is offered at a location
  through a `service_at_location` row.
- Other objects hold details: `address`, `phone`, `contact`, `schedule`, `language`,
  `accessibility`, `service_area`, `cost_option`, `funding`, `required_document`, `program`,
  `organization_identifier`, `url`, `service_capacity`, `unit`.
- Classification: `taxonomy` → `taxonomy_term`, applied to any object with `attribute`.
- Provenance: `metadata` (one row per change) and `meta_table_description`.
- Every `id` is a UUID. In JSON, related objects are embedded. In the tabular form (one CSV per
  object), they are linked by `*_id` columns.

Relationships between core objects, from the HSDS overview:

| Object | organization | service | location | service_at_location |
|---|---|---|---|---|
| program | 1:many | | | |
| service | 1:many | | | |
| service_at_location | | 1:1 | 1:1 | |
| location | 1:many | | | |
| phone | 1:many | 1:many | 1:many | 1:many |
| contact | 1:many | 1:many | | 1:many |
| address | | | 1:many | |
| schedule | | 1:many | 1:many | 1:many |
| funding | 1:many | 1:many | | |
| service_area | | 1:many | | |
| required_document | | 1:many | | |
| language | | 1:many | 1:many | |
| accessibility | | | 1:many | |
| cost_option | | 1:many | | |
| organization_identifier | 1:many | | | |

## License

HSDS is an Open Referral resource (<https://openreferral.org/>), licensed CC BY-SA 4.0. The
field descriptions in `objects/` come from the HSDS schema files. The guidance in
`conversion-guide.md` summarizes the HSDS documentation. See
`backend/transform/hsds_transform/schema/LICENSE`.
