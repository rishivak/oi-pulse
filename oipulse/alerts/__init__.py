"""Layer 5b — Alerts.

`docs/design/18-ROADMAP.md` Phase 5: *alert rules, evaluation, dedup, cooldown · SSE +
one out-of-band channel.*

**Alerts are a delivery concern, not a truth concern.** A signal exists whether or not
anyone is listening: with no destination configured, with delivery delayed, failed or
retried, the signal is unchanged. Nothing in this package may mutate a `Signal`, and
`tools/check_alert_purity.py` enforces that rather than leaving it to review.

The separation matters because the alternative is subtle and expensive: if delivery
could mark a signal "sent", then a research query over signals would silently become a
query over *delivered* signals, and the undelivered ones — often the interesting ones —
would vanish from the analysis.
"""

from oipulse.alerts.model import AlertOccurrence, AlertRule
from oipulse.alerts.routing import AlertRouter

__all__ = ["AlertOccurrence", "AlertRouter", "AlertRule"]
