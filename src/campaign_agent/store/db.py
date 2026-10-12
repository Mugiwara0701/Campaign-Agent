"""One PostgreSQL connection with explicit transactions.

The worker is single-threaded, so one connection is enough. It reconnects if the connection died.
`prepare_threshold=None` keeps it working behind Supabase's transaction pooler (port 6543), which does
not support prepared statements. A transaction is never held open across a model call.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

Conn = psycopg.Connection[dict[str, Any]]


class Database:
    def __init__(self, url: str) -> None:
        self._url = url
        self._conn: Conn | None = None

    def _connection(self) -> Conn:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = psycopg.connect(
                self._url,
                row_factory=dict_row,
                prepare_threshold=None,
                connect_timeout=15,
            )
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[Conn]:
        """Commit on success, roll back on any error."""
        conn = self._connection()
        try:
            yield conn
            conn.commit()
        except BaseException:
            if not conn.closed:
                conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None
