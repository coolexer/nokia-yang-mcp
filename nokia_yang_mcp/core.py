"""Core query helpers for Nokia YANG MCP."""

from __future__ import annotations

import re
import sqlite3

from .database import RELEASES, open_db, validate_product
from .models import Kind, MatrixResult, PathResult, SupportResult


def fts_query(query: str) -> str:
    """Turn free text into a safe SQLite FTS5 MATCH expression."""
    tokens = [token for token in query.strip().split() if token]
    parts: list[str] = []
    for token in tokens:
        prefix = token.endswith("*")
        if prefix:
            token = token[:-1]
        token = token.replace('"', '""')
        parts.append(f'"{token}"*' if prefix else f'"{token}"')
    return " ".join(parts)


def canonicalise_path(path: str) -> str:
    """Normalize concrete YANG list keys to the wildcard form stored in the DB."""
    return re.sub(r"(\[[^=\]]+=)[^\]]*\]", r"\1*]", path)


def get_stats(product: str) -> dict[str, str]:
    validate_product(product)
    with open_db(product) as conn:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())


def list_platforms(product: str) -> list[str]:
    validate_product(product)
    with open_db(product) as conn:
        return [
            row["name"]
            for row in conn.execute("SELECT name FROM platforms ORDER BY name")
        ]


def platform_bitmask(conn: sqlite3.Connection, fragment: str) -> tuple[int, list[str]]:
    frag = fragment.lower()
    bits = 0
    matched: list[str] = []
    for row in conn.execute("SELECT id, name FROM platforms"):
        if frag in row["name"].lower():
            bits |= 1 << row["id"]
            matched.append(row["name"])
    return bits, matched


def platforms_for_row(conn: sqlite3.Connection, platform_bits: int) -> list[str]:
    names: list[str] = []
    for row in conn.execute("SELECT id, name FROM platforms ORDER BY id"):
        if platform_bits & (1 << row["id"]):
            names.append(row["name"])
    return sorted(names)


def _kind_from_is_state(is_state: int | None) -> str:
    return "state" if is_state == 1 else "config"


def _path_result(conn: sqlite3.Connection, row: sqlite3.Row) -> PathResult:
    return PathResult(
        path=row["path"],
        path_prefix=row["path_prefix"] or "",
        type=row["type"] or "",
        node_type=row["node_type"] or "",
        description=row["description"] or "",
        kind=_kind_from_is_state(row["is_state"]),
        platforms=platforms_for_row(conn, row["platform_bits"]),
    )


