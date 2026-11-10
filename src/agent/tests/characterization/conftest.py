#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Frozen characterization harness for src/shared/tools.py (the agent's tools) and
# internal/tools/sanitycheck.py.
# Disclosed operator preparation for the Apex governed-evolution baseline;
# the evolution loop must not modify any file in this directory.
#
# The agent image COPYs src/shared/tools.py into src/agents (see src/agent/Dockerfile),
# so the service imports it as src.agents.tools. The repository carries no copy and no
# symlink of it, so this harness binds that module name to the shared file in code.
# Every HTTP call is answered in-process by httpx.MockTransport: no network, flagd,
# LLM or MCP connection is ever created, and no cassette is read.

import importlib.util
import os
import sys
import types
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS_PATH = REPO_ROOT / "src" / "shared" / "tools.py"
SANITYCHECK_PATH = REPO_ROOT / "internal" / "tools" / "sanitycheck.py"
MODULE_NAME = "src.agents.tools"


def _load_tools():
    # The default endpoint is the module's own; make the run independent of the environment.
    os.environ.pop("APPLICATION_ENDPOINT", None)
    for package in ("src", "src.agents"):
        if package not in sys.modules:
            stub = types.ModuleType(package)
            stub.__path__ = []
            sys.modules[package] = stub
    spec = importlib.util.spec_from_file_location(MODULE_NAME, TOOLS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


TOOLS = _load_tools()
_REAL_ASYNC_CLIENT = httpx.AsyncClient


class Backend:
    """An in-process stand-in for the shop's HTTP API."""

    def __init__(self):
        self.requests = []
        self.client_kwargs = []
        self._responder = lambda request: httpx.Response(200, json={})

    def respond(self, responder):
        self._responder = responder

    def reply(self, status=200, **kwargs):
        self._responder = lambda request: httpx.Response(status, **kwargs)

    def handle(self, request):
        self.requests.append(request)
        return self._responder(request)


@pytest.fixture
def tools():
    return TOOLS


@pytest.fixture
def backend(monkeypatch):
    backend = Backend()

    def factory(*args, **kwargs):
        backend.client_kwargs.append(dict(kwargs))
        return _REAL_ASYNC_CLIENT(*args, transport=httpx.MockTransport(backend.handle), **kwargs)

    monkeypatch.setattr(TOOLS.httpx, "AsyncClient", factory)
    return backend
