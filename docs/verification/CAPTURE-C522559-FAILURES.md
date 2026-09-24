# Failure capture for commit c522559

Commit: `c5225594fbadf949b6c42e77f8e8920cce807919`
Subject: Move the Upstox adapter to V3 semantics; resolve A-1, redefine A-13
`git rev-parse HEAD` returned that SHA.
`git status --short` was empty before this report was written.

No source file was modified. Nothing was fixed or reformatted. Phase 3 was not started.

## Environment

Captured from `/tmp/oipulse-phase1-venv`. No credentials, tokens, or environment-variable values are included.

```text
Python 3.12.14
pytest 8.3.3
mypy 1.13.0 (compiled: yes)
ruff 0.16.5
```

`pip show` versions:

| Package | Version |
| --- | --- |
| SQLAlchemy | 2.0.35 |
| asyncpg | 0.29.0 |
| fastapi | 0.115.0 |
| redis | 5.0.8 |
| httpx | 0.27.2 |
| websockets | 17.1 |
| protobuf | not installed (`Package(s) not found: protobuf`) |

Host: macOS, Darwin arm64. PostgreSQL is reachable on `127.0.0.1:5432` from this machine, which is why the preflight test reaches a real server.

## pytest -vv

Exit code 1. Collected 236 items. Result: 3 failed, 232 passed, 1 skipped, 15.23s.

The complete command output:

