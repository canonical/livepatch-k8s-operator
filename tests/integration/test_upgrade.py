#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import requests
from fixtures import deploy_package, fetch_charm, get_charm_resources
from helpers import ACTIVE_STATUS, APP_NAME, ensure_model
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


async def test_upgrade(ops_test: OpsTest):
    """Test upgrading from the current stable release works as expected."""

    test_name = "upgrade"
    await deploy_package(ops_test, test_name, use_current_stable=True)

    k8s_model_name = await ensure_model(test_name, ops_test, cloud_name="microk8s", cloud_type="k8s")
    with ops_test.model_context(k8s_model_name):
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
