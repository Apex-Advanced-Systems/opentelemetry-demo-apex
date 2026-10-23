#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Unit coverage for the effectful symbols of the recommendation service.
#
# Every effectful symbol of logger.py, metrics.py and recommendation_server.py
# is called directly from a module-level test below.
#
# The service binds tracer, logger, rec_svc_metrics and product_catalog_stub
# only under __main__, so each test that needs one of those four module
# attributes installs an in-process, network-free double for it. The
# feature-flag client is resolved through a double as well, so the cache-failure
# branch is decided by the test rather than by ambient provider state. No flagd,
# OTLP exporter or product catalog connection is ever created.

import logging
import os
import sys
from unittest import mock

SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import logger as service_logger  # noqa: E402
import metrics as service_metrics  # noqa: E402
import recommendation_server  # noqa: E402
from grpc_health.v1 import health_pb2  # noqa: E402

CACHE_FAILURE_FLAG = "recommendationCacheFailure"
REQUEST_COUNTER = "demo.recommendation.requests"
HEX_DIGITS = "0123456789abcdef"


class _Product:
    """Minimal stand-in for an entry of ListProductsResponse.products."""

    def __init__(self, product_id):
        self.id = product_id


class _CatalogResponse:
    def __init__(self, product_ids):
        self.products = []
        for product_id in product_ids:
            self.products.append(_Product(product_id))


class _CatalogStub:
    """Stand-in for ProductCatalogServiceStub that records call deadlines."""

    def __init__(self, product_ids):
        self._product_ids = list(product_ids)
        self.timeouts = []

    def ListProducts(self, request, timeout=None):
        self.timeouts.append(timeout)
        return _CatalogResponse(self._product_ids)


class _Span:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _SpanScope:
    def __init__(self, span):
        self._span = span

    def __enter__(self):
        return self._span

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _Tracer:
    """Tracer double exposing one recording span and the span names opened."""

    def __init__(self):
        self.span = _Span()
        self.span_names = []

    def start_as_current_span(self, name):
        self.span_names.append(name)
        return _SpanScope(self.span)


class _Counter:
    def __init__(self):
        self.adds = []

    def add(self, amount, attributes=None):
        self.adds.append((amount, attributes))


class _Meter:
    """Meter double recording every instrument the service asks for."""

    def __init__(self):
        self.created = []

    def create_counter(self, name, unit="", description=""):
        self.created.append((name, unit, description))
        return _Counter()


class _FlagClient:
    """OpenFeature client double recording every flag resolution."""

    def __init__(self, values):
        self._values = dict(values)
        self.resolved = []

    def get_boolean_value(self, flag_key, default_value):
        self.resolved.append((flag_key, default_value))
        return self._values.get(flag_key, default_value)


class _Request:
    def __init__(self, product_ids):
        self.product_ids = list(product_ids)


def test_init_metrics_registers_the_recommendation_request_counter():
    meter = _Meter()
    registry = service_metrics.init_metrics(meter)
    assert sorted(registry.keys()) == [REQUEST_COUNTER]
    assert meter.created == [
        (
            REQUEST_COUNTER,
            "recommendations",
            "Counts the total number of given recommendations",
        )
    ]
    assert hasattr(registry[REQUEST_COUNTER], "add")


def test_get_json_logger_installs_a_json_formatter_at_info_level():
    result = service_logger.getJSONLogger("apex.unit.cycle2.getjsonlogger")
    formatter_types = []
    for handler in result.handlers:
        formatter_types.append(type(handler.formatter))
    assert result.name == "apex.unit.cycle2.getjsonlogger"
    assert result.level == logging.INFO
    assert result.propagate is False
    assert service_logger.CustomJsonFormatter in formatter_types


def test_add_fields_populates_the_otel_identifiers():
    log_record = {}
    record = logging.LogRecord(
        "apex.unit.cycle2.formatter", logging.INFO, __file__, 1, "unit", None, None
    )
    service_logger.CustomJsonFormatter().add_fields(log_record, record, {})
    assert len(log_record["otelTraceID"]) == 32
    assert len(log_record["otelSpanID"]) == 16
    for character in log_record["otelTraceID"] + log_record["otelSpanID"]:
        assert character in HEX_DIGITS


def test_add_fields_keeps_identifiers_already_present_on_the_log_record():
    log_record = {"otelTraceID": "already-set-trace", "otelSpanID": "already-set-span"}
    record = logging.LogRecord(
        "apex.unit.cycle2.formatter", logging.INFO, __file__, 2, "unit", None, None
    )
    service_logger.CustomJsonFormatter().add_fields(log_record, record, {})
    assert log_record["otelTraceID"] == "already-set-trace"
    assert log_record["otelSpanID"] == "already-set-span"


