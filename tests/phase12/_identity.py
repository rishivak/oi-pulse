"""Shared fixtures for the security tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from oipulse.identity import Permission, RequestFacts, SessionRecord

NOW = datetime(2026, 3, 3, 11, 42, tzinfo=UTC)
ORIGIN = "https://terminal.oipulse.test"
ALLOWED = frozenset({ORIGIN})
TOKEN = "csrf-token-value-that-is-long-enough-01"


def session(
    *,
    session_id: str = "sess-1",
    user_id: str = "user-1",
    permissions: frozenset[Permission] = frozenset({Permission.PAPER_TRADE}),
    created_at: datetime = NOW - timedelta(hours=1),
    last_seen_at: datetime = NOW,
    expires_at: datetime = NOW + timedelta(hours=1),
    revoked_at: datetime | None = None,
    csrf_token: str = TOKEN,
) -> SessionRecord:
    return SessionRecord(
        id=session_id,
        user_id=user_id,
        created_at=created_at,
        last_seen_at=last_seen_at,
        expires_at=expires_at,
        revoked_at=revoked_at,
        user_agent="test",
        ip="203.0.113.1",
        permissions_snapshot=permissions,
        csrf_token=csrf_token,
    )


def lookup_of(*records: SessionRecord):
    """A session lookup over a fixed set of records."""
    table = {record.id: record for record in records}
    return table.get


def facts(
    method: str,
    path: str,
    *,
    session_id: str | None = "sess-1",
    origin: str | None = ORIGIN,
    referer: str | None = None,
    csrf: str | None = TOKEN,
    account_id: str | None = None,
) -> RequestFacts:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    if referer is not None:
        headers["referer"] = referer
    if csrf is not None:
        headers["x-oipulse-csrf"] = csrf
    cookies: dict[str, str] = {}
    if session_id is not None:
        cookies["oipulse_session"] = session_id
    return RequestFacts(
        method=method,
        path=path,
        headers=headers,
        cookies=cookies,
        account_id=account_id,
    )
