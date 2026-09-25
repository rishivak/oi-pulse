"""The Journal decision, and the claim it rests on.

The remediation brief §8 asks whether Journal is required, deferred, optional, or
dependent on a backend capability that is itself required now — and says not to
preserve the omission merely because no route existed.

The trace: `12-API_SPEC.md` §3 specifies `/journal`; `13-FRONTEND_IA.md` §6 and
`18-ROADMAP.md` Phase 12 both list the screen; `journal_entries` is created by
migration 0008. So the read contract was required and missing, and the remediation
adds it. The screen ships against it.

What remains absent is stated rather than smoothed over, and these tests keep the
statements true:

1. **No writer exists.** `JOURNAL_WRITER_IMPLEMENTED` is `False`, and this asserts
   the constant still matches the code — so when a writer lands, the test fails and
   the UI notice comes down with it rather than lingering as a lie.
2. **The hypothesis notes have no schema.** `13` §6 also describes free-text
   reflection; no table in the repository has a column for it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from oipulse.trading.journal import (
    JOURNAL_WRITER_IMPLEMENTED,
    JournalAvailability,
    JournalEntryType,
)

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "oipulse"


def _writes_journal_entries() -> list[str]:
    """Any module that inserts into `journal_entries`.

    A textual scan would match the table definition and the docstrings that discuss
    it, so this looks for an actual insert: a call whose source mentions both an
    insert and the table.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts or path.name in {
            "journal.py",
            "trading_tables.py",
            "serialisation.py",
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            rendered = ast.unparse(node)
            if "journal_entries" in rendered and (
                ".insert(" in rendered or "INSERT INTO" in rendered.upper()
            ):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    return offenders


class TheWriterClaim(unittest.TestCase):
    def test_the_constant_matches_the_code(self) -> None:
        """If a writer lands, this fails and the UI notice must come down with it."""
        offenders = _writes_journal_entries()
        self.assertEqual(
            JOURNAL_WRITER_IMPLEMENTED,
            bool(offenders),
            f"JOURNAL_WRITER_IMPLEMENTED is {JOURNAL_WRITER_IMPLEMENTED} but writers "
            f"found: {offenders}. Update the constant and remove the 'no writer' "
            f"notice from the Journal screen.",
        )

    def test_the_table_still_exists_to_read_from(self) -> None:
        """The read contract is over a real table, not an invented one."""
        schema = (PACKAGE / "persistence" / "trading_tables.py").read_text(encoding="utf-8")
        self.assertIn('"journal_entries"', schema)
        migration = (
            PACKAGE / "migrations" / "versions" / "0008_phase8_paper_trading.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"journal_entries"', migration)

    def test_the_hypothesis_notes_have_no_schema_anywhere(self) -> None:
        """`13` §6's "what was I thinking" half is not invented.

        A column would be the place it would appear first. There is none, in any
        table, so the screen is honest in saying the notes are not part of the build.
        """
        for name in ("journal_tables.py", "notes_tables.py"):
            self.assertFalse((PACKAGE / "persistence" / name).exists())
        schema = (PACKAGE / "persistence" / "trading_tables.py").read_text(encoding="utf-8")
        journal_block = schema.split("journal_entries = sa.Table(")[1].split(")\n\n")[0]
        for invented in ('"note"', '"hypothesis"', '"body"', '"text"', '"comment"'):
            self.assertNotIn(invented, journal_block)


class TheReadContract(unittest.TestCase):
    def test_the_entry_types_are_the_ledger_s_vocabulary(self) -> None:
        self.assertEqual(
            [t.value for t in JournalEntryType],
            ["OPENING", "FILL", "FEE", "RESERVATION", "RELEASE", "REALIZED_PNL"],
        )

    def test_the_two_empty_readings_are_distinct(self) -> None:
        self.assertNotEqual(
            JournalAvailability.NO_ENTRIES_RECORDED,
            JournalAvailability.NO_WRITER_IMPLEMENTED,
        )

    def test_the_route_exists_in_the_generated_contract(self) -> None:
        import json

        contract = json.loads(
            (REPO / "frontend" / "lib" / "api" / "contract.generated.json").read_text()
        )
        keys = {f"{r['method']} {r['path']}" for r in contract["routes"]}
        self.assertIn("GET /journal/entries", keys)
        self.assertIn("GET /journal/entries/{entry_id}", keys)

    def test_there_is_no_write_route(self) -> None:
        """`12` §3 says CRUD; only the read half is added, deliberately."""
        import json

        contract = json.loads(
            (REPO / "frontend" / "lib" / "api" / "contract.generated.json").read_text()
        )
        writes = [
            f"{r['method']} {r['path']}"
            for r in contract["routes"]
            if "/journal" in r["path"] and r["method"] != "GET"
        ]
        self.assertEqual(writes, [])

    def test_the_serializer_states_the_availability_on_every_page(self) -> None:
        from oipulse.trading.journal import JournalPage
        from oipulse.trading.serialisation import journal_page_to_dict

        body = journal_page_to_dict(
            JournalPage(entries=(), availability=JournalAvailability.NO_WRITER_IMPLEMENTED),
            account_id="a1",
        )
        self.assertEqual(body["meta"]["availability"], "NO_WRITER_IMPLEMENTED")
        self.assertIs(body["meta"]["writer_implemented"], False)


if __name__ == "__main__":
    unittest.main()
