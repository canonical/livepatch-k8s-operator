#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
import requests
from helpers import ACTIVE_STATUS, APP_NAME
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.usefixtures("deploy")
async def test_application_is_up(ops_test: OpsTest):
    """Test the app is up and running."""

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
