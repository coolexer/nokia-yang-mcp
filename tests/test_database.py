from __future__ import annotations

import lzma
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from nokia_yang_mcp.database import extract_xz_once
from scripts import yang_browser


def test_extract_xz_once_is_safe_under_concurrent_callers(tmp_path):
    source = tmp_path / "sample.db.xz"
    target = tmp_path / "cache" / "sample.db"

    raw_db = tmp_path / "raw.db"
    conn = sqlite3.connect(raw_db)
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('ok', 'yes')")
    conn.commit()
    conn.close()


    with open(raw_db, "rb") as src, lzma.open(source, "wb") as dst:
        dst.write(src.read())

    def worker():
        return extract_xz_once(source, target)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: worker(), range(16)))

    assert all(path == target for path in results)
    assert target.exists()
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT value FROM meta WHERE key = 'ok'").fetchone()[0] == "yes"
    conn.close()


def test_data_refresh_updates_packaged_database(tmp_path, monkeypatch):
    package_data = tmp_path / "package"
    package_data.mkdir()
    (package_data / "sros_old.db.xz").write_bytes(b"old")
    source = tmp_path / "sros_new.db.xz"
    source.write_bytes(b"new")
    monkeypatch.setattr(yang_browser, "PACKAGE_DATA_DIR", package_data)

    yang_browser.sync_package_db("sros", source)

    assert [path.name for path in package_data.iterdir()] == ["sros_new.db.xz"]
    assert (package_data / "sros_new.db.xz").read_bytes() == b"new"