def search_paths(
    product: str,
    *,
    query: str | None = None,
    platform: str | None = None,
    kind: Kind | None = None,
    substring: bool = False,
    limit: int = 50,
) -> list[PathResult]:
    validate_product(product)
    if limit < 1:
        raise ValueError("limit must be >= 1")

    with open_db(product) as conn:
        where: list[str] = []
        params: list[object] = []
        order_by = "ORDER BY paths.path"

        if query and not query.strip():
            query = None

        if query:
            if substring:
                where.append("(path LIKE ? OR path_prefix LIKE ? OR description LIKE ?)")
                like = f"%{query}%"
                params.extend([like, like, like])
                base = "FROM paths"
                order_by = "ORDER BY length(paths.path) ASC"
            else:
                where.append("paths_fts MATCH ?")
                params.append(fts_query(query))
                base = "FROM paths_fts f JOIN paths ON paths.id = f.rowid"
                order_by = "ORDER BY f.rank"
        else:
            base = "FROM paths"

        if platform:
            bits, _matched = platform_bitmask(conn, platform)
            if bits == 0:
                return []
            where.append("(paths.platform_bits & ?) != 0")
            params.append(bits)

        if kind == "config":
            where.append("paths.is_state = 0")
        elif kind == "state":
            where.append("paths.is_state = 1")

        sql = f"""
            SELECT paths.id, paths.path, paths.path_prefix, paths.type, paths.node_type,
                   paths.description, paths.is_state, paths.platform_bits
            {base}
            {"WHERE " + " AND ".join(where) if where else ""}
            {order_by}
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_path_result(conn, row) for row in rows]


def check_path_support(product: str, path: str, platform: str) -> SupportResult:
    validate_product(product)
    canonical = canonicalise_path(path)
    with open_db(product) as conn:
        row = conn.execute(
            "SELECT platform_bits FROM paths WHERE path = ?",
            (canonical,),
        ).fetchone()
        if row is None:
            return SupportResult("path-unknown", path, canonical, [], [])

        want_bits, want_names = platform_bitmask(conn, platform)
        if want_bits == 0:
            return SupportResult("platform-unknown", path, canonical, [], [])

        have_bits = row["platform_bits"]
        if have_bits == 0:
            return SupportResult("platform-agnostic", path, canonical, [], sorted(want_names))

        have_names = set(platforms_for_row(conn, have_bits))
        supported = sorted(set(want_names) & have_names)
        unsupported = sorted(set(want_names) - have_names)

        if supported and not unsupported:
            status = "fully-supported"
        elif supported and unsupported:
            status = "partially-supported"
        else:
            status = "not-supported"
        return SupportResult(status, path, canonical, supported, unsupported)


ALIASES = {
    "srv6": ["segment-routing-v6"],
    "sr-mpls": ["segment-routing"],
    "sr": ["segment-routing"],
    "evpn": ["bgp-evpn"],
    "l3vpn": ["vprn"],
    "l2vpn": ["vpls", "epipe"],
    "rsvp": ["rsvp", "rsvp-te"],
    "igp": ["isis", "ospf", "ospf3"],
    "oam": ["oam", "oam-pm"],
}


def _path_tail(path: str) -> str:
    tail = path.rsplit("/", 1)[-1]
    return re.sub(r"\[.*?\]", "", tail).lower()


def resolve_feature(product: str, feature: str, *, limit: int = 40) -> list[PathResult]:
    validate_product(product)
    feat = feature.lower().strip()
    if not feat:
        return []
    targets = {feat, *ALIASES.get(feat, [])}
    match = " OR ".join(f'"{target.replace(chr(34), chr(34) * 2)}"' for target in targets)

    with open_db(product) as conn:
        rows = conn.execute(
            """
            SELECT paths.id, paths.path, paths.path_prefix, paths.type, paths.node_type,
                   paths.description, paths.is_state, paths.platform_bits
            FROM paths_fts f JOIN paths ON paths.id = f.rowid
            WHERE paths_fts MATCH ?
              AND paths.node_type IN ('container','list')
              AND paths.is_state = 0
              AND paths.path NOT LIKE '/state/%'
              AND paths.path NOT LIKE '/configure/groups/%'
              AND paths.path NOT LIKE '/debug/%'
              AND paths.path NOT LIKE '/reset/%'
              AND paths.path NOT LIKE '/configure/log/%'
            ORDER BY length(paths.path), paths.path
            LIMIT 500
            """,
            (match,),
        ).fetchall()

        selected: list[PathResult] = []
        seen: set[str] = set()
        for row in rows:
            tail = _path_tail(row["path"])
            if not any(tail == target or tail.startswith(f"{target}-") for target in targets):
                continue
            if row["path"] in seen:
                continue
            selected.append(_path_result(conn, row))
            seen.add(row["path"])
            if len(selected) >= limit:
                break
        return selected


def feature_support_matrix(product: str, feature: str, platform: str) -> MatrixResult:
    validate_product(product)
    paths = resolve_feature(product, feature)
    with open_db(product) as conn:
        _want_bits, platform_names = platform_bitmask(conn, platform)
        platform_names = sorted(platform_names)
        name_to_bit = {
            row["name"]: 1 << row["id"]
            for row in conn.execute("SELECT id, name FROM platforms")
            if row["name"] in platform_names
        }

        rows: list[dict[str, object]] = []
        for result in paths:
            row = conn.execute(
                "SELECT platform_bits FROM paths WHERE path = ?",
                (result.path,),
            ).fetchone()
            bits = 0 if row is None else row["platform_bits"]
            rows.append(
                {
                    "path": result.path,
                    "kind": result.kind,
                    "node_type": result.node_type,
                    "support": {name: bool(bits & bit) for name, bit in name_to_bit.items()},
                }
            )

    info = RELEASES[product]
    return MatrixResult(
        product=info["product"],
        release=info["release"],
        platforms=platform_names,
        rows=rows,
    )
