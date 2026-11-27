"""core.db() reuses Postgres connections (the database is shared with Inbox Zero): `with db() as c` is still one
transaction that commits or rolls back, the connection goes back to the pool, a nested block gets its own connection,
and a broken connection is dropped and replaced. Fake connections only: nothing opens a socket."""
import os, sys, threading, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("EMAIL_ENCRYPT_SECRET", "test-secret")
os.environ.setdefault("EMAIL_ENCRYPT_SALT", "test-salt")

import psycopg  # noqa: E402

import core  # noqa: E402

IDLE, INTRANS = psycopg.pq.TransactionStatus.IDLE, psycopg.pq.TransactionStatus.INTRANS


class Conn:
    """A psycopg connection stand-in: counts commits and rollbacks; `down` makes every statement fail as a dropped
    server connection does (psycopg then reports it broken)."""
    n = 0

    def __init__(self):
        Conn.n += 1
        self.no, self.closed, self.broken, self.down = Conn.n, False, False, False
        self.commits, self.rollbacks, self.ran, self.status = 0, 0, [], IDLE
        self.info = self

    @property
    def transaction_status(self):
        return self.status

    def execute(self, sql, params=None):
        if self.down:
            self.broken = True
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        self.ran.append(sql)
        self.status = INTRANS
        return self

    def commit(self):
        if self.broken:
            raise psycopg.OperationalError("the connection is lost")
        self.commits += 1
        self.status = IDLE

    def rollback(self):
        if self.broken:
            raise psycopg.OperationalError("the connection is lost")
        self.rollbacks += 1
        self.status = IDLE

    def close(self):
        self.closed = True


class Pool(unittest.TestCase):
    def setUp(self):
        self.opened = []

        def connect(*a, **k):
            c = Conn()
            self.opened.append(c)
            return c
        self.pool = core._Pool(size=2)
        for p in (mock.patch.object(core, "POOL_DB", self.pool), mock.patch.object(core.psycopg, "connect", connect),
                  mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://iz_unified:x@h/db"})):
            p.start()
            self.addCleanup(p.stop)

    def test_one_connection_is_reused_and_each_block_commits(self):
        for _ in range(5):
            with core.db() as c:
                c.execute("SELECT 1")
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0].commits, 5)
        self.assertFalse(self.opened[0].closed)

    def test_an_exception_rolls_back_and_the_connection_is_still_reused(self):
        with self.assertRaises(ValueError):
            with core.db() as c:
                c.execute("INSERT ...")
                raise ValueError("bad input")
        self.assertEqual((self.opened[0].commits, self.opened[0].rollbacks), (0, 1))
        with core.db() as c:
            c.execute("SELECT 1")
        self.assertEqual(len(self.opened), 1)

    def test_a_nested_block_gets_its_own_connection_and_never_commits_the_outer_one(self):
        with core.db() as outer:
            outer.execute("UPDATE ...")
            with core.db() as inner:   # e.g. core.log inside a transaction
                inner.execute("INSERT INTO unified.log ...")
                self.assertIsNot(inner, outer)
            self.assertEqual(outer.commits, 0)   # still one open transaction
            with core.db() as again:
                again.execute("SELECT 1")
        self.assertEqual(outer.commits, 1)
        self.assertEqual(len(self.opened), 2)

    def test_a_connection_that_broke_mid_block_is_dropped_and_the_next_call_reconnects(self):
        with core.db() as c:
            c.execute("SELECT 1")
        first = self.opened[0]
        first.down = True   # Postgres restarted under it
        with self.assertRaises(psycopg.OperationalError):
            with core.db() as c:
                self.assertIs(c, first)
                c.execute("SELECT 1")
        self.assertTrue(first.closed)
        self.assertEqual(self.pool.idle, [])
        with core.db() as c:
            c.execute("SELECT 1")
        self.assertEqual(len(self.opened), 2)
        self.assertIsNot(c, first)
        self.assertEqual(c.commits, 1)

    def test_an_idle_connection_that_went_stale_is_checked_and_replaced_before_use(self):
        with core.db() as c:
            c.execute("SELECT 1")
        first = self.opened[0]
        first.down = True
        self.pool.idle = [(first, time.monotonic() - self.pool.PING_AFTER - 1)]   # idle a while: pinged first
        with core.db() as c:
            c.execute("SELECT 2")   # the caller never sees the broken one
        self.assertIsNot(c, first)
        self.assertTrue(first.closed)
        self.assertEqual(c.ran, ["SELECT 2"])

    def test_a_connection_psycopg_reports_closed_is_never_handed_out(self):
        with core.db() as c:
            c.execute("SELECT 1")
        self.opened[0].closed = True
        with core.db() as c:
            c.execute("SELECT 1")
        self.assertIsNot(c, self.opened[0])
        self.assertEqual(len(self.opened), 2)

    def test_a_failed_commit_raises_and_the_connection_is_not_reused(self):
        with self.assertRaises(psycopg.OperationalError):
            with core.db() as c:
                c.execute("SELECT 1")
                c.broken = True
        with core.db() as c2:
            c2.execute("SELECT 1")
        self.assertIsNot(c2, c)
        self.assertTrue(c.closed)

    def test_at_most_size_idle_connections_are_kept(self):
        held = [core.db() for _ in range(4)]
        for h in held:
            self.pool.local.depth = 1   # as if nested: none of these waits for a free one
            h.__enter__()
        for h in held:
            h.__exit__(None, None, None)
        self.assertEqual(len(self.pool.idle), 2)
        self.assertEqual(sum(c.closed for c in self.opened), 2)
        self.assertEqual(self.pool.busy, 0)

    def test_a_new_caller_waits_while_the_pool_is_in_use_then_gets_the_freed_one(self):
        a, b = core.db(), core.db()
        ca = a.__enter__()
        self.pool.local.depth = 0
        b.__enter__()
        got = []
        t = threading.Thread(target=lambda: [got.append(x) for x in [core.db().__enter__()]])
        t.start()
        time.sleep(0.2)
        self.assertEqual(got, [])                 # both in use: the third waits
        a.__exit__(None, None, None)
        t.join(5)
        self.assertEqual(got, [ca])               # and is given the one that came back, not a new connection
        self.assertEqual(len(self.opened), 2)

    def test_the_pool_size_is_configurable(self):
        with mock.patch.dict(os.environ, {"DB_POOL_SIZE": "4"}):
            self.assertEqual(core._Pool().size, 4)
        self.assertEqual(core._Pool(size=0).size, 1)


if __name__ == "__main__":
    unittest.main()