```text
/private/tmp/oipulse-phase1-venv/lib/python3.12/site-packages/pytest_asyncio/plugin.py:208: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture caching scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to function scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session"

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
============================= test session starts ==============================
platform darwin -- Python 3.12.14, pytest-8.3.3, pluggy-1.6.0 -- /private/tmp/oipulse-phase1-venv/bin/python3.12
cachedir: .pytest_cache
rootdir: /Users/apple/Projects/oi-pulse
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-0.24.0, anyio-4.15.1
asyncio: mode=Mode.STRICT, default_loop_scope=None
collecting ... collected 236 items

tests/integration/test_migrations_postgres.py::TestMigrationsApply::test_downgrade_then_upgrade_is_repeatable PASSED [  0%]
tests/integration/test_migrations_postgres.py::TestMigrationsApply::test_identity_indexes_exist PASSED [  0%]
tests/integration/test_migrations_postgres.py::TestMigrationsApply::test_partitions_exist_for_every_partitioned_parent PASSED [  1%]
tests/integration/test_migrations_postgres.py::TestMigrationsApply::test_phase_1_and_phase_2_tables_exist_after_upgrade PASSED [  1%]
tests/integration/test_migrations_postgres.py::TestMigrationsApply::test_upgrade_head_applies_the_whole_chain PASSED [  2%]
tests/phase1/test_foundation.py::TestClock::test_all_clocks_satisfy_the_protocol PASSED [  2%]
tests/phase1/test_foundation.py::TestClock::test_every_clock_returns_utc_aware PASSED [  2%]
tests/phase1/test_foundation.py::TestClock::test_frozen_clock_does_not_advance PASSED [  3%]
tests/phase1/test_foundation.py::TestClock::test_naive_datetime_is_rejected_not_assumed_utc PASSED [  3%]
tests/phase1/test_foundation.py::TestClock::test_replay_clock_cannot_move_backwards PASSED [  4%]
tests/phase1/test_foundation.py::TestClock::test_replay_clock_rejects_negative_advance PASSED [  4%]
tests/phase1/test_foundation.py::TestTimeAuthority::test_bucket_is_half_open_and_contiguous PASSED [  5%]
tests/phase1/test_foundation.py::TestTimeAuthority::test_buckets_anchor_to_session_open_not_wall_clock PASSED [  5%]
tests/phase1/test_foundation.py::TestTimeAuthority::test_flooring_is_idempotent PASSED [  5%]
tests/phase1/test_foundation.py::TestTimeAuthority::test_session_open_is_the_first_bucket_start PASSED [  6%]
tests/phase1/test_foundation.py::TestTemporalBounds::test_knowledge_at_bounds_both_axes_to_the_same_instant PASSED [  6%]
tests/phase1/test_foundation.py::TestTemporalBounds::test_knowledge_horizon_before_valid_time_is_incoherent PASSED [  7%]
tests/phase1/test_foundation.py::TestTemporalBounds::test_market_truth_requires_an_explicit_knowledge_horizon PASSED [  7%]
tests/phase1/test_foundation.py::TestTemporalBounds::test_market_truth_separates_the_two_axes PASSED [  8%]
tests/phase1/test_foundation.py::TestTemporalBounds::test_tradable_information_adds_the_availability_filter PASSED [  8%]
tests/phase1/test_foundation.py::TestTemporalBounds::test_unbounded_query_is_refused PASSED [  8%]
tests/phase1/test_foundation.py::TestTemporalRepository::test_derived_data_accepts_availability_semantics PASSED [  9%]
tests/phase1/test_foundation.py::TestTemporalRepository::test_raw_observations_accept_knowledge_and_market_truth PASSED [  9%]
tests/phase1/test_foundation.py::TestTemporalRepository::test_raw_observations_reject_availability_semantics PASSED [ 10%]
tests/phase1/test_foundation.py::TestConfigValidation::test_all_problems_reported_together PASSED [ 10%]
tests/phase1/test_foundation.py::TestConfigValidation::test_live_trading_is_off_by_default PASSED [ 11%]
tests/phase1/test_foundation.py::TestConfigValidation::test_live_trading_needs_both_environment_gates PASSED [ 11%]
tests/phase1/test_foundation.py::TestConfigValidation::test_missing_required_value_refuses_startup PASSED [ 11%]
tests/phase1/test_foundation.py::TestConfigValidation::test_placeholder_detection_is_case_insensitive PASSED [ 12%]
tests/phase1/test_foundation.py::TestConfigValidation::test_placeholder_secret_refuses_startup PASSED [ 12%]
tests/phase1/test_foundation.py::TestConfigValidation::test_production_rejects_sqlite PASSED [ 13%]
tests/phase1/test_foundation.py::TestConfigValidation::test_short_secret_refuses_startup PASSED [ 13%]
tests/phase1/test_foundation.py::TestConfigValidation::test_valid_configuration_loads PASSED [ 13%]
tests/phase1/test_foundation.py::TestPrimitives::test_decimal_and_str_accepted_exactly PASSED [ 14%]
tests/phase1/test_foundation.py::TestPrimitives::test_float_is_rejected_for_monetary_values PASSED [ 14%]
tests/phase1/test_foundation.py::TestPrimitives::test_ids_are_unique PASSED [ 15%]
tests/phase1/test_foundation.py::TestAggregateOrdering::test_event_ahead_of_its_turn_is_deferred PASSED [ 15%]
tests/phase1/test_foundation.py::TestAggregateOrdering::test_events_carry_ordering_identity PASSED [ 16%]
tests/phase1/test_foundation.py::TestAggregateOrdering::test_next_in_sequence_is_accepted PASSED [ 16%]
tests/phase1/test_foundation.py::TestAggregateOrdering::test_redelivery_behind_the_watermark_is_not_an_error PASSED [ 16%]
tests/phase1/test_foundation.py::TestAggregateOrdering::test_sequence_must_start_at_one PASSED [ 17%]
tests/phase1/test_foundation.py::TestTransactionalInbox::test_each_subscriber_applies_independently PASSED [ 17%]
tests/phase1/test_foundation.py::TestTransactionalInbox::test_failed_mutation_releases_the_claim_so_retry_works PASSED [ 18%]
tests/phase1/test_foundation.py::TestTransactionalInbox::test_first_delivery_applies PASSED [ 18%]
tests/phase1/test_foundation.py::TestTransactionalInbox::test_redelivery_is_a_noop PASSED [ 19%]
tests/phase1/test_foundation.py::TestCorrelation::test_nested_scopes_do_not_leak PASSED [ 19%]
tests/phase1/test_foundation.py::TestCorrelation::test_scope_allocates_and_restores PASSED [ 19%]
tests/phase1/test_foundation.py::TestGuardsAreWired::test_clock_access_guard_passes PASSED [ 20%]
tests/phase1/test_foundation.py::TestGuardsAreWired::test_import_boundary_guard_passes PASSED [ 20%]
tests/phase1/test_foundation.py::TestGuardsAreWired::test_temporal_repository_guard_passes PASSED [ 21%]
tests/phase1/test_remediation.py::TestMigrationChain::test_both_version_locations_are_configured PASSED [ 21%]
tests/phase1/test_remediation.py::TestMigrationChain::test_chain_is_valid PASSED [ 22%]
tests/phase1/test_remediation.py::TestMigrationChain::test_exactly_one_root_and_one_head PASSED [ 22%]
tests/phase1/test_remediation.py::TestMigrationChain::test_fresh_database_initialisation_is_preserved PASSED [ 22%]
tests/phase1/test_remediation.py::TestMigrationChain::test_guard_catches_a_second_root PASSED [ 23%]
tests/phase1/test_remediation.py::TestMigrationChain::test_no_migration_drops_or_truncates_legacy_tables PASSED [ 23%]
tests/phase1/test_remediation.py::TestMigrationChain::test_v2_continues_from_the_legacy_head PASSED [ 24%]
tests/phase1/test_remediation.py::TestEntrypoint::test_check_validates_configuration_and_exits_zero PASSED [ 24%]
tests/phase1/test_remediation.py::TestEntrypoint::test_documented_command_matches_the_real_one PASSED [ 25%]
tests/phase1/test_remediation.py::TestEntrypoint::test_entrypoint_is_importable_without_a_web_stack PASSED [ 25%]
tests/phase1/test_remediation.py::TestEntrypoint::test_health_routes_are_registered_on_the_router PASSED [ 25%]
tests/phase1/test_remediation.py::TestEntrypoint::test_invalid_configuration_refuses_to_start PASSED [ 26%]
tests/phase1/test_remediation.py::TestEntrypoint::test_later_phase_role_is_refused_with_its_phase_named PASSED [ 26%]
tests/phase1/test_remediation.py::TestEntrypoint::test_placeholder_secret_refuses_to_start PASSED [ 27%]
tests/phase1/test_remediation.py::TestCIGates::test_import_linter_forbids_only_internal_modules PASSED [ 27%]
tests/phase1/test_remediation.py::TestCIGates::test_import_linter_references_only_modules_that_exist PASSED [ 27%]
tests/phase1/test_remediation.py::TestCIGates::test_layers_match_the_real_import_graph PASSED [ 28%]
tests/phase1/test_remediation.py::TestCIGates::test_mypy_strict_is_still_meaningful PASSED [ 28%]
tests/phase1/test_remediation.py::TestCIGates::test_mypy_version_supports_pep695_type_parameters PASSED [ 29%]
tests/phase1/test_remediation.py::TestCIGates::test_no_gate_is_advisory PASSED [ 29%]
tests/phase1/test_remediation.py::TestAdrConsistency::test_ad28_no_authoritative_source_requires_platform_run PASSED [ 30%]
tests/phase1/test_remediation.py::TestAdrConsistency::test_ad29_deployment_does_not_require_pydantic_settings_in_core PASSED [ 30%]
tests/phase1/test_remediation.py::TestAdrConsistency::test_config_attributes_the_right_adr PASSED [ 30%]
tests/phase1/test_remediation.py::TestAdrConsistency::test_core_is_actually_dependency_free PASSED [ 31%]
tests/phase1/test_remediation.py::TestAdrConsistency::test_legacy_documentation_is_left_alone PASSED [ 31%]
tests/phase1/test_remediation.py::TestSchemaParity::test_every_observation_kind_has_a_table PASSED [ 32%]
tests/phase1/test_remediation.py::TestSchemaParity::test_parity_guard_passes PASSED [ 32%]
tests/phase1/test_remediation.py::TestSchemaParity::test_previously_missing_tables_are_present PASSED [ 33%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_identity_is_separate_from_metadata_version PASSED [ 33%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_march_exposure_uses_march_lot_size PASSED [ 33%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_negative_or_zero_strike_rejected PASSED [ 34%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_option_requires_its_defining_attributes PASSED [ 34%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_overlapping_versions_are_invalid_by_construction PASSED [ 35%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_reissued_vendor_key_resolves_to_the_right_instrument PASSED [ 35%]
tests/phase2/test_marketdata.py::TestInstrumentIdentity::test_vendor_key_is_external_and_temporal PASSED [ 36%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_days_to_expiry_is_derived_not_stored PASSED [ 36%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_expired_expiries_excluded PASSED [ 36%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_front_n_selects_more_than_the_front_expiry PASSED [ 37%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_monthly_selector_enables_term_structure PASSED [ 37%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_multi_expiry_chains_normalize_independently PASSED [ 38%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_universe_is_system_level_not_user_scoped PASSED [ 38%]
tests/phase2/test_marketdata.py::TestMultiExpiry::test_weekly_selector PASSED [ 38%]
tests/phase2/test_marketdata.py::TestNormalization::test_all_greeks_are_persisted_not_just_iv PASSED [ 39%]
tests/phase2/test_marketdata.py::TestNormalization::test_malformed_rows_are_reported_not_silently_skipped PASSED [ 39%]
tests/phase2/test_marketdata.py::TestNormalization::test_negative_oi_is_rejected PASSED [ 40%]
tests/phase2/test_marketdata.py::TestNormalization::test_paired_row_becomes_per_leg_observations PASSED [ 40%]
tests/phase2/test_marketdata.py::TestNormalization::test_quotes_capture_bid_ask_and_provider_prev_oi PASSED [ 41%]
tests/phase2/test_marketdata.py::TestNormalization::test_unknown_vendor_fields_preserved_in_raw_extra PASSED [ 41%]
tests/phase2/test_marketdata.py::TestNormalization::test_values_are_decimal_not_float PASSED [ 41%]
tests/phase2/test_marketdata.py::TestTimestampSemantics::test_missing_venue_timestamp_is_recorded_not_hidden PASSED [ 42%]
tests/phase2/test_marketdata.py::TestTimestampSemantics::test_naive_datetime_rejected PASSED [ 42%]
tests/phase2/test_marketdata.py::TestTimestampSemantics::test_the_canonical_1140_1144_fixture PASSED [ 43%]
tests/phase2/test_marketdata.py::TestTimestampSemantics::test_venue_and_receipt_times_are_distinct PASSED [ 43%]
tests/phase2/test_marketdata.py::TestHistoricalDailyOI::test_backfill_is_invisible_to_earlier_knowledge_queries PASSED [ 44%]
tests/phase2/test_marketdata.py::TestHistoricalDailyOI::test_cannot_be_constructed_without_an_interval PASSED [ 44%]
tests/phase2/test_marketdata.py::TestHistoricalDailyOI::test_carries_date_granularity_and_an_interval PASSED [ 44%]
tests/phase2/test_marketdata.py::TestHistoricalDailyOI::test_distinct_source_keeps_it_separable_forever PASSED [ 45%]
tests/phase2/test_marketdata.py::TestHistoricalDailyOI::test_interval_covers_the_session_not_an_instant PASSED [ 45%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_content_hash_fallback_is_weak PASSED [ 46%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_distinct_events_at_one_timestamp_have_distinct_identities PASSED [ 46%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_feed_sequence_identity_is_session_scoped PASSED [ 47%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_feed_sequence_is_the_second_tier PASSED [ 47%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_identical_content_yields_identical_identity PASSED [ 47%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_provider_event_id_is_the_strongest_tier PASSED [ 48%]
tests/phase2/test_marketdata.py::TestObservationIdentity::test_ws_hint_extraction_tolerates_absence PASSED [ 48%]
tests/phase2/test_marketdata.py::TestIngestionIdempotency::test_genuinely_different_ticks_are_both_kept PASSED [ 49%]
tests/phase2/test_marketdata.py::TestIngestionIdempotency::test_raw_store_refuses_availability_semantics PASSED [ 49%]
tests/phase2/test_marketdata.py::TestIngestionIdempotency::test_reconnect_overlap_does_not_duplicate PASSED [ 50%]
tests/phase2/test_marketdata.py::TestIngestionIdempotency::test_replaying_a_batch_inserts_nothing_new PASSED [ 50%]
tests/phase2/test_marketdata.py::TestIngestionIdempotency::test_restart_does_not_duplicate PASSED [ 50%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_duplicate_or_late_sequence_does_not_lower_the_watermark PASSED [ 51%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_illegal_transition_raises PASSED [ 51%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_in_session_sequence_gap_detected PASSED [ 52%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_lifecycle_order_is_enforced PASSED [ 52%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_reconnect_records_the_outage_window PASSED [ 52%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_sequence_reset_across_reconnect_is_not_a_gap PASSED [ 53%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_session_ordinals_are_monotonic_and_stored PASSED [ 53%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_watchdog_detects_a_silent_feed PASSED [ 54%]
tests/phase2/test_marketdata.py::TestWebSocketLifecycle::test_weak_identity_claims_no_sequence_gap PASSED [ 54%]
tests/phase2/test_marketdata.py::TestRecoveryAndCoherence::test_coherence_modes PASSED [ 55%]
tests/phase2/test_marketdata.py::TestRecoveryAndCoherence::test_divergence_detected_beyond_tolerance PASSED [ 55%]
tests/phase2/test_marketdata.py::TestRecoveryAndCoherence::test_gap_triggers_out_of_band_recovery_and_records_the_hole PASSED [ 55%]
tests/phase2/test_marketdata.py::TestRecoveryAndCoherence::test_no_divergence_within_tolerance PASSED [ 56%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_allocation_never_mixes_full_with_the_shared_pool PASSED [ 56%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_budget_declares_its_provenance PASSED [ 57%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_budget_is_configuration_not_a_constant PASSED [ 57%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_depth_is_shed_before_coverage PASSED [ 58%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_impossible_universe_refuses_rather_than_truncating PASSED [ 58%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_oversized_universe_degrades_and_records_why PASSED [ 58%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_protected_instruments_are_never_dropped PASSED [ 59%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_reserve_is_held_back PASSED [ 59%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_result_reports_the_numbers_it_used PASSED [ 60%]
tests/phase2/test_marketdata.py::TestSubscriptionPlanner::test_small_universe_accepted PASSED [ 60%]
tests/phase2/test_marketdata.py::TestRateLimitGovernance::test_429_penalises_the_whole_endpoint_class PASSED [ 61%]
tests/phase2/test_marketdata.py::TestRateLimitGovernance::test_budget_exhausts_then_recovers PASSED [ 61%]
tests/phase2/test_marketdata.py::TestRateLimitGovernance::test_cadence_derives_from_budget_so_expiries_cost_visibly PASSED [ 61%]
tests/phase2/test_marketdata.py::TestRateLimitGovernance::test_endpoint_classes_have_independent_budgets PASSED [ 62%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_drained_gaps_remain_in_the_permanent_record PASSED [ 62%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_gaps_drain_exactly_once PASSED [ 63%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_persist_is_idempotent_through_the_collector PASSED [ 63%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_persist_writes_through_the_async_sink PASSED [ 63%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_plan_carries_the_scope_recovery_needs PASSED [ 64%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_recovery_records_a_quality_issue PASSED [ 64%]
tests/phase2/test_marketdata.py::TestCollectorRecovery::test_the_durable_store_satisfies_the_sink_contract PASSED [ 65%]
tests/phase2/test_marketdata.py::TestRecoveryPathWiring::test_a_gap_drives_a_real_rest_refetch_and_persists_it PASSED [ 65%]
tests/phase2/test_marketdata.py::TestRecoveryPathWiring::test_an_unbound_expiry_fails_loudly PASSED [ 66%]
tests/phase2/test_marketdata.py::TestRecoveryPathWiring::test_recovery_overlapping_the_resumed_stream_does_not_duplicate PASSED [ 66%]
tests/phase2/test_marketdata.py::TestRecoveryPathWiring::test_the_gap_remains_a_permanent_record_after_recovery PASSED [ 66%]
tests/phase2/test_marketdata.py::TestRecoveryPathWiring::test_the_provider_implements_the_collector_contract PASSED [ 67%]
tests/phase2/test_migrations.py::TestMigrationChain::test_exactly_one_head PASSED [ 67%]
tests/phase2/test_migrations.py::TestMigrationChain::test_the_chain_is_linear_from_the_legacy_root PASSED [ 68%]
tests/phase2/test_migrations.py::TestMigrationChain::test_upgrading_from_the_legacy_revision_reaches_phase_2 PASSED [ 68%]
tests/phase2/test_migrations.py::TestOperationOrdering::test_every_partitioned_parent_is_partitioned_exactly_once PASSED [ 69%]
tests/phase2/test_migrations.py::TestOperationOrdering::test_no_revision_touches_a_table_before_creating_it PASSED [ 69%]
tests/phase2/test_migrations.py::TestOperationOrdering::test_partitioned_tables_are_created_before_the_partition_loop PASSED [ 69%]
tests/phase2/test_migrations.py::TestTableInventory::test_downgrade_mirrors_upgrade_in_both_revisions PASSED [ 70%]
tests/phase2/test_migrations.py::TestTableInventory::test_no_legacy_table_is_dropped_or_truncated PASSED [ 70%]
tests/phase2/test_migrations.py::TestTableInventory::test_phase_1_creates_exactly_the_sys_tables PASSED [ 71%]
tests/phase2/test_migrations.py::TestTableInventory::test_phase_2_creates_exactly_the_expected_tables PASSED [ 71%]
tests/phase2/test_migrations.py::TestSchemaAgreesWithMigration::test_every_schema_table_exists_in_the_migration PASSED [ 72%]
tests/phase2/test_migrations.py::TestIdentityIndexes::test_each_observation_table_gets_the_three_identity_indexes PASSED [ 72%]
tests/phase2/test_migrations.py::TestIdentityIndexes::test_instrument_observed_at_source_is_not_unique PASSED [ 72%]
tests/phase2/test_migrations.py::TestIdentityIndexes::test_tier_1_uniqueness_is_scoped_by_feed_session PASSED [ 73%]
tests/phase2/test_runtime.py::TestUniverseParsing::test_a_partial_expiry_binding_is_rejected PASSED [ 73%]
tests/phase2/test_runtime.py::TestUniverseParsing::test_a_valid_universe_parses PASSED [ 74%]
tests/phase2/test_runtime.py::TestUniverseParsing::test_an_empty_instrument_list_is_rejected PASSED [ 74%]
tests/phase2/test_runtime.py::TestUniverseParsing::test_an_unknown_data_mode_is_rejected PASSED [ 75%]
tests/phase2/test_runtime.py::TestUniverseParsing::test_malformed_json_is_rejected_with_the_variable_named PASSED [ 75%]
tests/phase2/test_runtime.py::TestUniverseParsing::test_missing_configuration_refuses_rather_than_defaulting PASSED [ 75%]
tests/phase2/test_runtime.py::TestPreflight::test_preflight_fails_when_a_dependency_is_unreachable FAILED [ 76%]
tests/phase2/test_runtime.py::TestPreflight::test_preflight_reports_every_failure_not_just_the_first PASSED [ 76%]
tests/phase2/test_runtime.py::TestPreflight::test_the_secret_bearing_url_is_never_in_the_failure_message PASSED [ 77%]
tests/phase2/test_runtime.py::TestAnchoring::test_a_failed_anchor_degrades_rather_than_refusing_the_session PASSED [ 77%]
tests/phase2/test_runtime.py::TestAnchoring::test_anchor_fetches_and_persists_every_bound_expiry PASSED [ 77%]
tests/phase2/test_runtime.py::TestShutdown::test_shutdown_is_idempotent PASSED [ 78%]
tests/phase2/test_runtime.py::TestShutdown::test_shutdown_stops_the_collector_and_closes_the_session PASSED [ 78%]
tests/phase2/test_runtime.py::TestSessionPredicate::test_the_predicate_is_replaceable PASSED [ 79%]
tests/phase2/test_runtime.py::TestSessionPredicate::test_the_window_is_closed_at_the_weekend PASSED [ 79%]
tests/phase2/test_runtime.py::TestSessionPredicate::test_the_window_is_closed_outside_market_hours PASSED [ 80%]
tests/phase2/test_runtime.py::TestSessionPredicate::test_the_window_is_open_during_market_hours PASSED [ 80%]
tests/phase2/test_runtime.py::TestReadinessProbes::test_a_missing_package_fails_the_runtime_probe_by_name FAILED [ 80%]
tests/phase2/test_runtime.py::TestReadinessProbes::test_an_unknown_role_is_a_failure_not_a_silent_pass PASSED [ 81%]
tests/phase2/test_runtime.py::TestReadinessProbes::test_every_role_declares_its_local_requirements PASSED [ 81%]
tests/phase2/test_runtime.py::TestReadinessProbes::test_provider_availability_is_not_a_readiness_dependency PASSED [ 82%]
tests/phase2/test_runtime.py::TestEntrypointDispatch::test_a_missing_universe_exits_as_a_configuration_error PASSED [ 82%]
tests/phase2/test_runtime.py::TestEntrypointDispatch::test_ingestor_is_not_listed_as_an_unimplemented_role PASSED [ 83%]
tests/phase2/test_runtime.py::TestEntrypointDispatch::test_the_entrypoint_dispatches_the_ingestor_role PASSED [ 83%]
tests/phase2/test_upstox_v3.py::TestDecoderFailsClosed::test_constructing_without_a_decoder_is_refused PASSED [ 83%]
tests/phase2/test_upstox_v3.py::TestDecoderFailsClosed::test_no_protobuf_schema_is_invented_anywhere PASSED [ 84%]
tests/phase2/test_upstox_v3.py::TestDecoderFailsClosed::test_the_refusal_happens_before_any_connection PASSED [ 84%]
tests/phase2/test_upstox_v3.py::TestDecoderFailsClosed::test_the_v3_module_contains_no_json_frame_parsing PASSED [ 85%]
tests/phase2/test_upstox_v3.py::TestNoSynthesizedProviderIdentity::test_a_local_counter_is_never_promoted_to_provider_identity PASSED [ 85%]
tests/phase2/test_upstox_v3.py::TestNoSynthesizedProviderIdentity::test_absent_provider_fields_resolve_to_the_derived_digest PASSED [ 86%]
tests/phase2/test_upstox_v3.py::TestNoSynthesizedProviderIdentity::test_an_explicitly_named_provider_field_is_still_honoured PASSED [ 86%]
tests/phase2/test_upstox_v3.py::TestNoSynthesizedProviderIdentity::test_hint_extraction_no_longer_guesses_key_names PASSED [ 86%]
tests/phase2/test_upstox_v3.py::TestNoSynthesizedProviderIdentity::test_no_provider_gap_detection_is_claimed_without_a_provider_sequence PASSED [ 87%]
tests/phase2/test_upstox_v3.py::TestNoSynthesizedProviderIdentity::test_the_derived_digest_is_not_labelled_a_provider_event_id PASSED [ 87%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_a_decoded_message_carries_no_provider_identity_attributes PASSED [ 88%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_a_missing_uri_raises_rather_than_returning_empty PASSED [ 88%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_absent_fields_stay_absent PASSED [ 88%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_an_empty_subscription_is_refused PASSED [ 89%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_market_info_frames_are_not_market_data PASSED [ 89%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_multiple_expiries_are_carried_as_distinct_instrument_keys PASSED [ 90%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_no_provider_limit_is_hardcoded_in_the_adapter PASSED [ 90%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_the_authorize_path_is_the_v3_market_data_endpoint PASSED [ 91%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_the_authorized_uri_is_extracted_from_both_documented_shapes PASSED [ 91%]
tests/phase2/test_upstox_v3.py::TestV3Lifecycle::test_the_subscribe_request_names_mode_and_instruments PASSED [ 91%]
tests/phase2/test_upstox_v3.py::TestV2IsNotTheProductionPath::test_the_ingestor_runtime_does_not_wire_the_v2_client PASSED [ 92%]
tests/phase2/test_upstox_v3.py::TestV2IsNotTheProductionPath::test_the_v2_module_is_marked_not_production PASSED [ 92%]
tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_every_capture_has_a_complete_manifest PASSED [ 93%]
tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_synthetic_fixtures_are_never_placed_under_recorded PASSED [ 93%]
tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_recorded_directory_exists_and_is_documented FAILED [ 94%]
tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_required_capture_set_is_recorded_as_outstanding SKIPPEDial/snapshot,
LTPC, full/Greeks, multiple instruments, multiple expiries, two feed
sessions. Blocked on network access to api.upstox.com, OAuth credentials
and a live market session, none of which exist in this environment.
Phase 2 cannot pass until captured.)                                     [ 94%]
tests/phase2/test_upstox_v3.py::TestIngestorRefusesWithoutADecoder::test_build_v3_feed_client_refuses_and_explains PASSED [ 94%]
tests/phase2/test_upstox_v3.py::TestIngestorRefusesWithoutADecoder::test_the_decoder_injection_point_returns_none_rather_than_a_guess PASSED [ 95%]
tests/phase2/test_upstox_v3.py::TestIngestorRefusesWithoutADecoder::test_the_ingestor_exits_with_a_distinct_status PASSED [ 95%]
tests/phase2/test_upstox_v3.py::TestGapTaxonomy::test_a_connectivity_gap_produces_a_rest_recovery_plan PASSED [ 96%]
tests/phase2/test_upstox_v3.py::TestGapTaxonomy::test_a_reconnect_is_recorded_as_a_connectivity_gap PASSED [ 96%]
tests/phase2/test_upstox_v3.py::TestGapTaxonomy::test_a_weak_identity_never_raises_a_provider_sequence_gap PASSED [ 97%]
tests/phase2/test_upstox_v3.py::TestGapTaxonomy::test_staleness_uses_a_defined_threshold_not_bare_elapsed_time PASSED [ 97%]
tests/phase2/test_ws_soak_offline.py::TestWebSocketSoakOffline::test_cross_session_event_ids_do_not_collide PASSED [ 97%]
tests/phase2/test_ws_soak_offline.py::TestWebSocketSoakOffline::test_real_duplicates_are_still_suppressed PASSED [ 98%]
tests/phase2/test_ws_soak_offline.py::TestWebSocketSoakOffline::test_sequence_restart_after_reconnect_is_not_a_gap PASSED [ 98%]
tests/phase2/test_ws_soak_offline.py::TestWebSocketSoakOffline::test_soak_is_deterministic PASSED [ 99%]
tests/phase2/test_ws_soak_offline.py::TestWebSocketSoakOffline::test_soak_with_provider_identity PASSED [ 99%]
tests/phase2/test_ws_soak_offline.py::TestWebSocketSoakOffline::test_soak_without_provider_identity PASSED [100%]

=================================== FAILURES ===================================
_____ TestPreflight.test_preflight_fails_when_a_dependency_is_unreachable ______

self = <tests.phase2.test_runtime.TestPreflight testMethod=test_preflight_fails_when_a_dependency_is_unreachable>

    def test_preflight_fails_when_a_dependency_is_unreachable(self):
        runtime, _ = _runtime()
        with self.assertRaises(PreflightFailed) as ctx:
            asyncio.run(runtime.preflight())
        message = str(ctx.exception)
        # No PostgreSQL, Redis or driver exists in this sandbox, so every probe fails.
        self.assertIn("postgres", message)
>       self.assertIn("redis", message)
E       AssertionError: 'redis' not found in 'ingestor cannot start: postgres: InvalidPasswordError: password authentication failed for user "u"'

tests/phase2/test_runtime.py:200: AssertionError
------------------------------ Captured log call -------------------------------
ERROR    oipulse.marketdata.runtime:runtime.py:215 ingestor_preflight_failed
__ TestReadinessProbes.test_a_missing_package_fails_the_runtime_probe_by_name __

self = <tests.phase2.test_runtime.TestReadinessProbes testMethod=test_a_missing_package_fails_the_runtime_probe_by_name>

    def test_a_missing_package_fails_the_runtime_probe_by_name(self):
        result = check_runtime_dependencies("ingestor")
        self.assertIsInstance(result, ProbeResult)
        # The sandbox has no sqlalchemy/asyncpg/redis, so this is the genuine failure.
>       self.assertFalse(result.ok)
E       AssertionError: True is not false

tests/phase2/test_runtime.py:293: AssertionError
_ TestRecordedFixtureContract.test_the_recorded_directory_exists_and_is_documented _

self = <tests.phase2.test_upstox_v3.TestRecordedFixtureContract testMethod=test_the_recorded_directory_exists_and_is_documented>

    def test_the_recorded_directory_exists_and_is_documented(self):
>       self.assertTrue(RECORDED.is_dir())
E       AssertionError: False is not true

tests/phase2/test_upstox_v3.py:258: AssertionError
=========================== short test summary info ============================
FAILED tests/phase2/test_runtime.py::TestPreflight::test_preflight_fails_when_a_dependency_is_unreachable - AssertionError: 'redis' not found in 'ingestor cannot start: postgres: InvalidPasswordError: password authentication failed for user "u"'
FAILED tests/phase2/test_runtime.py::TestReadinessProbes::test_a_missing_package_fails_the_runtime_probe_by_name - AssertionError: True is not false
FAILED tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_recorded_directory_exists_and_is_documented - AssertionError: False is not true
================== 3 failed, 232 passed, 1 skipped in 15.23s ===================
```

