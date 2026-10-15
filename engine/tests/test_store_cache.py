from liveatc_digest.cache import Cache
from liveatc_digest.store import Store


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "db.sqlite"
    s1 = Store(path)
    assert s1.schema_version() == "001_initial.sql"
    s1.close()
    s2 = Store(path)
    assert s2.migrate() == []
    s2.close()


def test_station_and_block_upserts(store):
    sid = store.upsert_station("kbed", "kbed_twr", "KBED Tower", "tower", True, [])
    assert store.upsert_station("kbed", "kbed_twr", "KBED Tower (new)", "tower", False, []) == sid
    row = store.get_station("kbed_twr")
    assert row["title"] == "KBED Tower (new)" and row["up"] == 0 and row["icao"] == "KBED"

    store.set_station_archive_id("kbed_twr", "KBED-Twr")
    assert store.get_station("kbed_twr")["archive_id"] == "KBED-Twr"
    # a later upsert without archive_id keeps the resolved one
    store.upsert_station("kbed", "kbed_twr", "KBED Tower", "tower", True, [])
    assert store.get_station("kbed_twr")["archive_id"] == "KBED-Twr"

    bid = store.upsert_audio_block(
        sid, "kbed", "2024-07-28T00:00:00Z", "2024-07-28T00:30:00Z", "missing"
    )
    assert (
        store.upsert_audio_block(
            sid, "kbed", "2024-07-28T00:00:00Z", "2024-07-28T00:30:00Z", "fetched"
        )
        == bid
    )


def test_audit_and_fts(store):
    store.audit("test.action", "target", answer=42)
    sid = store.upsert_station("kbed", "kbed_twr", "KBED Tower", "tower", True, [])
    bid = store.upsert_audio_block(
        sid, "kbed", "2024-07-28T00:00:00Z", "2024-07-28T00:30:00Z", "fetched"
    )
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO utterance (audio_block_id, start_utc, end_utc, text, "
            "asr_provider, asr_model) VALUES (?, ?, ?, ?, ?, ?)",
            (
                bid,
                "2024-07-28T00:01:00Z",
                "2024-07-28T00:01:05Z",
                "cleared to land runway two nine",
                "t",
                "m",
            ),
        )
    hit = store.conn.execute(
        "SELECT rowid FROM utterance_fts WHERE utterance_fts MATCH 'runway'"
    ).fetchall()
    assert len(hit) == 1
    assert store.counts()["audit_log"] == 1


def test_cache_put_and_prune(tmp_path):
    cache = Cache(tmp_path / "c")
    obj = cache.put_bytes(b"hello")
    assert cache.has(obj.sha256) and obj.path.read_bytes() == b"hello"
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    same = cache.put_file(src)
    assert same.sha256 == obj.sha256 and not src.exists()
    assert cache.stats() == (1, 5)
    assert cache.prune(older_than_s=-1) == 1
    assert cache.stats() == (0, 0)
