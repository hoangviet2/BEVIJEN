"""Command line: python3 -m hsds_transform convert|suggest-mapping|fields ..."""
from __future__ import annotations

import argparse
import json
import sys

from . import schema
from .convert import convert_files, load_mapping
from .mapper import Dataset, Mapper
from .readers import SUPPORTED_EXTENSIONS, read_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hsds_transform",
                                     description=f"Convert files to Open Referral HSDS {schema.HSDS_VERSION}.")
    sub = parser.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="convert one or more files to HSDS")
    conv.add_argument("files", nargs="+")
    conv.add_argument("-o", "--out", required=True, help="output folder")
    conv.add_argument("--source", default="source",
                      help="stable source name; used in deterministic ids (default: source)")
    conv.add_argument("--root", default="auto", choices=["auto"] + list(schema.tables()),
                      help="HSDS table each top-level record represents (default: auto-detect)")
    conv.add_argument("--mapping", help="JSON mapping file (see suggest-mapping)")
    conv.add_argument("--na", default="NA",
                      help="marker for missing fields: NA (default), any text, 'null', or 'omit'")
    conv.add_argument("--sheet", help="only read this spreadsheet sheet")

    sug = sub.add_parser("suggest-mapping", help="print the detected column mapping as an editable JSON file")
    sug.add_argument("file")
    sug.add_argument("--root", default="auto", choices=["auto"] + list(schema.tables()))
    sug.add_argument("--sheet")

    sub.add_parser("fields", help="list every HSDS table and field")
    sub.add_parser("formats", help="list supported file extensions")

    args = parser.parse_args(argv)

    if args.command == "fields":
        for name, t in schema.tables().items():
            print(f"{name} ({t.path})")
            for c in t.columns.values():
                flags = ", ".join(x for x in [c.type, c.format, "required" if c.required else None,
                                              f"enum={list(c.enum)}" if c.enum else None] if x)
                print(f"  {c.name}  [{flags}]")
        return 0
    if args.command == "formats":
        print(" ".join(SUPPORTED_EXTENSIONS))
        print("Files with other extensions are sniffed by content.")
        return 0
    if args.command == "suggest-mapping":
        records = read_file(args.file, sheet=args.sheet)
        ds = Dataset()
        mapper = Mapper(ds, "suggest", None, args.root)
        mapper.map_records(records, args.file)
        fields: dict[str, str | None] = {}
        for target, sources in ds.decisions.items():
            for src in sources:
                if "." not in src and "[" not in src:
                    fields.setdefault(src, target)
        for path in ds.unmapped_paths:
            if "." not in path and "[" not in path:
                fields.setdefault(path, None)
        from .mapper import guess_root
        out = {"root": args.root if args.root != "auto" else guess_root(records),
               "id": None,
               "fields": fields,
               "_help": "Edit targets as 'table.field' (e.g. 'organization.name'), 'taxonomy:<vocabulary>' "
                        "for category columns, or null to ignore a column. Set 'id' to the column holding "
                        "a stable record id. Pass with: convert --mapping this_file.json"}
        print(json.dumps(out, indent=2))
        return 0

    ds, issues, report = convert_files(args.files, args.out, args.source, load_mapping(args.mapping),
                                       args.root, args.na, args.sheet)
    print(f"HSDS {schema.HSDS_VERSION} output written to {args.out}", file=sys.stderr)
    for f, n in report["records_read"].items():
        print(f"  read {n} records from {f}", file=sys.stderr)
    for name, n in report["rows_per_table"].items():
        if n:
            print(f"  {name}: {n}", file=sys.stderr)
    print(f"  unmapped source values: {len(ds.unmapped)} (see report_unmapped_fields.csv)", file=sys.stderr)
    print(f"  validation issues: {len(issues)} (see report_validation.csv)", file=sys.stderr)
    for err in ds.errors:
        print(f"  ERROR {err['source_file']} record {err['record_index']}: {err['error']}", file=sys.stderr)
    return 1 if ds.errors else 0
