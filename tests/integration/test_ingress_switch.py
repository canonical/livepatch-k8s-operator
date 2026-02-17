#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
import requests
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_NAME, NGINX_INGRESS_CHARM_NAME, TRAEFIK_K8S_NAME
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_nginx_ingress_switch(ops_test: OpsTest):
    """Test the charm can integrate with different ingress integrators (provides the relation is removed)."""
    
    await deploy_package(ops_test, ingress_method="nginx-route")

    status = await ops_test.model.get_status()  # noqa: F821
    assert NGINX_INGRESS_CHARM_NAME in status["applications"]
    assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

    app_address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
    url = f"http://{app_address}:8080/debug/status"
    logger.info("Querying app address: %s", url)
    response = requests.get(url, timeout=2.0)
    assert response.status_code == 200

    # Switch to traefik-route 
    await ops_test.model.remove_relation(f"{APP_NAME}:nginx-route", f"{NGINX_INGRESS_CHARM_NAME}:nginx-route")
    await ops_test.model.applications[APP_NAME].set_config({"ingress-method": "traefik-route"})
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status=ACTIVE_STATUS, raise_on_blocked=False, timeout=600)
    await ops_test.model.relate(f"{APP_NAME}:ingress", f"{TRAEFIK_K8S_NAME}:ingress")

    status = await ops_test.model.get_status()  # noqa: F821
    assert TRAEFIK_K8S_NAME in status["applications"]
    assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

    app_address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
    url = f"http://{app_address}:8080/debug/status"
    logger.info("Querying app address: %s", url)
    response = requests.get(url, timeout=2.0)
    assert response.status_code == 200
