#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Frozen characterization harness for src/recommendation.
# Disclosed operator preparation for the Apex governed-evolution baseline;
# the evolution loop must not modify this file.
#
# The service builds tracer, logger, rec_svc_metrics and product_catalog_stub
# only under __main__, so the harness binds those four module attributes to
# in-process, network-free test doubles. No flagd, OTLP exporter or catalog
# connection is ever created. The harness owns its metrics double and does not
# import the service's metrics module.

import logging
import os
import sys

import pytest

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import demo_pb2  # noqa: E402
import grpc  # noqa: E402
import recommendation_server  # noqa: E402
from openfeature import api  # noqa: E402
from openfeature.provider.in_memory_provider import (  # noqa: E402
    InMemoryFlag,
    InMemoryProvider,
)
from opentelemetry import metrics, trace  # noqa: E402

CACHE_FAILURE_FLAG = "recommendationCacheFailure"


class FakeCatalog:
    """Stand-in for ProductCatalogServiceStub.

    The product list is held privately and is only reachable through a
    ListProducts call, which is counted. Any call signature is accepted so
    that callers may add timeouts, metadata or other options.
    """

    def __init__(self):
        self.__products = []
        self.__error = None
        self.calls = 0

    def set_products(self, ids):
        self.__products = list(ids)

    def fail_with(self, error):
        self.__error = error

    def ListProducts(self, *args, **kwargs):
        self.calls += 1
        if self.__error is not None:
            raise self.__error
        return demo_pb2.ListProductsResponse(
            products=[demo_pb2.Product(id=i, name=i) for i in self.__products]
        )


class PermissiveMetrics(dict):
    """Metrics registry double: any key yields a no-op counter."""

    def __init__(self):
        super().__init__()
        self._meter = metrics.NoOpMeter("characterization")

    def __missing__(self, key):
        counter = self._meter.create_counter(str(key))
        self[key] = counter
        return counter


class ContextAborted(Exception):
    pass


class FakeContext(grpc.ServicerContext):
    """grpc.ServicerContext double that records error signalling."""

    def __init__(self):
        self.code = None
        self.details = None
        self.aborted = False

    def set_code(self, code):
        self.code = code

    def set_details(self, details):
        self.details = details

    def abort(self, code, details=""):
        self.aborted = True
        self.code = code
        self.details = details
        raise ContextAborted(code, details)

    def abort_with_status(self, status):
        self.aborted = True
        self.code = getattr(status, "code", None)
        raise ContextAborted(status)

    def is_active(self):
        return True

    def time_remaining(self):
        return 30.0

    def invocation_metadata(self):
        return ()

    def add_callback(self, callback):
        return True

    def peer(self):
        return "ipv4:127.0.0.1:0"

    def set_trailing_metadata(self, metadata):
        pass

    def send_initial_metadata(self, metadata):
        pass

    def auth_context(self):
        return {}

    def cancel(self):
        pass

    def peer_identities(self):
        return None

    def peer_identity_key(self):
        return None


def _set_flag(enabled):
    api.clear_providers()
    api.set_provider(
        InMemoryProvider(
            {
                CACHE_FAILURE_FLAG: InMemoryFlag(
                    "on" if enabled else "off", {"on": True, "off": False}
                )
            }
        )
    )


@pytest.fixture(autouse=True)
def service_env(monkeypatch):
    """Fresh module state, flag off, empty catalog, for every test."""
    catalog = FakeCatalog()
    monkeypatch.setattr(recommendation_server, "tracer", trace.get_tracer("characterization"), raising=False)
    monkeypatch.setattr(recommendation_server, "logger", logging.getLogger("characterization"), raising=False)
    monkeypatch.setattr(recommendation_server, "rec_svc_metrics", PermissiveMetrics(), raising=False)
    monkeypatch.setattr(recommendation_server, "product_catalog_stub", catalog, raising=False)
    if hasattr(recommendation_server, "cached_ids"):
        monkeypatch.setattr(recommendation_server, "cached_ids", [])
    if hasattr(recommendation_server, "first_run"):
        monkeypatch.setattr(recommendation_server, "first_run", True)
    _set_flag(False)
    yield catalog
    api.clear_providers()


@pytest.fixture
def catalog(service_env):
    return service_env


@pytest.fixture
def cache_flag():
    return _set_flag


@pytest.fixture
def servicer():
    return recommendation_server.RecommendationService()


@pytest.fixture
def recommend(servicer):
    def _call(product_ids, user_id="characterization-user", context=None):
        request = demo_pb2.ListRecommendationsRequest(user_id=user_id, product_ids=list(product_ids))
        response = servicer.ListRecommendations(request, context if context is not None else FakeContext())
        return list(response.product_ids)

    return _call


@pytest.fixture
def make_context():
    return FakeContext


@pytest.fixture
def context_aborted():
    return ContextAborted
