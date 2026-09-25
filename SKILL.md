---
name: yang-browser
description: >
  Check Nokia SR OS and SR Linux feature support by platform using YANG models.
  Use whenever the user asks to verify whether a feature, protocol, or YANG path
  is supported on a specific Nokia platform or hardware variant — for example
  "does IXR-e3x support MSDP?", "is SRv6 available on 7750 SR-1s?", looking up
  gNMI/NETCONF paths for a feature, comparing platforms or releases, or building
  a feature × platform matrix for Statement-of-Compliance documents. Also trigger
  on "yang browser", "yang model", "platform support", "feature check" in a Nokia
  networking context. Covers the latest SR OS and SR Linux releases only
  (currently SR OS 26.7.R1 and SR Linux 26.7.1). To refresh data when newer
  releases ship, run `python3 scripts/yang_browser.py --release`.
---

# Nokia YANG Browser Skill

Fast local lookup of YANG paths and platform support for Nokia SR OS and SR Linux,
backed by a pre-built SQLite database with FTS5 full-text index. Covers the **latest
release of each product only**: SR OS 26.7.R1 (~134k paths) and SR Linux 26.7.1 (~19k paths).

## What this skill is good for

Answering questions like:

- "Is SRv6 supported on the IXR-e family?" → one command, full matrix
- "Does the 7250 IXR-e3x support EVPN-VXLAN?" → yes/no with exit code
- "What are the top-level container paths for BGP-EVPN in SROS?" → feature resolver
- "Give me a feature inventory of the 7220 IXR-D3 in SRL" → grouped counts
- "Compare BGP-EVPN support on SROS vs SR Linux" → cross-product check
- "Show me all YANG paths matching 'srv6 locator' on 7750 SR-1s" → ranked search

## How it works

- Source data: `paths.jsonl.gz` files from `https://yangbrowser.nokia.com`.
- Pre-built SQLite databases are shipped in `data/` as xz-compressed blobs
  (to keep the skill zip under the claude.ai 30 MB uncompressed upload limit):
  - `data/sros_26.7.R1.db.xz`    (~5.7 MB compressed → ~86 MB DB)
  - `data/srlinux_26.7.1.db.xz`  (~1.3 MB compressed → ~16 MB DB)

  On first use the script decompresses the relevant DB into
  the system temporary directory (or `$YANG_CACHE_DIR` if set). Subsequent
  runs open the cached DB directly.
- The databases use:
  - An FTS5 full-text index over `path`, `path_prefix`, and `description`, with
    custom tokenchars `-_` so that `bgp-evpn` and `segment-routing-v6` are single
    tokens while `/`, `[`, `]`, `=`, `*` correctly split tokens. BM25 ranking
    sorts results by relevance.
  - A bitmask column (`platform_bits`) for O(1) platform membership tests via
    bitwise AND (up to 63 platforms per product).
  - Interned namespace strings.
- Queries run in well under 1 ms once the DB is open; end-to-end including Python
  startup is around 180–300 ms.

## Quick reference

Run the script from this skill's directory:

```bash
python3 <skill-dir>/scripts/yang_browser.py [options]
```

### Recommended workflow for feature/platform questions

**1. If the user names a feature ("SRv6", "BGP-EVPN", "VXLAN"):**
Use `--feature NAME` — it resolves the feature to a list of top-level YANG
container paths, so you don't have to guess the exact path. Aliases are built
in: `srv6` → `segment-routing-v6`, `evpn` → `bgp-evpn`, `l3vpn` → `vprn`,
`l2vpn` → `vpls`/`epipe`, `sr` → `segment-routing`, etc.

**2. For multiple platforms at once, add `--matrix`:**
Prints a compact paths × platforms ASCII grid with ✓ / · marks. Ideal for SoC
documents and tender responses.

**3. For a definitive yes/no on an exact path:**
Use `--is-supported PATH -p PLATFORM`. Exit code is 0/1/2 — scriptable.

### Common patterns

