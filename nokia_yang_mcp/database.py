"""Database location, extraction, and connection helpers."""

from __future__ import annotations

import lzma
import os
import sqlite3
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

RELEASES = {
    "sros": {"release": "26.3.R2", "product": "SR OS"},
    "srlinux": {"release": "26.3.1", "product": "SR Linux"},
}

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
PACKAGE_DATA_DIR = PACKAGE_DIR / "data"
PROJECT_DATA_DIR = PROJECT_DIR / "data"
DATA_DIR = PACKAGE_DATA_DIR if PACKAGE_DATA_DIR.exists() else PROJECT_DATA_DIR
CACHE_DIR = Path(os.environ.get("YANG_CACHE_DIR", "/tmp/yang_browser_cache"))


def validate_product(product: str) -> str:
    if product not in RELEASES:
        allowed = ", ".join(sorted(RELEASES))
        raise ValueError(f"unknown product {product!r}; expected one of: {allowed}")
    return product


def db_stem(product: str) -> str:
    validate_product(product)
    return f"{product}_{RELEASES[product]['release']}"


@contextmanager
def file_lock(lock_path: Path, timeout_s: float = 30.0) -> Iterator[None]:
    """Acquire a simple cross-process lock using exclusive file creation."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError) as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for lock {lock_path}") from exc
            time.sleep(0.05)
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def extract_xz_once(source_xz: Path, target_db: Path) -> Path:
    """Extract ``source_xz`` to ``target_db`` once, safely under concurrency."""
    source_xz = Path(source_xz)
    target_db = Path(target_db)
    if target_db.exists():
        return target_db

    target_db.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target_db.with_suffix(target_db.suffix + ".lock")
    with file_lock(lock_path):
        if target_db.exists():
            return target_db
        tmp = target_db.with_name(f"{target_db.name}.{os.getpid()}.{uuid.uuid4().hex}.partial")
        try:
            with lzma.open(source_xz, "rb") as src, open(tmp, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            os.replace(tmp, target_db)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    return target_db


def db_path_for(product: str, *, data_dir: Path = DATA_DIR, cache_dir: Path = CACHE_DIR) -> Path:
    """Return an SQLite DB path, extracting the bundled compressed DB if needed."""
    stem = db_stem(product)
    cached = cache_dir / f"{stem}.db"
    if cached.exists():
        return cached

    shipped_xz = data_dir / f"{stem}.db.xz"
    if shipped_xz.exists():
        print(f"Decompressing {shipped_xz.name} -> {cached} (one-time) ...", file=sys.stderr)
        return extract_xz_once(shipped_xz, cached)

    shipped_db = data_dir / f"{stem}.db"
    if shipped_db.exists():
        return shipped_db

    return cached


def open_db(product: str) -> sqlite3.Connection:
    path = db_path_for(product)
    if not path.exists():
        raise FileNotFoundError(f"database not found for {product}: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
