#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
import requests
from conftest import fetch_charm, get_charm_resources
from helpers import ACTIVE_STATUS, APP_NAME
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.usefixtures("deploy_current_stable")
async def test_upgrade(ops_test: OpsTest):
    """Test upgrading from the current stable release works as expected."""

    logger.info("Getting model status")
    status = await ops_test.model.get_status()  # noqa: F821
    logger.info(f"Status: {status}")
    assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

    address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
    url = f"http://{address}:8080/debug/status"
    logger.info("Querying app address: %s", url)
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200
    logger.info(f"Output = {r.json()}")

    # Deploy the locally built charm and wait for active/idle status
    logger.info("refreshing running application with the new local charm")

    charm = await fetch_charm(ops_test)
    await ops_test.model.applications[APP_NAME].refresh(
        path=charm,
        resources=get_charm_resources(),
    )

    logger.info("waiting for the upgraded unit to be ready")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status=ACTIVE_STATUS,
        timeout=600,
    )

    logger.info("Getting model status after upgrade")
    status = await ops_test.model.get_status()  # noqa: F821
    logger.info(f"Status: {status}")
    assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

    address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
    url = f"http://{address}:8080/debug/status"
    logger.info("Querying app address: %s", url)
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200
    logger.info(f"Output = {r.json()}")
