# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Additional unit tests specifically for MetricsDB functionality."""

import unittest
from unittest.mock import patch

from ops.testing import Harness

from src.charm import LivepatchCharm

APP_NAME = "canonical-livepatch-server-k8s"


class TestMetricsDBFunctionality(unittest.TestCase):
    """Test MetricsDB specific functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.harness = Harness(LivepatchCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.disable_hooks()
        self.harness.begin()
        self.harness.set_leader(True)

        rel_id = self.harness.add_relation("livepatch", "livepatch")
        self.harness.add_relation_unit(rel_id, f"{APP_NAME}/1")

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

    def test_schema_upgrade_skips_timescale_when_influx_enabled(self):
        """Test schema upgrade skips Timescale when Influx is enabled."""
        self.harness.charm._state.dsn = "postgresql://primary"
        self.harness.charm._state.dsn_metrics = "postgresql://timescale"
        self.harness.model.unit.get_container("livepatch-schema-upgrade").can_connect = lambda: True

        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")
        self.harness.update_config({"influx.enabled": True})

        with patch("src.charm.LivepatchCharm.migration_is_required", return_value=True) as migration:
            with patch("src.charm.LivepatchCharm.schema_upgrade") as schema_upgrade:
                self.harness.charm.handle_schema_upgrade()

        self.assertEqual(migration.call_count, 1)
        self.assertEqual(migration.call_args.args[1], "postgresql://primary")

        self.assertEqual(schema_upgrade.call_count, 1)
        self.assertEqual(schema_upgrade.call_args.args[1], "postgresql://primary")

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