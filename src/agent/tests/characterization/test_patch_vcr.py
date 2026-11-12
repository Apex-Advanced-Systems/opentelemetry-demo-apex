#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Characterization of src/agent/src/agents/patch_vcr.py as it behaves at the seed:
# request normalisation, response cleaning and the fuzzy body matcher. Pure functions
# only; no cassette is read or written and USE_VCR stays unset, so the module's
# import-time patches are not installed.

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

PATCH_VCR_PATH = Path(__file__).resolve().parents[2] / "src" / "agents" / "patch_vcr.py"
MODULE_NAME = "src.agents.patch_vcr"


def _load():
    os.environ.pop("USE_VCR", None)
    os.environ.pop("VCR_MATCH_THRESHOLD", None)
    for package in ("src", "src.agents"):
        if package not in sys.modules:
            stub = types.ModuleType(package)
            stub.__path__ = []
            sys.modules[package] = stub
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PATCH_VCR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


VCR = _load()


class Req:
    def __init__(self, body, headers=None, uri="https://api.example/v1", method="GET"):
        self.body = body
        self.headers = headers if headers is not None else {"x": "y"}
        self.uri = uri
        self.method = method


def chat(*contents, role="user"):
    return Req(json.dumps({"messages": [{"role": role, "content": c} for c in contents]}).encode())


def test_threshold_default():
    assert VCR.VCR_MATCH_THRESHOLD == 0.85
    assert VCR.use_vcr is False


def test_normalize_body_sorts_json_and_rewrites_the_request():
    request = VCR.normalize_body(Req(b'{"b": 1, "a": 2}'))
    assert request.body == b'{"a": 2, "b": 1}'
    assert request.headers == {}
    assert request.uri == "https://vcr.local/"
    assert request.method == "POST"


def test_normalize_body_accepts_a_str_body():
    request = VCR.normalize_body(Req('{"z": [3, 1]}'))
    assert request.body == b'{"z": [3, 1]}'


def test_normalize_body_leaves_a_non_json_body_alone():
    request = VCR.normalize_body(Req(b"not json"))
    assert request.body == b"not json"
    assert request.headers == {"x": "y"}
    assert request.uri == "https://api.example/v1"


def test_normalize_body_leaves_an_empty_body_alone():
    request = VCR.normalize_body(Req(b""))
    assert request.method == "GET"


def test_clean_response_drops_non_2xx():
    assert VCR.clean_response({"status": {"code": 500}, "body": {"string": "{}"}}) is None
    assert VCR.clean_response({"status": {"code": "404"}, "body": {"string": "{}"}}) is None


def test_clean_response_drops_a_provider_error_payload():
    body = json.dumps({"error": {"message": "nope"}})
    assert VCR.clean_response({"status": {"code": 200}, "body": {"string": body}}) is None


def test_clean_response_normalises_a_completion():
    body = json.dumps({
        "id": "abc", "created": 123, "usage": {"t": 1}, "system_fingerprint": "f",
        "choices": [{"provider_specific_fields": 1,
                     "message": {"content": "hi", "annotations": [], "provider_specific_fields": 2}}],
    }).encode()
    response = {"status": {"code": 200}, "headers": {"h": "v"}, "body": {"string": body}}
    cleaned = VCR.clean_response(response)
    assert cleaned["headers"] == {}
    assert json.loads(cleaned["body"]["string"]) == {
        "id": "chatcmpl-fixture", "created": 0, "choices": [{"message": {"content": "hi"}}],
    }
    assert cleaned["body"]["string"] == json.dumps(json.loads(cleaned["body"]["string"]), sort_keys=True)


def test_clean_response_keeps_a_non_json_body_but_clears_headers():
    response = {"status": {"code": 201}, "headers": {"h": "v"}, "body": {"string": "plain"}}
    cleaned = VCR.clean_response(response)
    assert cleaned["headers"] == {}
    assert cleaned["body"]["string"] == "plain"


def test_clean_response_without_status_is_kept():
    cleaned = VCR.clean_response({"body": {"string": '{"choices": []}'}})
    assert cleaned["body"]["string"] == '{"choices": []}'


def test_parse_body():
    assert VCR._parse_body(Req(b'{"a": 1}')) == {"a": 1}
    assert VCR._parse_body(Req('{"a": 2}')) == {"a": 2}
    assert VCR._parse_body(Req(None)) is None
    assert VCR._parse_body(Req(b"\xff\xfe")) is None
    assert VCR._parse_body(Req(b"nope")) is None
    assert VCR._parse_body(object()) is None


def test_normalize_message_masks_only_tool_results():
    tool = {"role": "tool", "content": "42 items", "tool_call_id": "t1"}
    assert VCR._normalize_message(tool) == {"role": "tool", "content": "<tool-result>", "tool_call_id": "t1"}
    assert tool["content"] == "42 items"
    user = {"role": "user", "content": "hi"}
    assert VCR._normalize_message(user) is user
    assert VCR._normalize_message("raw") == "raw"


def test_stringify():
    assert VCR._stringify("s") == "s"
    assert VCR._stringify({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    assert VCR._stringify(None) == "null"
    assert VCR._stringify({1, 2}).startswith("{")


def test_body_similarity_identical_and_empty():
    assert VCR.body_similarity(chat("a", "b"), chat("a", "b")) == 1.0
    assert VCR.body_similarity(Req(b'{"messages": []}'), Req(b'{"messages": []}')) == 1.0


def test_body_similarity_message_count_mismatch_is_zero():
    assert VCR.body_similarity(chat("a"), chat("a", "b")) == 0.0


def test_body_similarity_weights_the_last_message():
    near = VCR.body_similarity(chat("same", "tail one"), chat("same", "tail two"))
    far = VCR.body_similarity(chat("same", "tail one"), chat("same", "completely different"))
    assert 0.0 < far < near < 1.0


def test_body_similarity_ignores_tool_result_content():
    one = Req(json.dumps({"messages": [{"role": "tool", "content": "x"}]}).encode())
    two = Req(json.dumps({"messages": [{"role": "tool", "content": "y" * 50}]}).encode())
    assert VCR.body_similarity(one, two) == 1.0


def test_body_similarity_whole_body_fallback():
    assert VCR.body_similarity(Req(b'{"input": "abc"}'), Req(b'{"input": "abc"}')) == 1.0
    score = VCR.body_similarity(Req(b'{"input": "abc"}'), Req(b"zzz"))
    assert 0.0 <= score < 1.0


def test_similar_body_matcher():
    assert VCR.similar_body_matcher(chat("a"), chat("a")) is None
    with pytest.raises(AssertionError, match="beyond threshold"):
        VCR.similar_body_matcher(chat("a"), chat("a", "b"))


def test_patched_deserialize_response_encodes_a_str_body(monkeypatch):
    seen = []
    monkeypatch.setattr(VCR, "_original_deserialize_response", lambda r, m: seen.append((r, m)) or "done")
    response = {"body": {"string": "é"}}
    assert VCR.patched_deserialize_response(response, "httpx") == "done"
    assert response["body"]["string"] == "é".encode()
    raw = {"body": {"string": b"x"}}
    VCR.patched_deserialize_response(raw, "httpx")
    assert raw["body"]["string"] == b"x"