**Feature-level support check (the killer feature):**
```bash
# One command answers "is SRv6 supported on IXR-e?"
python3 <script> --feature srv6 -p "IXR-e" --matrix

# Cross-product at a glance (SROS + SRL together)
python3 <script> --cross-product --feature bgp-evpn -p "IXR"
```

**Search SR OS for a feature, filter by platform (FTS keyword search, ranked):**
```bash
python3 <script> -s "msdp" -p "IXR-e3x" -v
python3 <script> -s "bgp-evpn" -p "7250 IXR-X1" -v
python3 <script> -s "srv6 locator" -p "7750 SR-1s" -v
```

**Search SR Linux:**
```bash
python3 <script> --product srlinux -s "bgp-evpn" -p "7220 IXR-D3" -v
python3 <script> --product srlinux --feature evpn -p "7220 IXR-D" --matrix
```

**Strict support check (exit-code-driven):**
```bash
python3 <script> --is-supported "/configure/router[router-name=*]/msdp" -p "IXR-e3x"
# exit 0 = fully supported on every matched platform
# exit 1 = partially or not supported (output lists which platforms do/don't)
# exit 2 = path not found OR platform fragment matches nothing
```

**Custom matrix with explicit paths:**
```bash
python3 <script> --matrix -p "IXR-e" \
    --path "/configure/router[router-name=*]/segment-routing/segment-routing-v6" \
    --path "/configure/service/vpls[service-name=*]/vxlan" \
    --path "/configure/service/vprn[service-name=*]/bgp-evpn"
```

**Feature inventory on a platform (capability overview):**
```bash
python3 <script> --by-platform "IXR-e3x" --kind config
python3 <script> --product srlinux --by-platform "7220 IXR-D3" --group-depth 1
```

**Filter to config vs state paths:**
```bash
python3 <script> -s "interface" -k config -p "7750 SR-1s"
python3 <script> -s "interface" -k state  -p "7750 SR-1s"
```

**Substring search** (slower LIKE fallback when FTS token matching is too strict):
```bash
python3 <script> -s "evi" --substring -p "7220 IXR-D3"
```

**List platforms / DB metadata:**
```bash
python3 <script> --platforms
python3 <script> --product srlinux --platforms
python3 <script> --stats                       # DB info for SROS
python3 <script> --product srlinux --stats
```

**JSON output** (for programmatic post-processing):
```bash
python3 <script> -s "srv6" -p "7250 IXR-X1" --json -l 200
```

**Rebuild the DB** (e.g. after a new Nokia release drops — update the `RELEASES`
dict in the script first, then):
```bash
python3 <script> --product sros    --build    # into cache dir
python3 <script> --product srlinux --build
# For maintainers shipping the skill:
python3 <script> --product sros --pack        # writes data/*.db.xz
```

**Refresh everything in one command** (recommended for periodic maintenance):
```bash
python3 <script> --release
```
This probes Nokia for newer SR OS / SR Linux releases, downloads any updates,
rebuilds the DBs, repacks them as `.db.xz`, edits the `RELEASES` dict in the
script, and bundles the final `yang-browser.zip` ready for upload to claude.ai.
Resumable — if interrupted, run `--update --skip-probe` to continue from where
it stopped, then `--pack-skill`.

Other update-related commands:
```bash
python3 <script> --check-updates        # probe only, no changes (exit 1 if updates available)
python3 <script> --update               # download + rebuild + repack, no zip
python3 <script> --update --dry-run     # show plan, no changes
python3 <script> --update --skip-probe  # resume an interrupted update
python3 <script> --pack-skill           # bundle yang-browser.zip
```

### All options

