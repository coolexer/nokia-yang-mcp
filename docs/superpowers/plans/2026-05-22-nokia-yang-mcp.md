# Nokia YANG MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shareable Nokia YANG MCP server for validating YANG paths and platform support before using a gNMI MCP server.

**Architecture:** Extract the existing runtime query logic into a small Python package, keep the CLI as a compatibility surface, and expose structured MCP tools over the same core API. Fix cache extraction and Windows output bugs before adding the server layer.

**Tech Stack:** Python 3.11+, SQLite FTS5, xz-compressed bundled databases, pytest, Click, FastMCP.

---

### Task 1: Project Skeleton and Tests

**Files:**
- Create: `pyproject.toml`
- Create: `nokia_yang_mcp/__init__.py`
- Create: `nokia_yang_mcp/models.py`
- Create: `tests/test_database.py`
- Create: `tests/test_core.py`

- [ ] Add package metadata, pytest dependency, and console scripts.
- [ ] Add typed result models that MCP tools can serialize.
- [ ] Add failing tests for concurrent extraction, path canonicalization, and structured search results.
- [ ] Run `python -m pytest` and confirm the new behavior tests fail before implementation.

### Task 2: Database Runtime

**Files:**
- Create: `nokia_yang_mcp/database.py`
- Modify: `scripts/yang_browser.py`

- [ ] Implement release metadata and product validation.
- [ ] Implement safe `.db.xz` extraction with a lock file and per-process temporary file.
- [ ] Implement read-only SQLite connection helper.
- [ ] Route legacy script DB opening through the new helper.
- [ ] Run database tests and confirm concurrent extraction passes.

### Task 3: Core Query API

**Files:**
- Create: `nokia_yang_mcp/core.py`
- Modify: `scripts/yang_browser.py`
- Modify: `tests/test_core.py`

- [ ] Move reusable query behavior into `core.py`.
- [ ] Return dataclasses or dictionaries instead of printing from core functions.
- [ ] Keep CLI formatting behavior in the legacy script.
- [ ] Add tests for `check_path_support`, `search_paths`, and `feature_support_matrix`.

### Task 4: MCP Server

**Files:**
- Create: `nokia_yang_mcp/mcp_server.py`
- Create: `tests/test_mcp_tools.py`
- Modify: `README.md`

- [ ] Add MCP tools over the core API.
- [ ] Make tool outputs compact and JSON-serializable.
- [ ] Add a smoke test that calls the Python tool functions without launching a client.
- [ ] Document MCP client configuration and gNMI pairing workflow.

### Task 5: Polish and Verification

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Create: `.github/workflows/ci.yml`

- [ ] Fix stale release text in `SKILL.md`.
- [ ] Add CI for Windows and Linux.
- [ ] Run `python -m pytest`.
- [ ] Run CLI smoke commands for SR OS and SR Linux.
- [ ] Run MCP import smoke test.
