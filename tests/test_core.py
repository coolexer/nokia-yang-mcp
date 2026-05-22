from __future__ import annotations

from nokia_yang_mcp.core import (
    canonicalise_path,
    check_path_support,
    feature_support_matrix,
    fts_query,
    get_stats,
    list_platforms,
    search_paths,
)


def test_canonicalise_path_replaces_concrete_list_keys():
    assert (
        canonicalise_path("/configure/router[router-name=Base]/bgp/group[group-name=core]")
        == "/configure/router[router-name=*]/bgp/group[group-name=*]"
    )


def test_fts_query_quotes_tokens_and_keeps_prefix_marker():
    assert fts_query('bgp-evpn srv6* "odd"') == '"bgp-evpn" "srv6"* """odd"""'


def test_get_stats_returns_bundled_release_metadata():
    stats = get_stats("sros")

    assert stats["product"] == "SR OS"
    assert stats["release"] == "26.3.R2"
    assert int(stats["path_count"]) > 100000


def test_list_platforms_returns_known_sros_platform():
    platforms = list_platforms("sros")

    assert "7250 IXR-e3x" in platforms


def test_search_paths_returns_structured_results():
    results = search_paths("sros", query="bgp-evpn", platform="IXR-X1", kind="config", limit=5)

    assert results
    assert all(result.kind == "config" for result in results)
    assert all("7250 IXR-X1" in result.platforms for result in results)


def test_check_path_support_canonicalises_concrete_keys():
    result = check_path_support(
        "sros",
        "/configure/router[router-name=Base]/segment-routing/segment-routing-v6",
        "7250 IXR-e3x",
    )

    assert result.status == "fully-supported"
    assert result.canonical_path == "/configure/router[router-name=*]/segment-routing/segment-routing-v6"
    assert "7250 IXR-e3x" in result.supported


def test_feature_support_matrix_returns_plain_structured_booleans():
    matrix = feature_support_matrix("sros", "srv6", "7250 IXR-e3x")

    assert matrix.product == "SR OS"
    assert matrix.release == "26.3.R2"
    assert matrix.platforms == ["7250 IXR-e3x"]
    assert matrix.rows
    assert isinstance(matrix.rows[0]["support"]["7250 IXR-e3x"], bool)
