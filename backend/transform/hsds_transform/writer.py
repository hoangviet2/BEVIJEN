"""Write a converted dataset: every HSDS table with every HSDS field, missing values
filled with the NA marker, plus nested HSDS JSON and provenance/quality reports."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from . import schema
from .mapper import Dataset, is_missing

NA_DEFAULT = "NA"

# Reference properties whose foreign key column is not simply "<table>_id".
_REF_COLUMN = {"taxonomy_detail": "taxonomy_id"}


class NAPolicy:
    """'NA' (default) or any marker string, 'null' (JSON null / empty CSV cell), or
    'omit' (drop missing properties so the JSON is strictly HSDS-valid)."""

    def __init__(self, marker: str = NA_DEFAULT):
        self.mode = marker if marker in ("null", "omit") else "marker"
        self.marker = marker

    def json_value(self):
        return None if self.mode in ("null", "omit") else self.marker

    def csv_value(self):
        return "" if self.mode in ("null", "omit") else self.marker


def full_row(table: str, row: dict, na: NAPolicy) -> dict:
    out = {}
    for name in schema.table(table).columns:
        value = row.get(name)
        if is_missing(value):
            if na.mode == "omit":
                continue
            out[name] = na.json_value()
        else:
            out[name] = value
    return out


def write_tables(ds: Dataset, out_dir: Path, na: NAPolicy) -> dict[str, int]:
    """One CSV per HSDS table (all tables, all columns), as in the HSDS tabular format."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    resources = []
    for name, tdef in schema.tables().items():
        rows = list(ds.rows.get(name, {}).values())
        counts[name] = len(rows)
        path = out_dir / tdef.path
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=tdef.column_names)
            writer.writeheader()
            for row in rows:
                writer.writerow({c: (na.csv_value() if is_missing(row.get(c)) else row[c])
                                 for c in tdef.column_names})
        resources.append({
            "name": name, "path": tdef.path, "format": "csv", "mediatype": "text/csv",
            "profile": "tabular-data-resource",
            "schema": {"fields": [{"name": c.name, "type": c.type, **({"format": c.format} if c.format else {}),
                                   "constraints": {"required": c.required,
                                                   **({"enum": list(c.enum)} if c.enum else {})}}
                                  for c in tdef.columns.values()],
                       "missingValues": [na.csv_value()]},
        })
    (out_dir / "datapackage.json").write_text(json.dumps({
        "name": "hsds-converted", "profile": "tabular-data-package",
        "hsds_version": schema.HSDS_VERSION, "resources": resources}, indent=2))
    return counts


class _Index:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self._by_col: dict[tuple[str, str], dict[str, list]] = {}

    def children(self, table: str, column: str, value: str) -> list[dict]:
        key = (table, column)
        if key not in self._by_col:
            idx = defaultdict(list)
            for row in self.ds.rows.get(table, {}).values():
                if not is_missing(row.get(column)):
                    idx[row[column]].append(row)
            self._by_col[key] = idx
        return self._by_col[key].get(value, [])


def nest(table: str, row: dict, ds: Dataset, na: NAPolicy, idx: _Index, ancestors: tuple = ()) -> dict:
    """Build the nested HSDS object for one row, with every schema property present."""
    tdef = schema.table(table)
    out: dict = {}
    for prop in tdef.property_order:
        if prop in tdef.columns:
            value = row.get(prop)
            if is_missing(value):
                if na.mode != "omit":
                    out[prop] = na.json_value()
            else:
                out[prop] = value
            continue
        rel = tdef.relations[prop]
        value = None
        if rel.table not in ancestors and rel.table != table:
            path = ancestors + (table,)
            if rel.many:
                if rel.table == "attribute":
                    kids = idx.children("attribute", "link_id", row["id"])
                elif rel.table == "metadata":
                    kids = idx.children("metadata", "resource_id", row["id"])
                elif f"{table}_id" in schema.table(rel.table).columns:
                    kids = idx.children(rel.table, f"{table}_id", row["id"])
                else:
                    kids = []
                if kids:
                    value = [nest(rel.table, k, ds, na, idx, path) for k in kids]
            else:
                fk = _REF_COLUMN.get(prop, f"{rel.table}_id")
                ref_id = row.get(fk)
                ref = ds.rows.get(rel.table, {}).get(ref_id) if not is_missing(ref_id) else None
                if ref is not None:
                    value = nest(rel.table, ref, ds, na, idx, path)
        if value is None:
            if na.mode != "omit":
                out[prop] = na.json_value()
        else:
            out[prop] = value
    return out


def write_outputs(ds: Dataset, out_dir: Path, na: NAPolicy, issues: list[dict]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = write_tables(ds, out_dir / "tables", na)
    idx = _Index(ds)

    def dump(name, data):
        (out_dir / name).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    dump("services.json", [nest("service", r, ds, na, idx) for r in ds.rows.get("service", {}).values()])
    dump("organizations.json",
         [nest("organization", r, ds, na, idx) for r in ds.rows.get("organization", {}).values()])
    dump("hsds_tables.json", {name: [full_row(name, r, na) for r in ds.rows.get(name, {}).values()]
                              for name in schema.tables()})

    _write_csv(out_dir / "report_source_map.csv", ds.source_map,
               ["hsds_table", "hsds_id", "source_name", "source_file", "record_index", "native_id", "source_path"])
    _write_csv(out_dir / "report_unmapped_fields.csv", ds.unmapped,
               ["source_file", "record_index", "hsds_table", "hsds_id", "source_path", "value"])
    _write_csv(out_dir / "report_validation.csv", issues, ["table", "id", "column", "problem", "value"])
    _write_csv(out_dir / "report_conflicts.csv", ds.conflicts, ["table", "id", "column", "kept", "ignored"])

    issue_summary: dict[str, int] = defaultdict(int)
    for issue in issues:
        issue_summary[f"{issue['table']}.{issue['column']}: {issue['problem']}"] += 1
    report = {
        "hsds_version": schema.HSDS_VERSION,
        "records_read": dict(ds.records_read),
        "record_errors": ds.errors,
        "rows_per_table": counts,
        "field_mapping": {target: dict(srcs) for target, srcs in sorted(ds.decisions.items())},
        "unmapped_source_fields": dict(ds.unmapped_paths.most_common()),
        "validation_issue_counts": dict(sorted(issue_summary.items())),
        "conflicting_values": len(ds.conflicts),
        "na_marker": na.marker,
    }
    dump("report.json", report)
    return report


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fields})