### Failing tests

1. `tests/phase2/test_runtime.py::TestPreflight::test_preflight_fails_when_a_dependency_is_unreachable`
   - File and line: `tests/phase2/test_runtime.py:200`
   - Assertion: `self.assertIn("redis", message)`
   - Diff: `AssertionError: 'redis' not found in 'ingestor cannot start: postgres: InvalidPasswordError: password authentication failed for user "u"'`
   - Captured log: `ERROR oipulse.marketdata.runtime:runtime.py:215 ingestor_preflight_failed`
   - The postgres probe reached a real server and failed authentication for user `u`. The assertion that `redis` also appears in the message never ran as a success.

2. `tests/phase2/test_runtime.py::TestReadinessProbes::test_a_missing_package_fails_the_runtime_probe_by_name`
   - File and line: `tests/phase2/test_runtime.py:293`
   - Assertion: `self.assertFalse(result.ok)`
   - Diff: `AssertionError: True is not false`
   - `check_runtime_dependencies("ingestor")` returned `ok=True` because sqlalchemy, asyncpg, and redis are installed. The test comment expects those packages to be absent.

3. `tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_recorded_directory_exists_and_is_documented`
   - File and line: `tests/phase2/test_upstox_v3.py:258`
   - Assertion: `self.assertTrue(RECORDED.is_dir())`
   - `RECORDED` is `tests/fixtures/recorded/upstox_v3`
   - Diff: `AssertionError: False is not true`
   - The directory does not exist, so the following `README.md` assertion was not reached.

