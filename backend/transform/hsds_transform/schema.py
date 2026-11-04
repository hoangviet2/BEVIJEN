"""HSDS v3.2.3 schema model, built from the vendored Open Referral schema files.

Every table and column comes from the official JSON schemas in ``schema/``; nothing
is typed in by hand, so the field list stays exactly the pinned specification.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

HSDS_VERSION = "3.2.3"
SCHEMA_DIR = Path(__file__).parent / "schema"

# File names for tables that the official tabular package does not list.
_EXTRA_PATHS = {"url": "urls.csv", "unit": "units.csv", "service_capacity": "service_capacities.csv"}


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # string | number | integer | boolean
    format: str | None = None
    enum: tuple[str, ...] | None = None
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class Relation:
    """A nested property: an array of child objects or a single referenced object."""
    name: str
    table: str
    many: bool


@dataclass
class Table:
    name: str
    path: str
    required: tuple[str, ...]
    columns: dict[str, Column] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    property_order: tuple[str, ...] = ()

    @property
    def column_names(self) -> list[str]:
        return list(self.columns)


def _ref_table(ref: str) -> str:
    return ref.rsplit("/", 1)[-1].removesuffix(".json")


@lru_cache(maxsize=1)
def tables() -> dict[str, Table]:
    tabular = json.loads((SCHEMA_DIR / "tabular.json").read_text())["properties"]
    order = list(tabular)
    result: dict[str, Table] = {}
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        if path.name == "tabular.json":
            continue
        schema = json.loads(path.read_text())
        name = path.stem
        required = tuple(schema.get("required", []))
        csv_path = tabular.get(name, {}).get("items", {}).get("path") or _EXTRA_PATHS.get(name, f"{name}s.csv")
        table = Table(name=name, path=csv_path, required=required)
        for prop, spec in schema.get("properties", {}).items():
            if spec.get("type") == "array" and "$ref" in spec.get("items", {}):
                table.relations[prop] = Relation(prop, _ref_table(spec["items"]["$ref"]), True)
            elif "$ref" in spec:
                table.relations[prop] = Relation(prop, _ref_table(spec["$ref"]), False)
            elif spec.get("type") in ("array", "object"):
                continue  # no such properties in 3.2.3; kept for forward safety
            else:
                enum = tuple(spec["enum"]) if "enum" in spec else None
                table.columns[prop] = Column(
                    name=prop,
                    type=spec.get("type", "string"),
                    format=spec.get("format"),
                    enum=enum,
                    required=prop in required,
                    description=spec.get("description", ""),
                )
        table.property_order = tuple(schema.get("properties", {}))
        result[name] = table
    # Stable order: official tabular order first, then the extra tables.
    ordered = {n: result[n] for n in order if n in result}
    ordered.update({n: t for n, t in result.items() if n not in ordered})
    return ordered


def table(name: str) -> Table:
    return tables()[name]


def has_column(table_name: str, column: str) -> bool:
    return column in tables()[table_name].columns
