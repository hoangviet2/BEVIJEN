"""High-level API: files or records in, HSDS dataset (and output folder) out."""
from __future__ import annotations

import json
from pathlib import Path

from .coerce import coerce_dataset
from .mapper import Dataset, Mapper
from .readers import read_file
from .validate import validate
from .writer import NAPolicy, write_outputs


def load_mapping(path: str | Path | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text())


def convert_records(records: list[dict], source_name: str = "source", file_label: str = "records",
                    mapping: dict | None = None, root: str = "auto", dataset: Dataset | None = None) -> Dataset:
    """Map already-parsed records (e.g. ingestion envelope payloads) into a dataset."""
    ds = dataset or Dataset()
    Mapper(ds, source_name, mapping, root).map_records(records, file_label)
    return ds


def convert_files(paths: list[str | Path], out_dir: str | Path | None = None, source_name: str = "source",
                  mapping: dict | None = None, root: str = "auto", na: str = "NA",
                  sheet: str | None = None) -> tuple[Dataset, list[dict], dict | None]:
    """Read, map, normalise and validate files; write outputs when out_dir is given.

    Returns (dataset, validation_issues, report)."""
    ds = Dataset()
    for path in paths:
        try:
            records = read_file(path, sheet=sheet)
        except Exception as exc:  # unreadable file: reported, other files still converted
            ds.errors.append({"source_file": str(path), "record_index": None,
                              "error": f"{type(exc).__name__}: {exc}"})
            continue
        Mapper(ds, source_name, mapping, root).map_records(records, str(path))
    coerce_dataset(ds)
    issues = validate(ds)
    report = write_outputs(ds, Path(out_dir), NAPolicy(na), issues) if out_dir else None
    return ds, issues, report
