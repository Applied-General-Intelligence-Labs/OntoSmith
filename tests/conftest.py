"""Test bootstrap: the staging root is the import root.

The pipeline package is imported as ``pipeline`` (its public
layout name); tests run from a bare checkout with no install
step, so the repo root goes on sys.path here.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_ambient_spend_cap(monkeypatch):
    """The batch spend cap lives in ONTOSMITH_MAX_COST_USD; a
    value exported in the developer's shell or CI must not
    leak into tests. Tests that want a cap set it themselves."""
    monkeypatch.delenv("ONTOSMITH_MAX_COST_USD", raising=False)
