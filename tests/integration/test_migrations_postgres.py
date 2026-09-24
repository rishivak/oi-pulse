"""Migration tests against a real PostgreSQL — P0-3.

`tests/phase2/test_migrations.py` reads the migration as source and proves its
*structure*. This module proves the DDL is actually **accepted and applied**, which is
the part that failed external verification: `obs_depth` was partitioned before it
existed, and no amount of source reading is a substitute for PostgreSQL saying yes.

Runs only when `DATABASE_URL` points at a PostgreSQL the test may create a database on.
It skips in the development sandbox, where no PostgreSQL is reachable; CI runs it
against a `postgres:16` service and fails the job if it skips. A skipped run is a
reported gap, never a pass.

**Safety (rules 19 and 20).** Every test runs inside a throwaway database named
`oipulse_mig_<random>` created for the run and dropped afterwards. Nothing touches the
database named in `DATABASE_URL`, so no existing table can be dropped or truncated. The
`downgrade` test therefore reverses only migrations this module applied.

`asyncpg` is the project's only PostgreSQL driver, so the engine here is async too.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import unittest
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("DATABASE_URL", "")

try:  # pragma: no cover - availability differs per environment
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

    HAVE_SQLALCHEMY = True
except ImportError:  # pragma: no cover
    HAVE_SQLALCHEMY = False

SKIP_REASON = (
    "needs DATABASE_URL pointing at a PostgreSQL plus an installed sqlalchemy/alembic; "
    "neither is available in the development sandbox"
)


def _await[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@unittest.skipUnless(DATABASE_URL and HAVE_SQLALCHEMY, SKIP_REASON)
class TestMigrationsApply(unittest.TestCase):
    """Apply the chain for real and introspect the result."""

    db_name: str
    db_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.db_name = f"oipulse_mig_{uuid.uuid4().hex[:10]}"
        cls.db_url = _with_database(DATABASE_URL, cls.db_name)

        async def create() -> None:
            engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
            async with engine.connect() as conn:
                await conn.execute(sa.text(f'CREATE DATABASE "{cls.db_name}"'))
            await engine.dispose()

        _await(create())

    @classmethod
    def tearDownClass(cls) -> None:
        async def drop() -> None:
            engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
            async with engine.connect() as conn:
                # Only ever the throwaway database this class created.
                await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{cls.db_name}"'))
            await engine.dispose()

        _await(drop())

    # ------------------------------------------------------------------ helpers

    def _alembic(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "DATABASE_URL": self.db_url,
            # Deterministic partition window regardless of when CI runs.
            "OIPULSE_PARTITION_ANCHOR": "2026-01-01",
        }
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _upgrade_head(self) -> None:
        result = self._alembic("upgrade", "head")
        self.assertEqual(
            result.returncode,
            0,
            f"alembic upgrade head failed:\nSTDOUT\n{result.stdout}\nSTDERR\n{result.stderr}",
        )

    def _query[T](self, fn: Callable[[AsyncConnection], Coroutine[Any, Any, T]]) -> T:
        async def run() -> T:
            engine = create_async_engine(self.db_url)
            try:
                async with engine.connect() as conn:
                    return await fn(conn)
            finally:
                await engine.dispose()

        return _await(run())

    def _tables(self) -> set[str]:
        async def fetch(conn: AsyncConnection) -> set[str]:
            names: list[str] = await conn.run_sync(
                lambda sync: sa.inspect(sync).get_table_names(schema="public")
            )
            return set(names)

        return self._query(fetch)

    def _rows(self, sql: str) -> list[Any]:
        async def fetch(conn: AsyncConnection) -> list[Any]:
            result = await conn.execute(sa.text(sql))
            return list(result.all())

        return self._query(fetch)

    # -------------------------------------------------------------------- tests

    def test_upgrade_head_applies_the_whole_chain(self) -> None:
        """The headline check.

        This is what failed with `relation "obs_depth" does not exist`. A green result
        here is the evidence the structural tests cannot give.
        """
        self._upgrade_head()

    def test_every_phase_table_exists_after_upgrade(self) -> None:
        from tests.phase2.test_migrations import (
            PHASE1_TABLES,
            PHASE2_TABLES,
            PHASE3_TABLES,
            PHASE4_TABLES,
        )

        self._upgrade_head()
        present = self._tables()
        for table in (*PHASE1_TABLES, *PHASE2_TABLES, *PHASE3_TABLES, *PHASE4_TABLES):
            with self.subTest(table=table):
                self.assertIn(table, present)

    def test_the_checkpoint_identity_constraint_is_enforced_by_postgres(self) -> None:
        """`UNIQUE (underlying_id, observed_at, knowledge_horizon, build_context_id)`.

        Asserted against the live catalogue rather than the migration source: a
        constraint that exists in a `.py` file and not in the database enforces
        nothing, and a key silently missing `knowledge_horizon` would let two
        genuinely different states collide.
        """
        self._upgrade_head()
        rows = self._rows(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'state_checkpoints'::regclass AND contype = 'u'"
        )
        definitions = " ".join(definition for _, definition in rows)
        for column in ("underlying_id", "observed_at", "knowledge_horizon", "build_context_id"):
            with self.subTest(column=column):
                self.assertIn(column, definitions)

    def test_partitions_exist_for_every_partitioned_parent(self) -> None:
        """A missing partition is otherwise discovered by a failed insert mid-session."""
        from tests.phase2.test_migrations import (
            PARTITIONED_TABLES,
            PHASE3_PARTITIONED,
            PHASE4_PARTITIONED,
        )

        self._upgrade_head()
        counts = dict(
            self._rows(
                "SELECT parent.relname, count(child.relname) "
                "FROM pg_inherits "
                "JOIN pg_class parent ON pg_inherits.inhparent = parent.oid "
                "JOIN pg_class child ON pg_inherits.inhrelid = child.oid "
                "GROUP BY parent.relname"
            )
        )
        for table in (*PARTITIONED_TABLES, *PHASE3_PARTITIONED, *PHASE4_PARTITIONED):
            with self.subTest(table=table):
                self.assertGreater(counts.get(table, 0), 0, f"{table} has no partitions")

    def test_identity_indexes_exist(self) -> None:
        """Tiered identity is enforced by the database, not by application convention."""
        self._upgrade_head()
        defs = " ".join(
            row[0]
            for row in self._rows("SELECT indexdef FROM pg_indexes WHERE schemaname='public'")
        )
        for tier in ("provider_event_id", "channel_sequence", "content_digest"):
            with self.subTest(tier=tier):
                self.assertIn(tier, defs, f"no index enforces the {tier} identity tier")
        self.assertIn(
            "COALESCE",
            defs,
            "tier-1 uniqueness must stay scoped by feed_session_id while A-13 is open",
        )

    def test_downgrade_then_upgrade_is_repeatable(self) -> None:
        """A downgrade that forgets a table leaves a schema the next upgrade cannot build.

        Safe because the database is a throwaway created by this class; the downgrade
        reverses only the revisions applied here.
        """
        from tests.phase2.test_migrations import (
            PHASE1_TABLES,
            PHASE2_TABLES,
            PHASE3_TABLES,
            PHASE4_TABLES,
        )

        self._upgrade_head()
        after_upgrade = self._tables()

        down = self._alembic("downgrade", "002")
        self.assertEqual(down.returncode, 0, f"{down.stdout}\n{down.stderr}")
        remaining = self._tables()
        for table in (*PHASE1_TABLES, *PHASE2_TABLES, *PHASE3_TABLES, *PHASE4_TABLES):
            with self.subTest(table=table):
                self.assertNotIn(table, remaining, f"{table} survived the downgrade")

        again = self._alembic("upgrade", "head")
        self.assertEqual(again.returncode, 0, f"{again.stdout}\n{again.stderr}")
        self.assertEqual(self._tables(), after_upgrade, "re-upgrade must reproduce the schema")

    def test_the_metric_identity_constraint_is_enforced_by_postgres(self) -> None:
        """`UNIQUE (feature_id, feature_version, scope_kind, scope_ref, observed_at,
        knowledge_horizon, build_context_id)` — asserted against the live catalogue.

        A constraint present in a `.py` file and absent from the database enforces
        nothing, and a key silently missing `feature_version` would let v2 of a
        formula overwrite v1's stored meaning.
        """
        self._upgrade_head()
        rows = self._rows(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'metric_values'::regclass"
        )
        definitions = " ".join(definition for _, definition in rows)
        for column in (
            "feature_id",
            "feature_version",
            "scope_kind",
            "scope_ref",
            "observed_at",
            "knowledge_horizon",
            "build_context_id",
        ):
            with self.subTest(column=column):
                self.assertIn(column, definitions)
        self.assertIn("available_at >= computed_at", definitions)


if __name__ == "__main__":
    unittest.main()
