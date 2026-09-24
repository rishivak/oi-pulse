"""Integration tests that need real infrastructure.

Every module here skips cleanly when the service it needs is absent, so the suite stays
runnable on a bare interpreter. A skip is a **reported gap**, not a pass: the phase
report lists which of these ran and which did not.
"""
