"""SQLite access: one connection shared by the event loop (calls are sub-millisecond at this size).

Migrations are numbered SQL files in db/migrations/ (0001_init.sql, 0002_...). A database's
`PRAGMA user_version` records the last applied number; newer files are applied in order, each in
its own transaction. WAL mode lets the admin CLI read/write while the server is running.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..util import iso_now as utc_now  # noqa: F401  (re-exported for the repositories)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class Database:
    def __init__(self, path: Path | str):
        self.path = path
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._depth = 0

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        """Atomic block; nests (inner blocks join the outer transaction)."""
        if self._depth:
            self._depth += 1
            try:
                yield self.conn
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self._depth = 0

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> list[int]:
        """Apply pending migrations; returns the numbers applied."""
        applied = []
        current = self.version()
        files = sorted((int(m.group(1)), p) for p in MIGRATIONS_DIR.glob("*.sql")
                       if (m := re.match(r"(\d+)_", p.name)))
        for num, path in files:
            if num <= current:
                continue
            with self.transaction() as c:
                for stmt in _split_sql(path.read_text(encoding="utf-8")):
                    c.execute(stmt)
                c.execute(f"PRAGMA user_version = {num}")
            applied.append(num)
        return applied


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements (executescript() would commit our transaction)."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