def test_must_map_env_returns_the_value_of_a_present_variable():
    with mock.patch.dict("os.environ", {"APEX_UNIT_CYCLE2_PRESENT": "catalog:1234"}):
        value = recommendation_server.must_map_env("APEX_UNIT_CYCLE2_PRESENT")
    assert value == "catalog:1234"


def test_must_map_env_raises_when_the_variable_is_absent():
    raised = None
    with mock.patch.dict("os.environ", {}, clear=True):
        try:
            recommendation_server.must_map_env("APEX_UNIT_CYCLE2_ABSENT")
        except Exception as error:
            raised = error
    assert raised is not None, "must_map_env must reject an unset variable"
    assert "APEX_UNIT_CYCLE2_ABSENT" in str(raised)


def test_check_feature_flag_resolves_the_named_flag_with_a_false_default():
    client = _FlagClient({CACHE_FAILURE_FLAG: True})
    with mock.patch("openfeature.api.get_client", return_value=client):
        assert recommendation_server.check_feature_flag(CACHE_FAILURE_FLAG) is True
        assert recommendation_server.check_feature_flag("apexUnitCycle2Absent") is False
    assert client.resolved == [
        (CACHE_FAILURE_FLAG, False),
        ("apexUnitCycle2Absent", False),
    ]


def test_get_product_list_returns_catalog_ids_without_the_requested_ids():
    catalog = _CatalogStub(["OLJCESPC7Z", "66VCHSJNUP", "1YMWWN1N4O", "L9ECAV7KIM"])
    tracer = _Tracer()
    with mock.patch("openfeature.api.get_client", return_value=_FlagClient({})), \
            mock.patch.object(recommendation_server, "tracer", tracer, create=True), \
            mock.patch.object(
                recommendation_server, "product_catalog_stub", catalog, create=True
            ), \
            mock.patch.object(recommendation_server, "cached_ids", []), \
            mock.patch.object(recommendation_server, "first_run", True):
        result = recommendation_server.get_product_list(["OLJCESPC7Z"])
    assert sorted(result) == ["1YMWWN1N4O", "66VCHSJNUP", "L9ECAV7KIM"]
    assert tracer.span_names == ["get_product_list"]
    assert tracer.span.attributes["demo.feature_flag.recommendation_cache"] is False
    assert tracer.span.attributes["demo.product.count"] == 4
    assert tracer.span.attributes["demo.product.filtered.count"] == 3
    assert catalog.timeouts == [recommendation_server.PRODUCT_CATALOG_TIMEOUT_SECONDS]


def test_list_recommendations_returns_filtered_ids_and_counts_them():
    catalog = _CatalogStub(["A1", "B2", "C3"])
    counter = _Counter()
    with mock.patch("openfeature.api.get_client", return_value=_FlagClient({})), \
            mock.patch.object(recommendation_server, "tracer", _Tracer(), create=True), \
            mock.patch.object(
                recommendation_server,
                "logger",
                logging.getLogger("apex.unit.cycle2.service"),
                create=True,
            ), \
            mock.patch.object(
                recommendation_server, "product_catalog_stub", catalog, create=True
            ), \
            mock.patch.object(
                recommendation_server,
                "rec_svc_metrics",
                {REQUEST_COUNTER: counter},
                create=True,
            ), \
            mock.patch.object(recommendation_server, "cached_ids", []), \
            mock.patch.object(recommendation_server, "first_run", True):
        response = recommendation_server.RecommendationService().ListRecommendations(
            _Request(["A1"]), None
        )
    assert sorted(response.product_ids) == ["B2", "C3"]
    assert counter.adds == [(2, {"recommendation.type": "catalog"})]


def test_check_reports_a_serving_status():
    response = recommendation_server.RecommendationService().Check(None, None)
    assert response.status == health_pb2.HealthCheckResponse.SERVING


def test_watch_never_reports_a_serving_status():
    # Watch names a status that the health protocol does not define, so the
    # observable contract is only that it never claims SERVING: it either
    # fails outright or answers with some other status.
    serving = health_pb2.HealthCheckResponse.SERVING
    observed = None
    failure = None
    try:
        observed = recommendation_server.RecommendationService().Watch(None, None).status
    except AttributeError as error:
        failure = error
    assert (failure is None) != (observed is None)
    assert observed != serving, "Watch must not report SERVING"
