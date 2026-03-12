#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pathlib
import asyncio
import json
import time

import pytest
import requests
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_NAME, extract_version_from_metadata
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


async def _wait_for_unit_agent_idle(ops_test: OpsTest, app: str, unit_num: int = 0, timeout: float = 300.0) -> None:
    """Wait until a unit's agent-status is idle.

    This is safe even if the workload status is Blocked/Waiting.
    """
    unit_name = f"{app}/{unit_num}"
    deadline = time.monotonic() + timeout
    last_agent_status = None

    while time.monotonic() < deadline:
        status = await ops_test.model.get_status()
        unit = status["applications"][app]["units"][unit_name]
        agent_status = unit.get("agent-status", {}).get("current")
        last_agent_status = agent_status
        if agent_status == "idle":
            return
        await asyncio.sleep(2)

    raise TimeoutError(f"Timed out waiting for {unit_name} agent idle (last={last_agent_status!r})")


async def _wait_for_gateway_route_requirer_app_data(
    ops_test: OpsTest,
    requirer_app: str = "gateway-route-configurator",
    timeout: float = 300.0,
) -> None:
    """Wait until the gateway-route requirer has published the required app data."""
    required = {"hostname", "model", "name", "port"}
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        exit_code, stdout, _ = await ops_test.juju("show-relation", "gateway-route", "--format=json")
        if exit_code == 0 and stdout.strip():
            try:
                relations = json.loads(stdout)
            except json.JSONDecodeError:
                relations = None

            if isinstance(relations, list):
                for rel in relations:
                    app_data = (
                        rel.get("applications", {})
                        .get(requirer_app, {})
                        .get("application-data", {})
                    )
                    if required.issubset(app_data.keys()) and all(str(app_data[k]).strip() for k in required):
                        return

        await asyncio.sleep(2)

    raise TimeoutError(
        f"Timed out waiting for {requirer_app} gateway-route app data keys: {sorted(required)}"
    )


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

    await ops_test.model.relate(f"{APP_NAME}:ingress", "gateway-route-configurator:ingress")

    await _wait_for_unit_agent_idle(ops_test, "gateway-route-configurator", timeout=600)

    await ops_test.model.relate(
        "gateway-api-integrator:gateway-route", "gateway-route-configurator:gateway-route"
    )

    await _wait_for_gateway_route_requirer_app_data(ops_test, timeout=600)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "gateway-route-configurator", "gateway-api-integrator", "self-signed-certificates"],
        status=ACTIVE_STATUS,
        idle_period=30,
        timeout=1200,
    )
