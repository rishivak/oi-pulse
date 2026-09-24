"""Regression tests for the Phase 1/2 remediation findings.

Each test pins a defect that independent verification found and that nothing in the
existing suite would have caught. They are grouped by the finding rather than by module,
so a future reader can trace a test back to the failure it prevents.

None of these needs a database, a provider or a third-party package.
"""

from __future__ import annotations

import ast
import configparser
import itertools
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.check_migration_chain import build_chain, load_version_locations

VALID_ENV = {
    "DATABASE_URL": "postgresql://localhost/oipulse",
    "SESSION_SECRET_KEY": "s" * 40,
    "TOKEN_ENCRYPTION_KEY": "t" * 44,
}


def _run_tool(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / name), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


# ------------------------------------------------- P0-1: alembic chain integration


class TestMigrationChain(unittest.TestCase):
    """The live database is at `alembic_version = '002'` from the legacy chain.

    The v2 chain started at `down_revision = None`, producing a second root in the same
    version table. `alembic upgrade head` then failed with "Can't locate revision
    identified by '002'".
    """

    def setUp(self):
        self.locations = load_version_locations(REPO / "alembic.ini")
        self.chain, self.problems = build_chain(self.locations)

    def test_chain_is_valid(self):
        self.assertEqual(self.problems, [], "revision DAG is not a single valid chain")

    def test_exactly_one_root_and_one_head(self):
        roots = [r for r in self.chain if r.down_revision is None]
        self.assertEqual(len(roots), 1)
        depended = {r.down_revision for r in self.chain if r.down_revision}
        heads = [r for r in self.chain if r.revision not in depended]
        self.assertEqual(len(heads), 1)

    def test_v2_continues_from_the_legacy_head(self):
        """Not a parallel chain: the existing database can reach the new revisions."""
        ids = [r.revision for r in self.chain]
        self.assertEqual(ids[:2], ["001", "002"], "legacy chain must come first")
        self.assertEqual(ids[2], "0001_phase1_sys_tables")
        phase1 = next(r for r in self.chain if r.revision == "0001_phase1_sys_tables")
        self.assertEqual(phase1.down_revision, "002")

    def test_fresh_database_initialisation_is_preserved(self):
        """A clean database walks the whole chain from the single root.

        The length is asserted rather than merely the linkage so that adding a
        revision is a deliberate edit here, visible in review, instead of a silent
        lengthening nobody notices.
        """
        self.assertEqual(len(self.chain), 5)
        self.assertIsNone(self.chain[0].down_revision)
        for previous, current in itertools.pairwise(self.chain):
            self.assertEqual(current.down_revision, previous.revision)

    def test_both_version_locations_are_configured(self):
        parser = configparser.ConfigParser()
        parser.read(REPO / "alembic.ini")
        locations = parser.get("alembic", "version_locations")
        self.assertIn("oipulse/migrations/versions", locations)
        self.assertIn("backend/alembic/versions", locations)

    def test_no_migration_drops_or_truncates_legacy_tables(self):
        """Legacy data is preserved: AD-20 keeps the legacy app running until cutover."""
        legacy_tables = {"oi_snapshots", "oi_strike_snapshots", "users", "upstox_accounts"}
        for path in (REPO / "oipulse/migrations/versions").glob("0*.py"):
            body = path.read_text()
            for table in legacy_tables:
                self.assertNotIn(f'drop_table("{table}")', body)
                self.assertNotIn(f"TRUNCATE {table}", body.upper())

    def test_guard_catches_a_second_root(self):
        result = _run_tool("check_migration_chain.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


# --------------------------------------------------- P0-2: API process entrypoint


class TestEntrypoint(unittest.TestCase):
    """Health and readiness routes existed with no supported way to start them."""

    def _run(self, *args: str, env_overrides: dict[str, str] | None = None):
        import os

        env = {**os.environ, **VALID_ENV, **(env_overrides or {})}
        for key in env_overrides or {}:
            if env[key] == "":
                env.pop(key)
        return subprocess.run(
            [sys.executable, "-m", "oipulse.run", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_entrypoint_is_importable_without_a_web_stack(self):
        """The heavy imports are lazy, so --help and --check work anywhere."""
        self.assertEqual(self._run("--help").returncode, 0)

    def test_check_validates_configuration_and_exits_zero(self):
        result = self._run("--role", "api", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configuration_valid", result.stdout)

    def test_invalid_configuration_refuses_to_start(self):
        result = self._run("--role", "api", "--check", env_overrides={"DATABASE_URL": ""})
        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to start", result.stderr)

    def test_placeholder_secret_refuses_to_start(self):
        result = self._run(
            "--role",
            "api",
            "--check",
            env_overrides={"TOKEN_ENCRYPTION_KEY": "REPLACE_WITH_FERNET_KEY"},
        )
        self.assertEqual(result.returncode, 2)

    def test_later_phase_role_is_refused_with_its_phase_named(self):
        result = self._run("--role", "processor")
        self.assertEqual(result.returncode, 4)
        self.assertIn("Phase 3", result.stderr)

    def test_documented_command_matches_the_real_one(self):
        """14-DEPLOYMENT.md must not document a command that does not exist."""
        doc = (REPO / "docs/design/14-DEPLOYMENT.md").read_text()
        self.assertIn("python -m oipulse.run --role api", doc)
        self.assertNotIn("platform.run", doc, "AD-28: the package is oipulse")

    def test_health_routes_are_registered_on_the_router(self):
        """Asserted without importing FastAPI, which is absent in this environment."""
        source = (REPO / "oipulse/api/health.py").read_text()
        tree = ast.parse(source)
        routes = {
            d.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            for d in node.decorator_list
            if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
        }
        self.assertEqual(routes, {"/health", "/ready"})
        self.assertIn('prefix="/ops"', source)


# ------------------------------------------------------ P0-3: CI gate enforcement


class TestCIGates(unittest.TestCase):
    def setUp(self):
        self.ci = (REPO / ".github/workflows/ci.yml").read_text()
        self.pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())

    def test_no_gate_is_advisory(self):
        self.assertNotIn("continue-on-error", self.ci, "a gate that cannot fail is not a gate")

    def test_mypy_version_supports_pep695_type_parameters(self):
        """`class TemporalRepository[T]` cannot be parsed by mypy 1.11."""
        dev = self.pyproject["project"]["optional-dependencies"]["dev"]
        mypy = next(d for d in dev if d.startswith("mypy=="))
        major, minor = (int(p) for p in mypy.split("==")[1].split(".")[:2])
        self.assertGreaterEqual((major, minor), (1, 12), f"{mypy} predates PEP 695 support")

    def test_mypy_strict_is_still_meaningful(self):
        mypy = self.pyproject["tool"]["mypy"]
        self.assertTrue(mypy["strict"])
        self.assertEqual(mypy["packages"], ["oipulse"])

    def test_import_linter_references_only_modules_that_exist(self):
        """A contract naming an absent package makes lint-imports fail outright."""
        contracts = self.pyproject["tool"]["importlinter"]["contracts"]
        missing = []
        for contract in contracts:
            for key in ("layers", "source_modules", "forbidden_modules"):
                for entry in contract.get(key, []):
                    for module in str(entry).split(":"):
                        module = module.strip()
                        if not module.startswith("oipulse"):
                            continue
                        rel = REPO / Path(module.replace(".", "/"))
                        if not (rel.exists() or rel.with_suffix(".py").exists()):
                            missing.append(f"{contract['name']}: {module}")
        self.assertEqual(missing, [])

    def test_import_linter_forbids_only_internal_modules(self):
        """External bans belong to the stdlib guard, which needs no install."""
        for contract in self.pyproject["tool"]["importlinter"]["contracts"]:
            for module in contract.get("forbidden_modules", []):
                self.assertTrue(
                    str(module).startswith("oipulse"),
                    f"{module} is external; import-linter would have to import it",
                )

    def test_layers_match_the_real_import_graph(self):
        """The declared order must reflect actual imports, not intention."""
        edges: dict[str, set[str]] = {}
        for path in (REPO / "oipulse").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            parts = path.relative_to(REPO / "oipulse").with_suffix("").parts
            src = "run" if path.name == "run.py" else parts[0]
            for node in ast.walk(ast.parse(path.read_text())):
                mods: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    mods = [node.module]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                for module in mods:
                    if module.startswith("oipulse."):
                        target = module.split(".")[1]
                        if target != src:
                            edges.setdefault(src, set()).add(target)

        layers = self.pyproject["tool"]["importlinter"]["contracts"][0]["layers"]
        rank = {
            module.strip().removeprefix("oipulse."): index
            for index, entry in enumerate(layers)
            for module in str(entry).split(":")
        }
        for src, targets in edges.items():
            for target in targets:
                if src in rank and target in rank:
                    self.assertLess(
                        rank[src],
                        rank[target],
                        f"{src} imports {target}, but the layers contract does not allow it",
                    )


# ----------------------------------------------- P0-4: AD-28 / AD-29 consistency


class TestAdrConsistency(unittest.TestCase):
    AUTHORITATIVE = ("docs/design", "oipulse", "tools", "pyproject.toml", "alembic.ini")

    def _authoritative_files(self):
        for entry in self.AUTHORITATIVE:
            path = REPO / entry
            if path.is_file():
                yield path
            else:
                for child in path.rglob("*"):
                    if (
                        child.suffix in {".md", ".py", ".toml", ".ini"}
                        and "__pycache__" not in child.parts
                    ):
                        yield child

    def test_ad28_no_authoritative_source_requires_platform_run(self):
        offenders = [
            str(p.relative_to(REPO))
            for p in self._authoritative_files()
            if "platform.run" in p.read_text()
        ]
        self.assertEqual(offenders, [])

    def test_ad29_deployment_does_not_require_pydantic_settings_in_core(self):
        doc = (REPO / "docs/design/14-DEPLOYMENT.md").read_text()
        self.assertNotIn("`pydantic-settings`, environment-sourced", doc)
        self.assertIn("AD-29", doc)

    def test_core_is_actually_dependency_free(self):
        for path in (REPO / "oipulse/core").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                mods: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    mods = [node.module]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                for module in mods:
                    root = module.split(".")[0]
                    self.assertFalse(
                        root
                        in {
                            "pydantic",
                            "pydantic_settings",
                            "sqlalchemy",
                            "fastapi",
                            "redis",
                            "httpx",
                        },
                        f"{path.name} imports {module}; core must stay dependency-free (AD-29)",
                    )

    def test_config_attributes_the_right_adr(self):
        source = (REPO / "oipulse/core/config.py").read_text()
        self.assertIn("AD-29", source)
        self.assertNotIn("Recorded as AD-28", source)

    def test_legacy_documentation_is_left_alone(self):
        """Legacy docs describe the legacy implementation and are not v2 sources."""
        self.assertTrue((REPO / "docs/ARCHITECTURE.md").exists())
        self.assertIn("legacy", (REPO / "docs/design/README.md").read_text().lower())


# ------------------------------------------------------- P0-5: schema parity


class TestSchemaParity(unittest.TestCase):
    """obs_depth, obs_ohlc, obs_index, instrument_options and instrument_futures were
    required by 02-DATA_MODEL.md but absent from schema and migration."""

    def test_parity_guard_passes(self):
        result = _run_tool("check_schema_parity.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_observation_kind_has_a_table(self):
        from tools.check_schema_parity import (
            KIND_TO_TABLE,
            migration_tables,
            observation_classes,
            schema_tables,
        )

        kinds = observation_classes(REPO / "oipulse/marketdata/observations.py")
        tables = schema_tables(REPO / "oipulse/marketdata/store/schema.py")
        created, _ = migration_tables(sorted((REPO / "oipulse/migrations/versions").glob("0*.py")))
        for kind in kinds:
            table = KIND_TO_TABLE[kind]
            self.assertIn(table, tables, f"{kind} missing from schema.py")
            self.assertIn(table, created, f"{kind} missing from the migration")

    def test_previously_missing_tables_are_present(self):
        from tools.check_schema_parity import migration_tables

        created, dropped = migration_tables(
            sorted((REPO / "oipulse/migrations/versions").glob("0*.py"))
        )
        for table in (
            "obs_depth",
            "obs_ohlc",
            "obs_index",
            "instrument_options",
            "instrument_futures",
        ):
            self.assertIn(table, created, f"{table} is required by 02-DATA_MODEL.md")
            self.assertIn(table, dropped, f"{table} is not dropped on downgrade")


if __name__ == "__main__":
    unittest.main(verbosity=2)
