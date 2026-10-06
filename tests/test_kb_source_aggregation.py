"""Source aggregation contracts for SQLite and the PostgreSQL query path.

The PostgreSQL store uses a cursor adapter that executes its generated SQL
against SQLite after translating the two dialect-specific constructs. This
exercises grouping, binding, and result mapping without a live pgvector DB.
"""

from autolangchat.db.kb_postgres import PgVectorKBStore

from .test_admin_kb_routes import _build_app
from .test_kb_credibility_decay import SQLiteKBStore


class _PostgresCursor:
    def __init__(self, connection):
        self.connection = connection
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, params):
        self.statements.append((sql, params))
        assert "d.metadata::jsonb ->> 'source_type'" in sql
        assert "FROM source_groups" in sql
        translated = sql.replace("(d.metadata::jsonb ->> 'source_type')", "json_extract(d.metadata, '$.source_type')")
        translated = translated.replace("CHR(", "CHAR(")
        translated = translated.replace("%s", "?")
        self._cursor = self.connection.execute(translated, params)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()


class _PostgresConnection:
    def __init__(self, sqlite_connection):
        self.sqlite_connection = sqlite_connection
        self.cursors = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def cursor(self):
        cursor = _PostgresCursor(self.sqlite_connection)
        self.cursors.append(cursor)
        return cursor


def _seed(store):
    documents = [
        ("a1", "alpha", "web", "2026-10-01 00:00:00", 2),
        ("a2", "alpha", "web", "2026-10-03 00:00:00", 0),
        ("b1", "beta", "local", "2026-10-02 00:00:00", 1),
        ("f1", "feedback", None, "2026-10-04 00:00:00", 1),
        ("m1", "mixed", "web", "2026-10-01 00:00:00", 1),
        ("m2", "mixed", "file", "2026-10-05 00:00:00", 2),
        ("m3", "mixed", "mystery", "2026-10-06 00:00:00", 0),
        ("u1", "unknown", None, "2026-10-02 00:00:00", 0),
        ("x1", "", "web", "2026-10-02 00:00:00", 0),
        ("x2", "   ", "web", "2026-10-02 00:00:00", 0),
        ("x3", None, "web", "2026-10-02 00:00:00", 0),
        ("x4", "\t\n ", "web", "2026-10-02 00:00:00", 0),
    ]
    for doc_id, source, kind, created_at, chunks in documents:
        metadata = {"source_type": kind} if kind is not None else None
        store.add_document(doc_id, "content", source=source, metadata=metadata)
        store.conn.execute("UPDATE documents SET created_at = ? WHERE id = ?", (created_at, doc_id))
        for i in range(chunks):
            store.conn.execute(
                "INSERT INTO chunks (id, document_id, content, chunk_index) VALUES (?, ?, 'text', ?)",
                (f"{doc_id}-{i}", doc_id, i),
            )
    store.conn.commit()


def _backends(tmp_path):
    sqlite_store = SQLiteKBStore(db_path=str(tmp_path / "sources.db"))
    _seed(sqlite_store)
    postgres_store = object.__new__(PgVectorKBStore)
    connection = _PostgresConnection(sqlite_store.conn)
    postgres_store._get_conn = lambda: connection
    return sqlite_store, postgres_store, connection


def test_both_backends_group_full_counts_types_dates_and_blank_names(tmp_path):
    sqlite, postgres, connection = _backends(tmp_path)
    try:
        for store in (sqlite, postgres):
            rows = store.list_sources()
            assert [r["source"] for r in rows] == ["alpha", "beta", "feedback", "mixed", "unknown"]
            assert [r["source_type"] for r in rows] == ["web", "file", "feedback", None, None]
            assert [(r["document_count"], r["chunk_count"]) for r in rows] == [(2, 2), (1, 1), (1, 1), (3, 3), (1, 0)]
            assert str(rows[0]["last_created_at"]) == "2026-10-03 00:00:00"
            assert str(rows[3]["last_created_at"]) == "2026-10-06 00:00:00"
            assert store.count_sources() == 5
        assert "LEFT JOIN chunk_counts" in connection.cursors[0].statements[0][0]
        assert "chunk_counts" not in connection.cursors[1].statements[0][0]
    finally:
        sqlite.conn.close()


def test_both_backends_filter_names_but_retain_whole_source_counts(tmp_path):
    sqlite, postgres, connection = _backends(tmp_path)
    try:
        for store in (sqlite, postgres):
            for kind, expected in (
                ("web", ["alpha", "mixed"]),
                ("file", ["beta", "mixed"]),
                ("feedback", ["feedback"]),
                ("unknown", []),
            ):
                rows = store.list_sources(source_type=kind)
                assert [r["source"] for r in rows] == expected
                assert store.count_sources(source_type=kind) == len(expected)
                if kind == "file":
                    assert rows[1]["source_type"] is None
                    assert rows[1]["document_count"] == 3
                    assert rows[1]["chunk_count"] == 3
                    assert str(rows[1]["last_created_at"]) == "2026-10-06 00:00:00"
        for cursor in connection.cursors:
            sql, params = cursor.statements[0]
            assert "%s IS NULL" not in sql
            assert params[0] in ("web", "file", "feedback", "unknown")
    finally:
        sqlite.conn.close()


def test_both_backends_paginate_grouped_names_with_unpaginated_total(tmp_path):
    sqlite, postgres, _ = _backends(tmp_path)
    try:
        for store in (sqlite, postgres):
            assert [r["source"] for r in store.list_sources(limit=2, offset=1)] == ["beta", "feedback"]
            assert [r["source"] for r in store.list_sources(limit=1, offset=1, source_type="web")] == ["mixed"]
            assert store.list_sources(limit=2, offset=9) == []
            assert store.count_sources() == 5
            assert store.count_sources("web") == 2
    finally:
        sqlite.conn.close()


def test_empty_store_has_no_sources(tmp_path):
    store = SQLiteKBStore(db_path=str(tmp_path / "empty.db"))
    try:
        assert store.list_sources() == []
        assert store.count_sources() == 0
        assert store.count_sources("feedback") == 0
    finally:
        store.conn.close()


def test_http_route_returns_paginated_aggregates_from_sqlite(tmp_path):
    sqlite, _, _ = _backends(tmp_path)
    try:
        client = _build_app(sqlite)
        response = client.get("/bedrock-chat/admin/kb/sources", params={"source_type": "file", "limit": 1})
        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {
                    "source": "beta",
                    "source_type": "file",
                    "document_count": 1,
                    "chunk_count": 1,
                    "last_created_at": "2026-10-02T00:00:00",
                }
            ],
            "total": 2,
            "limit": 1,
            "offset": 0,
        }
        mixed = client.get("/bedrock-chat/admin/kb/sources", params={"source_type": "file", "limit": 1, "offset": 1})
        assert mixed.status_code == 200
        assert mixed.json()["items"][0]["source"] == "mixed"
        assert mixed.json()["items"][0]["source_type"] is None
        assert mixed.json()["items"][0]["document_count"] == 3
        assert mixed.json()["items"][0]["chunk_count"] == 3
        assert mixed.json()["total"] == 2
    finally:
        sqlite.conn.close()