### Skipped test

`tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_required_capture_set_is_recorded_as_outstanding`

`skipTest` because `tests/fixtures/recorded/upstox_v3` has no `*.bin` captures. The skip message is:

```text
OUTSTANDING: no recorded Upstox V3 frames. Required: market_info, initial/snapshot, LTPC, full/Greeks, multiple instruments, multiple expiries, two feed sessions. Blocked on network access to api.upstox.com, OAuth credentials and a live market session, none of which exist in this environment. Phase 2 cannot pass until captured.
```

The verbose listing wrapped that message across the progress line. The text above is the string in `tests/phase2/test_upstox_v3.py`.

## pytest -q

Exit code 1. `3 failed, 232 passed, 1 skipped in 14.00s`.

```text
/private/tmp/oipulse-phase1-venv/lib/python3.12/site-packages/pytest_asyncio/plugin.py:208: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture caching scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to function scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session"

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [ 30%]
........................................................................ [ 61%]
...................................F..........F......................... [ 91%]
.....Fs.............                                                     [100%]
=================================== FAILURES ===================================
_____ TestPreflight.test_preflight_fails_when_a_dependency_is_unreachable ______

self = <tests.phase2.test_runtime.TestPreflight testMethod=test_preflight_fails_when_a_dependency_is_unreachable>

    def test_preflight_fails_when_a_dependency_is_unreachable(self):
        runtime, _ = _runtime()
        with self.assertRaises(PreflightFailed) as ctx:
            asyncio.run(runtime.preflight())
        message = str(ctx.exception)
        # No PostgreSQL, Redis or driver exists in this sandbox, so every probe fails.
        self.assertIn("postgres", message)
>       self.assertIn("redis", message)
E       AssertionError: 'redis' not found in 'ingestor cannot start: postgres: InvalidPasswordError: password authentication failed for user "u"'

tests/phase2/test_runtime.py:200: AssertionError
------------------------------ Captured log call -------------------------------
ERROR    oipulse.marketdata.runtime:runtime.py:215 ingestor_preflight_failed
__ TestReadinessProbes.test_a_missing_package_fails_the_runtime_probe_by_name __

self = <tests.phase2.test_runtime.TestReadinessProbes testMethod=test_a_missing_package_fails_the_runtime_probe_by_name>

    def test_a_missing_package_fails_the_runtime_probe_by_name(self):
        result = check_runtime_dependencies("ingestor")
        self.assertIsInstance(result, ProbeResult)
        # The sandbox has no sqlalchemy/asyncpg/redis, so this is the genuine failure.
>       self.assertFalse(result.ok)
E       AssertionError: True is not false

tests/phase2/test_runtime.py:293: AssertionError
_ TestRecordedFixtureContract.test_the_recorded_directory_exists_and_is_documented _

self = <tests.phase2.test_upstox_v3.TestRecordedFixtureContract testMethod=test_the_recorded_directory_exists_and_is_documented>

    def test_the_recorded_directory_exists_and_is_documented(self):
>       self.assertTrue(RECORDED.is_dir())
E       AssertionError: False is not true

tests/phase2/test_upstox_v3.py:258: AssertionError
=========================== short test summary info ============================
FAILED tests/phase2/test_runtime.py::TestPreflight::test_preflight_fails_when_a_dependency_is_unreachable
FAILED tests/phase2/test_runtime.py::TestReadinessProbes::test_a_missing_package_fails_the_runtime_probe_by_name
FAILED tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_recorded_directory_exists_and_is_documented
3 failed, 232 passed, 1 skipped in 14.00s
```

