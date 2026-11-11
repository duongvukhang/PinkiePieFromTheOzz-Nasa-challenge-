"""Shared fixtures and API guard helpers for science acceptance tests."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

REF_VALUES_PATH = Path(__file__).with_name("reference_values.json")


def load_reference_values() -> dict[str, Any]:
    """Load pinned catalog and literature reference values from JSON."""
    if not REF_VALUES_PATH.is_file():
        pytest.fail(f"Missing pinned reference values file: {REF_VALUES_PATH}", pytrace=False)
    return json.loads(REF_VALUES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def reference_values() -> dict[str, Any]:
    """Session-scoped pinned reference values dictionary."""
    return load_reference_values()


def require_science_api(module_path: str, symbol: str, criterion: str) -> Any:
    """Import a `sphx_core` symbol or fail clearly with the missing acceptance criterion."""
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        pytest.fail(
            f"NOT IMPLEMENTED: `{module_path}.{symbol}` cannot be imported ({exc}); "
            f"required for [{criterion}].",
            pytrace=False,
        )
    target = getattr(module, symbol, None)
    if target is None:
        pytest.fail(
            f"NOT IMPLEMENTED: `{module_path}.{symbol}` is missing; required for [{criterion}].",
            pytrace=False,
        )
    return target
