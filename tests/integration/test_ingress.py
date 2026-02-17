#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
import requests
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_NAME, TRAEFIK_K8S_NAME
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_traefik_ingress_relation(ops_test: OpsTest):
    """Test the charm integrates with traefik-route."""
    await deploy_package(ops_test, ingress_method="traefik-route")

    status = await ops_test.model.get_status()  # noqa: F821
    assert TRAEFIK_K8S_NAME in status["applications"]
    assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

    app_address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
    url = f"http://{app_address}:8080/debug/status"
    logger.info("Querying app address: %s", url)
    response = requests.get(url, timeout=2.0)
    assert response.status_code == 200

# @pytest.mark.asyncio
# async def test_nginx_ingress_relation(ops_test: OpsTest):
#     """Test the charm integrates with nginx-route."""
#     await deploy_package(ops_test, ingress_method="nginx-route")

#     status = await ops_test.model.get_status()  # noqa: F821
#     assert TRAEFIK_K8S_NAME not in status["applications"]
#     assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

#     app_address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
#     url = f"http://{app_address}:8080/debug/status"
#     logger.info("Querying app address: %s", url)
#     response = requests.get(url, timeout=2.0)
#     assert response.status_code == 200
