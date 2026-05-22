#!/usr/bin/env python3
"""
Nokia YANG Browser — fast local CLI backed by SQLite FTS5.

Data source: https://yangbrowser.nokia.com/releases/{sros|srlinux}/{release}/paths.jsonl.gz

This skill ships with a pre-built SQLite DB for the latest SR OS and SR Linux
releases (see the data/ directory next to this script). Other releases are not
supported by this simplified skill — if you need an older release, adapt the
build_db() function or regenerate the DB with a different RELEASES entry.

Usage examples:
    # Search paths
    python3 yang_browser.py -s "msdp" -p "IXR-e3x" -v
    python3 yang_browser.py --product srlinux -s "bgp" -p "7220 IXR-D3"

    # Check support (exits 0 if supported, 1 if not, 2 if path unknown)
    python3 yang_browser.py --is-supported "/configure/router[router-name=*]/msdp" -p "IXR-e3x"

    # List platforms / releases / stats
    python3 yang_browser.py --platforms
    python3 yang_browser.py --stats

    # All config paths available on a specific platform (useful for SoC docs)
    python3 yang_browser.py --by-platform "7250 IXR-X1" --kind config --limit 500
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from nokia_yang_mcp.database import db_path_for as runtime_db_path_for

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

DATA_DIR = SCRIPT_DIR.parent / "data"
CACHE_DIR = Path(os.environ.get("YANG_CACHE_DIR", "/tmp/yang_browser_cache"))
BASE_URL = "https://yangbrowser.nokia.com/releases"

# Only the latest releases are supported by this skill.
RELEASES = {
    "sros":   {"release": "26.3.R2", "product": "SR OS"},
    "srlinux": {"release": "26.3.1", "product": "SR Linux"},
}


# ---------------------------------------------------------------------------
# Build: JSONL.gz  ->  compact SQLite + FTS5
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous  = OFF;
PRAGMA temp_store   = MEMORY;
PRAGMA page_size    = 4096;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE platforms (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE namespaces (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE paths (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL,    -- canonical form, identical to gnmi-path and xpath
    path_prefix   TEXT,             -- nokia-module:path form (aka model-path, json-instance-path)
    type          TEXT,             -- "[container]", "[leaf:string]", ...
    node_type     TEXT,             -- container / leaf / list / leaf-list / ...
    description   TEXT,
    is_state      INTEGER,          -- 0=config, 1=state, NULL=unknown (SRL has no is-state)
    status        TEXT,             -- "supported" etc. (SROS only)
    namespace_id  INTEGER REFERENCES namespaces(id),
    platform_bits INTEGER NOT NULL DEFAULT 0   -- bitmask: bit i set => platforms.id = i supports this path
);

CREATE INDEX idx_paths_path     ON paths(path);
CREATE INDEX idx_paths_is_state ON paths(is_state);

-- FTS5 index over the text fields we want to search.
-- Custom tokenchars: we keep `-` and `_` so that `bgp-evpn` and `segment-routing`
-- are single tokens. But we MUST NOT include `/` `[` `]` `=` `*` in tokenchars,
-- otherwise FTS5 treats the whole path as one giant token (a bug in the previous
-- version of this schema). With the current settings, a path like
-- `/configure/router/segment-routing-v6` tokenises to
--   {"configure", "router", "segment-routing-v6"}
-- which is what we want for feature-level matching.
CREATE VIRTUAL TABLE paths_fts USING fts5(
    path, path_prefix, description,
    content='paths', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2 tokenchars ''-_'''
);
"""


def download_jsonl(product_dir: str, release: str) -> Path:
    """Download paths.jsonl.gz for a release into CACHE_DIR."""
    url = f"{BASE_URL}/{product_dir}/{release}/paths.jsonl.gz"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{product_dir}_{release}_paths.jsonl.gz"
    if out.exists() and out.stat().st_size > 0:
        return out
    req = urllib.request.Request(url, headers={"User-Agent": "yang-browser-skill/1.0"})
    print(f"Downloading {url} ...", file=sys.stderr)
    with urllib.request.urlopen(req, timeout=60) as resp, open(out, "wb") as f:
        while chunk := resp.read(65536):
            f.write(chunk)
    print(f"Cached: {out} ({out.stat().st_size/1024/1024:.1f} MB)", file=sys.stderr)
    return out


