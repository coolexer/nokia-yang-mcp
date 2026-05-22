"""FastMCP server for Nokia YANG path and platform support."""

from __future__ import annotations

from typing import Any

from .core import (
    check_path_support,
    feature_support_matrix,
    get_stats,
    list_platforms,
    resolve_feature,
    search_paths,
)
from .database import RELEASES, validate_product

MAX_LIMIT = 200


def _clean_limit(limit: int, *, default: int = 50) -> int:
    if limit is None:
        return default
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, MAX_LIMIT)


def yang_stats(product: str = "sros") -> dict[str, Any]:
    """Return metadata for the bundled Nokia YANG database.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.

    Returns product name, release, source URL, path count, and platform count.
    Use this first when the answer must cite which Nokia release was checked.
    """
    validate_product(product)
    return get_stats(product)


def yang_list_platforms(product: str = "sros") -> dict[str, Any]:
    """List platforms known for a Nokia product release.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.

    Returns the product display name, release, and sorted platform names.
    Use this to choose a precise platform string before support checks.
    """
    validate_product(product)
    return {
        "product": RELEASES[product]["product"],
        "release": RELEASES[product]["release"],
        "platforms": list_platforms(product),
    }


def yang_search_paths(
    product: str = "sros",
    query: str | None = None,
    platform: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    substring: bool = False,
) -> dict[str, Any]:
    """Search Nokia YANG paths using SQLite FTS5/BM25 ranking.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.
        query: Search text such as `bgp-evpn`, `srv6 locator`, or `interface`.
        platform: Optional case-insensitive platform fragment, for example
            `IXR-e3x`, `7250 IXR-X1`, or `7220 IXR-D3`.
        kind: Optional `config` or `state` filter.
        limit: Maximum results to return. Values above 200 are capped.
        substring: Use slower substring matching instead of FTS token matching.

    Returns structured path records with canonical path, prefixed path, node type,
    description, config/state kind, and supported platforms.
    """
    validate_product(product)
    if kind not in (None, "config", "state"):
        raise ValueError("kind must be 'config', 'state', or null")
    limit = _clean_limit(limit)
    results = search_paths(
        product,
        query=query,
        platform=platform,
        kind=kind,  # type: ignore[arg-type]
        limit=limit,
        substring=substring,
    )
    return {
        "product": RELEASES[product]["product"],
        "release": RELEASES[product]["release"],
        "results": [result.to_dict() for result in results],
    }


def yang_check_path_support(product: str, path: str, platform: str) -> dict[str, Any]:
    """Check whether an exact YANG path is supported on matching platforms.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.
        path: Exact YANG/gNMI path. Concrete list keys are canonicalized to
            wildcard keys, for example `[router-name=Base]` becomes
            `[router-name=*]`.
        platform: Case-insensitive platform fragment.

    Returns status, original path, canonical path, supported platforms, and
    unsupported platforms. Status is one of `fully-supported`,
    `partially-supported`, `not-supported`, `platform-agnostic`, or
    `path-unknown`.
    """
    validate_product(product)
    return check_path_support(product, path, platform).to_dict()


def yang_resolve_feature(product: str, feature: str, limit: int = 40) -> dict[str, Any]:
    """Resolve a feature name to top-level config containers/lists.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.
        feature: Feature shorthand or canonical tail such as `srv6`, `evpn`,
            `bgp-evpn`, `l3vpn`, or `segment-routing-v6`.
        limit: Maximum resolved paths to return. Values above 200 are capped.

    Returns candidate top-level paths for the feature. Use this before gNMI
    planning when the user names a feature rather than an exact path.
    """
    validate_product(product)
    limit = _clean_limit(limit, default=40)
    results = resolve_feature(product, feature, limit=limit)
    return {
        "product": RELEASES[product]["product"],
        "release": RELEASES[product]["release"],
        "feature": feature,
        "paths": [result.to_dict() for result in results],
    }


def yang_feature_support_matrix(product: str, feature: str, platform: str) -> dict[str, Any]:
    """Build a structured feature-by-platform support matrix.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.
        feature: Feature shorthand or canonical tail.
        platform: Case-insensitive platform fragment, which may match multiple
            platform variants.

    Returns product, release, matched platforms, and rows containing path metadata
    plus boolean support per platform. This is the structured MCP equivalent of
    the legacy CLI `--feature ... --matrix` output.
    """
    validate_product(product)
    return feature_support_matrix(product, feature, platform).to_dict()


def yang_cross_product_feature_support(feature: str, platform: str) -> dict[str, Any]:
    """Compare feature support across SR OS and SR Linux.

    Args:
        feature: Feature shorthand or canonical tail such as `bgp-evpn`.
        platform: Case-insensitive platform fragment used in both product DBs.

    Returns two support matrices keyed by `sros` and `srlinux`.
    """
    return {
        product: yang_feature_support_matrix(product, feature, platform)
        for product in RELEASES
    }


def yang_suggest_gnmi_candidates(
    product: str = "sros",
    feature_or_query: str = "",
    platform: str | None = None,
    kind: str = "config",
    limit: int = 20,
) -> dict[str, Any]:
    """Suggest YANG/gNMI candidate paths for follow-up gNMI MCP operations.

    Args:
        product: `sros` for SR OS or `srlinux` for SR Linux.
        feature_or_query: Feature name or search text.
        platform: Optional platform fragment. When supplied, each candidate is
            annotated with support status for that platform.
        kind: `config` or `state`; used when falling back to search.
        limit: Maximum candidates to return. Values above 200 are capped.

    Returns candidate paths with `gnmi_path`, metadata, source (`feature` or
    `search`), and optional support status. Use this as the bridge tool before
    asking a gNMI MCP server to read or configure a device.
    """
    validate_product(product)
    if kind not in ("config", "state"):
        raise ValueError("kind must be 'config' or 'state'")
    limit = _clean_limit(limit, default=20)
    resolved = resolve_feature(product, feature_or_query, limit=limit)
    if resolved:
        candidates = resolved
        source = "feature"
    else:
        candidates = search_paths(
            product,
            query=feature_or_query,
            platform=platform,
            kind=kind,  # type: ignore[arg-type]
            limit=limit,
        )
        source = "search"

    items: list[dict[str, Any]] = []
    for candidate in candidates:
        item = candidate.to_dict()
        if platform:
            item["support"] = check_path_support(product, candidate.path, platform).to_dict()
        item["gnmi_path"] = candidate.path
        item["source"] = source
        items.append(item)

    return {
        "product": RELEASES[product]["product"],
        "release": RELEASES[product]["release"],
        "query": feature_or_query,
        "platform": platform,
        "candidates": items,
    }


try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only in broken installs
    raise RuntimeError("Install the package with MCP support: pip install -e .") from exc


mcp = FastMCP(
    name="nokia-yang-mcp",
    instructions=(
        "Nokia SR OS and SR Linux YANG path support lookup. "
        "Use this before gNMI get/set operations to find candidate paths and "
        "validate platform support."
    ),
)

mcp.tool()(yang_stats)
mcp.tool()(yang_list_platforms)
mcp.tool()(yang_search_paths)
mcp.tool()(yang_check_path_support)
mcp.tool()(yang_resolve_feature)
mcp.tool()(yang_feature_support_matrix)
mcp.tool()(yang_cross_product_feature_support)
mcp.tool()(yang_suggest_gnmi_candidates)


def build_server():
    return mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
