"""Tests for codeassist.custom_tools_loader — the process-wide singleton and
trust-registry propagation (§8 backlog)."""

import pytest

from codeassist import custom_tools_loader as ctl


@pytest.fixture
def isolated_singleton():
    """The registry is a process-wide singleton; reset it before each test and
    restore the original after, so one test's priming cannot leak into another."""
    original = ctl._custom_tool_registry
    ctl._custom_tool_registry = None
    try:
        yield
    finally:
        ctl._custom_tool_registry = original


def _sentinel():
    """Stand-in for a real TrustRegistry so the test stays hermetic (a real
    registry reads ~/.codeassist/trusted_tools.json on construction)."""
    return object()


def test_singleton_is_reused_for_the_same_workspace(isolated_singleton, tmp_path):
    r1 = ctl.get_custom_tool_registry(tmp_path, trust_registry=_sentinel())
    r2 = ctl.get_custom_tool_registry(tmp_path, trust_registry=_sentinel())
    assert r1 is r2


def test_later_trust_registry_takes_effect_on_reuse(isolated_singleton, tmp_path):
    """An early call with trust_registry=None must not pin trust checks off
    process-wide: a later call supplying a real registry updates the existing
    singleton (§8)."""
    r1 = ctl.get_custom_tool_registry(tmp_path, trust_registry=None)
    assert r1.trust_registry is None

    real = _sentinel()
    r2 = ctl.get_custom_tool_registry(tmp_path, trust_registry=real)

    assert r2 is r1, "the singleton is reused for the same workspace"
    assert r2.trust_registry is real, "the supplied registry propagates on reuse"


def test_explicit_none_does_not_wipe_a_real_registry(isolated_singleton, tmp_path):
    """Once a real registry is in place, a later None call leaves it intact."""
    real = _sentinel()
    r1 = ctl.get_custom_tool_registry(tmp_path, trust_registry=real)

    r2 = ctl.get_custom_tool_registry(tmp_path, trust_registry=None)

    assert r2 is r1
    assert r2.trust_registry is real
