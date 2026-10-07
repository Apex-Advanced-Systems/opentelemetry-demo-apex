#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Frozen characterization of the recommendation health check.
# Disclosed operator preparation for the Apex governed-evolution baseline;
# the evolution loop must not modify this file.

import pytest
from grpc_health.v1 import health_pb2


@pytest.mark.parametrize("service", ["", "oteldemo.RecommendationService"])
def test_check_reports_serving(servicer, make_context, service):
    response = servicer.Check(health_pb2.HealthCheckRequest(service=service), make_context())
    assert response.status == health_pb2.HealthCheckResponse.SERVING
