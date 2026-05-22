"""Structured result models for Nokia YANG MCP queries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Product = Literal["sros", "srlinux"]
Kind = Literal["config", "state"]
SupportStatus = Literal[
    "fully-supported",
    "partially-supported",
    "not-supported",
    "platform-agnostic",
    "path-unknown",
]


@dataclass(frozen=True)
class PathResult:
    path: str
    path_prefix: str
    type: str
    node_type: str
    description: str
    kind: str
    platforms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SupportResult:
    status: SupportStatus
    path: str
    canonical_path: str
    supported: list[str]
    unsupported: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatrixResult:
    product: str
    release: str
    platforms: list[str]
    rows: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
