#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Frozen characterization of ListRecommendations observable behaviour.
# Disclosed operator preparation for the Apex governed-evolution baseline;
# the evolution loop must not modify this file.

import uuid

import demo_pb2
import grpc
import pytest

import recommendation_server

MAX_RECOMMENDATIONS = 5
REPEATS = 40


def ids(n, prefix="P"):
    return [f"{prefix}-{uuid.uuid4().hex}" for _ in range(n)]


def unknown_id():
    return f"UNKNOWN-{uuid.uuid4().hex}"


class CatalogUnavailable(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "product catalog unavailable"


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5])
def test_requested_id_is_never_returned_small_catalog(recommend, catalog, size):
    products = ids(size)
    catalog.set_products(products)
    requested = products[0]
    result = recommend([requested])
    assert requested not in result


def test_requested_id_is_never_returned_large_catalog(recommend, catalog):
    products = ids(20)
    catalog.set_products(products)
    for requested in products:
        for _ in range(3):
            assert requested not in recommend([requested])


def test_returns_at_most_five_ids(recommend, catalog):
    products = ids(40)
    catalog.set_products(products)
    for _ in range(REPEATS):
        assert len(recommend([])) <= MAX_RECOMMENDATIONS
        assert len(recommend([products[0]])) <= MAX_RECOMMENDATIONS


def test_returns_five_when_at_least_five_are_eligible(recommend, catalog):
    products = ids(6)
    catalog.set_products(products)
    assert len(recommend([products[0]])) == MAX_RECOMMENDATIONS
    products = ids(20)
    catalog.set_products(products)
    for _ in range(REPEATS):
        assert len(recommend([products[3]])) == MAX_RECOMMENDATIONS


@pytest.mark.parametrize("size", [2, 3, 4, 5, 6])
def test_returns_every_eligible_id_when_fewer_than_six_are_eligible(recommend, catalog, size):
    products = ids(size)
    catalog.set_products(products)
    requested = products[-1]
    assert sorted(recommend([requested])) == sorted(products[:-1])


@pytest.mark.parametrize("size", [1, 3, 5])
def test_request_without_product_ids_returns_catalog_ids(recommend, catalog, size):
    products = ids(size)
    catalog.set_products(products)
    assert sorted(recommend([])) == sorted(products)


def test_returns_only_ids_from_the_catalog(recommend, catalog):
    products = ids(20)
    catalog.set_products(products)
    known = set(products)
    for _ in range(REPEATS):
        assert set(recommend([unknown_id()])) <= known
        assert set(recommend([products[1]])) <= known


def test_returned_ids_are_unique(recommend, catalog):
    products = ids(20)
    catalog.set_products(products)
    for _ in range(REPEATS):
        result = recommend([products[2]])
        assert len(result) == len(set(result))


def test_unknown_requested_id_does_not_reduce_results(recommend, catalog):
    products = ids(3)
    catalog.set_products(products)
    assert sorted(recommend([unknown_id()])) == sorted(products)


def test_empty_catalog_returns_empty_response(recommend, catalog):
    catalog.set_products([])
    assert recommend([]) == []
    assert recommend([unknown_id()]) == []


def test_catalog_consisting_only_of_requested_id_returns_empty(recommend, catalog):
    products = ids(1)
    catalog.set_products(products)
    assert recommend(products) == []


def test_flag_off_reflects_current_catalog_without_stale_ids(recommend, catalog, cache_flag):
    cache_flag(False)
    first = ids(5, prefix="A")
    second = ids(5, prefix="B")
    for _ in range(10):
        catalog.set_products(first)
        calls_before = catalog.calls
        assert set(recommend([])) == set(first)
        assert catalog.calls > calls_before
        catalog.set_products(second)
        calls_before = catalog.calls
        assert set(recommend([])) == set(second)
        assert catalog.calls > calls_before


def test_flag_off_does_not_grow_service_cache(recommend, catalog, cache_flag):
    cache_flag(False)
    products = ids(20)
    catalog.set_products(products)
    before = list(getattr(recommendation_server, "cached_ids", []))
    for _ in range(REPEATS):
        recommend([products[0]])
    after = list(getattr(recommendation_server, "cached_ids", []))
    assert len(after) <= len(before)


def test_flag_on_preserves_response_invariants(recommend, catalog, cache_flag):
    cache_flag(True)
    products = ids(20)
    catalog.set_products(products)
    known = set(products)
    for i in range(REPEATS):
        requested = products[i % len(products)]
        result = recommend([requested])
        assert requested not in result
        assert set(result) <= known
        assert len(result) == len(set(result))
        assert len(result) <= MAX_RECOMMENDATIONS


def test_catalog_failure_is_signalled_to_the_caller(servicer, catalog, make_context, context_aborted):
    # Failure must be signalled, never turned into a successful response:
    # propagating the catalog RpcError (today), propagating any other
    # exception, or a non-OK status via context.abort/set_code all qualify.
    catalog.fail_with(CatalogUnavailable())
    context = make_context()
    request = demo_pb2.ListRecommendationsRequest(user_id="u", product_ids=[unknown_id()])
    try:
        servicer.ListRecommendations(request, context)
    except context_aborted:
        assert context.code not in (None, grpc.StatusCode.OK)
    except Exception:
        pass
    else:
        assert context.code not in (None, grpc.StatusCode.OK), (
            "catalog failure produced a successful response"
        )