def build_db(product_dir: str, db_path: Path) -> None:
    """Build a compact SQLite DB from JSONL.gz for the given product's latest release."""
    info = RELEASES[product_dir]
    release = info["release"]
    src = download_jsonl(product_dir, release)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Pass 1: collect dictionaries and buffer entries.
    platform_id: dict[str, int] = {}
    namespace_id: dict[str, int] = {}
    entries: list[dict] = []
    with gzip.open(src, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            for p in e.get("platforms") or ():
                if p not in platform_id:
                    platform_id[p] = len(platform_id)
            ns = e.get("namespace") or ""
            if ns and ns not in namespace_id:
                namespace_id[ns] = len(namespace_id) + 1
            entries.append(e)

    if len(platform_id) > 63:
        # A 64-bit INTEGER in SQLite gives us 63 usable bits (sign bit reserved).
        # None of Nokia's releases come close, but fail loudly if that ever changes.
        raise RuntimeError(f"{len(platform_id)} platforms exceeds 63-bit bitmask capacity")

    conn.executemany(
        "INSERT INTO platforms(id, name) VALUES (?, ?)",
        [(pid, name) for name, pid in platform_id.items()],
    )
    conn.executemany(
        "INSERT INTO namespaces(id, name) VALUES (?, ?)",
        [(nid, name) for name, nid in namespace_id.items()],
    )

    # Pass 2: build rows.
    rows = []
    for e in entries:
        bits = 0
        for p in e.get("platforms") or ():
            bits |= 1 << platform_id[p]
        # In the Nokia SROS JSONL dump, `is-state` is present (and True) only for state
        # nodes; its absence marks a config node. SR Linux always includes `is-state`
        # as a boolean. Either way, "missing key" => config (0).
        is_state = 1 if e.get("is-state") is True else 0
        rows.append((
            e.get("path", ""),
            e.get("path-with-prefix", ""),
            e.get("type", ""),
            e.get("node_type", ""),
            e.get("description", ""),
            is_state,
            e.get("status", ""),
            namespace_id.get(e.get("namespace") or ""),
            bits,
        ))

    conn.executemany(
        """INSERT INTO paths
           (path, path_prefix, type, node_type, description,
            is_state, status, namespace_id, platform_bits)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )

    conn.execute(
        "INSERT INTO paths_fts(rowid, path, path_prefix, description) "
        "SELECT id, path, path_prefix, description FROM paths"
    )
    conn.execute("INSERT INTO paths_fts(paths_fts) VALUES('optimize')")

    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        [
            ("product",      info["product"]),
            ("product_dir",  product_dir),
            ("release",      release),
            ("source_url",   f"{BASE_URL}/{product_dir}/{release}/paths.jsonl.gz"),
            ("path_count",   str(len(entries))),
            ("platform_count", str(len(platform_id))),
        ],
    )
    conn.commit()
    conn.execute("VACUUM")
    conn.close()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def db_path_for(product_dir: str) -> Path:
    """Location of the pre-built DB for a product (SR OS or SR Linux).

    The skill ships the DB as a compressed ``.db.xz`` file inside its own
    ``data/`` directory. On first use we decompress it into ``CACHE_DIR`` so
    that SQLite can mmap it directly. Subsequent runs open the cached file.
    The compressed form drops the total DB payload from ~80 MB to ~3 MB, which
    keeps the skill's zip well under the 30 MB uncompressed upload limit.
    """
    return runtime_db_path_for(product_dir, data_dir=DATA_DIR, cache_dir=CACHE_DIR)


def open_db(product_dir: str) -> sqlite3.Connection:
    """Open the pre-built DB read-only. Build it on demand if missing."""
    path = db_path_for(product_dir)
    if not path.exists():
        print(f"DB not found at {path}, building from source (one-time) ...", file=sys.stderr)
        build_db(product_dir, path)
    # Open read-only via URI so we don't accidentally write or create an empty DB.
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fts_query(q: str) -> str:
    """Turn a free-text query into an FTS5 MATCH expression.

    Each whitespace-separated token is quoted (so hyphens, slashes, brackets
    inside a token don't get parsed as FTS5 operators), and tokens are ANDed.
    A trailing asterisk on a token means prefix-match.
    """
    tokens = [t for t in q.strip().split() if t]
    parts = []
    for tok in tokens:
        prefix = tok.endswith("*")
        if prefix:
            tok = tok[:-1]
        tok = tok.replace('"', '""')
        parts.append(f'"{tok}"*' if prefix else f'"{tok}"')
    return " ".join(parts)


def platform_bitmask(conn: sqlite3.Connection, fragment: str) -> tuple[int, list[str]]:
    """Return (bitmask, matched_names) for all platforms whose name contains `fragment` (case-insensitive)."""
    frag = fragment.lower()
    bits = 0
    matched = []
    for row in conn.execute("SELECT id, name FROM platforms"):
        if frag in row["name"].lower():
            bits |= 1 << row["id"]
            matched.append(row["name"])
    return bits, matched


def platforms_for_row(conn: sqlite3.Connection, platform_bits: int) -> list[str]:
    """Expand a platform_bits integer back into a sorted list of platform names."""
    names = []
    for row in conn.execute("SELECT id, name FROM platforms ORDER BY id"):
        if platform_bits & (1 << row["id"]):
            names.append(row["name"])
    return sorted(names)


def search(conn: sqlite3.Connection, *, query: str | None, platform: str | None,
           kind: str | None, substring: bool, limit: int) -> list[sqlite3.Row]:
    """Search YANG paths.

    - query: free-text, uses FTS5 by default. With substring=True, falls back
      to LIKE on path/path_prefix/description (slower but finds sub-token matches).
    - platform: case-insensitive substring match against platform names.
    - kind: 'config' or 'state' — filters on is_state.
    """
    where = []
    params: list = []
    order_by = ""  # Only set when FTS is used — FTS5 exposes a `rank` column
                   # with BM25 scores; lower rank == better match.

    # Bug fix: argparse passes "  " through as truthy; treat whitespace-only as empty
    # so that --search "   " doesn't crash inside fts_query() with an empty token list.
    if query and not query.strip():
        query = None

    if query:
        if substring:
            where.append("(path LIKE ? OR path_prefix LIKE ? OR description LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
            base = "FROM paths"
            # For LIKE fallback, prefer shorter paths (usually the canonical container
            # comes before long leaf paths) as a cheap relevance proxy.
            order_by = "ORDER BY length(paths.path) ASC"
        else:
            where.append("paths_fts MATCH ?")
            params.append(fts_query(query))
            base = "FROM paths_fts f JOIN paths ON paths.id = f.rowid"
            # BM25 ranking — best matches first
            order_by = "ORDER BY f.rank"
    else:
        base = "FROM paths"
        # Empty query + platform/kind filter: sort by path for deterministic output
        order_by = "ORDER BY paths.path"

    if platform:
        bits, matched = platform_bitmask(conn, platform)
        if bits == 0:
            print(f"Warning: no platform matches fragment {platform!r}", file=sys.stderr)
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
    return conn.execute(sql, params).fetchall()


def _canonicalise_path(path: str) -> str:
    """Normalise a YANG path so that list keys are in the canonical wildcard form.

    The DB stores paths with `[key=*]` (wildcards). Users may paste paths with
    concrete key values (e.g. `[router-name=Base]`) copied from a live device
    via gNMI tools. Without normalisation, --is-supported would say
    "path-unknown" for such paths even though the path-template exists.
    """
    import re as _r
    # Replace [key=anything-but-]] with [key=*]
    return _r.sub(r"(\[[^=\]]+=)[^\]]*\]", r"\1*]", path)


def is_supported(conn: sqlite3.Connection, path: str,
                 platform_fragment: str) -> tuple[str, list[str], list[str]]:
    """Check whether the given YANG path is supported on platforms matching the fragment.

    Returns (status, supported, unsupported):
      - status = "fully-supported"     => every matching platform supports the path
      - status = "partially-supported" => some do, some don't
      - status = "not-supported"       => path exists with a non-empty platform list,
                                          but none of the matched platforms are in it
      - status = "platform-agnostic"   => path exists in YANG, but Nokia's source data has
                                          no platform list for it (~4.5% of SROS paths).
                                          Likely a license- or MDA-gated feature; the path
                                          is not actively excluded for the user's platform.
      - status = "path-unknown"        => path not found, or platform fragment matches nothing
    `supported` and `unsupported` list platform names within the matched set.

    The input path is canonicalised: any concrete list keys (e.g. `[name=Base]`)
    are replaced with `[name=*]` so that paths copied from live devices match
    the path-templates stored in the DB.
    """
    canonical = _canonicalise_path(path)
    row = conn.execute("SELECT platform_bits FROM paths WHERE path = ?", (canonical,)).fetchone()
    if row is None:
        return "path-unknown", [], []

    bits_have = row["platform_bits"]
    want_bits, want_names = platform_bitmask(conn, platform_fragment)
    if want_bits == 0:
        return "path-unknown", [], []

    # Empty platforms list in source data: treat as platform-agnostic (don't claim
    # the path is "not supported" — Nokia just didn't tag it).
    if bits_have == 0:
        return "platform-agnostic", [], sorted(want_names)

    have_names = set(platforms_for_row(conn, bits_have))
    supported = sorted(set(want_names) & have_names)
    unsupported = sorted(set(want_names) - have_names)

    if supported and not unsupported:
        return "fully-supported", supported, unsupported
    if supported and unsupported:
        return "partially-supported", supported, unsupported
    return "not-supported", supported, unsupported


def resolve_feature(conn: sqlite3.Connection, feature: str,
                    max_containers: int = 40) -> list[sqlite3.Row]:
    """Find top-level container/list paths for a named feature.

    Matching strategy (all case-insensitive, applied to the LAST URI segment
    after stripping list keys):
      1. exact match
      2. well-known aliases (e.g. 'srv6' -> 'segment-routing-v6', 'sr' -> 'segment-routing')
      3. the tail starts with the feature and the next char is not a letter/digit
         (so 'bgp' matches 'bgp-evpn' but not 'bgp-ipvpn' if user asked 'bgp' —
          actually it DOES match both, which is the intended behaviour for
          "show me all bgp-related top-level knobs")

    Excludes state mirrors, group templates, debug/reset trees, and log events.
    Results are ranked so shorter/more canonical paths come first.
    """
    feat = feature.lower().strip()

    # Aliases: user's short-hand -> canonical tail(s). Both original and canonical
    # forms are tried, so 'srv6' finds both 'srv6' and 'segment-routing-v6' tails.
    ALIASES = {
        "srv6":              ["segment-routing-v6"],
        "sr-mpls":           ["segment-routing"],
        "sr":                ["segment-routing"],
        "evpn":              ["bgp-evpn"],
        "l3vpn":             ["vprn"],
        "l2vpn":             ["vpls", "epipe"],
        "ldp":               ["ldp"],
        "rsvp":              ["rsvp", "rsvp-te"],
        "igp":               ["isis", "ospf", "ospf3"],
        "oam":               ["oam", "oam-pm"],
    }
    targets = {feat}
    for a in ALIASES.get(feat, []):
        targets.add(a)

    # FTS pre-filter: only fetch rows whose path mentions any of the target tokens.
    # Build an FTS OR query over all targets.
    fts_clause = " OR ".join(f'"{t}"' for t in targets)

    sql = """
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
        ORDER BY f.rank
        LIMIT 2000
    """
    rows = conn.execute(sql, [fts_clause]).fetchall()

    out = []
    for r in rows:
        p = r["path"].rstrip("/")
        last = p.rsplit("/", 1)[-1]
        tail = last.split("[", 1)[0].lower()
        if tail in targets:
            out.append(r)

    # De-dup by path (FTS may return the same row via different token hits).
    seen = set()
    uniq = []
    for r in out:
        if r["path"] not in seen:
            seen.add(r["path"])
            uniq.append(r)

    # Rank: shortest path first (most canonical), then alphabetical
    uniq.sort(key=lambda r: (len(r["path"]), r["path"]))
    return uniq[:max_containers]


def support_matrix(conn: sqlite3.Connection, paths: list[str],
                   platform_fragment: str) -> tuple[list[str], list[tuple[str, dict[str, bool]]]]:
    """Return (platform_names, rows) where each row is (path, {platform: supported?}).

    `platform_names` is the sorted list of platforms that match the fragment;
    `rows` preserves the order of `paths`. Paths that don't exist in the DB are
    included with all False and an asterisk suffix, so SoC docs can show them
    as "N/A" rows explicitly.
    """
    want_bits, want_names = platform_bitmask(conn, platform_fragment)
    want_names_sorted = sorted(want_names)
    # Map platform name -> bit
    name_to_bit: dict[str, int] = {}
    for row in conn.execute("SELECT id, name FROM platforms"):
        if row["name"] in want_names:
            name_to_bit[row["name"]] = 1 << row["id"]

    result = []
    for path in paths:
        canonical = _canonicalise_path(path)
        row = conn.execute("SELECT platform_bits FROM paths WHERE path = ?", (canonical,)).fetchone()
        if row is None:
            result.append((canonical + "  (path-unknown)",
                           {n: False for n in want_names_sorted}))
            continue
        have = row["platform_bits"]
        per_plat = {n: bool(have & bit) for n, bit in name_to_bit.items()}
        # Display the canonical form so the user sees what was actually queried
        result.append((canonical, per_plat))
    return want_names_sorted, result


def print_matrix(platforms: list[str], rows: list[tuple[str, dict[str, bool]]],
                 product_label: str, truncate_path: int = 70) -> None:
    """Print a compact ASCII yes/no matrix of paths × platforms."""
    if not rows:
        print("  (no paths to display)")
        return

    # Abbreviate long platform names. For SROS the common prefix is "7250 IXR-";
    # if every name shares a common prefix, we strip it and show it in the header.
    def common_prefix(names: list[str]) -> str:
        if not names:
            return ""
        prefix = names[0]
        for n in names[1:]:
            while not n.startswith(prefix):
                prefix = prefix[:-1]
                if not prefix:
                    return ""
        return prefix

    prefix = common_prefix(platforms)
    short_names = [n[len(prefix):] or n for n in platforms]

    path_col = max(12, min(truncate_path, max(len(r[0]) for r in rows)))
    plat_col_widths = [max(len(n), 3) for n in short_names]

    # Header
    if prefix:
        print(f"  (platform names share prefix {prefix!r}, shown abbreviated)")
    hdr_plats = "  ".join(f"{n:{w}s}" for n, w in zip(short_names, plat_col_widths))
    print(f"  {'PATH':{path_col}s}  {hdr_plats}")
    print(f"  {'-'*path_col}  {'  '.join('-'*w for w in plat_col_widths)}")

    # Rows
    for path, per_plat in rows:
        display_path = path if len(path) <= truncate_path else "..." + path[-(truncate_path-3):]
        cells = []
        for n, w in zip(platforms, plat_col_widths):
            mark = "✓" if per_plat.get(n) else "·"
            cells.append(f"{mark:^{w}s}")
        print(f"  {display_path:{path_col}s}  {'  '.join(cells)}")


def inventory_by_platform(conn: sqlite3.Connection, platform_fragment: str,
                          kind: str | None, group_by_depth: int = 2,
                          limit: int = 5000, include_templates: bool = False) -> dict[str, int]:
    """Return a {top-level-group: count} summary of paths supported on the platform.

    Groups paths by the first ``group_by_depth`` segments of the path (e.g.
    `/configure/router` or `/configure/service/vpls`). This gives a compact
    overview of what capability areas are present on a given platform.

    By default, ``/configure/groups/...`` is excluded because it's just a
    template-copy of the rest of the config tree and would otherwise dominate
    the totals. Pass ``include_templates=True`` to include it.
    """
    bits, matched = platform_bitmask(conn, platform_fragment)
    if bits == 0:
        return {}

    where = ["(platform_bits & ?) != 0"]
    params: list = [bits]
    if kind == "config":
        where.append("is_state = 0")
    elif kind == "state":
        where.append("is_state = 1")
    if not include_templates:
        where.append("path NOT LIKE '/configure/groups/%'")

    sql = f"""
        SELECT path FROM paths
        WHERE {' AND '.join(where)}
        LIMIT {int(limit) * 100}
    """  # fetch broadly — we'll group in Python
    counts: dict[str, int] = {}
    for (path,) in conn.execute(sql, params):
        parts = [p for p in path.split("/") if p]
        # Strip list keys from each segment for grouping
        clean = [p.split("[", 1)[0] for p in parts[:group_by_depth]]
        key = "/" + "/".join(clean) if clean else "/"
        counts[key] = counts.get(key, 0) + 1
    return counts


def cross_product_support(feature: str, platform_fragment: str) -> dict[str, dict]:
    """Run feature support check across BOTH SROS and SRL DBs.

    Returns {product_dir: {"product": str, "release": str, "results": [(path, status, sup, unsup)...]}}
    """
    out = {}
    for product_dir in ("sros", "srlinux"):
        try:
            conn = open_db(product_dir)
        except Exception as e:
            out[product_dir] = {"error": str(e)}
            continue
        info = RELEASES[product_dir]
        containers = resolve_feature(conn, feature)
        results = []
        for c in containers[:10]:  # Keep output manageable
            status, sup, unsup = is_supported(conn, c["path"], platform_fragment)
            results.append((c["path"], status, sup, unsup))
        out[product_dir] = {
            "product": info["product"],
            "release": info["release"],
            "results": results,
            "resolved_count": len(containers),
        }
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Update — probe Nokia for newer releases and refresh the bundled DBs
# ---------------------------------------------------------------------------
#
# Nokia does not publish a manifest of available releases, so we discover them
# by probing well-formed URLs. A real release responds with
#   Content-Type: application/x-gzip   and a multi-MB Content-Length.
# A non-existent release falls through to the SPA shell:
#   Content-Type: text/html            and Content-Length around 2 KB.
# An infrastructure throttle (Anthropic egress proxy quirk) returns
#   Content-Length: 18, Content-Type: text/plain.
# We must distinguish all three.
#
# Release versioning conventions on yangbrowser.nokia.com:
#   SR OS    YY.M.Rn      where M ∈ {3, 7, 10}, n is a 1-based revision
#   SR Linux YY.M.N       where M ∈ {3, 7, 10, 11}, N is a 1-based revision
# Quarters cap at 4 (typically Q1=March, Q2=July, Q3=Oct, Q4=Nov for SRL).

import time as _time
import urllib.error
import re as _re

PROBE_HEADERS = {"User-Agent": "yang-browser-skill/1.0"}
PROBE_PAUSE_S = 3.5            # delay between probe requests
PROBE_THROTTLE_RETRIES = 4
PROBE_THROTTLE_BACKOFF_S = 8

# Quarters (months) where a major.minor release line typically opens
SROS_QUARTERS = (3, 7, 10)
SRL_QUARTERS  = (3, 7, 10, 11)


def _probe_url(url: str) -> tuple[str, int]:
    """HEAD a URL with throttle-retry. Returns (content_type, content_length).

    On hard error returns ("", 0). On Anthropic-egress soft throttle
    (CL=18 plain text) we retry a few times with backoff.
    """
    for attempt in range(PROBE_THROTTLE_RETRIES):
        req = urllib.request.Request(url, method="HEAD", headers=PROBE_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                cl = int(resp.headers.get("Content-Length") or 0)
        except urllib.error.HTTPError as e:
            return f"http-{e.code}", 0
        except Exception as e:
            ct, cl = f"err:{type(e).__name__}", 0

        # Soft throttle indicator
        if cl == 18 and ct.startswith("text/plain"):
            wait = PROBE_THROTTLE_BACKOFF_S * (attempt + 1)
            print(f"    (throttled, retry in {wait}s) ", end="", file=sys.stderr, flush=True)
            _time.sleep(wait)
            continue
        return ct, cl
    return "throttled", 0


def _is_real_release(ct: str, cl: int) -> bool:
    """A real release has gzip content-type AND a sensibly large body."""
    return "gzip" in ct and cl > 10_000  # SRL is ~1.2 MB, SROS ~7 MB


def _next_revisions(release: str, max_check: int = 5) -> list[str]:
    """Return revision strings to probe AFTER the given current release.

    SROS '26.3.R1' -> ['26.3.R2', '26.3.R3', ...]
    SRL  '25.10.2' -> ['25.10.3', '25.10.4', ...]
    """
    m = _re.match(r"^(\d+\.\d+)\.R?(\d+)$", release)
    if not m:
        return []
    base, n = m.group(1), int(m.group(2))
    is_rseries = ".R" in release.upper() or release.upper().split(".")[-1].startswith("R")
    sep = "R" if is_rseries else ""
    return [f"{base}.{sep}{n + i}" for i in range(1, max_check + 1)]


def _next_quarters(release: str, product_dir: str, max_quarters: int = 6) -> list[str]:
    """Return future major.minor.R1 / .1 candidates to probe."""
    m = _re.match(r"^(\d+)\.(\d+)\.", release)
    if not m:
        return []
    year, month = int(m.group(1)), int(m.group(2))
    quarters = SROS_QUARTERS if product_dir == "sros" else SRL_QUARTERS
    suffix = ".R1" if product_dir == "sros" else ".1"

    out = []
    # Walk forward through the calendar
    y, q_idx = year, quarters.index(month) if month in quarters else 0
    for _ in range(max_quarters):
        # Move to next quarter
        q_idx += 1
        if q_idx >= len(quarters):
            q_idx = 0
            y += 1
        out.append(f"{y}.{quarters[q_idx]}{suffix}")
    return out


def find_latest(product_dir: str, current_release: str,
                verbose: bool = True) -> str | None:
    """Probe yangbrowser.nokia.com to find the newest release for this product.

    Strategy:
      1. Walk forward through revisions of the current minor (R2, R3, ...).
      2. Then probe the next major.minor.R1 / .1 candidates.
      Stop on the first 404 within each phase; the previous one is the latest.

    Returns the latest release string, or None if probing fails entirely.
    """
    base_url = f"{BASE_URL}/{product_dir}"
    latest = current_release

    if verbose:
        print(f"  Probing newer revisions of {current_release} ...", file=sys.stderr)

    for cand in _next_revisions(current_release):
        url = f"{base_url}/{cand}/paths.jsonl.gz"
        if verbose:
            print(f"    {cand:14s} ", end="", file=sys.stderr, flush=True)
        ct, cl = _probe_url(url)
        if _is_real_release(ct, cl):
            if verbose:
                print(f"EXISTS  ({cl/1024/1024:.1f} MB)", file=sys.stderr)
            latest = cand
            _time.sleep(PROBE_PAUSE_S)
            continue
        if verbose:
            print(f"absent  (CT={ct} CL={cl})", file=sys.stderr)
        break

    if verbose:
        print(f"  Probing next major.minor lines ...", file=sys.stderr)

    # Don't break on the first absent quarter: Nokia sometimes skips months
    # (e.g. an extra .11 release exists for SRL but not always). Probe a
    # full year-ahead window and pick up anything that exists.
    for cand in _next_quarters(latest, product_dir, max_quarters=5):
        url = f"{base_url}/{cand}/paths.jsonl.gz"
        if verbose:
            print(f"    {cand:14s} ", end="", file=sys.stderr, flush=True)
        ct, cl = _probe_url(url)
        _time.sleep(PROBE_PAUSE_S)
        if not _is_real_release(ct, cl):
            if verbose:
                print(f"absent", file=sys.stderr)
            continue
        if verbose:
            print(f"EXISTS  ({cl/1024/1024:.1f} MB)", file=sys.stderr)
        latest = cand
        # Once a new major.minor exists, also probe its revisions
        for sub in _next_revisions(cand):
            url2 = f"{base_url}/{sub}/paths.jsonl.gz"
            if verbose:
                print(f"    {sub:14s} ", end="", file=sys.stderr, flush=True)
            ct2, cl2 = _probe_url(url2)
            _time.sleep(PROBE_PAUSE_S)
            if _is_real_release(ct2, cl2):
                if verbose:
                    print(f"EXISTS  ({cl2/1024/1024:.1f} MB)", file=sys.stderr)
                latest = sub
            else:
                if verbose:
                    print(f"absent", file=sys.stderr)
                break

    return latest if latest != current_release else None


def update_releases_dict_in_script(updates: dict[str, str]) -> bool:
    """Rewrite the RELEASES = {...} block in this very script.

    `updates` is {product_dir: new_release}. Only the release strings change;
    the product display names stay as-is. Returns True if the script file was
    modified.
    """
    script_path = Path(__file__).resolve()
    text = script_path.read_text()

    # The block is short and well-formed; do a line-precise rewrite.
    new_lines = []
    in_block = False
    changed = False
    for line in text.splitlines(keepends=True):
        if line.startswith("RELEASES = {"):
            in_block = True
            new_lines.append(line)
            continue
        if in_block:
            if line.strip() == "}":
                in_block = False
                new_lines.append(line)
                continue
            m = _re.match(r'^(\s*)"(\w+)":\s*\{"release":\s*"([^"]+)",\s*"product":\s*"([^"]+)"\},?\s*$',
                          line)
            if m and m.group(2) in updates:
                indent, key, _old_rel, prod = m.groups()
                new_rel = updates[key]
                new_lines.append(f'{indent}"{key}":'.ljust(13) +
                                 f' {{"release": "{new_rel}", "product": "{prod}"}},\n')
                changed = True
                continue
        new_lines.append(line)

    if changed:
        script_path.write_text("".join(new_lines))
    return changed


def cmd_check_updates(verbose: bool = True) -> dict[str, str | None]:
    """Probe both products. Returns {product_dir: new_release_or_None}."""
    out = {}
    for pdir, info in RELEASES.items():
        if verbose:
            print(f"\n# {info['product']} (currently {info['release']})", file=sys.stderr)
        out[pdir] = find_latest(pdir, info["release"], verbose=verbose)
        _time.sleep(PROBE_PAUSE_S)
    return out


def cmd_update(products: list[str] | None = None, dry_run: bool = False,
               skip_probe: bool = False) -> int:
    """Check for updates, download, build DB, repack as .db.xz, edit RELEASES.

    Returns 0 if everything succeeded (or nothing to do), 1 if any product
    failed mid-way, 2 if probing failed entirely.

    If `skip_probe=True`, do NOT contact Nokia. Instead, rebuild whatever .db.xz
    files are already present in DATA_DIR for releases newer than what's in
    RELEASES. Useful to resume an interrupted update.
    """
    import lzma

    targets = products or list(RELEASES.keys())

    if skip_probe:
        # Find any data/<pdir>_<release>.db.xz where <release> != current
        found = {}
        for pdir in targets:
            cur = RELEASES[pdir]["release"]
            for f in DATA_DIR.glob(f"{pdir}_*.db.xz"):
                rel = f.stem.replace(f"{pdir}_", "").replace(".db", "")
                if rel != cur:
                    found[pdir] = rel
                    break
    else:
        found = cmd_check_updates(verbose=True)

    print(file=sys.stderr)
    todo = {p: r for p, r in found.items() if r is not None and p in targets}
    if not todo:
        print("All bundled DBs are already on the latest release.", file=sys.stderr)
        return 0

    print("Update plan:", file=sys.stderr)
    for p, r in todo.items():
        print(f"  {RELEASES[p]['product']}: {RELEASES[p]['release']} -> {r}", file=sys.stderr)
    if dry_run:
        print("(dry run — no changes made)", file=sys.stderr)
        return 0

    # Build a temporary RELEASES copy so download_jsonl + build_db can use it
    new_releases = {k: dict(v) for k, v in RELEASES.items()}
    for p, r in todo.items():
        new_releases[p]["release"] = r

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for pdir, new_rel in todo.items():
        info = new_releases[pdir]
        out_xz = DATA_DIR / f"{pdir}_{new_rel}.db.xz"

        # Skip if a valid (decompressable) xz already exists
        if out_xz.exists():
            try:
                with lzma.open(out_xz) as f:
                    f.read(1024)  # just verify the stream is well-formed
                print(f"\n=== {info['product']} {new_rel} (already packed) ===", file=sys.stderr)
                # Still need to drop obsolete xz files for this product
                for old in DATA_DIR.glob(f"{pdir}_*.db.xz"):
                    if old != out_xz:
                        print(f"Removing obsolete {old.name}", file=sys.stderr)
                        old.unlink()
                continue
            except (lzma.LZMAError, EOFError):
                print(f"\n=== {info['product']} {new_rel} (re-packing — old xz was truncated) ===",
                      file=sys.stderr)
                out_xz.unlink()

        print(f"\n=== {info['product']} {new_rel} ===", file=sys.stderr)

        # Patch RELEASES temporarily so download_jsonl/build_db pick up the new release
        old_entry = RELEASES[pdir]
        RELEASES[pdir] = info
        try:
            scratch = CACHE_DIR / f"{pdir}_{new_rel}.db"
            if not scratch.exists():
                build_db(pdir, scratch)
            else:
                print(f"Reusing existing build at {scratch.name}", file=sys.stderr)

            # Atomic xz write: write to .partial, then rename.
            # preset=9 is the sweet spot — preset=9|EXTREME gives only ~3% smaller
            # output but takes 3x longer.
            partial = out_xz.with_suffix(".xz.partial")
            print(f"Compressing {scratch.name} -> {out_xz.name} ...", file=sys.stderr)
            with open(scratch, "rb") as src, lzma.open(partial, "wb", preset=9) as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            partial.replace(out_xz)
            print(f"  {scratch.stat().st_size/1024/1024:.1f} MB -> "
                  f"{out_xz.stat().st_size/1024/1024:.1f} MB", file=sys.stderr)

            # Drop obsolete .db.xz for this product
            for old in DATA_DIR.glob(f"{pdir}_*.db.xz"):
                if old != out_xz:
                    print(f"Removing obsolete {old.name}", file=sys.stderr)
                    old.unlink()
        except Exception as e:
            print(f"FAILED for {pdir}: {e}", file=sys.stderr)
            RELEASES[pdir] = old_entry
            return 1

    # Persist the new RELEASES dict in the script itself
    edits = {p: r for p, r in todo.items()}
    if update_releases_dict_in_script(edits):
        print(f"\nUpdated RELEASES dict in {Path(__file__).name}", file=sys.stderr)
    else:
        print(f"\nWARNING: failed to rewrite RELEASES dict — please edit manually:", file=sys.stderr)
        for p, r in todo.items():
            print(f'  "{p}": {{"release": "{r}", ...}}', file=sys.stderr)

    print("\nDone. Run --pack-skill to build the final yang-browser.zip "
          "for upload to claude.ai.", file=sys.stderr)
    return 0


def cmd_pack_skill(out_path: Path | None = None) -> int:
    """Bundle SKILL.md + scripts/ + data/ into a zip ready for claude.ai upload."""
    import zipfile
    skill_root = SCRIPT_DIR.parent  # the directory containing SKILL.md
    if not (skill_root / "SKILL.md").exists():
        print(f"Error: SKILL.md not found at {skill_root}", file=sys.stderr)
        return 2
    if out_path is None:
        out_path = skill_root.parent / "yang-browser.zip"
    out_path = out_path.resolve()

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(skill_root):
            root_p = Path(root)
            # Skip hidden dirs / __pycache__
            if any(part.startswith(".") or part == "__pycache__" for part in root_p.parts):
                continue
            for fname in files:
                if fname.startswith(".") or fname.endswith(".pyc"):
                    continue
                f = root_p / fname
                rel = Path(skill_root.name) / f.relative_to(skill_root)
                zf.write(f, rel)

    # Verify size
    total_uncompressed = sum(f.file_size for f in zipfile.ZipFile(out_path).infolist())
    LIMIT = 30 * 1024 * 1024
    print(f"\nPacked: {out_path}", file=sys.stderr)
    print(f"  zip size:           {out_path.stat().st_size/1024/1024:.2f} MB", file=sys.stderr)
    print(f"  uncompressed total: {total_uncompressed/1024/1024:.2f} MB  (claude.ai limit: 30 MB)",
          file=sys.stderr)
    if total_uncompressed > LIMIT:
        print(f"  WARNING: exceeds claude.ai 30 MB uncompressed limit!", file=sys.stderr)
        return 1
    print(f"\nUpload {out_path.name} via claude.ai → Settings → Skills → Create skill.",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_header(product_dir: str) -> None:
    """Print a one-line reference of what DB we're querying."""
    info = RELEASES[product_dir]
    print(f"# {info['product']} {info['release']}", file=sys.stderr)


def kind_letter(row: sqlite3.Row) -> str:
    return "S" if row["is_state"] == 1 else "C"


def print_results(conn: sqlite3.Connection, results: list[sqlite3.Row], verbose: bool) -> None:
    for row in results:
        print(f"  [{kind_letter(row)}] {row['path']}  {row['type']}")
        if verbose:
            if row["description"]:
                # Collapse multi-line descriptions
                desc = " ".join(row["description"].split())
                print(f"       {desc}")
            names = platforms_for_row(conn, row["platform_bits"])
            if names:
                shown = ", ".join(names[:8]) + ("..." if len(names) > 8 else "")
                print(f"       Platforms ({len(names)}): {shown}")
            print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Nokia YANG Browser — fast local search over SR OS & SR Linux YANG paths.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # SR OS keyword + platform
  %(prog)s -s "msdp" -p "IXR-e3x" -v
  %(prog)s -s "bgp-evpn" -p "7250 IXR-X1" -v

  # SR Linux
  %(prog)s --product srlinux -s "bgp" -p "7220 IXR-D3" -v

  # Strict support check (exit code: 0=supported, 1=not, 2=path unknown)
  %(prog)s --is-supported "/configure/router[router-name=*]/msdp" -p "IXR-e3x"

  # Listings
  %(prog)s --platforms
  %(prog)s --stats

  # Feature resolver — finds top-level containers for a named feature
  %(prog)s --feature srv6 -p "IXR-e" --matrix
  %(prog)s --feature bgp-evpn -p "IXR-X"

  # Support matrix — explicit paths × platforms grid
  %(prog)s --matrix -p "IXR-e" \\
      --path "/configure/router[router-name=*]/segment-routing/segment-routing-v6" \\
      --path "/configure/service/vpls[service-name=*]/vxlan"

  # Feature inventory on a platform
  %(prog)s --by-platform "IXR-e3x" --kind config

  # Cross-product check (SROS + SRL at once)
  %(prog)s --cross-product --feature bgp-evpn -p "IXR"

  # Refresh everything in one shot (probe Nokia → update DBs → repack zip)
  %(prog)s --release
        """,
    )
    p.add_argument("--product", choices=["sros", "srlinux"], default="sros",
                   help="Which product to query (default: sros)")
    p.add_argument("--search",  "-s", help="Search keyword (FTS5 full-token match, ranked by relevance)")
    p.add_argument("--substring", action="store_true",
                   help="Use substring LIKE search instead of FTS (slower, finds sub-token matches)")
    p.add_argument("--platform", "-p", help="Filter by platform (case-insensitive substring, e.g. 'IXR-X1')")
    p.add_argument("--kind",     "-k", choices=["config", "state"], help="Filter to config or state paths")
    p.add_argument("--limit",    "-l", type=int, default=50, help="Max results (default: 50)")
    p.add_argument("--verbose",  "-v", action="store_true", help="Show description and platform list")
    p.add_argument("--json",     action="store_true", help="Output results as JSON")

    p.add_argument("--is-supported", metavar="PATH",
                   help="Exact path support check (requires --platform). Exits 0/1/2.")
    p.add_argument("--feature",   metavar="NAME",
                   help="Resolve a feature name (e.g. 'srv6', 'bgp-evpn') to its top-level "
                        "container paths, and print them. Combine with --matrix or -p for "
                        "platform-scoped support check.")
    p.add_argument("--matrix",    action="store_true",
                   help="Produce a paths × platforms ASCII matrix. Use with --feature and/or --path. "
                        "Requires --platform.")
    p.add_argument("--path",      action="append", default=[],
                   help="Explicit YANG path for --matrix. May be given multiple times.")
    p.add_argument("--by-platform", metavar="PLATFORM",
                   help="Print a grouped feature inventory for a specific platform.")
    p.add_argument("--group-depth", type=int, default=2,
                   help="For --by-platform: path segments to group by (default 2)")
    p.add_argument("--include-templates", action="store_true",
                   help="For --by-platform: include /configure/groups/* templates "
                        "in counts (excluded by default as they duplicate the tree)")
    p.add_argument("--cross-product", action="store_true",
                   help="With --feature, run resolution and support check across BOTH SROS and SRL.")
    p.add_argument("--platforms", action="store_true", help="List all platforms for the product")
    p.add_argument("--stats",     action="store_true", help="Show DB metadata (release, path count, etc.)")
    p.add_argument("--build",     action="store_true",
                   help="Force (re)build the DB from source JSONL.gz into the cache dir")
    p.add_argument("--pack",      action="store_true",
                   help="Maintainer: build DB and save as data/<product>_<release>.db.xz "
                        "for shipping inside the skill zip")
    p.add_argument("--check-updates", action="store_true",
                   help="Probe Nokia for newer SR OS / SR Linux releases. "
                        "Exits 0 if up-to-date, 1 if updates are available.")
    p.add_argument("--update", action="store_true",
                   help="Same as --check-updates, but also download newer release(s), "
                        "rebuild the DB(s), repack as .db.xz, and edit RELEASES "
                        "in this script. Use --product to limit which one is updated.")
    p.add_argument("--dry-run", action="store_true",
                   help="With --update: show the plan but do not download or modify anything")
    p.add_argument("--skip-probe", action="store_true",
                   help="With --update: do not contact Nokia. Resume an interrupted update by "
                        "rebuilding from any newer .db.xz files already in data/.")
    p.add_argument("--pack-skill", action="store_true",
                   help="Bundle the whole skill (SKILL.md + scripts/ + data/) into a zip "
                        "ready for upload to claude.ai. Output: ./yang-browser.zip")
    p.add_argument("--release", action="store_true",
                   help="One-shot: probe Nokia for newer releases, download/build/repack any "
                        "updates, and bundle the final zip. Equivalent to "
                        "--update followed by --pack-skill, with a single summary at the end.")

    args = p.parse_args()

    if args.check_updates:
        results = cmd_check_updates(verbose=True)
        any_new = False
        print(file=sys.stderr)
        for pdir, new_rel in results.items():
            cur = RELEASES[pdir]["release"]
            prod = RELEASES[pdir]["product"]
            if new_rel and new_rel != cur:
                print(f"  {prod}: {cur}  ->  {new_rel}  (UPDATE AVAILABLE)", file=sys.stderr)
                any_new = True
            else:
                print(f"  {prod}: {cur}  (up to date)", file=sys.stderr)
        if any_new:
            print("\nRun --update to download and rebuild. Then --pack-skill to make the upload zip.",
                  file=sys.stderr)
            return 1
        return 0

    if args.update:
        # Always update both products (user preference: keep them in sync).
        products = [args.product] if "--product" in sys.argv else None
        return cmd_update(products=products, dry_run=args.dry_run, skip_probe=args.skip_probe)

    if args.pack_skill:
        return cmd_pack_skill()

    if args.release:
        # One-shot: update (if needed) + pack-skill, with a clear final summary.
        # This is the recommended way to refresh the skill.
        before = {p: info["release"] for p, info in RELEASES.items()}
        rc = cmd_update(products=None, dry_run=args.dry_run, skip_probe=args.skip_probe)
        if rc != 0:
            print("\nUpdate phase failed; not packing.", file=sys.stderr)
            return rc
        if args.dry_run:
            return 0
        # Reload module-level RELEASES from the just-edited script source so the
        # summary below reflects what was written. (cmd_update mutates RELEASES
        # transiently but restores it on failure; on success it relies on the
        # script-edit to persist the new values, which we already see in memory.)
        after = {p: info["release"] for p, info in RELEASES.items()}
        changes = [(p, before[p], after[p]) for p in RELEASES if before[p] != after[p]]

        print("\n" + "=" * 60, file=sys.stderr)
        print("Packing final skill zip ...", file=sys.stderr)
        rc = cmd_pack_skill()
        print("=" * 60, file=sys.stderr)
        if changes:
            print(f"\nReleases updated:", file=sys.stderr)
            for p, old, new in changes:
                print(f"  {RELEASES[p]['product']}: {old}  ->  {new}", file=sys.stderr)
            print(f"\nNext step: upload yang-browser.zip via claude.ai → "
                  f"Settings → Skills → Create skill (replace the existing one).",
                  file=sys.stderr)
        else:
            print(f"\nNo release changes — DBs were already up-to-date. "
                  f"yang-browser.zip rebuilt anyway.", file=sys.stderr)
        return rc

    if args.pack:
        # Build to a scratch path, then xz-compress into DATA_DIR.
        import lzma
        info = RELEASES[args.product]
        scratch = CACHE_DIR / f"{args.product}_{info['release']}.db"
        build_db(args.product, scratch)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = DATA_DIR / f"{args.product}_{info['release']}.db.xz"
        print(f"Compressing {scratch.name} -> {out.name} (xz, preset=9) ...", file=sys.stderr)
        with open(scratch, "rb") as src, lzma.open(out, "wb", preset=9) as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
        src_mb = scratch.stat().st_size / 1024 / 1024
        dst_mb = out.stat().st_size / 1024 / 1024
        print(f"  {src_mb:.1f} MB -> {dst_mb:.1f} MB", file=sys.stderr)
        return 0

    if args.build:
        # Force a rebuild of the cached DB from JSONL.gz (ignore any shipped .db.xz)
        info = RELEASES[args.product]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached = CACHE_DIR / f"{args.product}_{info['release']}.db"
        build_db(args.product, cached)
        print(f"Built {cached}", file=sys.stderr)
        return 0

    # --------- cross-product (must open both DBs; handle before single-DB open) ---------
    if args.cross_product:
        if not args.feature:
            print("Error: --cross-product requires --feature", file=sys.stderr)
            return 2
        if not args.platform:
            print("Error: --cross-product requires --platform", file=sys.stderr)
            return 2
        out = cross_product_support(args.feature, args.platform)
        for pdir, data in out.items():
            if "error" in data:
                print(f"\n# {pdir}: ERROR {data['error']}")
                continue
            print(f"\n# {data['product']} {data['release']}  "
                  f"({data['resolved_count']} top-level container(s) resolved for "
                  f"feature={args.feature!r})")
            if not data["results"]:
                print(f"  (no containers resolved)")
                continue
            for path, status, sup, unsup in data["results"]:
                icon = {"fully-supported":"✓", "partially-supported":"◐",
                        "not-supported":"✗", "path-unknown":"?",
                        "platform-agnostic":"~"}.get(status, "?")
                summary = status.replace("-", " ")
                print(f"  {icon} {path}")
                print(f"      {summary}  (supported {len(sup)}, unsupported {len(unsup)})")
        return 0

    conn = open_db(args.product)

    if args.stats:
        print_header(args.product)
        rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        width = max(len(k) for k in rows)
        for k, v in rows.items():
            print(f"  {k:{width}s}  {v}")
        print(f"  {'db_file':{width}s}  {db_path_for(args.product)}")
        print(f"  {'db_size_mb':{width}s}  {db_path_for(args.product).stat().st_size/1024/1024:.1f}")
        return 0

    if args.platforms:
        info = RELEASES[args.product]
        names = [r["name"] for r in conn.execute("SELECT name FROM platforms ORDER BY name")]
        print(f"Platforms for {info['product']} {info['release']} ({len(names)}):")
        for n in names:
            print(f"  {n}")
        return 0

    # --------- feature resolver / matrix ---------
    if args.feature or args.matrix:
        print_header(args.product)
        paths = list(args.path)

        if args.feature:
            containers = resolve_feature(conn, args.feature)
            if not containers:
                print(f"No top-level containers found for feature {args.feature!r}", file=sys.stderr)
                return 2
            print(f"Feature {args.feature!r} resolves to {len(containers)} top-level path(s):")
            for c in containers:
                print(f"  {c['path']}")
            paths.extend(c["path"] for c in containers)

        if args.matrix:
            if not args.platform:
                print("Error: --matrix requires --platform", file=sys.stderr)
                return 2
            if not paths:
                print("Error: --matrix needs --feature or --path", file=sys.stderr)
                return 2
            # Dedupe while preserving order
            seen = set()
            unique_paths = [p for p in paths if not (p in seen or seen.add(p))]
            platforms, rows = support_matrix(conn, unique_paths, args.platform)
            if not platforms:
                print(f"Warning: no platform matches fragment {args.platform!r}", file=sys.stderr)
                return 2
            info = RELEASES[args.product]
            print(f"\nSupport matrix — {info['product']} {info['release']}, "
                  f"{len(unique_paths)} path(s) × {len(platforms)} platform(s)")
            print_matrix(platforms, rows, info["product"])
        elif args.feature and args.platform:
            # Feature specified but no --matrix: give a one-line summary per container
            print()
            for c in containers:
                status, sup, unsup = is_supported(conn, c["path"], args.platform)
                icon = {"fully-supported":"✓","partially-supported":"◐",
                        "not-supported":"✗","path-unknown":"?",
                        "platform-agnostic":"~"}.get(status,"?")
                print(f"  {icon} {c['path']}  — {status.replace('-',' ')} "
                      f"(sup={len(sup)}, unsup={len(unsup)})")
        return 0

    # --------- by-platform inventory ---------
    if args.by_platform:
        print_header(args.product)
        counts = inventory_by_platform(conn, args.by_platform,
                                       kind=args.kind, group_by_depth=args.group_depth,
                                       include_templates=args.include_templates)
        if not counts:
            print(f"Warning: no platform matches fragment {args.by_platform!r}", file=sys.stderr)
            return 2
        info = RELEASES[args.product]
        print(f"\nFeature inventory — platforms matching {args.by_platform!r} in "
              f"{info['product']} {info['release']}")
        print(f"  (grouped by first {args.group_depth} path segments"
              f"{f', kind={args.kind}' if args.kind else ''})")
        total = sum(counts.values())
        # Sort by count descending
        for group, n in sorted(counts.items(), key=lambda x: -x[1]):
            bar = "█" * min(40, int(40 * n / max(counts.values())))
            print(f"  {group:45s}  {n:6d}  {bar}")
        print(f"  {'TOTAL':45s}  {total:6d}")
        return 0

    if args.is_supported:
        if not args.platform:
            print("Error: --is-supported requires --platform", file=sys.stderr)
            return 2
        print_header(args.product)
        status, supported, unsupported = is_supported(conn, args.is_supported, args.platform)
        if status == "path-unknown":
            print(f"PATH UNKNOWN: {args.is_supported!r} not found in {RELEASES[args.product]['product']} "
                  f"{RELEASES[args.product]['release']}, or platform fragment "
                  f"{args.platform!r} matches nothing.", file=sys.stderr)
            return 2
        if status == "fully-supported":
            print(f"SUPPORTED on all matched platforms: {', '.join(supported)}")
            return 0
        if status == "partially-supported":
            print(f"PARTIAL support for {args.is_supported}:")
            print(f"  supported   ({len(supported)}): {', '.join(supported)}")
            print(f"  unsupported ({len(unsupported)}): {', '.join(unsupported)}")
            return 1
        if status == "platform-agnostic":
            print(f"PLATFORM-AGNOSTIC: {args.is_supported}")
            print(f"  Path exists in YANG, but Nokia's source data has no platform list for it.")
            print(f"  This usually means the path is license-gated, MDA-specific, or universally available")
            print(f"  rather than restricted to specific platforms. Verify with the actual feature docs.")
            return 0
        # not-supported
        print(f"NOT SUPPORTED on any matched platform ({len(unsupported)}): "
              f"{', '.join(unsupported)}")
        return 1

    if not args.search and not args.platform and not args.kind:
        p.print_help()
        return 0

    print_header(args.product)
    results = search(conn,
                     query=args.search, platform=args.platform,
                     kind=args.kind, substring=args.substring, limit=args.limit)

    info = RELEASES[args.product]
    print(f"Found {len(results)} results in {info['product']} {info['release']}"
          f"{' (limit reached)' if len(results) >= args.limit else ''}", file=sys.stderr)

    if args.json:
        out = []
        for r in results:
            out.append({
                "path": r["path"],
                "path_prefix": r["path_prefix"],
                "type": r["type"],
                "node_type": r["node_type"],
                "description": r["description"],
                "kind": {1: "state", 0: "config"}.get(r["is_state"]),
                "platforms": platforms_for_row(conn, r["platform_bits"]),
            })
        print(json.dumps(out, indent=2))
    else:
        print_results(conn, results, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Caller closed stdout (e.g. `| head`, `| less`). Exit quietly.
        # Devnull stderr so the cleanup flush doesn't raise another error.
        try:
            sys.stderr.close()
        except Exception:
            pass
        sys.exit(0)