## mypy --strict oipulse --show-error-codes

Exit code 1. `Found 23 errors in 6 files (checked 50 source files)`.

```text
oipulse/marketdata/providers/upstox/ws.py:150: error: Returning Any from function declared to return "str"  [no-any-return]
oipulse/marketdata/providers/upstox/ws.py:207: error: Statement is unreachable  [unreachable]
oipulse/observability/readiness.py:135: error: Call to untyped function "from_url" in typed context  [no-untyped-call]
oipulse/marketdata/store/schema.py:49: error: Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:50: error: Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:52: error: Argument 2 to "Column" has incompatible type "DateTime"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:53: error: Argument 2 to "Column" has incompatible type "DateTime"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:55: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:56: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:57: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:58: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:59: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:60: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:61: error: Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:62: error: Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:63: error: Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/schema.py:65: error: Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] | TypeEngine[object] | SchemaEventTarget | None"  [arg-type]
oipulse/marketdata/store/postgres.py:133: error: Incompatible types in assignment (expression has type "ReturningInsert[Any]", variable has type "Insert")  [assignment]
oipulse/marketdata/providers/upstox/rest.py:159: error: Returning Any from function declared to return "dict[str, Any]"  [no-any-return]
oipulse/marketdata/runtime.py:222: error: Argument 1 to "tuple" has incompatible type "range"; expected "Iterable[InstrumentId]"  [arg-type]
oipulse/marketdata/runtime.py:222: note: Following member(s) of "range" have conflicts:
oipulse/marketdata/runtime.py:222: note:     Expected:
oipulse/marketdata/runtime.py:222: note:         def __iter__(self) -> Iterator[InstrumentId]
oipulse/marketdata/runtime.py:222: note:     Got:
oipulse/marketdata/runtime.py:222: note:         def __iter__(self) -> Iterator[int]
oipulse/marketdata/runtime.py:320: error: Cannot infer type of lambda  [misc]
oipulse/marketdata/runtime.py:353: error: Argument 1 to "UpstoxV3FeedClient" has incompatible type "object"; expected "RestAuthorizer"  [arg-type]
oipulse/marketdata/runtime.py:508: error: Argument 5 to "UpstoxMarketDataProvider" has incompatible type "UpstoxV3FeedClient"; expected "WsTransport | None"  [arg-type]
Found 23 errors in 6 files (checked 50 source files)
```

