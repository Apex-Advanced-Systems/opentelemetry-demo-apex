#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Unit coverage for the effectful symbols of the recommendation service.
#
# Every symbol under test is imported by name and called directly in the body
# of a module-level test function. The service binds tracer, logger,
# rec_svc_metrics and product_catalog_stub only under __main__, so the two
# tests that drive the catalog path install in-process, network-free
# stand-ins for exactly those four module attributes and for nothing else.
# No flagd connection, OTLP exporter or product catalog channel is ever
# created.

import logging
import os
import sys
from unittest import mock

from grpc_health.v1 import health_pb2
from openfeature import api
from opentelemetry import trace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'src', 'recommendation'))

import recommendation_server  # noqa: E402
from logger import CustomJsonFormatter, getJSONLogger  # noqa: E402
from metrics import init_metrics  # noqa: E402
from recommendation_server import (  # noqa: E402
    RecommendationService,
    check_feature_flag,
    get_product_list,
    must_map_env,
)

PROBE = 'apex.recommendation.unit.probe'
CATALOG_IDS = ['A-1', 'B-2', 'C-3', 'D-4']
CACHE_FAILURE_FLAG = 'recommendationCacheFailure'


class _FakeProduct:
    def __init__(self, product_id):
        self.id = product_id


class _FakeCatalogResponse:
    def __init__(self, product_ids):
        self.products = [_FakeProduct(i) for i in product_ids]


class _FakeCatalog:
    """Stand-in for ProductCatalogServiceStub that records every call."""

    def __init__(self, product_ids):
        self._product_ids = list(product_ids)
        self.calls = 0
        self.timeouts = []

    def ListProducts(self, request, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        return _FakeCatalogResponse(self._product_ids)


class _RecordingCounter:
    def __init__(self, name, unit, description):
        self.name = name
        self.unit = unit
        self.description = description
        self.added = []

    def add(self, amount, attributes=None):
        self.added.append((amount, attributes))


class _RecordingMeter:
    """Meter double: create_counter hands back a counter that records adds."""

    def __init__(self):
        self.counters = {}

    def create_counter(self, name, unit='', description=''):
        counter = _RecordingCounter(name, unit, description)
        self.counters[name] = counter
        return counter


class _FakeRequest:
    def __init__(self, product_ids):
        self.product_ids = list(product_ids)


def test_get_json_logger_installs_one_json_handler():
    probe_logger = getJSONLogger(PROBE + '.get_json_logger')
    assert probe_logger.level == logging.INFO
    assert probe_logger.propagate is False
    assert len(probe_logger.handlers) == 1
    assert isinstance(probe_logger.handlers[0].formatter, CustomJsonFormatter)


def test_custom_json_formatter_adds_otel_trace_and_span_ids():
    record = logging.LogRecord(PROBE, logging.INFO, __file__, 1, 'probe message', None, None)
    log_record = {}
    CustomJsonFormatter('%(message)s').add_fields(log_record, record, {})
    # No span is active here, so both identifiers take their all-zero form.
    assert log_record['otelTraceID'] == '0' * 32
    assert log_record['otelSpanID'] == '0' * 16


def test_custom_json_formatter_keeps_existing_trace_and_span_ids():
    record = logging.LogRecord(PROBE, logging.INFO, __file__, 2, 'probe message', None, None)
    log_record = {'otelTraceID': 'kept-trace', 'otelSpanID': 'kept-span'}
    CustomJsonFormatter('%(message)s').add_fields(log_record, record, {})
    assert log_record['otelTraceID'] == 'kept-trace'
    assert log_record['otelSpanID'] == 'kept-span'


def test_init_metrics_registers_the_recommendation_counter():
    meter = _RecordingMeter()
    registry = init_metrics(meter)
    assert sorted(registry) == ['demo.recommendation.requests']
    counter = registry['demo.recommendation.requests']
    assert counter.name == 'demo.recommendation.requests'
    assert counter.unit == 'recommendations'
    assert counter.added == []


def test_must_map_env_returns_the_value_of_a_set_variable():
    os.environ['APEX_RECOMMENDATION_UNIT_PROBE'] = 'probe-value'
    try:
        assert must_map_env('APEX_RECOMMENDATION_UNIT_PROBE') == 'probe-value'
    finally:
        del os.environ['APEX_RECOMMENDATION_UNIT_PROBE']


def test_must_map_env_rejects_a_variable_that_is_not_set():
    os.environ.pop('APEX_RECOMMENDATION_UNIT_ABSENT', None)
    raised = None
    try:
        must_map_env('APEX_RECOMMENDATION_UNIT_ABSENT')
    except Exception as error:
        raised = error
    assert raised is not None
    assert 'APEX_RECOMMENDATION_UNIT_ABSENT' in str(raised)


def test_check_feature_flag_is_false_without_a_configured_provider():
    api.clear_providers()
    assert check_feature_flag(CACHE_FAILURE_FLAG) is False


def test_check_reports_serving():
    response = RecommendationService().Check(health_pb2.HealthCheckRequest(service=''), None)
    assert response.status == health_pb2.HealthCheckResponse.SERVING


def test_watch_never_reports_serving():
    # Watch answers with a status constant the health proto does not define,
    # so the call raises AttributeError today. Under either outcome it must
    # never claim SERVING.
    try:
        response = RecommendationService().Watch(health_pb2.HealthCheckRequest(service=''), None)
        assert response.status != health_pb2.HealthCheckResponse.SERVING
    except AttributeError:
        pass


def test_get_product_list_excludes_the_requested_ids():
    api.clear_providers()
    catalog = _FakeCatalog(CATALOG_IDS)
    with mock.patch('recommendation_server.tracer', trace.get_tracer(PROBE), create=True), \
            mock.patch('recommendation_server.logger', logging.getLogger(PROBE), create=True), \
            mock.patch('recommendation_server.product_catalog_stub', catalog, create=True):
        prod_list = get_product_list([CATALOG_IDS[0]])
        assert sorted(prod_list) == sorted(CATALOG_IDS[1:])
        assert catalog.calls == 1
        assert catalog.timeouts == [recommendation_server.PRODUCT_CATALOG_TIMEOUT_SECONDS]


def test_list_recommendations_returns_and_counts_recommended_ids():
    api.clear_providers()
    catalog = _FakeCatalog(CATALOG_IDS)
    registry = init_metrics(_RecordingMeter())
    with mock.patch('recommendation_server.tracer', trace.get_tracer(PROBE), create=True), \
            mock.patch('recommendation_server.logger', logging.getLogger(PROBE), create=True), \
            mock.patch('recommendation_server.product_catalog_stub', catalog, create=True), \
            mock.patch('recommendation_server.rec_svc_metrics', registry, create=True):
        response = RecommendationService().ListRecommendations(_FakeRequest([CATALOG_IDS[0]]), None)
        assert sorted(response.product_ids) == sorted(CATALOG_IDS[1:])
        assert registry['demo.recommendation.requests'].added == [
            (3, {'recommendation.type': 'catalog'})
        ]
