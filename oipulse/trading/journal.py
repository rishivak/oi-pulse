"""The trade journal read model. `12-API_SPEC.md` §3, `13-FRONTEND_IA.md` §6.

### What this is, and what the design asks for

Two different things share the name "journal", and conflating them would produce a
screen that lies about what it shows.

**The accounting journal.** `18-ROADMAP.md` Phase 8 lists `journal_entries` among its
schema, and migration 0008 creates it: `entry_type`, `occurred_at`, `cash_delta`,
`realized_pnl_delta`, `fees_delta`, `cash_after`, `order_id`, `fill_key`, and a
`source_event_key` that makes one entry per source event. It is a cash ledger,
traceable to an order and a fill.

**The trader's notebook.** `13-FRONTEND_IA.md` §6 describes the Journal *screen* as
"Entries linked to signals, intents and trades. Supports revisiting whether the
original hypothesis was correct." That is free-text reflection. It has no column in
`journal_entries`, no table anywhere, and no writer in any phase.

This module implements the first. The second is not invented here — a `note` column
and an authoring path would be a domain concept no phase specified, and Phase 12 is
a presentation layer over verified contracts.

### The entries are real and the table is empty

Phase 8 created the table and did not populate it: nothing in `oipulse/` inserts a
row, because the paper ledger holds cash in memory and `PaperLedgerSnapshot` is its
read model. So this reader is a correct implementation of a specified contract over
a store that no writer has filled.

That is worth stating precisely rather than smoothing over, because the two
statements a reader might confuse are:

- *this account recorded no cash movements in this period* — a fact about trading;
- *no component in this build writes journal entries* — a fact about the system.

`JournalAvailability` below keeps them apart, and the API and the screen carry the
distinction through to the operator. A screen that showed an empty table for both
would be the stub `13` §2 rejects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

__all__ = [
    "JournalAvailability",
    "JournalEntry",
    "JournalEntryType",
    "JournalPage",
    "JournalReader",
]


class JournalEntryType(StrEnum):
    """What moved the cash.

    Exactly the events the Phase 8 ledger recognises. No type is invented: a journal
    that could report an event the runtime cannot produce would be describing a
    system other than this one.
    """

    #: Opening balance when the account was created.
    OPENING = "OPENING"
    #: A fill settled: consideration moved.
    FILL = "FILL"
    #: Costs attached to a fill (brokerage, taxes, slippage is in the price).
    FEE = "FEE"
    #: Cash reserved against a resting order.
    RESERVATION = "RESERVATION"
    #: A reservation released when the order left the book.
    RELEASE = "RELEASE"
    #: Realized P&L booked when a position closed.
    REALIZED_PNL = "REALIZED_PNL"


class JournalAvailability(StrEnum):
    """Why a journal query returned what it did."""

    #: Entries were found.
    AVAILABLE = "AVAILABLE"
    #: The store is reachable and holds no entry for this account and period.
    #: A fact about trading.
    NO_ENTRIES_RECORDED = "NO_ENTRIES_RECORDED"
    #: No component in this build writes journal entries. A fact about the system,
    #: and the reason an empty result here is not evidence of a quiet account.
    NO_WRITER_IMPLEMENTED = "NO_WRITER_IMPLEMENTED"


#: Phase 8 declared `journal_entries` and shipped no writer; nothing in `oipulse/`
#: inserts a row. `tests/phase12/test_journal_contract.py` asserts this constant
#: still agrees with the code, so it cannot quietly go stale when a writer lands.
JOURNAL_WRITER_IMPLEMENTED = False


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One row of `journal_entries`.

    Decimals stay `Decimal` in the domain and become strings on the wire, for the
    reason `oipulse/marketstate/serialisation.py` gives: a rupee amount that
    round-trips through a double is no longer the amount that moved.
    """

    entry_id: str
    account_id: str
    entry_type: JournalEntryType
    occurred_at: datetime
    cash_delta: Decimal
    realized_pnl_delta: Decimal
    fees_delta: Decimal
    cash_after: Decimal
    order_id: str | None = None
    fill_key: str | None = None
    #: Idempotency key: one entry per `(account_id, source_event_key)`.
    source_event_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "account_id": self.account_id,
            "entry_type": self.entry_type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "cash_delta": str(self.cash_delta),
            "realized_pnl_delta": str(self.realized_pnl_delta),
            "fees_delta": str(self.fees_delta),
            "cash_after": str(self.cash_after),
            # The traceability `12` §3 requires: an entry links to the order and
            # the fill that caused it, and through the order to the intent, the
            # risk decision and the signal.
            "order_id": self.order_id,
            "fill_key": self.fill_key,
            "source_event_key": self.source_event_key,
        }


@dataclass(frozen=True, slots=True)
class JournalPage:
    """A page of entries, with why it looks the way it does."""

    entries: tuple[JournalEntry, ...]
    availability: JournalAvailability
    #: Cursor pagination on `(occurred_at, id)` per `12-API_SPEC.md` §5.
    next_cursor: str | None = None

    @property
    def is_empty(self) -> bool:
        return len(self.entries) == 0


class JournalReader(Protocol):
    """What the API needs from persistence.

    A protocol, so the route contract is testable without a database — the same
    arrangement every other reader in this codebase uses.
    """

    def entries(
        self,
        *,
        account_id: str,
        entry_type: JournalEntryType | None = None,
        order_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> JournalPage:
        """Entries for one account, newest first, filtered and paginated."""
        ...

    def entry(self, *, entry_id: str) -> JournalEntry | None:
        """One entry, or `None`."""
        ...
