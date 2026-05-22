# nokia-yang-mcp

Fast local lookup of YANG paths and platform support for Nokia SR OS and
SR Linux, exposed as a Python MCP server for Claude Code, Codex, or any other
MCP client.

Backed by pre-built SQLite databases with FTS5 indexes. The bundled databases
currently cover **SR OS 26.3.R2** and **SR Linux 26.3.1**.

## What It Does

Answers questions like:

- "Is SRv6 supported on the IXR-e family?"
- "Does the 7250 IXR-e3x support EVPN-VXLAN?"
- "Compare BGP-EVPN support on SR OS vs SR Linux."
- "Give me feature candidates for configuring SRv6 via gNMI."

The project is also kept compatible with the original Claude skill layout; see
`SKILL.md` for the long-form skill reference.

## Install

Use Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The package installs:

```bash
nokia-yang --help
nokia-yang serve
```

`nokia-yang-mcp` is also provided as a direct MCP-server alias.

## MCP Server

Run the MCP server over stdio:

```bash
nokia-yang serve
```

Example MCP client config:

```json
{
  "mcpServers": {
    "nokia-yang-mcp": {
      "command": "nokia-yang",
      "args": ["serve"]
    }
  }
}
```

Available MCP tools:

- `yang_stats`
- `yang_list_platforms`
- `yang_search_paths`
- `yang_check_path_support`
- `yang_resolve_feature`
- `yang_feature_support_matrix`
- `yang_cross_product_feature_support`
- `yang_suggest_gnmi_candidates`

## Pairing With A gNMI MCP

Use this MCP as a read-only guardrail before using a gNMI MCP to read or
configure a router.

Typical agent flow for a config task:

```text
User: Configure SRv6 on a 7250 IXR-e3x.

1. nokia-yang-mcp.yang_suggest_gnmi_candidates(
     product="sros",
     feature_or_query="srv6",
     platform="7250 IXR-e3x",
     kind="config"
   )
2. Agent selects supported candidate paths and asks the gNMI MCP to read current
   state/config.
3. Agent prepares the gNMI set operation only for paths that are known in the
   bundled Nokia YANG release and supported on the platform.
```

For exact paths copied from a device or config snippet:

```text
nokia-yang-mcp.yang_check_path_support(
  product="sros",
  path="/configure/router[router-name=Base]/segment-routing/segment-routing-v6",
  platform="7250 IXR-e3x"
)
```

The tool canonicalizes list keys to the YANG template form:

```text
/configure/router[router-name=*]/segment-routing/segment-routing-v6
```

Use `yang_stats` in final answers when you need to cite the checked release.

## Layout

```text
.
├── nokia_yang_mcp/          # package runtime, core API, MCP server, CLI
├── nokia_yang_mcp/data/     # packaged DB payload for wheel/editable installs
├── data/                    # Claude skill layout DB payload
├── scripts/yang_browser.py  # compatibility CLI + release maintenance
├── tests/                   # package, MCP tool, and CLI tests
└── SKILL.md                 # Claude skill instructions
```

## Original Claude Skill

Build the skill zip:

```bash
python3 scripts/yang_browser.py --pack-skill
```

This produces `yang-browser.zip` in the parent directory, ready for upload via
Claude.ai Settings -> Skills -> Create skill.

## Updating To Newer Nokia Releases

```bash
python3 scripts/yang_browser.py --release
```

This probes `yangbrowser.nokia.com`, downloads newer `paths.jsonl.gz` files when
available, rebuilds the SQLite DBs, repacks them as `.db.xz`, updates the
release metadata, and rebuilds the skill zip.

The update is resumable. If interrupted, run:

```bash
python3 scripts/yang_browser.py --update --skip-probe
python3 scripts/yang_browser.py --pack-skill
```

## Legacy CLI

The original script remains usable:

```bash
python3 scripts/yang_browser.py --feature srv6 -p "IXR-e" --matrix
python3 scripts/yang_browser.py --cross-product --feature bgp-evpn -p "IXR"
python3 scripts/yang_browser.py --product srlinux --by-platform "7220 IXR-D3"
```

## Development

```bash
python -m pytest
python -m ruff check nokia_yang_mcp tests
python -m compileall nokia_yang_mcp tests scripts
```

The query runtime lives in `nokia_yang_mcp/`; `scripts/yang_browser.py` is kept
as the compatibility CLI and release-maintenance utility for the Claude skill
layout.

## Data Source

All data is sourced from:

```text
https://yangbrowser.nokia.com/releases/{sros|srlinux}/{release}/paths.jsonl.gz
```

This project is unaffiliated with Nokia.
