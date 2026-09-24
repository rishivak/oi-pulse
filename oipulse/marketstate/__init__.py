"""Layer 3 — MarketState.

`docs/design/04-MARKETSTATE.md`. An immutable, deterministically-assembled representation
of one underlying's market picture at a market time, under an explicit knowledge horizon
and an explicit assembly configuration.

This layer reads observations and reorganises them. It computes **no** analytics: a
surface is a reorganisation of observed values with no formula applied, and anything
requiring a formula is a Phase 4 `MetricValue` with its own `feature_version` and
`available_at` (`04` §6).
"""
