#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Characterization of src/shared/tools.py as it behaves at the seed: request shape,
# successful return values, and the strings returned (never raised) on failure.

import asyncio
import json

import httpx
import pytest

BASE = "http://localhost:8080"


def run(coroutine):
    return asyncio.run(coroutine)


def test_endpoint_and_timeout_defaults(tools):
    assert tools.BASE_URL == "localhost:8080"
    assert tools.TIMEOUT == httpx.Timeout(10.0)


def test_every_call_opens_a_client_with_the_module_timeout(tools, backend):
    backend.reply(json=[])
    run(tools.list_products())
    run(tools.get_ads("travel"))
    assert backend.client_kwargs == [{"timeout": tools.TIMEOUT}, {"timeout": tools.TIMEOUT}]


def test_get_ads_requests_the_context_and_returns_the_json(tools, backend):
    backend.reply(json=[{"text": "ad"}])
    assert run(tools.get_ads("telescopes")) == [{"text": "ad"}]
    (request,) = backend.requests
    assert request.method == "GET"
    assert str(request.url) == f"{BASE}/api/data?contextKeys=telescopes"


def test_add_to_cart_posts_the_item_and_the_user(tools, backend):
    backend.reply(json={"ok": True})
    assert run(tools.add_to_cart("u1", "p1", 3)) == {"ok": True}
    (request,) = backend.requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/api/cart"
    assert json.loads(request.content) == {"item": {"productId": "p1", "quantity": 3}, "userId": "u1"}


def test_add_to_cart_defaults_the_quantity_to_one(tools, backend):
    backend.reply(json={})
    run(tools.add_to_cart("u1", "p1"))
    assert json.loads(backend.requests[0].content)["item"]["quantity"] == 1


def test_get_cart_queries_by_user(tools, backend):
    backend.reply(json={"items": []})
    assert run(tools.get_cart("u1")) == {"items": []}
    (request,) = backend.requests
    assert request.method == "GET"
    assert str(request.url) == f"{BASE}/api/cart?user_id=u1"


def test_empty_cart_sends_a_delete_with_a_body_and_reports_success_on_204(tools, backend):
    backend.reply(status=204)
    assert run(tools.empty_cart("u1")) == {"status": "success", "message": "Cart emptied for user u1"}
    (request,) = backend.requests
    assert request.method == "DELETE"
    assert str(request.url) == f"{BASE}/api/cart"
    assert json.loads(request.content) == {"userId": "u1"}


def test_empty_cart_reports_success_on_an_empty_200_and_returns_a_json_body_otherwise(tools, backend):
    backend.reply(status=200, content=b"")
    assert run(tools.empty_cart("u2")) == {"status": "success", "message": "Cart emptied for user u2"}
    backend.reply(status=200, json={"emptied": 2})
    assert run(tools.empty_cart("u2")) == {"emptied": 2}


def test_list_products_and_get_product(tools, backend):
    backend.reply(json={"products": []})
    assert run(tools.list_products()) == {"products": []}
    assert run(tools.get_product("OLJCESPC7Z")) == {"products": []}
    assert [str(request.url) for request in backend.requests] == [
        f"{BASE}/api/products",
        f"{BASE}/api/products/OLJCESPC7Z",
    ]


def test_get_supported_currencies(tools, backend):
    backend.reply(json=["USD", "EUR"])
    assert run(tools.get_supported_currencies()) == ["USD", "EUR"]
    assert str(backend.requests[0].url) == f"{BASE}/api/currency"


def test_get_recommendations_passes_the_product_id(tools, backend):
    backend.reply(json=["p2"])
    assert run(tools.get_recommendations("p1")) == ["p2"]
    assert str(backend.requests[0].url) == f"{BASE}/api/recommendations?productIds=p1"


def test_checkout_posts_the_person_and_returns_the_order(tools, backend):
    backend.reply(json={"orderId": "o1"})
    person = {"userId": "u1", "userCurrency": "USD"}
    assert run(tools.checkout(person)) == {"orderId": "o1"}
    (request,) = backend.requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/api/checkout"
    assert json.loads(request.content) == person


