#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for MetricsDB functionality."""

import logging

import pytest
from helpers import ACTIVE_STATUS, APP_NAME
from fixtures import deploy_package
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_metrics_db_relation_integration(ops_test: OpsTest):
    """Test MetricsDB relation integration with PostgreSQL charm."""
    await deploy_package(ops_test)

    await ops_test.model.deploy(
        "postgresql-k8s",
        application_name="postgresql-metrics",
        channel="16/beta", 
        trust=True,
        config={
        "plugin-timescaledb-enable": True,
        }
    )
    
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "postgresql-metrics"],
        status=ACTIVE_STATUS,
        timeout=600,
    )
    
    await ops_test.model.integrate(APP_NAME + ":metrics-db", "postgresql-metrics:database")
    
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "postgresql-metrics"],
        status=ACTIVE_STATUS,
        timeout=300,
    )
    
    status = await ops_test.model.get_status()
    assert status["applications"][APP_NAME]["status"] == ACTIVE_STATUS
    assert len(status["applications"][APP_NAME]["relations"]["metrics-db"]) > 0


@pytest.mark.asyncio 
async def test_metrics_db_config_options(ops_test: OpsTest):
    """Test MetricsDB configuration options."""
    
    await ops_test.model.applications[APP_NAME].set_config({
        "timescale.connection-pool-max": "25",
        "timescale.connection-lifetime-max": "20m",
        "timescale.work_mem": "64",
    })
    
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status=ACTIVE_STATUS,
        timeout=120,
    )
    
    status = await ops_test.model.get_status()
    assert status["applications"][APP_NAME]["status"] == ACTIVE_STATUS


@pytest.mark.asyncio
async def test_metrics_db_influx_mutual_exclusion(ops_test: OpsTest):
    """Test that MetricsDB and InfluxDB are mutually exclusive."""
    
    await ops_test.model.applications[APP_NAME].set_config({
        "influx.enabled": "true",
        "influx.url": "http://influx.example.com:8086",
        "influx.token": "test-token",
        "influx.bucket": "test-bucket",
        "influx.organization": "test-org",
    })
    
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status=ACTIVE_STATUS,
        timeout=120,
    )
    
    status = await ops_test.model.get_status()
    assert status["applications"][APP_NAME]["status"] == ACTIVE_STATUS
    
    await ops_test.model.applications[APP_NAME].set_config({
        "influx.enabled": "false",
    })
    
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status=ACTIVE_STATUS,
        timeout=120,
    )
    
    status = await ops_test.model.get_status()
    assert status["applications"][APP_NAME]["status"] == ACTIVE_STATUS


@pytest.mark.asyncio
async def test_metrics_db_relation_broken(ops_test: OpsTest):
    """Test behavior when MetricsDB relation is broken."""
    
    await ops_test.model.applications[APP_NAME].remove_relation("metrics-db", "postgresql-metrics:database")
    
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "postgresql-metrics"],
        status=ACTIVE_STATUS,
        timeout=300,
    )
    
    status = await ops_test.model.get_status()
    assert status["applications"][APP_NAME]["status"] == ACTIVE_STATUS
    
    relations = status["applications"][APP_NAME].get("relations", {})
    assert "metrics-db" not in relations or len(relations["metrics-db"]) == 0


@pytest.mark.asyncio
async def test_cleanup(ops_test: OpsTest):
    """Clean up test artifacts."""
    
    await ops_test.model.remove_application("postgresql-metrics", block_until_done=True)
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)