| File | Line | Code | Message |
| --- | --- | --- | --- |
| `oipulse/marketdata/providers/upstox/ws.py` | 150 | `no-any-return` | Returning Any from function declared to return "str" |
| `oipulse/marketdata/providers/upstox/ws.py` | 207 | `unreachable` | Statement is unreachable |
| `oipulse/observability/readiness.py` | 135 | `no-untyped-call` | Call to untyped function "from_url" in typed context |
| `oipulse/marketdata/store/schema.py` | 49 | `arg-type` | Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 50 | `arg-type` | Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 52 | `arg-type` | Argument 2 to "Column" has incompatible type "DateTime"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 53 | `arg-type` | Argument 2 to "Column" has incompatible type "DateTime"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 55 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 56 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 57 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 58 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 59 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 60 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 61 | `arg-type` | Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 62 | `arg-type` | Argument 2 to "Column" has incompatible type "type[Text]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 63 | `arg-type` | Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/schema.py` | 65 | `arg-type` | Argument 2 to "Column" has incompatible type "type[BigInteger]"; expected "type[TypeEngine[object]] \| TypeEngine[object] \| SchemaEventTarget \| None" |
| `oipulse/marketdata/store/postgres.py` | 133 | `assignment` | Incompatible types in assignment (expression has type "ReturningInsert[Any]", variable has type "Insert") |
| `oipulse/marketdata/providers/upstox/rest.py` | 159 | `no-any-return` | Returning Any from function declared to return "dict[str, Any]" |
| `oipulse/marketdata/runtime.py` | 222 | `arg-type` | Argument 1 to "tuple" has incompatible type "range"; expected "Iterable[InstrumentId]" |
| `oipulse/marketdata/runtime.py` | 320 | `misc` | Cannot infer type of lambda |
| `oipulse/marketdata/runtime.py` | 353 | `arg-type` | Argument 1 to "UpstoxV3FeedClient" has incompatible type "object"; expected "RestAuthorizer" |
| `oipulse/marketdata/runtime.py` | 508 | `arg-type` | Argument 5 to "UpstoxMarketDataProvider" has incompatible type "UpstoxV3FeedClient"; expected "WsTransport \| None" |

