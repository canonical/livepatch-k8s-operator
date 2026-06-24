#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the OTLP metrics relation."""

import json
import logging

import pytest
import yaml
from cosl.utils import LZMABase64
from fixtures import deploy_package_if_needed
from helpers import ACTIVE_STATUS, APP_NAME
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

OTEL_COLLECTOR_APP = "opentelemetry-collector-k8s"
OTEL_COLLECTOR_CHANNEL = "dev/edge"
LIVEPATCH_SEND_OTLP_ENDPOINT = "send-otlp"
COLLECTOR_RECEIVE_OTLP_ENDPOINT = "receive-otlp"


async def _get_pebble_env(ops_test: OpsTest) -> dict:
    """Return the environment dict from the livepatch service's current Pebble plan."""
    rc, stdout, stderr = await ops_test.juju(
        "exec",
        "--unit",
        f"{APP_NAME}/0",
        "--",
        "PEBBLE_SOCKET=/charm/containers/livepatch/pebble.socket /charm/bin/pebble plan",
    )
    assert rc == 0, f"pebble plan failed: {stderr}"
    plan = yaml.safe_load(stdout)
    return plan.get("services", {}).get("livepatch", {}).get("environment", {})


async def _get_published_prometheus_alert_names(ops_test: OpsTest) -> set:
    """Return the set of Prometheus alert names livepatch published on the send-otlp relation."""
    # The rules are written to livepatch's own app databag, so they are visible as
    # application-data from the collector side (receive-otlp endpoint).
    rc, stdout, stderr = await ops_test.juju("show-unit", f"{OTEL_COLLECTOR_APP}/0", "--format=yaml")
    assert rc == 0, f"show-unit failed: {stderr}"
    unit_data = yaml.safe_load(stdout)[f"{OTEL_COLLECTOR_APP}/0"]
    app_data = next(
        rel.get("application-data", {})
        for rel in unit_data.get("relation-info", [])
        if rel.get("endpoint") == COLLECTOR_RECEIVE_OTLP_ENDPOINT
    )
    assert "rules" in app_data, f"no alert rules published on {LIVEPATCH_SEND_OTLP_ENDPOINT}: {app_data}"
    rules = json.loads(LZMABase64.decompress(json.loads(app_data["rules"])))
    groups = rules["promql"].get("groups", [])
    return {rule["alert"] for group in groups for rule in group.get("rules", []) if "alert" in rule}


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_deploy_and_relate_otel_collector(ops_test: OpsTest):
    """Test that livepatch can relate to the OpenTelemetry Collector over the OTLP interface."""
    await deploy_package_if_needed(ops_test)

    await ops_test.model.deploy(
        OTEL_COLLECTOR_APP,
        application_name=OTEL_COLLECTOR_APP,
        channel=OTEL_COLLECTOR_CHANNEL,
        trust=True,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[OTEL_COLLECTOR_APP],
            raise_on_blocked=False,
            timeout=600,
        )

    await ops_test.model.relate(
        f"{APP_NAME}:{LIVEPATCH_SEND_OTLP_ENDPOINT}",
        f"{OTEL_COLLECTOR_APP}:{COLLECTOR_RECEIVE_OTLP_ENDPOINT}",
    )

    # The collector requires at least one output sink (send-remote-write, send-loki-logs, etc.)
    # to build a valid OTel pipeline.  Deployed standalone here it will go blocked after
    # the receive-otlp relation is created.
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, OTEL_COLLECTOR_APP],
        raise_on_blocked=False,
        timeout=300,
    )

    status = await ops_test.model.get_status()
    assert status["applications"][APP_NAME]["status"].status == ACTIVE_STATUS


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_otel_metrics_env_vars_populated(ops_test: OpsTest):
    """Test that the OTLP endpoint env vars are populated in the Pebble plan after relating."""
    env = await _get_pebble_env(ops_test)

    assert env.get(
        "LP_OTEL_METRICS_OTLP_ENDPOINT"
    ), "LP_OTEL_METRICS_OTLP_ENDPOINT should be set after relating to the collector"
    assert env.get("LP_OTEL_METRICS_PROTOCOL") in (
        "grpc",
        "http",
    ), f"LP_OTEL_METRICS_PROTOCOL should be 'grpc' or 'http', got {env.get('LP_OTEL_METRICS_PROTOCOL')!r}"
    logger.info(
        "OTLP endpoint: %s, protocol: %s, insecure: %s",
        env.get("LP_OTEL_METRICS_OTLP_ENDPOINT"),
        env.get("LP_OTEL_METRICS_PROTOCOL"),
        env.get("LP_OTEL_METRICS_INSECURE"),
    )


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_prometheus_alert_rules_published(ops_test: OpsTest):
    """Test that livepatch publishes its Prometheus alert rules over the send-otlp relation."""
    alert_names = await _get_published_prometheus_alert_names(ops_test)
    expected = {
        "LivepatchDatabaseResponseTimeHigh",
        "LivepatchDatabaseErrorsHigh",
        "LivepatchEndpointLatencyHigh",
        "LivepatchContractsServerErrorsHigh",
        "LivepatchContractsTokenErrorsHigh",
    }
    assert expected.issubset(
        alert_names
    ), f"published Prometheus alert rules {alert_names} should include all livepatch alerts {expected}"


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_otel_metrics_enabled_flag_is_independent(ops_test: OpsTest):
    """Test that LP_OTEL_METRICS_ENABLED is driven by config, not the relation."""
    env = await _get_pebble_env(ops_test)

    # The relation being present must NOT auto-enable exporting; the operator
    # must explicitly set otel-metrics.enabled=true.
    assert (
        env.get("LP_OTEL_METRICS_ENABLED") == "false"
    ), "LP_OTEL_METRICS_ENABLED should not be set to 'true' by the relation alone"


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_otel_metrics_enabled_via_config(ops_test: OpsTest):
    """Test that setting otel-metrics.enabled=true is reflected in the Pebble env."""
    await ops_test.model.applications[APP_NAME].set_config({"otel-metrics.enabled": "true"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status=ACTIVE_STATUS,
            timeout=120,
        )

    env = await _get_pebble_env(ops_test)
    assert (
        env.get("LP_OTEL_METRICS_ENABLED") == "true"
    ), "LP_OTEL_METRICS_ENABLED should be 'true' after setting otel-metrics.enabled config"

    # Reset for subsequent tests.
    await ops_test.model.applications[APP_NAME].set_config({"otel-metrics.enabled": "false"})
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status=ACTIVE_STATUS, timeout=120)


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_otel_metrics_relation_removed_clears_env_vars(ops_test: OpsTest):
    """Test that removing the OTLP relation clears the endpoint env vars."""
    await ops_test.model.applications[APP_NAME].remove_relation(
        LIVEPATCH_SEND_OTLP_ENDPOINT,
        f"{OTEL_COLLECTOR_APP}:{COLLECTOR_RECEIVE_OTLP_ENDPOINT}",
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status=ACTIVE_STATUS,
            timeout=300,
        )

    env = await _get_pebble_env(ops_test)

    assert not env.get(
        "LP_OTEL_METRICS_OTLP_ENDPOINT"
    ), "LP_OTEL_METRICS_OTLP_ENDPOINT should be cleared after removing the relation"
    assert not env.get(
        "LP_OTEL_METRICS_PROTOCOL"
    ), "LP_OTEL_METRICS_PROTOCOL should be cleared after removing the relation"


@pytest.mark.asyncio
async def test_cleanup(ops_test: OpsTest):
    """Remove the OpenTelemetry Collector application."""
    if OTEL_COLLECTOR_APP in ops_test.model.applications:
        await ops_test.model.remove_application(OTEL_COLLECTOR_APP, block_until_done=True)
