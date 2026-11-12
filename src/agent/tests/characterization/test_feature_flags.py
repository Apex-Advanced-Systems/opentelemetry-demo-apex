#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Characterization of src/agent/src/agents/feature_flags.py as it behaves at the seed.
# No flagd connection is made: the flagd provider class is replaced in the module, and
# integer flags are served by openfeature's in-memory provider.

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from openfeature import api
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider

FLAGS_PATH = Path(__file__).resolve().parents[2] / "src" / "agents" / "feature_flags.py"
MODULE_NAME = "src.agents.feature_flags"


def _load():
    for package in ("src", "src.agents"):
        if package not in sys.modules:
            stub = types.ModuleType(package)
            stub.__path__ = []
            sys.modules[package] = stub
    spec = importlib.util.spec_from_file_location(MODULE_NAME, FLAGS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


FLAGS = _load()


@pytest.fixture(autouse=True)
def clean_openfeature():
    api.clear_providers()
    api.clear_hooks()
    yield
    api.clear_providers()
    api.clear_hooks()


def test_unknown_flag_returns_the_default_without_a_provider():
    assert FLAGS.get_int_feature_flag("absent") == 0
    assert FLAGS.get_int_feature_flag("absent", 7) == 7


def test_flag_value_comes_from_the_provider():
    api.set_provider(InMemoryProvider({
        "llmRateLimitError": InMemoryFlag("on", {"on": 3, "off": 0}),
        "zero": InMemoryFlag("off", {"on": 3, "off": 0}),
    }))
    assert FLAGS.get_int_feature_flag("llmRateLimitError") == 3
    assert FLAGS.get_int_feature_flag("zero", 9) == 0
    assert FLAGS.get_int_feature_flag("missing", 5) == 5


def test_init_uses_flagd_host_and_port_and_adds_the_tracing_hook(monkeypatch):
    made = []

    class FakeFlagd(InMemoryProvider):
        def __init__(self, host, port):
            super().__init__({})
            made.append((host, port))

    monkeypatch.setattr(FLAGS, "FlagdProvider", FakeFlagd)
    monkeypatch.delenv("FLAGD_HOST", raising=False)
    monkeypatch.delenv("FLAGD_PORT", raising=False)
    FLAGS.init_feature_flags()
    monkeypatch.setenv("FLAGD_HOST", "flags.local")
    monkeypatch.setenv("FLAGD_PORT", "9999")
    FLAGS.init_feature_flags()
    assert made == [("flagd", 8013), ("flags.local", 9999)]
    assert [type(h).__name__ for h in api.get_hooks()] == ["TracingHook", "TracingHook"]
