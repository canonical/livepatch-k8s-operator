# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Additional unit tests specifically for MetricsDB functionality."""

from typing import Any, Dict
import unittest
from unittest.mock import Mock, patch
import pathlib
import os

from ops.testing import Harness

from src.charm import LivepatchCharm

TEST_TOKEN = "test-token"  # nosec
APP_NAME = "canonical-livepatch-server-k8s"


class TestMetricsDBFunctionality(unittest.TestCase):
    """Test MetricsDB specific functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.harness = Harness(LivepatchCharm)
        self.addCleanup(self.harness.cleanup)

        self.version_file = pathlib.Path("version")
        pathlib.Path.touch(self.version_file)
        self.addCleanup(lambda: os.remove(self.version_file))

        self.harness.disable_hooks()
        self.harness.add_oci_resource("livepatch-server-image")
        self.harness.add_oci_resource("livepatch-schema-upgrade-tool-image")
        self.harness.begin()
        self.harness.set_leader(True)

        rel_id = self.harness.add_relation("livepatch", "livepatch")
        self.harness.add_relation_unit(rel_id, f"{APP_NAME}/1")
        self.harness.container_pebble_ready("livepatch")
        self.harness.container_pebble_ready("livepatch-schema-upgrade")

    def _assert_environment_contains(self, contains: Dict[str, Any]):
        """Assert Pebble plan environment contains given key/value pairs."""
        plan = self.harness.get_container_pebble_plan("livepatch")
        environment = plan.to_dict()["services"]["livepatch"]["environment"]
        self.assertEqual(environment, environment | contains, "environment does not contain expected key/value pairs")

    def start_container(self):
        """Setup and start a configured container."""
        self.harness.charm._state.dsn = "postgresql://123"
        self.harness.charm._state.resource_token = TEST_TOKEN

        container = self.harness.model.unit.get_container("livepatch")
        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

            self.harness.update_config(
                {
                    "auth.sso.enabled": True,
                    "patch-storage.type": "filesystem",
                    "patch-storage.filesystem-path": "/srv/",
                    "patch-cache.enabled": True,
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": True,
                    "contracts.url": "http://contracts.host.name",
                }
            )
            self.harness.charm.on.config_changed.emit()

            # Emit the pebble-ready event for livepatch
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Check the that the plan was updated
        plan = self.harness.get_container_pebble_plan("livepatch")
        required_environment = {
            "LP_AUTH_SSO_ENABLED": True,
            "LP_PATCH_STORAGE_TYPE": "filesystem",
            "LP_PATCH_STORAGE_FILESYSTEM_PATH": "/srv/",
            "LP_PATCH_CACHE_ENABLED": True,
            "LP_DATABASE_CONNECTION_STRING": "postgresql://123",
            "LP_CONTRACTS_URL": "http://contracts.host.name",
        }
        environment = plan.to_dict()["services"]["livepatch"]["environment"]
        self.assertEqual(environment, environment | required_environment)
    def test_get_db_info_with_metrics_relation(self):
        """Test _get_db_info method works correctly with MetricsDB relation."""
        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        mock_relation_data = {
            metrics_rel_id: {
                "endpoints": "postgres.example.com:5432,postgres2.example.com:5432",
                "username": "metrics_user",
                "password": "metrics_pass",  # nosec B105
            }
        }

        with patch.object(self.harness.charm.metrics_db, "is_resource_created", return_value=True), patch.object(
            self.harness.charm.metrics_db, "fetch_relation_data", return_value=mock_relation_data
        ):

            db_info = self.harness.charm._get_db_info(self.harness.charm.metrics_db)

            self.assertIsNotNone(db_info)
            self.assertEqual(db_info["endpoint"], "postgres.example.com:5432")
            self.assertEqual(db_info["user"], "metrics_user")
            self.assertEqual(db_info["password"], "metrics_pass")

    def test_get_db_info_returns_none_when_no_resource(self):
        """Test _get_db_info returns None when database resource not created."""
        self.harness.add_relation("metrics-db", "postgresql")

        with patch.object(self.harness.charm.metrics_db, "is_resource_created", return_value=False):
            db_info = self.harness.charm._get_db_info(self.harness.charm.metrics_db)
            self.assertIsNone(db_info)

    def test_get_db_info_returns_none_when_no_relation_data(self):
        """Test _get_db_info returns None when relation data is missing."""
        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        with patch.object(self.harness.charm.metrics_db, "is_resource_created", return_value=True), patch.object(
            self.harness.charm.metrics_db, "fetch_relation_data", return_value={}
        ):

            db_info = self.harness.charm._get_db_info(self.harness.charm.metrics_db)
            self.assertIsNone(db_info)

    def test_schema_upgrade_runs_for_timescale_when_enabled(self):
        """Test schema upgrade checks and upgrades Timescale when enabled."""
        self.harness.charm._state.dsn = "postgresql://primary"
        self.harness.charm._state.dsn_metrics = "postgresql://timescale"
        self.harness.model.unit.get_container("livepatch-schema-upgrade").can_connect = lambda: True

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")
        self.harness.update_config({"influx.enabled": False})

        with patch("src.charm.LivepatchCharm.migration_is_required", side_effect=[True, True]) as migration:
            with patch("src.charm.LivepatchCharm.schema_upgrade") as schema_upgrade:
                self.harness.charm.handle_schema_upgrade()

        self.assertEqual(migration.call_count, 2)
        self.assertEqual(migration.call_args_list[0].args[1], "postgresql://primary")
        self.assertEqual(migration.call_args_list[1].args[1], "postgresql://timescale")

        self.assertEqual(schema_upgrade.call_count, 2)
        self.assertEqual(schema_upgrade.call_args_list[0].args[1], "postgresql://primary")
        self.assertEqual(schema_upgrade.call_args_list[1].args[1], "postgresql://timescale")

    def test_schema_upgrade_action_runs_for_timescale_when_enabled(self):
        """Test schema-upgrade action upgrades both primary and Timescale databases."""
        self.harness.charm._state.dsn = "postgresql://primary"
        self.harness.charm._state.dsn_metrics = "postgresql://timescale"
        self.harness.model.unit.get_container("livepatch-schema-upgrade").can_connect = lambda: True

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")
        self.harness.update_config({"influx.enabled": False})

        with patch("src.charm.LivepatchCharm.schema_upgrade") as schema_upgrade:
            self.harness.run_action("schema-upgrade")

        self.assertEqual(schema_upgrade.call_count, 2)
        self.assertEqual(schema_upgrade.call_args_list[0].args[1], "postgresql://primary")
        self.assertEqual(schema_upgrade.call_args_list[1].args[1], "postgresql://timescale")

    def test_metrics_db_event_handles_relation_created(self):
        """Test MetricsDB relation created event is handled properly."""
        self.harness.set_leader(True)
        self.start_container()
        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        self.harness.update_relation_data(
            metrics_rel_id,
            "postgresql",
            {
                "endpoints": "postgres.local:5432",
                "username": "tsuser",
                "password": "tspass",  # nosec B105
            },
        )

        self.harness.charm._on_metrics_db_event(Mock(relation=Mock(name="metrics-db")))
    
        expected_dsn = "postgresql://tsuser:tspass@postgres.local:5432/livepatch-metrics-db"
        self.assertEqual(self.harness.charm._state.dsn_metrics, expected_dsn)

    def test_metrics_db_environment_variables_set_when_enabled(self):
        """Test MetricsDB environment variables are set when relation exists."""
        self.harness.set_leader(True)
        self.start_container()

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        self.harness.update_config(
            {
                "timescale_db.enabled": True,
                "timescale_db.connection_pool_max": 20,
                "timescale_db.connection_lifetime_max": "30m",
                "timescale_db.work_mem": 32,
            }
        )

        self.harness.charm.on.config_changed.emit()

        self._assert_environment_contains(
            {
                "LP_TIMESCALE_DB_ENABLED": True,
                "LP_TIMESCALE_DB_CONNECTION_POOL_MAX": 20,
                "LP_TIMESCALE_DB_CONNECTION_LIFETIME_MAX": "30m",
                "LP_TIMESCALE_DB_WORK_MEM": 32,
            }
        )

    def test_metrics_db_not_set_when_no_relation(self):
        """Test MetricsDB is not configured when no relation exists."""
        self.harness.set_leader(True)
        self.start_container()

        self.harness.update_config(
            {
                "timescale_db.connection_pool_max": 20,
            }
        )

        self.harness.charm.on.config_changed.emit()

        plan = self.harness.get_container_pebble_plan("livepatch")
        environment = plan.to_dict()["services"]["livepatch"]["environment"]

        self.assertNotIn("LP_TIMESCALE_DB_CONNECTION_STRING", environment)

    def test_metrics_db_event_defers_when_no_db_info(self):
        """Test MetricsDB event is deferred when database info is not available."""
        self.harness.set_leader(True)

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        mock_event = Mock(relation=Mock(name="metrics-db"))
        mock_event.defer = Mock()

        self.harness.charm._on_metrics_db_event(mock_event)

        mock_event.defer.assert_called_once()

    def test_metrics_db_event_ignores_non_leader_units(self):
        """Test MetricsDB event is ignored on non-leader units."""
        self.harness.set_leader(False)
        self.start_container()

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        self.harness.update_relation_data(
            metrics_rel_id,
            "postgresql",
            {
                "endpoints": "postgresql://postgres.local:5432",
                "username": "tsuser",
                "password": "tspass",  # nosec B105
            },
        )

        mock_event = Mock(relation=Mock(name="metrics-db"))

        initial_dsn = getattr(self.harness.charm._state, "dsn_metrics", None)

        self.harness.charm._on_metrics_db_event(mock_event)

        final_dsn = getattr(self.harness.charm._state, "dsn_metrics", None)
        self.assertEqual(initial_dsn, final_dsn)

    def test_metrics_db_partial_config_handling(self):
        """
        Test partial MetricsDB configuration is handled correctly.
        The charm should use default values for missing config options and set environment variables accordingly.
        """

        self.harness.set_leader(True)
        self.start_container()

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        self.harness.update_config(
            {
                "timescale_db.enabled": True,
                "timescale_db.connection_pool_max": 15,
                # Intentionally omit other options
            }
        )

        self.harness.charm.on.config_changed.emit()

        plan = self.harness.get_container_pebble_plan("livepatch")
        environment = plan.to_dict()["services"]["livepatch"]["environment"]

        self.assertIn("LP_TIMESCALE_DB_ENABLED", environment)
        self.assertEqual(environment["LP_TIMESCALE_DB_ENABLED"], True)
        self.assertIn("LP_TIMESCALE_DB_CONNECTION_POOL_MAX", environment)
        self.assertEqual(environment["LP_TIMESCALE_DB_CONNECTION_POOL_MAX"], 15)

        self.assertIn("LP_TIMESCALE_DB_CONNECTION_LIFETIME_MAX", environment)
        self.assertEqual(environment["LP_TIMESCALE_DB_CONNECTION_LIFETIME_MAX"], "10m")
        self.assertIn("LP_TIMESCALE_DB_WORK_MEM", environment)
        self.assertEqual(environment["LP_TIMESCALE_DB_WORK_MEM"], 16)