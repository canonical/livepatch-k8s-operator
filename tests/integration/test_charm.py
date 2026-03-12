#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pathlib

import pytest
import requests
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_NAME, extract_version_from_metadata
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_application_is_up(ops_test: OpsTest):
    """Test the app is up and running."""

    await deploy_package(ops_test)

    logger.info("Getting model status")
    status = await ops_test.model.get_status()  # noqa: F821
    logger.info(f"Status: {status}")
    assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

    app_address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
    url = f"http://{app_address}:8080/debug/status"
    logger.info("Querying app address: %s", url)
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200
    logger.info(f"Output = {r.json()}")


@pytest.mark.abort_on_fail
async def test_charm_version_is_set(ops_test: OpsTest):
    """Test correct version is set"""
    status = await ops_test.model.get_status()
    version = status.applications[APP_NAME].charm_version
    metadata_path = pathlib.Path("metadata.yaml")
    expected_version = extract_version_from_metadata(metadata_path)
    assert version == expected_version, f"expected {expected_version}, got {version}"

@pytest.mark.abort_on_fail
async def test_charm_integrates_with_nginx_route(ops_test: OpsTest):
    """Test charm can integrate with nginx-route interface."""
    await ops_test.model.applications[APP_NAME].set_config({"ingress-interface": "legacy-nginx-route"})
    await ops_test.model.deploy("nginx-ingress-integrator", channel="latest/stable", trust=True)
    await ops_test.model.relate(f"{APP_NAME}:nginx-route", "nginx-ingress-integrator:nginx-route")
    await ops_test.model.wait_for_idle(apps=[APP_NAME, "nginx-ingress-integrator"], status=ACTIVE_STATUS)

    status = await ops_test.model.get_status()
    nginx_app_status = status.applications["nginx-ingress-integrator"]
    assert "Ingress IP(s):" in nginx_app_status, "nginx-ingress-integrator application does not have an ingress address"

@pytest.mark.abort_on_fail
async def test_charm_integrates_with_gateway_api(ops_test: OpsTest):
    """Test charm can integrate with ingress interface via Gateway API."""
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

    status = await ops_test.model.get_status()
    nginx_app_status = status.applications["gateway-api-integrator"]
    assert "Gateway addresses:" in nginx_app_status, "gateway-api-integrator application does not have an ingress address"