def test_checkout_failure_names_the_status_the_user_and_the_body(tools, backend):
    backend.reply(status=400, text=" empty cart \n")
    assert run(tools.checkout({"userId": "u1"})) == (
        "Checkout failed with HTTP 400 for user u1: empty cart. "
        "Note: the user's cart must contain at least one item before "
        "calling checkout; call add_to_cart first."
    )


def test_checkout_failure_defaults_for_a_missing_user_and_an_empty_body(tools, backend):
    backend.reply(status=500, content=b"")
    message = run(tools.checkout({}))
    assert message.startswith("Checkout failed with HTTP 500 for user <unknown>: <empty body>. ")


def test_shipping_quote_normalises_the_items(tools, backend):
    backend.reply(json={"cost": 5})
    address = {"city": "Oslo"}
    items = [{"product_id": "p1"}, {"productId": "p2", "quantity": 4}]
    assert run(tools.get_shipping_quote(items, "USD", address)) == {"cost": 5}
    (request,) = backend.requests
    assert request.url.path == "/api/shipping"
    params = dict(request.url.params)
    assert json.loads(params["itemList"]) == [
        {"productId": "p1", "quantity": 1},
        {"productId": "p2", "quantity": 4},
    ]
    assert params["currencyCode"] == "USD"
    assert json.loads(params["address"]) == address


def test_shipping_quote_accepts_a_single_item_dict(tools, backend):
    backend.reply(json={})
    run(tools.get_shipping_quote({"productId": "p1"}, "EUR", {}))
    assert json.loads(dict(backend.requests[0].url.params)["itemList"]) == [
        {"productId": "p1", "quantity": 1}
    ]


def test_shipping_quote_rejects_bad_items_without_calling_the_backend(tools, backend):
    assert run(tools.get_shipping_quote(["p1"], "USD", {})) == (
        "Error fetching shipping quote: invalid item 'p1'"
    )
    assert run(tools.get_shipping_quote([{"quantity": 2}], "USD", {})) == (
        "Error fetching shipping quote: each item must include productId"
    )
    assert backend.requests == []


def test_shipping_quote_failure_names_the_status_and_the_body(tools, backend):
    backend.reply(status=500, text="boom")
    assert run(tools.get_shipping_quote([{"productId": "p1"}], "USD", {})) == (
        "Shipping quote failed with HTTP 500: boom. "
    )


CALLS = [
    ("get_ads", ("travel",), "Error fetching ads: "),
    ("add_to_cart", ("u1", "p1"), "Error while adding product to cart: "),
    ("get_cart", ("u1",), "Error while fetching cart: "),
    ("empty_cart", ("u1",), "Error while emptying cart: "),
    ("list_products", (), "Error while fetching product list: "),
    ("get_product", ("p1",), "Error while fetching product p1: "),
    ("checkout", ({"userId": "u1"},), "Error while performing checkout: "),
    ("get_supported_currencies", (), "Error while fetching currency list: "),
    ("get_recommendations", ("p1",), "Error fetching recommendations: "),
    ("get_shipping_quote", ([{"productId": "p1"}], "USD", {}), "Error fetching shipping quote: "),
]


@pytest.mark.parametrize(("name", "args", "prefix"), CALLS, ids=[call[0] for call in CALLS])
def test_a_transport_failure_is_returned_as_a_string_not_raised(tools, backend, name, args, prefix):
    def refuse(request):
        raise httpx.ConnectError("no route", request=request)

    backend.respond(refuse)
    assert run(getattr(tools, name)(*args)) == f"{prefix}no route"


RAISING = [call for call in CALLS if call[0] not in {"checkout", "get_shipping_quote"}]


@pytest.mark.parametrize(("name", "args", "prefix"), RAISING, ids=[call[0] for call in RAISING])
def test_an_http_error_status_is_returned_as_a_string_not_raised(tools, backend, name, args, prefix):
    backend.reply(status=503)
    result = run(getattr(tools, name)(*args))
    assert isinstance(result, str)
    assert result.startswith(prefix)
    assert "503" in result
