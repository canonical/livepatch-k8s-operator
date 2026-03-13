#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pytest

from fixtures import deploy_package_if_needed
from helpers import ACTIVE_STATUS, APP_NAME
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_charm_integrates(ops_test: OpsTest):
    """Test charm can integrate both ingress interfaces."""
    await deploy_package_if_needed(ops_test)
    
    # nginx
    await ops_test.model.applications[APP_NAME].set_config({"ingress-interface": "legacy-nginx-route"})
    await ops_test.model.deploy("nginx-ingress-integrator", channel="latest/stable", trust=True)
    await ops_test.model.relate(f"{APP_NAME}:nginx-route", "nginx-ingress-integrator:nginx-route")
    await ops_test.model.wait_for_idle(apps=[APP_NAME, "nginx-ingress-integrator"], status=ACTIVE_STATUS)

    assert ops_test.model.applications["nginx-ingress-integrator"].status == ACTIVE_STATUS

    # ingress via gateway API
    await ops_test.model.applications[APP_NAME].set_config({"ingress-interface": "ingress"})

    await ops_test.model.deploy("self-signed-certificates", channel="1/stable")
    await ops_test.model.deploy("gateway-api-integrator", channel="latest/stable", trust=True)
    await ops_test.model.deploy("gateway-route-configurator", channel="latest/stable")

    external_hostname = "livepatch.com"
    await ops_test.model.applications["gateway-api-integrator"].set_config(
        {"gateway-class": "traefik", "external-hostname": external_hostname}
    )
    await ops_test.model.applications["gateway-route-configurator"].set_config({"hostname": external_hostname})

    await ops_test.model.relate(
        "self-signed-certificates:certificates", "gateway-api-integrator:certificates"
    )
    await ops_test.model.relate(
        "gateway-api-integrator:gateway-route", "gateway-route-configurator:gateway-route"
    )

    await ops_test.model.relate(f"{APP_NAME}:ingress", "gateway-route-configurator:ingress")

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "gateway-route-configurator", "gateway-api-integrator", "self-signed-certificates"],
        status=ACTIVE_STATUS,
        idle_period=30,
        timeout=1200,
    )

    assert ops_test.model.applications["gateway-api-integrator"].status == ACTIVE_STATUS
    assert ops_test.model.applications["gateway-route-configurator"].status == ACTIVE_STATUS
    assert ops_test.model.applications["self-signed-certificates"].status == ACTIVE_STATUS
    