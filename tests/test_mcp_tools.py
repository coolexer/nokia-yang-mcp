from __future__ import annotations

from nokia_yang_mcp.mcp_server import (
    yang_check_path_support,
    yang_resolve_feature,
    yang_search_paths,
    yang_suggest_gnmi_candidates,
)


def test_mcp_search_tool_returns_json_serialisable_shape():
    result = yang_search_paths(
        product="sros",
        query="bgp-evpn",
        platform="IXR-X1",
        kind="config",
        limit=2,
    )

    assert result["product"] == "SR OS"
    assert result["results"]
    assert "path" in result["results"][0]


def test_mcp_support_tool_returns_status_shape():
    result = yang_check_path_support(
        "sros",
        "/configure/router[router-name=Base]/segment-routing/segment-routing-v6",
        "7250 IXR-e3x",
    )

    assert result["status"] == "fully-supported"
    assert result["canonical_path"].endswith("[router-name=*]/segment-routing/segment-routing-v6")


def test_gnmi_candidate_tool_annotates_support_when_platform_is_given():
    result = yang_suggest_gnmi_candidates(
        product="sros",
        feature_or_query="srv6",
        platform="7250 IXR-e3x",
        limit=3,
    )

    assert result["candidates"]
    assert "gnmi_path" in result["candidates"][0]
    assert "support" in result["candidates"][0]


def test_mcp_tools_cap_large_limits():
    result = yang_resolve_feature("sros", "srv6", limit=10_000)

    assert len(result["paths"]) <= 200