The two `runtime.py:222` notes are attached to the `range` / `InstrumentId` error. They are not additional errors. mypy's summary is 23 errors.

## Passing checks

| Command | Exit | Output |
| --- | --- | --- |
| `ruff check oipulse tools tests` | 0 | All checks passed! |
| `ruff format --check oipulse tools tests` | 0 | 75 files already formatted |
| `python -m compileall -q oipulse tools tests` | 0 | no output |
| `python tools/check_clock_access.py oipulse` | 0 | PASS  no wall-clock access outside oipulse/core/clock.py |
| `python tools/check_import_boundaries.py` | 0 | PASS  layer boundaries clean (4 contracts armed: analytics-is-pure, nothing-imports-api, risk-is-independent, core-is-dependency-free) |
| `python tools/check_temporal_repository.py oipulse` | 0 | PASS  every repository read accepts a temporal bound |

## Upstox V3 decoder boundary

`import oipulse.marketdata.providers.upstox.v3` succeeds. The module exports `ProtoDecoderUnavailable`.

There is no production Protobuf decoder. `ProtoFrameDecoder` is a Protocol. `UpstoxV3FeedClient.__init__` raises `ProtoDecoderUnavailable` when `decoder is None`, before any socket is opened. The exact message is:

```text
UpstoxV3FeedClient requires a Protobuf decoder built from the official Upstox V3 .proto definition. None is bundled: the definition is provider-owned and was not available when this adapter was written. Supply one rather than falling back to JSON -- V3 frames are binary, and parsing them as JSON yields wrong values, not an error.
```

`protobuf` is not installed in this environment. This run did not add a decoder and did not change the V3 boundary.

## Confirmation

Source files were not modified, reformatted, or fixed. The only file added by this run is this report.
