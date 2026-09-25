"""`/journal` — `12-API_SPEC.md` §3.

| GET | `/journal/entries` | entries for an account, filterable, cursor-paginated |
| GET | `/journal/entries/{entry_id}` | one entry |

`12` §3 specifies "CRUD on entries, linkable to signals, intents and trades". This
is the **read** half, which is what Phase 12 needs and all it is entitled to add:
writing an entry is a domain action, `journal_entries` carries a `source_event_key`
that makes entries derived from events rather than authored, and inventing an
authoring path would add a concept no phase specified.

The linkage `12` §3 asks for is present through `order_id` and `fill_key`: from an
entry the terminal reaches the order, and from the order the intent, the risk
decision and the signal — the same chain `oipulse/trading/audit.py` assembles.

### The empty answer is qualified, not bare

Nothing in this build writes journal entries. Phase 8 created the table and shipped
no writer. So `meta.availability` distinguishes `NO_ENTRIES_RECORDED` from
`NO_WRITER_IMPLEMENTED`, and the terminal renders them differently. Without that,
an operator reading an empty journal would conclude the account had been quiet.

Absent a reader this router answers **503 naming the missing dependency**, for the
reason `oipulse/api/signals.py` gives: an empty list is indistinguishable from
there being nothing, and those are different facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from oipulse.trading.journal import JournalEntryType
from oipulse.trading.serialisation import journal_entry_to_dict, journal_page_to_dict

router = APIRouter(prefix="/journal", tags=["journal"])

__all__ = ["router"]

#: Matches the cursor pagination limit the rest of the API uses (`12` §5).
MAX_LIMIT = 500


def _reader(request: Request) -> Any:
    reader = getattr(request.app.state, "journal_reader", None)
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no journal reader is configured on this process. An empty page is "
                "not returned: it would be indistinguishable from an account that "
                "recorded no cash movement, which is a different fact."
            ),
        )
    return reader


@router.get("/entries")
async def list_entries(
    request: Request,
    account_id: str = Query(..., description="the account whose journal to read"),
    entry_type: str | None = Query(None, description="filter by entry type"),
    order_id: str | None = Query(None, description="entries caused by one order"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    cursor: str | None = Query(None, description="cursor from a previous page"),
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    """Entries for one account, newest first."""
    kind: JournalEntryType | None = None
    if entry_type is not None:
        try:
            kind = JournalEntryType(entry_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"unknown entry_type {entry_type!r}; expected one of "
                    f"{sorted(t.value for t in JournalEntryType)}"
                ),
            ) from exc
    if since is not None and until is not None and until < since:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"until {until.isoformat()} precedes since {since.isoformat()}",
        )

    page = _reader(request).entries(
        account_id=account_id,
        entry_type=kind,
        order_id=order_id,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )
    return journal_page_to_dict(page, account_id=account_id)


@router.get("/entries/{entry_id}")
async def get_entry(request: Request, entry_id: str) -> dict[str, Any]:
    entry = _reader(request).entry(entry_id=entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no journal entry {entry_id}"
        )
    return journal_entry_to_dict(entry)
