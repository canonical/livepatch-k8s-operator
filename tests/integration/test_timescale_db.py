#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for MetricsDB functionality."""

import logging

import pytest
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_NAME, ensure_model
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

POSTGRESQL_METRICS_APP = "postgresql-metrics"
POSTGRESQL_METRICS_CHARM = "postgresql"
POSTGRESQL_METRICS_CHANNEL = "16/stable"
POSTGRESQL_METRICS_ENDPOINT = "database"
METRICS_OFFER_NAME = "postgresql-metrics-offer"
TEST_NAME = "timescale-db"


@pytest.mark.asyncio
async def test_metrics_db_relation_integration(ops_test: OpsTest):
    """Test MetricsDB relation integration using a machine PostgreSQL offer."""
    k8s_model_name = await ensure_model(TEST_NAME, ops_test, cloud_name="microk8s", cloud_type="k8s")
    lxd_model_name = await ensure_model(TEST_NAME, ops_test, cloud_name="localhost", cloud_type="lxd")

    with ops_test.model_context(k8s_model_name):
        await deploy_package(ops_test)

    with ops_test.model_context(lxd_model_name):
        await ops_test.model.deploy(
            POSTGRESQL_METRICS_CHARM,
            application_name=POSTGRESQL_METRICS_APP,
            channel=POSTGRESQL_METRICS_CHANNEL,
            base="ubuntu@24.04",
            num_units=1,
        )

        await ops_test.model.wait_for_idle(
            apps=[POSTGRESQL_METRICS_APP],
            status=ACTIVE_STATUS,
            timeout=600,
        )

        logger.info("Creating application offer for cross-model relation: %s", METRICS_OFFER_NAME)
        await ops_test.model.create_offer(
            endpoint=f"{POSTGRESQL_METRICS_APP}:{POSTGRESQL_METRICS_ENDPOINT}",
            offer_name=METRICS_OFFER_NAME,
        )

    with ops_test.model_context(k8s_model_name):
        await ops_test.juju("consume", f"{lxd_model_name}.{METRICS_OFFER_NAME}")
        await ops_test.model.relate(f"{APP_NAME}:metrics-db", METRICS_OFFER_NAME)

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status=ACTIVE_STATUS,
            timeout=300,
        )

        status = await ops_test.model.get_status()
        assert status["applications"][APP_NAME]["status"].status == ACTIVE_STATUS
        assert len(status["applications"][APP_NAME]["relations"]["metrics-db"]) > 0


@pytest.mark.asyncio
async def test_metrics_db_config_options(ops_test: OpsTest):
    """Test MetricsDB configuration options."""
    k8s_model_name = await ensure_model(TEST_NAME, ops_test, cloud_name="microk8s", cloud_type="k8s")

    with ops_test.model_context(k8s_model_name):
        await ops_test.model.applications[APP_NAME].set_config(
            {
                "timescale_db.connection_pool_max": "25",
                "timescale_db.connection_lifetime_max": "20m",
                "timescale_db.work_mem": "64",
            }
        )

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status=ACTIVE_STATUS,
            timeout=120,
        )

        status = await ops_test.model.get_status()
        assert status["applications"][APP_NAME]["status"].status == ACTIVE_STATUS


@pytest.mark.asyncio
async def test_metrics_db_relation_broken(ops_test: OpsTest):
    """Test behavior when MetricsDB relation is broken."""
    k8s_model_name = await ensure_model(TEST_NAME, ops_test, cloud_name="microk8s", cloud_type="k8s")

    with ops_test.model_context(k8s_model_name):
        await ops_test.model.applications[APP_NAME].remove_relation("metrics-db", METRICS_OFFER_NAME)

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status=ACTIVE_STATUS,
            timeout=300,
        )

        status = await ops_test.model.get_status()
        assert status["applications"][APP_NAME]["status"].status == ACTIVE_STATUS

        relations = status["applications"][APP_NAME].get("relations", {})
        assert "metrics-db" not in relations or len(relations["metrics-db"]) == 0


@pytest.mark.asyncio
async def test_cleanup(ops_test: OpsTest):
    """Clean up test artifacts."""
    k8s_model_name = await ensure_model(TEST_NAME, ops_test, cloud_name="microk8s", cloud_type="k8s")
    lxd_model_name = await ensure_model(TEST_NAME, ops_test, cloud_name="localhost", cloud_type="lxd")

    with ops_test.model_context(lxd_model_name):
        if POSTGRESQL_METRICS_APP in ops_test.model.applications:
            await ops_test.model.remove_application(POSTGRESQL_METRICS_APP, block_until_done=True)

    with ops_test.model_context(k8s_model_name):
        if APP_NAME in ops_test.model.applications:
            await ops_test.model.remove_application(APP_NAME, block_until_done=True)
