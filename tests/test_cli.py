from __future__ import annotations

from click.testing import CliRunner

from nokia_yang_mcp.cli import main


def test_cli_stats_command_outputs_release_metadata():
    result = CliRunner().invoke(main, ["stats", "--product", "sros"])

    assert result.exit_code == 0
    assert "26.3.R2" in result.output


def test_cli_platforms_command_lists_known_platform():
    result = CliRunner().invoke(main, ["platforms", "--product", "sros"])

    assert result.exit_code == 0
    assert "7250 IXR-e3x" in result.output
