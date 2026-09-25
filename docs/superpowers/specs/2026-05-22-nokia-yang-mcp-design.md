# Nokia YANG MCP Design

## Goal

Turn the Nokia YANG MCP skill into a shareable MCP server that agents can use beside a gNMI MCP server to validate YANG paths, platform support, and feature candidates before reading from or configuring Nokia routers.

## Scope

Version 1 is a read-only capability and path intelligence server. It does not connect to routers, push configuration, or generate full service configs. It answers whether a path or feature exists and where it is supported, using the bundled SR OS and SR Linux SQLite databases.

## Architecture

The project will become a Python package with three layers:

- `nokia_yang_mcp.database`: release metadata, DB path resolution, safe extraction of bundled `.db.xz` files, and read-only SQLite connection setup.
- `nokia_yang_mcp.core`: pure query functions for search, platform matching, path support checks, feature resolution, matrices, and cross-product summaries.
- `nokia_yang_mcp.mcp_server`: FastMCP tools that expose the core functions as structured JSON outputs.
- `nokia_yang_mcp.cli`: a Click CLI following the `nokia-docs-search` pattern, including `nokia-yang serve`.

The existing CLI remains available, but becomes a thin compatibility layer over the package. Maintainer-only update and pack commands can stay in the legacy script until they are worth separating.

## MCP Tools

- `yang_stats(product)`: return release, path count, platform count, source URL, and DB file.
- `list_platforms(product)`: return known platforms for SR OS or SR Linux.
- `search_paths(product, query, platform, kind, limit, substring)`: ranked path search with optional platform and config/state filters.
- `check_path_support(product, path, platform)`: exact path support with list-key canonicalization.
- `resolve_feature(product, feature, platform)`: resolve a feature name to top-level config containers and optional support summaries.
- `feature_support_matrix(product, feature, platform)`: structured path by platform support matrix.
- `cross_product_feature_support(feature, platform)`: compare SR OS and SR Linux.
- `suggest_gnmi_candidates(product, feature_or_query, platform, kind, limit)`: return supported candidate paths suitable for follow-up gNMI get/set planning.

## Reliability Requirements

- First-use DB extraction must be safe under concurrent processes.
- Machine-facing outputs must not depend on Unicode table symbols.
- Query functions must return structured Python data, not formatted stdout.
- The package must work offline with bundled DB files.
- The MCP server must open SQLite databases read-only.
- Windows and Linux should both pass tests.

## Public Sharing Requirements

- `pyproject.toml` with package metadata and console entry points.
- `LICENSE` with MIT terms.
- Tests for cache extraction, path canonicalization, support checks, and MCP tool-shape smoke behavior.
- README sections for install, MCP configuration, gNMI pairing examples, and data source caveats.
- `SKILL.md` release text must match the actual bundled releases.
