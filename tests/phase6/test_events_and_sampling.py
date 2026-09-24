"""Event detection, sampling policy and study windows.

Covers brief requirements 1-10 and 22-24: definition identity, deterministic
detection and sampling, overlap policy, minimum separation, clustering, window
boundaries, incomplete windows, quality gating and missing-data behaviour.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketstate.staleness import QualityStatus
from oipulse.research.access import PointInTimeAccessor
from oipulse.research.events import (
    EVENT_DEFINITIONS,
    DuplicateEventDefinition,
    EventDefinitionRegistry,
    EventKind,
    detect_over,
)
from oipulse.research.sampling import (
    ClusterMethod,
    EventSamplingPolicy,
    OverlapPolicy,
    SamplingPolicy,
    apply_sampling,
)
from tests.phase6._fixtures import at, metric, surge_definition


def _metrics(minutes, value="120", quality=QualityStatus.OK):
    return tuple(
        metric(
            value=Decimal(value),
            observed_at=at(m),
            knowledge_horizon=at(m),
            available_at=at(m, 2),
            quality=quality,
            digest=f"d{m}",
        )
        for m in minutes
    )


def _events(minutes, **kwargs):
    metrics = _metrics(minutes, **kwargs)
    accessor = PointInTimeAccessor(metrics=metrics)
    return detect_over(accessor, surge_definition(), [m.available_at for m in metrics])


class TestEventDefinitionIdentity(unittest.TestCase):
    """Requirements 1-2 and 8: definitions are versioned and content-addressed."""

    def test_the_same_definition_has_the_same_digest(self):
        self.assertEqual(surge_definition().content_digest, surge_definition().content_digest)

    def test_a_threshold_change_changes_the_digest(self):
        """A silent threshold edit must not redefine a stored study's meaning."""
        self.assertNotEqual(
            surge_definition(threshold="100").content_digest,
            surge_definition(threshold="150").content_digest,
        )

    def test_a_version_bump_changes_the_digest(self):
        self.assertNotEqual(
            surge_definition(version=1).content_digest,
            surge_definition(version=2).content_digest,
        )

    def test_registering_a_duplicate_version_is_refused(self):
        registry = EventDefinitionRegistry()
        registry.register(surge_definition())
        with self.assertRaises(DuplicateEventDefinition):
            registry.register(surge_definition())

    def test_versions_coexist(self):
        registry = EventDefinitionRegistry()
        registry.register(surge_definition(version=1))
        registry.register(surge_definition(version=2, threshold="150"))
        self.assertEqual(len(registry), 2)

    def test_the_event_kinds_are_the_documented_two(self):
        """Requirement 8: no event type beyond the authoritative specification."""
        self.assertEqual(
            {k.value for k in EventKind},
            {"signal_transition", "feature_threshold_crossing"},
        )

    def test_the_global_registry_starts_empty(self):
        """Event definitions are supplied by a study, not preloaded."""
        self.assertEqual(len(EVENT_DEFINITIONS), 0)


class TestDeterministicDetection(unittest.TestCase):
    """Requirement 3: detection is deterministic and refuses look-ahead."""

    def test_a_crossing_is_detected(self):
        self.assertEqual(len(_events([0])), 1)

    def test_a_non_crossing_is_not(self):
        self.assertEqual(len(_events([0], value="50")), 0)

    def test_detection_is_repeatable(self):
        self.assertEqual(
            [e.occurrence_id for e in _events([0, 5, 10])],
            [e.occurrence_id for e in _events([0, 5, 10])],
        )

    def test_scan_order_does_not_change_the_result(self):
        metrics = _metrics([0, 5, 10])
        accessor = PointInTimeAccessor(metrics=metrics)
        forward = detect_over(accessor, surge_definition(), [m.available_at for m in metrics])
        reverse = detect_over(
            accessor, surge_definition(), list(reversed([m.available_at for m in metrics]))
        )
        self.assertEqual([e.occurrence_id for e in forward], [e.occurrence_id for e in reverse])

    def test_one_condition_across_several_scans_is_one_event(self):
        """Deduplicated by occurrence identity before any sampling policy applies."""
        metrics = _metrics([0])
        accessor = PointInTimeAccessor(metrics=metrics)
        events = detect_over(accessor, surge_definition(), [at(1), at(2), at(3), at(4)])
        self.assertEqual(len(events), 1)

    def test_an_unreliable_value_is_excluded_by_default(self):
        self.assertEqual(len(_events([0], quality=QualityStatus.UNRELIABLE)), 0)

    def test_an_unreliable_value_is_included_when_the_definition_opts_in(self):
        metrics = _metrics([0], quality=QualityStatus.UNRELIABLE)
        accessor = PointInTimeAccessor(metrics=metrics)
        events = detect_over(
            accessor,
            surge_definition(exclude_unreliable=False),
            [m.available_at for m in metrics],
        )
        self.assertEqual(len(events), 1)

    def test_an_event_can_never_precede_its_market_time(self):
        for event in _events([0, 5]):
            self.assertGreaterEqual(event.available_at, event.market_time)

    def test_the_occurrence_id_excludes_detection_time(self):
        """When detection ran is execution metadata, not part of what the event is."""
        metrics = _metrics([0])
        accessor = PointInTimeAccessor(metrics=metrics)
        early = detect_over(accessor, surge_definition(), [at(1)])[0]
        late = detect_over(accessor, surge_definition(), [at(500)])[0]
        self.assertEqual(early.occurrence_id, late.occurrence_id)
        self.assertNotEqual(early.detected_at, late.detected_at)

    def test_an_event_carries_resolvable_evidence(self):
        """Requirement 13: the reference resolves back to the metric row."""
        event = _events([0])[0]
        self.assertTrue(event.evidence_refs)
        self.assertIn("PUT_OI_MIGRATION@v1", event.evidence_refs[0])


