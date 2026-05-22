"""Click CLI entrypoint: nokia-yang <command>."""

from __future__ import annotations

import json

import click

from .core import get_stats, list_platforms
from .database import RELEASES, validate_product


@click.group()
def main() -> None:
    """Nokia YANG MCP tools."""


@main.command("serve")
def serve_cmd() -> None:
    """Start the MCP server over stdio."""
    from .mcp_server import mcp

    mcp.run()


@main.command("stats")
@click.option("--product", default="sros", show_default=True, type=click.Choice(sorted(RELEASES)))
@click.option("--json-output", is_flag=True, help="Emit JSON instead of text.")
def stats_cmd(product: str, json_output: bool) -> None:
    """Show bundled database metadata."""
    validate_product(product)
    stats = get_stats(product)
    if json_output:
        click.echo(json.dumps(stats, indent=2))
        return
    for key, value in stats.items():
        click.echo(f"{key:16s} {value}")


@main.command("platforms")
@click.option("--product", default="sros", show_default=True, type=click.Choice(sorted(RELEASES)))
@click.option("--json-output", is_flag=True, help="Emit JSON instead of text.")
def platforms_cmd(product: str, json_output: bool) -> None:
    """List platforms known for a product."""
    validate_product(product)
    platforms = list_platforms(product)
    if json_output:
        click.echo(json.dumps({"product": product, "platforms": platforms}, indent=2))
        return
    for platform in platforms:
        click.echo(platform)
