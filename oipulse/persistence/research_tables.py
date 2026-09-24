"""Tables for studies, datasets, results and signal evaluations.

`docs/design/09-RESEARCH.md` §4 and §7, `18-ROADMAP.md` Phase 6:
`research_studies`, `research_datasets`, `research_results`,
`research_signal_evaluations`.

**Why these live in `persistence/`.** The `research-is-pure` contract forbids
`research/*` from importing SQLAlchemy, for the same reason it forbids it of analytics
and signals: a table declaration in the pure layer puts a database one import away from
the engine, and the engine must be replayable.

Two content addresses are the point of the schema:

* `research_datasets.content_hash` is `UNIQUE`. A rebuild with the same parameters must
  produce the same hash; if it does not, something non-deterministic changed and the
  study result is suspect (`09` §4).
* `research_results.content_hash` is `UNIQUE`. Re-running a study over the same
  immutable dataset yields the same artifact rather than a second row.

Decision artifacts are **immutable and retained forever** (`02` §9), so nothing here is
partitioned by time for pruning: these are small, permanent records, and they hold
retention locks over the large tables rather than being pruned themselves.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "RESEARCH_TABLES",
    "research_datasets",
    "research_results",
    "research_signal_evaluations",
    "research_studies",
]

METADATA = sa.MetaData()

RESEARCH_TABLES: list[str] = [
    "research_studies",
    "research_datasets",
    "research_results",
    "research_signal_evaluations",
]

research_studies = sa.Table(
    "research_studies",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("study_id", sa.Text, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("question", sa.Text, nullable=False),
    # Content address of the definition. A threshold or horizon edit changes it, so
    # studies run before and after are distinguishable rather than pooled.
    sa.Column("content_digest", sa.Text, nullable=False),
    sa.Column("event_definition_id", sa.Text, nullable=False),
    sa.Column("event_definition_version", sa.Integer, nullable=False),
    sa.Column("event_definition_digest", sa.Text, nullable=False),
    sa.Column("query_mode", sa.Text, nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("universe", postgresql.JSONB, nullable=False),
    sa.Column("horizons_seconds", postgresql.JSONB, nullable=False),
    sa.Column("feature_versions", postgresql.JSONB, nullable=False),
    sa.Column("signal_versions", postgresql.JSONB, nullable=False),
    sa.Column("sampling", postgresql.JSONB, nullable=False),
    sa.Column("controls", postgresql.JSONB, nullable=False),
    sa.Column("minimum_sample", sa.Integer, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("study_id", "version", name="uq_research_studies_identity"),
    sa.CheckConstraint("period_end >= period_start", name="ck_research_study_period"),
    sa.CheckConstraint("minimum_sample >= 1", name="ck_research_study_minimum_sample"),
)

research_datasets = sa.Table(
    "research_datasets",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    # The reproducibility check. UNIQUE, so an identical rebuild reuses the row and a
    # divergent one is immediately visible as a new hash.
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("query_mode", sa.Text, nullable=False),
    sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
    sa.Column("build_context_id", sa.Text, nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("universe", postgresql.JSONB, nullable=False),
    sa.Column("feature_versions", postgresql.JSONB, nullable=False),
    sa.Column("builder_version", sa.Text, nullable=False),
    sa.Column("row_count", sa.Integer, nullable=False),
    sa.Column("quality_summary", postgresql.JSONB, nullable=False),
    # Execution metadata, deliberately not part of the content hash.
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("content_hash", name="uq_research_datasets_content"),
    sa.CheckConstraint("row_count >= 0", name="ck_research_dataset_row_count"),
)

research_results = sa.Table(
    "research_results",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("study_id", sa.Text, nullable=False),
    sa.Column("study_version", sa.Integer, nullable=False),
    sa.Column("study_digest", sa.Text, nullable=False),
    sa.Column("event_definition_digest", sa.Text, nullable=False),
    sa.Column("dataset_content_hash", sa.Text, nullable=False),
    sa.Column("query_mode", sa.Text, nullable=False),
    sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
    sa.Column("build_context_id", sa.Text, nullable=False),
    sa.Column("builder_version", sa.Text, nullable=False),
    sa.Column("feature_versions", postgresql.JSONB, nullable=False),
    sa.Column("signal_versions", postgresql.JSONB, nullable=False),
    # Status is NOT NULL and 'insufficient_sample' is a real value: below the minimum
    # the study reports insufficiency, which is a finding rather than an absent row.
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("status_detail", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("raw_events", sa.Integer, nullable=False),
    sa.Column("effective_sample", sa.Integer, nullable=False),
    sa.Column("clusters", sa.Integer, nullable=False),
    sa.Column("excluded_quality", sa.Integer, nullable=False),
    sa.Column("excluded_incomplete_window", sa.Integer, nullable=False),
    sa.Column("missing_observations", sa.Integer, nullable=False),
    # Recorded so a reader can discount for multiple comparisons (`09` §3).
    sa.Column("comparisons", sa.Integer, nullable=False),
    sa.Column("random_seed", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("horizons", postgresql.JSONB, nullable=False),
    # Execution metadata, kept separate from semantic identity by design.
    sa.Column("execution", postgresql.JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("content_hash", name="uq_research_results_content"),
    sa.CheckConstraint("effective_sample <= raw_events", name="ck_research_result_sample_bounds"),
    sa.CheckConstraint("comparisons >= 1", name="ck_research_result_comparisons"),
)

research_signal_evaluations = sa.Table(
    "research_signal_evaluations",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("result_content_hash", sa.Text, nullable=False),
    sa.Column("signal_type", sa.Text, nullable=False),
    sa.Column("rule_version", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("sample", sa.Integer, nullable=False),
    sa.Column("minimum_sample", sa.Integer, nullable=False),
    sa.Column("reportable", sa.Boolean, nullable=False),
    # Nullable: withheld below the minimum sample, which is different from zero.
    sa.Column("distribution", postgresql.JSONB, nullable=True),
    sa.Column("evidence_attribution", postgresql.JSONB, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "result_content_hash",
        "signal_type",
        "rule_version",
        "status",
        name="uq_research_signal_evaluation_identity",
    ),
    sa.CheckConstraint("sample >= 0", name="ck_research_signal_evaluation_sample"),
)
