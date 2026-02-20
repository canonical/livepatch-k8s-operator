# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.


import asyncio
import logging

import pytest
from charm_utils import fetch_charm
from helpers import (
    ACTIVE_STATUS,
    APP_NAME,
    BLOCKED_STATUS,
    POSTGRESQL_CHANNEL,
    POSTGRESQL_NAME,
    get_unit_url,
    oci_image,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def deploy_package(
    ops_test: OpsTest,
    use_current_stable: bool = False,
):
    """
    Deploy the application and its dependencies.

    :param use_current_stable: if True, the current latest stable release of the
                               charm is deployed (instead of the charm in the
                               current working directory)
    """

    jammy = "ubuntu@22.04"
    config = {
        "patch-storage.type": "postgres",
        "auth.basic.enabled": True,
        "contracts.enabled": False,
        "patch-cache.cache-size": 128,
        "patch-cache.cache-ttl": "1h",
        "patch-cache.enabled": True,
        "patch-sync.enabled": True,
        "server.burst-limit": 500,
        "server.concurrency-limit": 50,
        "server.is-hosted": True,
        "server.log-level": "info",
    }

    if use_current_stable:
        logger.info("Deploying current stable release")
        deployed_application = ops_test.model.deploy(
            APP_NAME,
            config=config,
            num_units=1,
            application_name=APP_NAME,
            base=jammy,
        )
    else:
        logger.info("Building local charm")
        charm = await fetch_charm(ops_test)
        deployed_application = ops_test.model.deploy(
            charm,
            config=config,
            resources=get_charm_resources(),
            num_units=1,
            application_name=APP_NAME,
            base=jammy,
        )

    asyncio.gather(
        deployed_application,
        ops_test.model.deploy(
            POSTGRESQL_NAME,
            base=jammy,
            channel=POSTGRESQL_CHANNEL,
            trust=True,
            application_name=POSTGRESQL_NAME,
        ),
    )

    async with ops_test.fast_forward():
        logger.info(f"Waiting for {POSTGRESQL_NAME}")
        await ops_test.model.wait_for_idle(
            apps=[POSTGRESQL_NAME], status=ACTIVE_STATUS, raise_on_blocked=False, timeout=600
        )
        logger.info(f"Waiting for {APP_NAME}")
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status=BLOCKED_STATUS, raise_on_blocked=False, timeout=600)

        logger.info("Making relations")
        await perform_livepatch_integrations(ops_test)
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status=BLOCKED_STATUS, raise_on_blocked=False, timeout=600)

        logger.info("Setting server.url-template")
        url = await get_unit_url(ops_test, application=APP_NAME, unit=0, port=8080)
        url_template = f"{url}/v1/patches/{{filename}}"
        logger.info(f"Set server.url-template to {url_template}")
        await ops_test.model.applications[APP_NAME].set_config({"server.url-template": url_template})

        # The charm automatically triggers schema upgrade after the integrations are made.
        # So, we should wait for an active state.
        logger.info("Waiting for active idle")
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status=ACTIVE_STATUS, raise_on_blocked=False, timeout=300)
        assert ops_test.model.applications[APP_NAME].units[0].workload_status == ACTIVE_STATUS


async def perform_livepatch_integrations(ops_test: OpsTest):
    """Add relations between Livepatch charm and postgresql-k8s.

    Args:
        ops_test: PyTest object.
    """
    logger.info("Integrating Livepatch and Postgresql")
    await ops_test.model.relate(f"{APP_NAME}:database", f"{POSTGRESQL_NAME}:database")


def get_charm_resources():
    """Get charm resources dict."""
    return {
        "livepatch-server-image": oci_image("./metadata.yaml", "livepatch-server-image"),
        "livepatch-schema-upgrade-tool-image": oci_image("./metadata.yaml", "livepatch-schema-upgrade-tool-image"),
    }

