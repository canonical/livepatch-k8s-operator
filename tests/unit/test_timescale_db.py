# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Additional unit tests specifically for MetricsDB functionality."""

import unittest
from unittest.mock import Mock, patch
from ops.testing import Harness
from src.charm import LivepatchCharm


class TestMetricsDBFunctionality(unittest.TestCase):
    """Test MetricsDB specific functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.harness = Harness(LivepatchCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_get_db_info_with_metrics_relation(self):
        """Test _get_db_info method works correctly with MetricsDB relation."""
        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        mock_relation_data = {
            metrics_rel_id: {
                "endpoints": "postgres.example.com:5432,postgres2.example.com:5432",
                "username": "metrics_user",
                "password": "metrics_pass",
            }
        }

        with patch.object(self.harness.charm.metrics_db, 'is_resource_created', return_value=True), \
             patch.object(self.harness.charm.metrics_db, 'fetch_relation_data', return_value=mock_relation_data):
            
            db_info = self.harness.charm._get_db_info(self.harness.charm.metrics_db)
            
            self.assertIsNotNone(db_info)
            self.assertEqual(db_info["endpoint"], "postgres.example.com:5432")
            self.assertEqual(db_info["user"], "metrics_user")
            self.assertEqual(db_info["password"], "metrics_pass")

    def test_get_db_info_returns_none_when_no_resource(self):
        """Test _get_db_info returns None when database resource not created."""
        self.harness.add_relation("metrics-db", "postgresql")

        with patch.object(self.harness.charm.metrics_db, 'is_resource_created', return_value=False):
            db_info = self.harness.charm._get_db_info(self.harness.charm.metrics_db)
            self.assertIsNone(db_info)

    def test_get_db_info_returns_none_when_no_relation_data(self):
        """Test _get_db_info returns None when relation data is missing."""
        metrics_rel_id = self.harness.add_relation("metrics-db", "postgresql")
        self.harness.add_relation_unit(metrics_rel_id, "postgresql/0")

        with patch.object(self.harness.charm.metrics_db, 'is_resource_created', return_value=True), \
             patch.object(self.harness.charm.metrics_db, 'fetch_relation_data', return_value={}):
            
            db_info = self.harness.charm._get_db_info(self.harness.charm.metrics_db)
            self.assertIsNone(db_info)
