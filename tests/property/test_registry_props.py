"""Property 2: Registry Register-Then-Get Round-Trip.

Feature: pluggable-analysis-pipeline, Property 2: Registry Register-Then-Get Round-Trip

For any component object and any valid registry key (including
analyzer.*, judge.*, scorer, suite_store), registering the component
with that key and then calling get() with the same key should return
the exact same object (identity check with `is`).

Validates: Requirements 1.4, 2.4, 4.5, 8.6
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.registry import Registry


# ---------------------------------------------------------------------------
# Hypothesis strategies for valid registry keys
# ---------------------------------------------------------------------------

_name_suffix = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=20,
)

st_analyzer_key = _name_suffix.map(lambda s: f"analyzer.{s}")
st_judge_key = _name_suffix.map(lambda s: f"judge.{s}")
st_fixed_keys = st.sampled_from(["scorer", "suite_store"])

st_registry_key = st.one_of(st_analyzer_key, st_judge_key, st_fixed_keys)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Ensure a clean registry for every test."""
    Registry.clear()


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(key=st_registry_key)
@settings(max_examples=20)
def test_register_then_get_returns_same_object(key: str) -> None:
    """Registering a component then getting it returns the same object.

    **Validates: Requirements 1.4, 2.4, 4.5, 8.6**
    """
    Registry.clear()
    component = object()
    Registry.register(key, component)
    retrieved = Registry.get(key)
    assert retrieved is component


# ---------------------------------------------------------------------------
# Strategies for Property 7: mixed legacy + pipeline keys
# ---------------------------------------------------------------------------

st_legacy_key = st.sampled_from(["processor", "checker", "data_source", "reporter", "scorer"])
st_pipeline_key = st.one_of(
    _name_suffix.map(lambda s: f"analyzer.{s}"),
    _name_suffix.map(lambda s: f"judge.{s}"),
    st.just("suite_store"),
)


@given(
    legacy_keys=st.lists(st_legacy_key, min_size=1, max_size=5, unique=True),
    pipeline_keys=st.lists(st_pipeline_key, min_size=1, max_size=5, unique=True),
)
@settings(max_examples=20)
def test_registry_backward_compatibility_with_mixed_keys(
    legacy_keys: list[str],
    pipeline_keys: list[str],
) -> None:
    """Legacy and new pipeline keys coexist and resolve independently.

    Feature: pluggable-analysis-pipeline, Property 7: Registry Backward Compatibility with Mixed Keys

    **Validates: Requirements 14.2, 14.3**
    """
    Registry.clear()

    # Create a unique component per key
    components: dict[str, object] = {}
    for key in legacy_keys + pipeline_keys:
        comp = object()
        components[key] = comp
        Registry.register(key, comp)

    # Each key resolves to its own registered component independently
    for key, expected in components.items():
        assert Registry.get(key) is expected, f"Registry.get({key!r}) returned wrong object"

    # keys() contains all registered keys
    registered = set(Registry.keys())
    for key in components:
        assert key in registered, f"{key!r} missing from Registry.keys()"
