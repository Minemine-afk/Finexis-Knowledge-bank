"""Database access. Every query runs inside `as_user`, which tells row-level security who is asking."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ.get("DATABASE_URL", "postgresql://kb_app:kb_app@localhost:5432/knowledge_bank"),
            kwargs={"row_factory": dict_row}, min_size=1, max_size=10, open=True,
        )
    return _pool


@contextmanager
def as_user(user_id: int | None) -> Iterator[psycopg.Connection]:
    """One transaction with app.user_id set for its duration only (SET LOCAL semantics)."""
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.user_id', %s, true)", ("" if user_id is None else str(user_id),))
            yield conn


def user_by_name(username: str) -> dict | None:
    with as_user(None) as conn:
        return conn.execute("SELECT id, username, display_name FROM users WHERE username = %s",
                            (username,)).fetchone()


def all_users() -> list[dict]:
    with as_user(None) as conn:
        return conn.execute("SELECT id, username, display_name FROM users ORDER BY username").fetchall()


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