| Flag | Short | Description |
|------|-------|-------------|
| `--product` | | `sros` (default) or `srlinux` |
| `--search` | `-s` | Keyword search (FTS5 full-token match, BM25-ranked) |
| `--substring` | | Use substring LIKE search instead of FTS |
| `--platform` | `-p` | Case-insensitive platform substring (e.g. `IXR-e3x`, `VSR`) |
| `--kind` | `-k` | Filter to `config` or `state` paths |
| `--limit` | `-l` | Max results (default 50) |
| `--verbose` | `-v` | Include description and platform list per result |
| `--json` | | Emit results as JSON |
| `--is-supported PATH` | | Strict support check; requires `-p`. Exits 0/1/2 |
| `--feature NAME` | | Resolve a feature name to its top-level containers (aliases built in) |
| `--matrix` | | Produce paths × platforms ASCII matrix. Use with `--feature` and/or `--path` |
| `--path PATH` | | Explicit YANG path for `--matrix`. May be given multiple times |
| `--by-platform NAME` | | Grouped feature inventory for a specific platform |
| `--group-depth N` | | For `--by-platform`: path segments to group by (default 2) |
| `--include-templates` | | For `--by-platform`: include `/configure/groups/*` templates (excluded by default) |
| `--cross-product` | | With `--feature`, compare support across BOTH SROS and SRL |
| `--platforms` | | List all platforms known for the product |
| `--stats` | | Show DB metadata (release, path count, etc.) |
| `--build` | | (Re)build the DB in the cache dir from Nokia's JSONL.gz source |
| `--pack` | | Maintainer: build DB and save as `data/<product>_<release>.db.xz` |
| `--check-updates` | | Probe Nokia for newer releases (exit 1 if updates available) |
| `--update` | | Probe + download + rebuild + repack newer release(s); edits `RELEASES` |
| `--dry-run` | | With `--update`: show plan without making changes |
| `--skip-probe` | | With `--update`: resume an interrupted update from local files |
| `--pack-skill` | | Bundle SKILL.md + scripts/ + data/ into `yang-browser.zip` |
| `--release` | | One-shot: `--update` + `--pack-skill`. Recommended for periodic refresh |

## Answering "is feature X supported on platform Y?"

There are three patterns, depending on how exact the question is:

**Pattern 1 — User names a feature generically** ("SRv6", "BGP-EVPN", "VXLAN"):
```bash
python3 <script> --feature <name> -p <platform> --matrix
```
This resolves the feature to all its top-level container paths and prints a
matrix. No guessing required. The header of the output includes the SR OS
release so the user knows what version the answer applies to.

**Pattern 2 — User names an exact YANG path:**
```bash
python3 <script> --is-supported "<path>" -p "<platform>"
```
The exit code is definitive (0/1/2). The output distinguishes *fully supported
on all matched platforms*, *partially supported* (lists which platforms do and
don't), and *not supported at all*. For SoC compliance docs, partial support is
the case that matters most — the output tells you exactly which variants are
the exceptions.

**Pattern 3 — User asks about both SROS and SRL:**
```bash
python3 <script> --cross-product --feature <name> -p <platform>
```
Runs the resolver and support check against both DBs and prints a side-by-side
summary.

## Notes on the data

- `path`, `gnmi-path`, and `xpath` are identical in the source data, so we keep
  only `path`. `path-with-prefix`, `model-path`, and `json-instance-path` are
  likewise identical; we keep `path_prefix`.
- `is-state` in the JSONL dump is `true` only for state nodes. Its absence
  means config. SR Linux always includes `is-state` explicitly.
- Platform bitmasks use up to 63 bits (signed 64-bit INTEGER). SROS currently
  has 52 platforms and SRL 24, so there is plenty of headroom. If Nokia ever
  ships more than 63 platforms for a product the build will fail loudly rather
  than silently corrupting.
- The `/configure/groups/*` subtree is a template-copy of the rest of the
  config tree. `--by-platform` excludes it by default to avoid double-counting;
  pass `--include-templates` to count it anyway.
- The FTS5 tokenizer was deliberately tuned to treat `-` and `_` as part of
  words (so `bgp-evpn` is one token) but NOT to treat `/` `[` `]` `=` `*` as
  part of words — otherwise the whole path would become a single giant token
  and FTS ranking would break. The `--substring` fallback is available for
  sub-token searches.

## Data source

All data is pulled from `https://yangbrowser.nokia.com/releases/{sros|srlinux}/{release}/paths.jsonl.gz`
(see `--stats` for the exact URL used per DB). Raw downloads are cached in
the system temporary directory (or `$YANG_CACHE_DIR`) between rebuilds.
