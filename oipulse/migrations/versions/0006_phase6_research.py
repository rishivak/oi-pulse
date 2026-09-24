"""Phase 6: research studies, datasets, results and signal evaluations.

`docs/design/09-RESEARCH.md` §4 and §7, `18-ROADMAP.md` Phase 6.

Two `UNIQUE` constraints carry the reproducibility gate:

* `research_datasets.content_hash` — a rebuild with the same parameters must produce
  the same hash. If it does not, something non-deterministic changed and every study
  resting on it is suspect (`09` §4).
* `research_results.content_hash` — re-running a study over the same immutable dataset
  yields the same artifact rather than a second row.

`research_results.status` is `NOT NULL` and `'insufficient_sample'` is a real value:
below the declared minimum a study reports insufficiency, which is a finding, not an
absent row.

**Nothing here is partitioned.** Decision artifacts are immutable and retained forever
(`02` §9); these are small, permanent records that hold retention locks over the large
partitioned tables rather than being pruned themselves. Introducing time partitions
would imply a pruning story that must not exist for this data.

No Phase 7 backtest table is created. Backtesting is a later phase.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0006_phase6_research
Revises: 0005_phase5_signals
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_phase6_research"
down_revision = "0005_phase5_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_studies",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("study_id", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
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

    op.create_table(
        "research_datasets",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_hash", name="uq_research_datasets_content"),
        sa.CheckConstraint("row_count >= 0", name="ck_research_dataset_row_count"),
    )

    op.create_table(
        "research_results",
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
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("status_detail", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("raw_events", sa.Integer, nullable=False),
        sa.Column("effective_sample", sa.Integer, nullable=False),
        sa.Column("clusters", sa.Integer, nullable=False),
        sa.Column("excluded_quality", sa.Integer, nullable=False),
        sa.Column("excluded_incomplete_window", sa.Integer, nullable=False),
        sa.Column("missing_observations", sa.Integer, nullable=False),
        sa.Column("comparisons", sa.Integer, nullable=False),
        sa.Column("random_seed", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("horizons", postgresql.JSONB, nullable=False),
        sa.Column("execution", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_hash", name="uq_research_results_content"),
        sa.CheckConstraint(
            "effective_sample <= raw_events", name="ck_research_result_sample_bounds"
        ),
        sa.CheckConstraint("comparisons >= 1", name="ck_research_result_comparisons"),
    )

    op.create_table(
        "research_signal_evaluations",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("result_content_hash", sa.Text, nullable=False),
        sa.Column("signal_type", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("sample", sa.Integer, nullable=False),
        sa.Column("minimum_sample", sa.Integer, nullable=False),
        sa.Column("reportable", sa.Boolean, nullable=False),
        # Nullable: withheld below the minimum sample, which is not the same as zero.
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

    # Indexes for the real access patterns: find a study's results, find a result by
    # the dataset it used, and resolve a signal evaluation back to its result.
    op.create_index(
        "ix_research_results_study", "research_results", ["study_id", "study_version"]
    )
    op.create_index(
        "ix_research_results_dataset", "research_results", ["dataset_content_hash"]
    )
    op.create_index(
        "ix_research_signal_evaluations_result",
        "research_signal_evaluations",
        ["result_content_hash"],
    )
    op.create_index("ix_research_studies_lookup", "research_studies", ["study_id", "version"])


def downgrade() -> None:
    # Reverse creation order. Only Phase 6 tables are touched; no observation,
    # checkpoint, metric, signal or alert table is affected.
    op.drop_table("research_signal_evaluations")
    op.drop_table("research_results")
    op.drop_table("research_datasets")
    op.drop_table("research_studies")
