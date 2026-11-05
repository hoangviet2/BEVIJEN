"""Map arbitrary source records (flat rows or nested objects) onto HSDS tables.

How a source key is resolved, in order:
  1. an explicit mapping file entry ("Agency Name": "organization.name")
  2. a foreign key spelling (organization_id, idOrganization) -> deterministic UUID
  3. an exact HSDS column or a known alias for the current table
  4. a scalar that describes a child table ("languages": "English, Spanish")
  5. a table prefix ("org_website", "site_city") or a field unique to another table
     ("zip", "latitude", "tax_id") -> a related row is created and linked
Nested dicts/lists under table-like keys ("phones", "locations", "address") become
child rows linked by the HSDS foreign keys. Anything left over is never dropped:
it is written to the unmapped report with its source path and value.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import schema, vocab
from .vocab import norm

ID_NAMESPACE = uuid.UUID("5d3f6a7e-2b1c-5e8f-9a0b-1c2d3e4f5a6b")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Primary column used when a list of plain strings appears under a table key.
PRIMARY_COLUMN = {
    "language": "name", "phone": "number", "service_area": "name", "required_document": "document",
    "url": "url", "funding": "source", "contact": "name", "address": "address_1",
    "schedule": "description", "organization": "name", "location": "name", "service": "name",
    "program": "name", "accessibility": "description", "cost_option": "amount_description",
    "organization_identifier": "identifier",
}
_CORE_ROOTS = ("service", "organization", "location", "program", "contact")


def is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in vocab.MISSING_TOKENS
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    if isinstance(value, float) and value != value:  # NaN
        return True
    return False


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


@dataclass
class Dataset:
    """HSDS rows keyed by table and id, plus provenance and reports."""
    rows: dict[str, dict[str, dict]] = field(default_factory=lambda: defaultdict(dict))
    source_map: list[dict] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)
    unmapped_paths: Counter = field(default_factory=Counter)
    decisions: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    conflicts: list[dict] = field(default_factory=list)
    records_read: Counter = field(default_factory=Counter)
    errors: list[dict] = field(default_factory=list)

    def add(self, table: str, values: dict) -> dict:
        store = self.rows[table]
        rid = values["id"]
        existing = store.get(rid)
        if existing is None:
            store[rid] = values
            return values
        for col, val in values.items():
            if is_missing(existing.get(col)):
                existing[col] = val
            elif not is_missing(val) and existing[col] != val:
                self.conflicts.append({"table": table, "id": rid, "column": col,
                                       "kept": existing[col], "ignored": val})
        return existing


@dataclass
class _Ctx:
    file: str
    index: int


class Mapper:
    def __init__(self, dataset: Dataset, source_name: str = "source", mapping: dict | None = None,
                 root: str = "auto"):
        self.ds = dataset
        self.source = source_name
        mapping = mapping or {}
        self.root = mapping.get("root", root) if root == "auto" else root
        self.id_key = mapping.get("id")
        self.field_map: dict[str, str | None] = {}
        for key, target in (mapping.get("fields") or {}).items():
            self.field_map[key] = target
            self.field_map.setdefault(norm(key), target)
        self._implied_locations: dict[str, str] = {}
        self._implied_sals: set[str] = set()

    # ------------------------------------------------------------------ ids
    def make_id(self, table: str, native=None, identity: str | None = None, path: str | None = None) -> str:
        if not is_missing(native):
            native = str(native).strip()
            if is_uuid(native):
                return native.lower()
            return str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|{table}|{native}"))
        if identity:
            return str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|{table}|identity|{identity}"))
        return str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|{table}|path|{path}"))

    def fk_id(self, table: str, native) -> str:
        return self.make_id(table, native)

    # ------------------------------------------------------------------ entry points
    def map_records(self, records: list[dict], file: str) -> None:
        root = self.root if self.root != "auto" else guess_root(records)
        for i, record in enumerate(records):
            self.ds.records_read[file] += 1
            try:
                if not isinstance(record, dict):
                    record = {"value": record}
                self.map_object(record, root, None, _Ctx(file, i), f"{Path(file).name}#{i}", "")
            except Exception as exc:  # report the record, keep the batch going
                self.ds.errors.append({"source_file": file, "record_index": i,
                                       "error": f"{type(exc).__name__}: {exc}"})

    # ------------------------------------------------------------------ resolution
    def _override(self, key: str, nk: str, key_path: str):
        for candidate in (key, nk, key_path, norm(key_path)):
            if candidate and candidate in self.field_map:
                return True, self.field_map[candidate]
        return False, None

    def resolve(self, nk: str, table: str, allow_cross: bool = True):
        """Return a target tuple for a scalar key, or None.

        ("col", column) | ("fk", column, ref_table) | ("self_id",) |
        ("child", table, column, split) | ("implied", table, column) | ("day", code) |
        ("lang_flag", name) | ("taxonomy",)
        """
        cols = schema.table(table).columns
        if nk == "id":
            return ("self_id",)
        if nk in vocab.FK_KEYS:
            ref = vocab.FK_KEYS[nk]
            if ref == table and nk != "parent_id" and nk != "parent_organization_id":
                return ("self_id",)
            col = nk if nk in cols else f"{ref}_id"
            if col in cols:
                return ("fk", col, ref)
        if nk in cols:
            return ("col", nk)
        alias = vocab.FIELD_ALIASES.get(table, {}).get(nk)
        if alias and alias in cols:
            return ("col", alias)
        if not allow_cross:
            return None
        if nk in vocab.TAXONOMY_KEYS and table not in ("taxonomy_term", "taxonomy"):
            return ("taxonomy",)
        if table == "service" and nk in ("age", "ages", "age_range", "age_group", "ages_served", "age_groups"):
            return ("ages",)
        child = vocab.SCALAR_CHILD.get(nk)
        if child and child[0] != table:
            return ("child", *child)
        m = re.match(r"^(?:lang|language|languages)_([a-z_]+)$", nk)
        if m and table != "language" and m.group(1) not in ("code", "codes", "name", "notes"):
            return ("lang_flag", m.group(1).replace("_", " ").title())
        m = re.match(r"^([a-z]+?)(?:_(hours|open_hours|opening_hours|schedule))?$", nk)
        if m and m.group(1) in vocab.DAYS and len(m.group(1)) >= 3 and table != "schedule":
            return ("day", vocab.DAYS[m.group(1)])
        for prefix in sorted(vocab.PREFIX_TABLES, key=len, reverse=True):
            if nk.startswith(prefix + "_"):
                rest = nk[len(prefix) + 1:]
                other = vocab.PREFIX_TABLES[prefix]
                hit = self.resolve(rest, other, allow_cross=False)
                if hit and hit[0] == "col":
                    return ("col", hit[1]) if other == table else ("implied", other, hit[1])
        if nk not in vocab.GENERIC_KEYS:
            for other in vocab.IMPLIED_TABLES:
                if other == table:
                    continue
                hit = self.resolve(nk, other, allow_cross=False)
                if hit and hit[0] == "col" and hit[1] != "id":
                    return ("implied", other, hit[1])
        return None

    # ------------------------------------------------------------------ core
    def map_object(self, obj: dict, table: str, parent: tuple[str, dict] | None, ctx: _Ctx,
                   path: str, key_path: str, defaults: dict | None = None,
                   forced_id: str | None = None) -> str | None:
        expanded = self._expand(table, obj)
        last = None
        for i, item in enumerate(expanded):
            sub_path = path if len(expanded) == 1 else f"{path}[{i}]"
            last = self._map_one(item, table, parent, ctx, sub_path, key_path, defaults,
                                 forced_id if len(expanded) == 1 else None)
        return last

    def _map_one(self, obj, table, parent, ctx, path, key_path, defaults, forced_id):
        values: dict = {}
        native = None
        deferred: list = []      # (kind, payload) processed once the row has an id
        implied: dict[str, dict] = defaultdict(dict)
        day_hours: dict[str, str] = {}
        leftovers: list = []     # (source_path, value)

        quiet = key_path.startswith("~")  # internal object built by the mapper itself

        def assign(col, val, src):
            if is_missing(val):
                return
            if is_missing(values.get(col)):
                values[col] = val
                if not quiet:
                    self.ds.decisions[f"{table}.{col}"][src] += 1
            elif values[col] != val:
                leftovers.append((src, val))

        def handle(key, val, src, unqualified_ok=True):
            nonlocal native
            nk = norm(key)
            if nk.startswith("_") or nk == "":
                return
            found, target = self._override(key, nk, src)
            if found:
                if target is None or is_missing(val):
                    return
                if target == "taxonomy" or target.startswith("taxonomy:"):
                    vocab_name = target.split(":", 1)[1] if ":" in target else nk
                    deferred.append(("taxonomy", (vocab_name, _as_list(val, r"[;,|]"), src)))
                    return
                t_table, _, t_col = target.partition(".")
                if t_col == "id" and t_table == table:
                    native = val
                elif t_table == table:
                    assign(t_col, val, src)
                elif t_table in schema.tables():
                    implied[t_table][t_col] = val
                return
            if self.id_key and key == self.id_key:
                native = val
                return
            if is_missing(val):
                return
            if isinstance(val, dict):
                child_table = vocab.TABLE_KEYS.get(nk)
                if child_table and child_table != table or (child_table == table and nk == "parent_organization"):
                    extra = {}
                    if nk in vocab.ADDRESS_TYPE_FROM_KEY:
                        extra["address_type"] = vocab.ADDRESS_TYPE_FROM_KEY[nk]
                    deferred.append(("child", (child_table, val, src, extra, nk)))
                    return
                if nk in vocab.TAXONOMY_KEYS:
                    deferred.append(("taxonomy", (nk, [val], src)))
                    return
                flatten = nk in vocab.FLATTEN_KEYS
                for k2, v2 in val.items():
                    n2 = norm(k2)
                    qualified = f"{nk}_{n2}"
                    hit = self.resolve(qualified, table) if not isinstance(v2, (dict, list)) else None
                    if hit:
                        handle(qualified, v2, f"{src}.{k2}")
                    elif flatten:
                        handle(k2, v2, f"{src}.{k2}")
                    elif not is_missing(v2):
                        leftovers.append((f"{src}.{k2}", v2))
                return
            if isinstance(val, list):
                items = [v for v in val if not is_missing(v)]
                if not items:
                    return
                if nk in vocab.TAXONOMY_KEYS and table not in ("taxonomy_term", "taxonomy"):
                    deferred.append(("taxonomy", (nk, items, src)))
                    return
                child_table = vocab.TABLE_KEYS.get(nk)
                if all(isinstance(v, dict) for v in items):
                    if child_table:
                        extra = {}
                        if nk in vocab.ADDRESS_TYPE_FROM_KEY:
                            extra["address_type"] = vocab.ADDRESS_TYPE_FROM_KEY[nk]
                        for j, item in enumerate(items):
                            deferred.append(("child", (child_table, item, f"{src}[{j}]", extra, nk)))
                    else:
                        leftovers.append((src, val))
                    return
                if nk in vocab.FK_LIST_KEYS:
                    for v in items:
                        deferred.append(("link", (vocab.FK_LIST_KEYS[nk], v)))
                    return
                if child_table and child_table in PRIMARY_COLUMN and child_table != table:
                    for j, v in enumerate(items):
                        deferred.append(("child", (child_table, {PRIMARY_COLUMN[child_table]: v},
                                                   f"{src}[{j}]", {}, nk)))
                    return
                hit = self.resolve(nk, table)
                if hit and hit[0] == "col":
                    if hit[1] == "email" or schema.table(table).columns[hit[1]].format in ("uri", "email"):
                        assign(hit[1], items[0], src)
                        for v in items[1:]:
                            leftovers.append((src, v))
                    else:
                        assign(hit[1], "; ".join(str(v) for v in items), src)
                    return
                if hit and hit[0] == "child":
                    for v in items:
                        handle(key, v, src)
                    return
                leftovers.append((src, val))
                return
            # scalar
            hit = self.resolve(nk, table)
            if hit is None:
                leftovers.append((src, val))
                return
            kind = hit[0]
            if kind == "self_id":
                if native is None:
                    native = val
                else:
                    leftovers.append((src, val))
            elif kind == "col":
                assign(hit[1], val, src)
            elif kind == "fk":
                assign(hit[1], self.fk_id(hit[2], val), src)
            elif kind == "taxonomy":
                deferred.append(("taxonomy", (nk, _as_list(val, r"[;,|]"), src)))
            elif kind == "child":
                _, c_table, c_col, split = hit
                if split is None and c_table != "phone":
                    # One related row per record: merge with other fields for that table
                    # (an "address" column joins "city"/"zip" in the same address row).
                    if is_missing(implied[c_table].get(c_col)):
                        implied[c_table][c_col] = val
                        self.ds.decisions[f"{c_table}.{c_col}"][src] += 1
                    else:
                        leftovers.append((src, val))
                    return
                parts = _as_list(val, split) if split and isinstance(val, str) else [val]
                for j, part in enumerate(parts):
                    item = {c_col: part}
                    if c_table == "phone" and nk in vocab.PHONE_TYPE_FROM_KEY:
                        item["type"] = vocab.PHONE_TYPE_FROM_KEY[nk]
                    deferred.append(("child", (c_table, item, f"{src}" + (f"[{j}]" if len(parts) > 1 else ""),
                                               {}, nk)))
                self.ds.decisions[f"{c_table}.{c_col}"][src] += 1
            elif kind == "implied":
                _, i_table, i_col = hit
                if is_missing(implied[i_table].get(i_col)):
                    implied[i_table][i_col] = val
                    self.ds.decisions[f"{i_table}.{i_col}"][src] += 1
                else:
                    leftovers.append((src, val))
            elif kind == "ages":
                lo, hi = _parse_ages(str(val))
                if lo is None and hi is None:
                    leftovers.append((src, val))
                else:
                    assign("minimum_age", lo, src)
                    assign("maximum_age", hi, src)
            elif kind == "day":
                day_hours[hit[1]] = str(val)
                self.ds.decisions["schedule.byday/opens_at/closes_at"][src] += 1
            elif kind == "lang_flag":
                if str(val).strip().lower() in ("true", "yes", "y", "1", "x"):
                    deferred.append(("child", ("language", {"name": hit[1]}, src, {}, nk)))
                    self.ds.decisions["language.name"][src] += 1
                else:
                    leftovers.append((src, val))

        for key, val in obj.items():
            src = f"{key_path}.{key}" if key_path else str(key)
            handle(key, val, src)

        for col, val in (defaults or {}).items():
            if is_missing(values.get(col)) and col in schema.table(table).columns:
                values[col] = val

        # A nested object with nothing mappable (e.g. {"description": null}) adds no row.
        if parent is not None and not deferred and not implied and not day_hours and \
                all(is_missing(v) for v in values.values()):
            for src, val in leftovers:
                self._unmapped(ctx, parent[0], parent[1]["id"], src, val)
            return None

        # ---- id
        if forced_id and is_missing(native):
            rid = forced_id
        else:
            identity = None
            if is_missing(native):
                if table in ("organization", "taxonomy") and not is_missing(values.get("name")):
                    identity = norm(values["name"])
            rid = self.make_id(table, native, identity, path)
        values["id"] = rid
        id_src = lambda s: s  # noqa: E731  child key used in child id paths
        if not is_missing(native):
            # Children of a row with a source id get ids from that row (and their key relative to it),
            # not from the record they were read in: ODRS repeats the same location (with its address
            # and schedules) inside every opportunity offered there, which otherwise added one copy
            # of each child per opportunity.
            path = f"{table}:{rid}"
            prefix = f"{key_path}." if key_path else ""
            id_src = lambda s: s[len(prefix):] if prefix and s.startswith(prefix) else s  # noqa: E731

        # ---- link to parent, then store
        if parent is not None:
            self._link(table, values, parent[0], parent[1])
        if table == "service_at_location":
            self._drop_implied_sal(values)
        row = self.ds.add(table, values)
        self.ds.source_map.append({
            "hsds_table": table, "hsds_id": rid, "source_name": self.source, "source_file": ctx.file,
            "record_index": ctx.index, "native_id": None if is_missing(native) else str(native),
            "source_path": key_path or "(record)",
        })
        for src, val in leftovers:
            self._unmapped(ctx, table, rid, src, val)

        # ---- children (locations first, so addresses/accessibility attach to them)
        deferred.sort(key=lambda d: 0 if d[0] == "child" and d[1][0] in ("location", "service_at_location") else 1)
        for kind, payload in deferred:
            if kind == "child":
                c_table, item, src, extra, nk = payload
                forced = None
                fk = f"{c_table}_id"
                if fk in schema.table(table).columns and not is_missing(row.get(fk)) and c_table != table:
                    forced = row[fk]
                if nk == "parent_organization":
                    child_id = self.map_object(item, "organization", None, ctx, f"{path}/{id_src(src)}", src, extra)
                    if child_id and is_missing(row.get("parent_organization_id")):
                        row["parent_organization_id"] = child_id
                    continue
                self.map_object(item, c_table, (table, row), ctx, f"{path}/{id_src(src)}", src, extra, forced)
            elif kind == "taxonomy":
                vocab_name, items, src = payload
                self._add_terms(vocab_name, items, table, row, ctx, f"{path}/{src}", src)
            elif kind == "link":
                ref_table, native_ref = payload
                self._link_ids(table, row, ref_table, self.fk_id(ref_table, native_ref), path)

        on_site = table in ("service", "organization", "service_at_location")
        if on_site and "address" in implied:
            # Rows sharing an address share one location (and one address row).
            a = implied["address"]
            identity = "|".join(norm(a.get(k) or "") for k in ("address_1", "address_2", "city", "postal_code"))
            self._ensure_location(table, row, ctx, identity if identity.strip("|") else None)
        for i_table, fields in implied.items():
            if i_table == "location" and on_site:
                loc = self._ensure_location(table, row, ctx)
                for col, val in fields.items():
                    if is_missing(loc.get(col)):
                        loc[col] = val
                continue
            forced = row.get(f"{i_table}_id") if f"{i_table}_id" in schema.table(table).columns else None
            if i_table in ("address", "accessibility") and on_site:
                loc = self._ensure_location(table, row, ctx)
                forced = str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|{i_table}|of-location|{loc['id']}"))
            self.map_object(fields, i_table, (table, row), ctx, f"{path}/implied:{i_table}",
                            "~implied", None, None if is_missing(forced) else forced)

        if day_hours:
            for j, sched in enumerate(_schedules_from_days(day_hours)):
                self.map_object(sched, "schedule", (table, row), ctx, f"{path}/hours[{j}]", "~hours")
        return rid

    # ------------------------------------------------------------------ helpers
    def _unmapped(self, ctx, table, rid, src, val):
        pattern = re.sub(r"\[\d+\]", "[]", src)
        self.ds.unmapped_paths[pattern] += 1
        self.ds.unmapped.append({
            "source_file": ctx.file, "record_index": ctx.index, "hsds_table": table, "hsds_id": rid,
            "source_path": src,
            "value": val if isinstance(val, (str, int, float, bool)) else json.dumps(val, ensure_ascii=False),
        })

    def _expand(self, table: str, obj: dict) -> list[dict]:
        if table == "schedule":
            return _expand_schedule(obj)
        if table == "language":
            return _expand_language(obj)
        return [obj]

    def _link(self, c: str, cv: dict, p: str, pv: dict) -> None:
        if c == p:
            return
        c_cols = schema.table(c).columns
        p_cols = schema.table(p).columns
        if c == "attribute":
            cv.setdefault("link_id", pv["id"])
            cv.setdefault("link_entity", p)
        elif c == "metadata":
            cv.setdefault("resource_id", pv["id"])
            cv.setdefault("resource_type", p)
        elif f"{p}_id" in c_cols:
            if is_missing(cv.get(f"{p}_id")):
                cv[f"{p}_id"] = pv["id"]
        elif f"{c}_id" in p_cols:
            if is_missing(pv.get(f"{c}_id")):
                pv[f"{c}_id"] = cv["id"]
        elif {c, p} == {"service", "location"}:
            service, location = (pv, cv) if p == "service" else (cv, pv)
            self._add_sal(service["id"], location["id"])
            if is_missing(location.get("organization_id")) and not is_missing(service.get("organization_id")):
                location["organization_id"] = service["organization_id"]
        elif c in ("address", "accessibility") and p in ("service", "organization", "service_at_location"):
            loc = self._ensure_location(p, pv, None)
            if is_missing(cv.get("location_id")):
                cv["location_id"] = loc["id"]
        # Propagate the organization to services/locations created under one another.
        if c in ("service", "location") and p in ("service", "location", "program") and \
                "organization_id" in c_cols and is_missing(cv.get("organization_id")) and \
                not is_missing(pv.get("organization_id")):
            cv["organization_id"] = pv["organization_id"]

    def _add_sal(self, service_id: str, location_id: str) -> None:
        for existing in self.ds.rows["service_at_location"].values():
            if existing.get("service_id") == service_id and existing.get("location_id") == location_id:
                return
        sid = str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|service_at_location|{service_id}|{location_id}"))
        self.ds.add("service_at_location", {"id": sid, "service_id": service_id, "location_id": location_id})
        self._implied_sals.add(sid)

    def _drop_implied_sal(self, values: dict) -> None:
        """An explicit service_at_location replaces one implied from a service/location link."""
        pair = (values.get("service_id"), values.get("location_id"))
        if is_missing(pair[0]) or is_missing(pair[1]):
            return
        for sid in list(self._implied_sals):
            row = self.ds.rows["service_at_location"].get(sid)
            if row and (row.get("service_id"), row.get("location_id")) == pair and sid != values["id"]:
                del self.ds.rows["service_at_location"][sid]
                self._implied_sals.discard(sid)

    def _link_ids(self, table: str, row: dict, ref_table: str, ref_id: str, path: str) -> None:
        if {table, ref_table} == {"service", "location"}:
            service_id, location_id = (row["id"], ref_id) if table == "service" else (ref_id, row["id"])
            self._add_sal(service_id, location_id)
        elif f"{ref_table}_id" in schema.table(table).columns and is_missing(row.get(f"{ref_table}_id")):
            row[f"{ref_table}_id"] = ref_id

    def _ensure_location(self, p: str, pv: dict, ctx, identity: str | None = None) -> dict:
        lid = self._implied_locations.get(pv["id"])
        if lid is None and identity:
            lid = str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|location|address|{identity}"))
            if p == "service":  # unless the service already has a location of its own
                for sal in self.ds.rows["service_at_location"].values():
                    if sal.get("service_id") == pv["id"]:
                        lid = sal["location_id"]
                        break
            self._implied_locations[pv["id"]] = lid
        if lid is None:
            # Reuse a location already linked to this service/organization if there is one.
            if p == "service":
                for sal in self.ds.rows["service_at_location"].values():
                    if sal.get("service_id") == pv["id"]:
                        lid = sal["location_id"]
                        break
            elif p == "service_at_location" and not is_missing(pv.get("location_id")):
                lid = pv["location_id"]
            if lid is None:
                lid = str(uuid.uuid5(ID_NAMESPACE, f"{self.source}|location|implied|{p}|{pv['id']}"))
            self._implied_locations[pv["id"]] = lid
        loc = self.ds.rows["location"].get(lid) or self.ds.add("location", {"id": lid})
        if p == "service":
            self._add_sal(pv["id"], lid)
            if is_missing(loc.get("organization_id")) and not is_missing(pv.get("organization_id")):
                loc["organization_id"] = pv["organization_id"]
        elif p == "organization":
            if is_missing(loc.get("organization_id")):
                loc["organization_id"] = pv["id"]
        elif p == "service_at_location" and is_missing(pv.get("location_id")):
            pv["location_id"] = lid
        return loc

    def _add_terms(self, vocab_name: str, items: list, table: str, row: dict, ctx: _Ctx,
                   path: str, src: str) -> None:
        tax_id = self.make_id("taxonomy", identity=norm(vocab_name))
        self.ds.add("taxonomy", {"id": tax_id, "name": vocab_name})
        for j, item in enumerate(items):
            term: dict = {}
            native_link = None
            if isinstance(item, dict):
                for k, v in item.items():
                    nk = norm(k)
                    if is_missing(v):
                        continue
                    if nk == "id":
                        native_link = v
                        continue
                    hit = self.resolve(nk, "taxonomy_term", allow_cross=False)
                    if hit and hit[0] == "col" and is_missing(term.get(hit[1])):
                        term[hit[1]] = v
                    else:
                        self._unmapped(ctx, "taxonomy_term", None, f"{src}[{j}].{k}", v)
            else:
                term["name"] = item
            if is_missing(term.get("name")) and is_missing(term.get("code")):
                continue
            key = norm(term.get("code") or term.get("name"))
            term_id = self.make_id("taxonomy_term", identity=f"{norm(vocab_name)}|{key}")
            term.update({"id": term_id, "taxonomy": vocab_name, "taxonomy_id": tax_id})
            self.ds.add("taxonomy_term", term)
            attr_id = self.make_id("attribute", native_link, identity=None if native_link else f"{row['id']}|{term_id}")
            self.ds.add("attribute", {"id": attr_id, "link_id": row["id"], "link_entity": table,
                                      "taxonomy_term_id": term_id})
            self.ds.decisions["taxonomy_term.name (+attribute)"][re.sub(r"\[\d+\]", "[]", src)] += 1


# ---------------------------------------------------------------------- root guessing

def guess_root(records: list[dict]) -> str:
    votes: Counter = Counter()
    for record in records[:50]:
        if isinstance(record, dict):
            votes[_guess_one(record)] += 1
    return votes.most_common(1)[0][0] if votes else "service"


def _guess_one(record: dict) -> str:
    keys = {norm(k): v for k, v in record.items()}
    for k, v in keys.items():
        if vocab.TABLE_KEYS.get(k) == "service" and isinstance(v, list) and v and isinstance(v[0], dict):
            return "organization"
    for k, v in keys.items():
        if vocab.TABLE_KEYS.get(k) == "organization" and isinstance(v, dict):
            return "service"
    scores = Counter()
    for t in _CORE_ROOTS:
        cols = schema.table(t).columns
        aliases = vocab.FIELD_ALIASES.get(t, {})
        for k in keys:
            if k in vocab.GENERIC_KEYS:
                continue
            if k in cols or k in aliases:
                scores[t] += 1
    best = max(scores.values(), default=0)
    for t in _CORE_ROOTS:  # ties go to service, then organization
        if scores[t] == best:
            return t
    return "service"


# ---------------------------------------------------------------------- value helpers

def _as_list(val, split: str | None) -> list:
    if isinstance(val, list):
        return [v for v in val if not is_missing(v)]
    if split and isinstance(val, str):
        return [p.strip() for p in re.split(split, val) if not is_missing(p.strip())]
    return [val]


_DAY_FIELD = re.compile(r"^([a-z]+?)_?(start|end|open|opens|close|closes|from|to|hours|opens_at|closes_at)?$")


def _expand_schedule(obj: dict) -> list[dict]:
    """Expand weekly hours held as per-day fields or a list of day entries into one
    schedule row per day (freq WEEKLY, byday, opens_at, closes_at)."""
    base: dict = {}
    days: dict[str, dict] = {}
    entries: list[dict] = []
    for key, val in obj.items():
        nk = norm(key)
        if isinstance(val, list) and val and all(isinstance(v, dict) for v in val) and any(
                norm(k) in ("day", "days", "weekday", "byday", "day_of_week") for v in val for k in v):
            entries.extend(val)
            continue
        m = _DAY_FIELD.match(nk)
        if m and m.group(1) in vocab.DAYS and len(m.group(1)) >= 3 and not isinstance(val, (dict, list)):
            part = m.group(2) or "hours"
            slot = days.setdefault(vocab.DAYS[m.group(1)], {})
            if part in ("start", "open", "opens", "from", "opens_at"):
                slot["opens_at"] = val
            elif part in ("end", "close", "closes", "to", "closes_at"):
                slot["closes_at"] = val
            else:
                slot["hours"] = val
            continue
        base[key] = val
    out: list[dict] = []
    native = next((v for k, v in base.items() if norm(k) == "id"), None)
    for entry in entries:
        item = {k: v for k, v in base.items() if norm(k) != "id"}
        item.update(entry)
        item.setdefault("freq", "WEEKLY")
        out.append(item)
    for code in vocab.DAY_ORDER:
        slot = days.get(code)
        if not slot:
            continue
        opens, closes = slot.get("opens_at"), slot.get("closes_at")
        if is_missing(opens) and is_missing(closes) and not is_missing(slot.get("hours")):
            opens, closes = _parse_range(str(slot["hours"]))
            if opens is None:
                if str(slot["hours"]).strip().lower() not in ("closed",):
                    item = {k: v for k, v in base.items() if norm(k) != "id"}
                    item.update({"byday": code, "freq": "WEEKLY", "description": f"{code}: {slot['hours']}"})
                    out.append(item)
                continue
        if is_missing(opens) and is_missing(closes):
            continue
        item = {k: v for k, v in base.items() if norm(k) != "id"}
        item.update({"byday": code, "freq": "WEEKLY", "opens_at": opens, "closes_at": closes})
        out.append(item)
    if not out:
        out = _expand_hours_text(base)
    if not out:
        return [obj]
    if not is_missing(native):
        for i, item in enumerate(out):
            item["id"] = f"{native}#{i}"
    return out


def _expand_language(obj: dict) -> list[dict]:
    name_key = None
    for key, val in obj.items():
        nk = norm(key)
        if (nk == "name" or vocab.FIELD_ALIASES["language"].get(nk) == "name") and isinstance(val, str):
            name_key = key
            break
    if name_key is None:
        return [obj]
    names = [p.strip() for p in re.split(r"[,;/]| and ", obj[name_key]) if not is_missing(p.strip())]
    if len(names) <= 1:
        return [obj]
    out = []
    for name in names:
        item = {k: v for k, v in obj.items() if norm(k) not in ("id", "codes", "code", "language_code")}
        item[name_key] = name
        out.append(item)
    return out


_TIME = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
_RANGE = re.compile(rf"^\s*{_TIME}\s*(?:-|–|—|to|until)\s*{_TIME}\s*$", re.I)


def _parse_range(text: str):
    m = _RANGE.match(text)
    if not m:
        return None, None
    h1, m1, ap1, h2, m2, ap2 = m.groups()
    closes = _fmt_time(h2, m2, ap2)
    if ap1 is None and ap2 is not None:  # "9-5pm": borrow pm unless that opens after closing
        opens = _fmt_time(h1, m1, ap2)
        if opens > closes:
            opens = _fmt_time(h1, m1, "am")
        return opens, closes
    if ap1 is None and ap2 is None and int(h2) < int(h1) and int(h2) < 12:  # "9-5" means 09:00-17:00
        closes = _fmt_time(h2, m2, "pm")
    return _fmt_time(h1, m1, ap1), closes


def _fmt_time(h, m, ap) -> str:
    hour = int(h)
    if ap:
        ap = ap.lower().replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
    return f"{hour:02d}:{int(m or 0):02d}"


def _schedules_from_days(day_hours: dict[str, str]) -> list[dict]:
    """Columns like Monday="9am-5pm" -> schedule rows grouped by identical hours."""
    groups: dict[tuple, list[str]] = {}
    texts: list[dict] = []
    for code in vocab.DAY_ORDER:
        if code not in day_hours:
            continue
        text = day_hours[code]
        if text.strip().lower() == "closed":
            continue
        opens, closes = _parse_range(text)
        if opens is None:
            texts.append({"byday": code, "freq": "WEEKLY", "description": f"{code}: {text}"})
        else:
            groups.setdefault((opens, closes), []).append(code)
    out = [{"byday": ",".join(days), "freq": "WEEKLY", "opens_at": o, "closes_at": c}
           for (o, c), days in groups.items()]
    return out + texts


def _parse_ages(text: str):
    t = text.strip().lower()
    m = re.match(r"^(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})(?:\s*(?:years|yrs|y))?$", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d{1,3})\s*(?:\+|and (?:over|older|up)|or (?:over|older))$", t) or \
        re.match(r"^(?:over|ages?|at least|minimum)\s*(\d{1,3})\+?$", t)
    if m:
        return int(m.group(1)), None
    m = re.match(r"^(?:under|below|younger than)\s*(\d{1,3})$", t)
    if m:
        return None, int(m.group(1)) - 1
    m = re.match(r"^(?:up to|max(?:imum)?)\s*(\d{1,3})$", t)
    if m:
        return None, int(m.group(1))
    return None, None


_DAYS_PART = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?"
_HOURS_SEG = re.compile(rf"^\s*({_DAYS_PART}(?:\s*(?:-|–|to|,|&|and)\s*{_DAYS_PART})*)\s*:?\s*(.+?)\s*$", re.I)


def _expand_hours_text(base: dict) -> list[dict]:
    """'Mon-Fri 9am-5pm; Sat 10-2' -> weekly rows. Only used when every segment parses;
    otherwise the text stays in description and no times are guessed."""
    if any(vocab.FIELD_ALIASES["schedule"].get(norm(k), norm(k)) in ("opens_at", "closes_at", "byday")
           for k in base):
        return []
    text_key = next((k for k in base if vocab.FIELD_ALIASES["schedule"].get(norm(k), norm(k)) == "description"
                     and isinstance(base[k], str)), None)
    if text_key is None:
        return []
    segments = [s for s in re.split(r"[;\n]+", base[text_key]) if s.strip()]
    out = []
    for seg in segments:
        m = _HOURS_SEG.match(seg)
        if not m:
            return []
        opens, closes = _parse_range(m.group(2))
        if opens is None:
            if m.group(2).strip().lower() == "closed":
                continue
            return []
        days = m.group(1).lower()
        item = {k: v for k, v in base.items() if norm(k) != "id"}
        item.update({"byday": days, "freq": "WEEKLY", "opens_at": opens, "closes_at": closes})
        out.append(item)
    return out
