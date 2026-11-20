"""Small psycopg2 helpers. Every function opens a short-lived connection."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from . import config


def connect():
    return psycopg2.connect(config.DATABASE_URL)


@contextmanager
def transaction():
    """Yield a dict cursor; commit on success, roll back on error."""
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def run_many(statements):
    """Run a list of (sql, params) pairs in ONE transaction. Returns rows touched."""
    total = 0
    with transaction() as cur:
        for sql, params in statements:
            cur.execute(sql, params)
            total += max(cur.rowcount, 0)
    return total


def bulk(sql, rows, template=None, page_size=500):
    """Fast multi-row INSERT: sql must contain a single 'VALUES %s'. Returns rows sent."""
    if not rows:
        return 0
    with transaction() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=page_size)
    return len(rows)