class TestSamplingPolicy(unittest.TestCase):
    """Requirements 4-7 and `09` §3's overlap problem."""

    def setUp(self):
        # The design's own example: four events five minutes apart sharing most of a
        # 30-minute forward window. Raw n = 4, independent information about 1.
        self.events = _events([0, 5, 10, 15])
        self.thirty = timedelta(minutes=30)

    def test_both_counts_are_always_reported(self):
        result = apply_sampling(self.events, SamplingPolicy(minimum_event_separation=self.thirty))
        self.assertEqual(result.raw_count, 4)
        self.assertEqual(result.cluster_count, 1)
        self.assertEqual(result.effective_sample, 1)
        self.assertEqual(result.mean_overlap, Decimal(4))

    def test_first_per_cluster_is_the_conservative_default(self):
        policy = SamplingPolicy()
        self.assertIs(policy.event_sampling_policy, EventSamplingPolicy.FIRST_PER_CLUSTER)

    def test_first_per_cluster_selects_the_earliest(self):
        result = apply_sampling(
            self.events,
            SamplingPolicy(
                event_sampling_policy=EventSamplingPolicy.FIRST_PER_CLUSTER,
                minimum_event_separation=self.thirty,
            ),
        )
        self.assertEqual(len(result.selected), 1)
        self.assertEqual(result.selected[0].market_time, at(0))

    def test_all_keeps_every_event_but_still_reports_the_effective_sample(self):
        result = apply_sampling(
            self.events,
            SamplingPolicy(
                event_sampling_policy=EventSamplingPolicy.ALL,
                overlap_policy=OverlapPolicy.ALLOW,
                minimum_event_separation=self.thirty,
            ),
        )
        self.assertEqual(len(result.selected), 4)
        self.assertEqual(result.cluster_count, 1, "overlap is still known and reported")

    def test_decorrelated_enforces_the_minimum_separation(self):
        events = _events([0, 5, 10, 40, 45, 80])
        result = apply_sampling(
            events,
            SamplingPolicy(
                event_sampling_policy=EventSamplingPolicy.DECORRELATED,
                minimum_event_separation=self.thirty,
            ),
        )
        times = [e.market_time for e in result.selected]
        self.assertEqual(times, [at(0), at(40), at(80)])
        self.assertEqual(result.dropped_separation, 3)

    def test_the_separation_defaults_to_the_longest_horizon(self):
        """`09` §3: forward windows cannot overlap unless the researcher opts in."""
        policy = SamplingPolicy.for_horizons(
            (timedelta(minutes=5), timedelta(minutes=30), timedelta(minutes=15))
        )
        self.assertEqual(policy.minimum_event_separation, timedelta(minutes=30))

    def test_events_exactly_one_separation_apart_are_separate_clusters(self):
        """A boundary case: the gap is not less than the separation, so it breaks."""
        events = _events([0, 30])
        result = apply_sampling(events, SamplingPolicy(minimum_event_separation=self.thirty))
        self.assertEqual(result.cluster_count, 2)

    def test_events_inside_the_separation_are_one_cluster(self):
        events = _events([0, 29])
        result = apply_sampling(events, SamplingPolicy(minimum_event_separation=self.thirty))
        self.assertEqual(result.cluster_count, 1)

    def test_simultaneous_events_cluster_together(self):
        events = _events([0])
        doubled = (*events, *events)
        result = apply_sampling(doubled, SamplingPolicy(minimum_event_separation=self.thirty))
        self.assertEqual(result.raw_count, 2)
        self.assertEqual(result.cluster_count, 1)

    def test_sampling_is_deterministic_under_reordering(self):
        """Requirement 20: reordered inputs must select identically."""
        policy = SamplingPolicy(minimum_event_separation=self.thirty)
        forward = apply_sampling(self.events, policy)
        reverse = apply_sampling(tuple(reversed(self.events)), policy)
        self.assertEqual(
            [e.occurrence_id for e in forward.selected],
            [e.occurrence_id for e in reverse.selected],
        )

    def test_signal_instance_clustering_groups_by_evidence(self):
        result = apply_sampling(
            self.events,
            SamplingPolicy(
                cluster_method=ClusterMethod.SIGNAL_INSTANCE,
                minimum_event_separation=self.thirty,
            ),
        )
        # Each synthetic metric carries a distinct digest, so each is its own instance.
        self.assertEqual(result.cluster_count, 4)

    def test_the_policy_is_content_addressed(self):
        a = SamplingPolicy(minimum_event_separation=self.thirty)
        b = SamplingPolicy(minimum_event_separation=timedelta(minutes=60))
        self.assertEqual(
            a.content_digest, SamplingPolicy(minimum_event_separation=self.thirty).content_digest
        )
        self.assertNotEqual(a.content_digest, b.content_digest)

    def test_an_empty_stream_reports_zero_not_an_error(self):
        result = apply_sampling((), SamplingPolicy())
        self.assertEqual(result.raw_count, 0)
        self.assertEqual(result.effective_sample, 0)
        self.assertEqual(result.mean_overlap, Decimal(0))


if __name__ == "__main__":
    unittest.main()
