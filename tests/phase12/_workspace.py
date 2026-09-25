"""A disposable copy of the tree, for mutation-testing the Phase 12 guards.

A guard that has never failed is a guard nobody has tested. Each mutation test
copies the parts of the repository a guard reads, breaks exactly one thing, and
asserts the guard reports it — which is the only way to know the check is wired to
the code and not merely to a happy path.

Only `frontend/` and `oipulse/` are copied. `tools/` is imported from the real tree:
the guards take the root as an argument, so the code under test is the committed
code rather than a copy that might drift from it.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: `.claude` and `.mcp.json` are session tooling artifacts the sandbox makes
#: unreadable, and `shutil.copytree` raises on them rather than skipping. Excluded
#: by name; everything the guards read is source under the three copied trees.
_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "node_modules",
    ".next",
    ".ruff_cache",
    ".claude",
    ".mcp.json",
    ".git",
)


@contextmanager
def workspace() -> Iterator[Path]:
    """Yield a temporary root containing `frontend/` and `oipulse/`."""
    with tempfile.TemporaryDirectory(prefix="phase12-guard-") as raw:
        root = Path(raw)
        for name in ("frontend", "oipulse", "tools"):
            shutil.copytree(REPO / name, root / name, ignore=_IGNORE)
        yield root


def edit(path: Path, old: str, new: str) -> None:
    """Replace `old` with `new`, failing loudly if the anchor has moved.

    A mutation test whose target string no longer exists silently stops mutating
    anything and then passes, reporting that a guard caught a problem that was
    never introduced. This raises instead.
    """
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(
            f"mutation anchor not found in {path.name}: {old!r}. The file changed and "
            f"this test would otherwise pass without mutating anything."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
