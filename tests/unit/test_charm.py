# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
# Learn more about testing at: https://juju.is/docs/sdk/testing

import os
import pathlib
import unittest
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import yaml
from ops import pebble
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import ActionFailed, Harness
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

from src.charm import LIVEPATCH_SERVICE_NAME, SERVER_PORT,LivepatchCharm
from src.state import State

APP_NAME = "canonical-livepatch-server-k8s"

TEST_TOKEN = "test-token"  # nosec
TEST_CA_CERT = "VGVzdCBDQSBDZXJ0Cg=="
TEST_CA_CERT_1 = "TmV3IFRlc3QgQ0EgQ2VydAo="

TEST_OLD_REACTIVE_CONFIG = "./tests/unit/test-data/old_config.yaml"
EXPECTED_OPS_CONFIG = "./tests/unit/test-data/expected_config.yaml"


class MockOutput:
    """A wrapper class for command output and errors."""

    def __init__(self, stdout, stderr):
        self._stdout = stdout
        self._stderr = stderr

    def wait_output(self):
        """Return the stdout and stderr from running the command."""
        return self._stdout, self._stderr


def mock_exec(_, command, environment) -> MockOutput:
    """Mock Execute the commands."""
    if len(command) != 1:
        return MockOutput("", "unexpected number of commands")
    cmd: str = command[0]
    if cmd == "/usr/bin/pg_isready":
        return MockOutput(0, "")
    if cmd == "/usr/local/bin/livepatch-schema-tool upgrade":
        return MockOutput("", "")
    return MockOutput("", "unexpected command")


# pylint: disable=too-many-public-methods,too-many-lines
class TestCharm(unittest.TestCase):
    """A wrapper class for charm unit tests."""

    def setUp(self):
        self.harness = Harness(LivepatchCharm)
        self.addCleanup(self.harness.cleanup)

        # create version file
        self.version_file = pathlib.Path("version")
        pathlib.Path.touch(self.version_file)
        self.addCleanup(lambda: os.remove(self.version_file))

        self.harness.disable_hooks()
        self.harness.add_oci_resource("livepatch-server-image")
        self.harness.add_oci_resource("livepatch-schema-upgrade-tool-image")
        self.harness.begin()
        rel_id = self.harness.add_relation("livepatch", "livepatch")
        self.harness.add_relation_unit(rel_id, f"{APP_NAME}/1")
        self.harness.container_pebble_ready("livepatch")
        self.harness.container_pebble_ready("livepatch-schema-upgrade")

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

    def test_start_container(self):
        """A test for config changed hook."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        # This should work without an exception.
        self.start_container()

    def test_on_start(self):
        """Test on-start event handler."""
        self.start_container()

        self.harness.charm.on.start.emit()

        self.assertEqual(self.harness.charm.unit.status.name, ActiveStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "")

    def test_on_stop(self):
        """Test on-stop event handler."""
        self.start_container()

        self.harness.charm.on.stop.emit()

        self.assertEqual(self.harness.charm.unit.status.name, WaitingStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "service stopped")

    def test_on_update_status(self):
        """Test on-update-status event handler."""
        self.start_container()

        self.harness.charm.on.update_status.emit()

        self.assertEqual(self.harness.charm.unit.status.name, ActiveStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "")

    def test_restart_action__success(self):
        """Test the scenario where `restart` action finished successfully."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        self.harness.run_action("restart")

        self.assertEqual(self.harness.charm.unit.status.name, ActiveStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "")

    def test_container_config(self):
        """Test specific config values match what is expected."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()
        self.harness.update_config({"patch-sync.sync-tiers": True})

        self.start_container()

        self._assert_environment_contains({"LP_PATCH_SYNC_SYNC_TIERS": True})

    def test_sync_token_set(self):
        """Test specific config values match what is expected."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()
        self.harness.update_config({"patch-sync.token": "AAAABBBB"})

        self.start_container()

        self._assert_environment_contains({"LP_PATCH_SYNC_TOKEN": "AAAABBBB"})  # nosec

    def test_schema_upgrade_action__success(self):
        """Test the scenario where `schema-upgrade` action finishes successfully."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        schema_upgrade_container = self.harness.model.unit.get_container("livepatch-schema-upgrade")

        def container_exists_side_effect(path: str) -> bool:
            if path == "/usr/local/bin/livepatch-schema-tool":
                return True
            return False

        schema_upgrade_container.exists = Mock(side_effect=container_exists_side_effect)

        def container_exec_side_effect(command: List[str]):
            self.assertEqual(
                command,
                [
                    "/usr/local/bin/livepatch-schema-tool",
                    "upgrade",
                    "--db",
                    "postgresql://123",
                ],
            )
            process_mock = Mock()
            process_mock.wait_output.side_effect = lambda: (None, None)
            return process_mock

        schema_upgrade_container.exec = Mock(side_effect=container_exec_side_effect)

        self.harness.run_action("schema-upgrade")

        self.assertEqual(self.harness.charm.unit.status.name, WaitingStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "Schema migration done")

    def test_schema_upgrade_action__failure(self):
        """Test the scenario where `schema-upgrade` action fails."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        schema_upgrade_container = self.harness.model.unit.get_container("livepatch-schema-upgrade")

        def container_exists_side_effect(path: str) -> bool:
            if path == "/usr/local/bin/livepatch-schema-tool":
                return True
            return False

        schema_upgrade_container.exists = Mock(side_effect=container_exists_side_effect)

        def container_exec_side_effect(command: List[str]):
            self.assertEqual(
                command,
                [
                    "/usr/local/bin/livepatch-schema-tool",
                    "upgrade",
                    "--db",
                    "postgresql://123",
                ],
            )

            def throw():
                raise pebble.ExecError([], 1, "", "some error")

            process_mock = Mock()
            process_mock.wait_output.side_effect = throw
            return process_mock

        schema_upgrade_container.exec = Mock(side_effect=container_exec_side_effect)

        with self.assertRaises(ActionFailed) as ex:
            self.harness.run_action("schema-upgrade")

        self.assertEqual(
            ex.exception.message,
            "schema migration failed: non-zero exit code 1 executing [], stdout='', stderr='some error'",
        )

    def test_on_config_changed__failure__cannot_connect_to_schema_upgrade_container(self):
        """
        Test the scenario where `on-config-changed` event handler fails due to
        failure to connect to schema-upgrade container.
        """
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        schema_upgrade_container = self.harness.model.unit.get_container("livepatch-schema-upgrade")
        schema_upgrade_container.can_connect = lambda: False

        self.harness.charm.on.config_changed.emit()

        self.assertEqual(self.harness.charm.unit.status.name, WaitingStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "Waiting to connect - schema container.")

    def test_on_config_changed__failure__dsn_not_set(self):
        """
        Test the scenario where `on-config-changed` event handler fails due to
        unassigned DSN.
        """
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        self.harness.charm._state.dsn = ""

        self.harness.charm.on.config_changed.emit()

        self.assertEqual(self.harness.charm.unit.status.name, BlockedStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "Waiting for postgres relation to be established.")

    def test_on_config_changed__failure__state_not_ready(self):
        """
        Test the scenario where `on-config-changed` event handler fails due to
        `state` not being ready.
        """
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        self.harness.charm._state = State("foo", lambda: None)

        self.harness.charm.on.config_changed.emit()

        # Note that in this case, nothing should happen, including no exception.
        # Also, since the state of the unit is not changed, there's nothing to
        # assert against.

    def test_schema_version_action__success(self):
        """Test the scenario where `schema-version` action finishes successfully."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        schema_upgrade_container = self.harness.model.unit.get_container("livepatch-schema-upgrade")

        def container_exists_side_effect(path: str) -> bool:
            if path == "/usr/local/bin/livepatch-schema-tool":
                return True
            return False

        schema_upgrade_container.exists = Mock(side_effect=container_exists_side_effect)

        def container_exec_side_effect(command: List[str]):
            self.assertEqual(
                command,
                [
                    "/usr/local/bin/livepatch-schema-tool",
                    "check",
                    "--db",
                    "postgresql://123",
                ],
            )
            process_mock = Mock()
            process_mock.wait_output.side_effect = lambda: (None, None)
            return process_mock

        schema_upgrade_container.exec = Mock(side_effect=container_exec_side_effect)

        output = self.harness.run_action("schema-version")

        self.assertEqual(output.results, {"migration-required": False})

    def test_schema_version_action__success__migration_required(self):
        """
        Test the scenario where `schema-version` action finishes successfully
        while database migration is still required.
        """
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        schema_upgrade_container = self.harness.model.unit.get_container("livepatch-schema-upgrade")

        def container_exists_side_effect(path: str) -> bool:
            if path == "/usr/local/bin/livepatch-schema-tool":
                return True
            return False

        schema_upgrade_container.exists = Mock(side_effect=container_exists_side_effect)

        def container_exec_side_effect(command: List[str]):
            self.assertEqual(
                command,
                [
                    "/usr/local/bin/livepatch-schema-tool",
                    "check",
                    "--db",
                    "postgresql://123",
                ],
            )

            def throw():
                raise pebble.ExecError([], 2, "", "exit code of 2 means migration is required")

            process_mock = Mock()
            process_mock.wait_output.side_effect = throw
            return process_mock

        schema_upgrade_container.exec = Mock(side_effect=container_exec_side_effect)

        output = self.harness.run_action("schema-version")

        self.assertEqual(output.results, {"migration-required": True})

    def test_schema_version_action__failure(self):
        """Test the scenario where `schema-version` action fails."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        schema_upgrade_container = self.harness.model.unit.get_container("livepatch-schema-upgrade")

        def container_exists_side_effect(path: str) -> bool:
            if path == "/usr/local/bin/livepatch-schema-tool":
                return True
            return False

        schema_upgrade_container.exists = Mock(side_effect=container_exists_side_effect)

        def container_exec_side_effect(command: List[str]):
            self.assertEqual(
                command,
                [
                    "/usr/local/bin/livepatch-schema-tool",
                    "check",
                    "--db",
                    "postgresql://123",
                ],
            )

            def throw():
                raise pebble.ExecError([], 1, "", "some error")

            process_mock = Mock()
            process_mock.wait_output.side_effect = throw
            return process_mock

        schema_upgrade_container.exec = Mock(side_effect=container_exec_side_effect)

        with self.assertRaises(ActionFailed) as ex:
            self.harness.run_action("schema-version")

        self.assertEqual(
            ex.exception.message,
            "schema version check failed: non-zero exit code 1 executing [], stdout='', stderr='some error'",
        )

    def test_get_resource_token_action__success(self):
        """Test the scenario where `get-resource-token` action finishes successfully."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()
        del self.harness.charm._state.resource_token
        contracts_url = self.harness.charm.config.get("contracts.url")

        def make_request_side_effect(method: str, url: str, *args, **kwargs):
            if method == "POST":
                self.assertEqual(url, f"{contracts_url}/v1/context/machines/token")
                return {"machineToken": "some-machine-token"}
            if method == "GET":
                self.assertEqual(
                    url, f"{contracts_url}/v1/resources/livepatch-onprem/context/machines/livepatch-onprem"
                )
                return {"resourceToken": "some-resource-token"}
            raise AssertionError("unexpected request")

        with patch("utils.make_request", Mock(side_effect=make_request_side_effect)):
            output = self.harness.run_action("get-resource-token", {"contract-token": "some-token"})

        self.assertEqual(self.harness.charm._state.resource_token, "some-resource-token")
        self.assertEqual(output.results, {"result": "resource token set"})

    def test_emit_updated_config__failure_bad_format(self):
        """Test the scenario where `emit-updated-config` action fails due to bad yaml formatting."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()
        self.start_container()

        old_config = "invalid"

        with self.assertRaises(ActionFailed) as ex:
            self.harness.run_action("emit-updated-config", {"config-file": old_config})

        self.assertEqual(ex.exception.message, "invalid config file format. Got content invalid")

    def test_emit_updated_config__failure_missing_value(self):
        """Test the scenario where `emit-updated-config` action fails."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()
        self.start_container()

        old_config = """application: canonical-livepatch-server
application-config:
  trust:
    default: false
    description: Does this application have access to trusted credentials
    source: default
    type: bool
    value: false
charm: canonical-livepatch-server
settings:
  auth_basic_users:
    default: ""
    description: Comma-separated list of <user>:<bcrypt password hash> pairs.
    source: default
    type: string
    value:"""

        with self.assertRaises(ActionFailed) as ex:
            self.harness.run_action("emit-updated-config", {"config-file": old_config})

        self.assertEqual(
            ex.exception.message,
            "Failed to map old config to new config: auth_basic_users doesn't have a set value for it",
        )

    def test_emit_updated_config__success(self):
        """Test the scenario where `emit-updated-config` action finishes successfully."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()
        self.maxDiff = None
        self.start_container()

        old_config = ""
        new_config = ""

        with open(TEST_OLD_REACTIVE_CONFIG, "r") as f:
            old_config = f.read().strip()

        with open(EXPECTED_OPS_CONFIG, "r") as f:
            new_config = f.read().strip()

        output = self.harness.run_action("emit-updated-config", {"config-file": old_config})

        expected_dict = {
            "new-config": new_config,
            "removed-keys": ["psql_dbname", "psql_roles"],
            "unrecognized-keys": ["filestore_path", "nagios_context", "nagios_servicegroups", "port"],
        }

        self.assertEqual(output.results, {"result": expected_dict})

    def test_get_resource_token_action__failure__non_leader_unit(self):
        """Test the scenario where `get-resource-token` action fails because unit is not leader."""
        self.harness.enable_hooks()

        self.start_container()
        del self.harness.charm._state.resource_token

        output = self.harness.run_action("get-resource-token", {"contract-token": "some-token"})

        self.assertEqual(output.results, {"error": "cannot fetch the resource token: unit is not the leader"})

    def test_get_resource_token_action__failure__empty_contract_token(self):
        """Test the scenario where `get-resource-token` action fails because contract token is empty."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()
        del self.harness.charm._state.resource_token

        output = self.harness.run_action("get-resource-token", {"contract-token": ""})

        self.assertEqual(output.results, {"error": "cannot fetch the resource token: no contract token provided"})

    def test_get_resource_token_action__failure__sync_token_already_set(self):
        """Test the scenario where `get-resource-token` action fails because sync token is already set."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()
        del self.harness.charm._state.resource_token
        self.harness.update_config({"patch-sync.token": "AAAABBBB"})

        output = self.harness.run_action("get-resource-token", {"contract-token": "some-token"})

        self.assertEqual(
            output.results,
            {"error": "patch-sync.token is already set. It should be unset before setting a resource token"},
        )

    def test_missing_url_template_config_causes_blocked_state(self):
        """A test for missing url template."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

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
                    "server.is-hosted": True,
                }
            )
            self.harness.charm.on.config_changed.emit()

            # Emit the pebble-ready event for livepatch
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Check the that the plan was updated
        plan = self.harness.get_container_pebble_plan("livepatch")
        self.assertEqual(plan.to_dict(), {})
        self.assertEqual(self.harness.charm.unit.status.name, BlockedStatus.name)
        self.assertEqual(self.harness.charm.unit.status.message, "✘ server.url-template config not set")

    def test_sync_token_enough_active_state(self):
        """For on-prem servers, a sync token and url template should be enough for active state."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"

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
                    "server.is-hosted": False,
                    "patch-sync.token": "test-token",
                }
            )
            self.harness.charm.on.config_changed.emit()

            # Emit the pebble-ready event for livepatch
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Check the that the plan was updated
        self.assertEqual(self.harness.charm.unit.status.name, ActiveStatus.name)

    def test_missing_sync_token_causes_blocked_state(self):
        """For on-prem servers, a missing sync token should cause a blocked state."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"
        # self.harness.charm._state.resource_token = ""

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
                    "server.is-hosted": False,
                }
            )
            self.harness.charm.on.config_changed.emit()

            # Emit the pebble-ready event for livepatch
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Check the that the plan was updated
        plan = self.harness.get_container_pebble_plan("livepatch")
        self.assertEqual(plan.to_dict(), {})
        self.assertEqual(self.harness.charm.unit.status.name, BlockedStatus.name)
        self.assertEqual(
            self.harness.charm.unit.status.message, "✘ patch-sync token not set, run get-resource-token action"
        )

    def test_config_ca_cert(self):
        """Assure the contract.ca is pushed to the workload container."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        self.harness.charm._state.dsn = "postgresql://123"

        container = self.harness.model.unit.get_container("livepatch")
        self.harness.charm.on.livepatch_pebble_ready.emit(container)

        self.harness.handle_exec("livepatch", [], result=0)
        self.harness.update_config(
            {
                "contracts.ca": TEST_CA_CERT,
            }
        )
        self.harness.charm.on.config_changed.emit()

        # Emit the pebble-ready event for livepatch
        self.harness.charm.on.livepatch_pebble_ready.emit(container)
        # Ensure that the content looks sensible
        root = self.harness.get_filesystem_root("livepatch")
        cert = (root / "usr/local/share/ca-certificates/trusted-contracts.ca.crt").read_text()
        self.assertEqual(cert, "Test CA Cert\n")

        self.harness.update_config(
            {
                "contracts.ca": TEST_CA_CERT_1,
            }
        )
        self.harness.charm.on.config_changed.emit()

        # Emit the pebble-ready event for livepatch
        self.harness.charm.on.livepatch_pebble_ready.emit(container)
        # Ensure that the content looks sensible
        root = self.harness.get_filesystem_root("livepatch")
        cert = (root / "usr/local/share/ca-certificates/trusted-contracts.ca.crt").read_text()
        self.assertEqual(cert, "New Test CA Cert\n")

    def test_logrotate_config_pushed(self):
        """Assure that logrotate config is pushed."""
        self.harness.enable_hooks()

        # Trigger config-changed so that logrotate config gets written
        self.harness.charm.on.config_changed.emit()

        # Ensure that the content looks sensible
        root = self.harness.get_filesystem_root("livepatch")
        config = (root / "etc/logrotate.d/livepatch").read_text()
        self.assertIn("/var/log/livepatch {", config)

    # wokeignore:rule=master
    def test_legacy_db_master_changed(self):
        """test `_legacy_db_master_changed event` handler."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        legacy_db_rel_id = self.harness.add_relation("database-legacy", "postgres")

        # The `ops-lib-pgsql` library calls `leader-get` and `leader-set` tools
        # from juju help-tools, so we need to mock calls that try to spawn a
        # subprocess.
        stored_data = "'{}'"

        def set_database_name_using_juju_leader_set(cmd: List[str]):
            nonlocal stored_data
            self.assertEqual(cmd[0], "leader-set")
            self.assertTrue(cmd[1].startswith("interface.pgsql="))
            stored_data = yaml.safe_dump(cmd[1].removeprefix("interface.pgsql="))

        check_call_mock = Mock(side_effect=set_database_name_using_juju_leader_set)

        def get_database_name_using_juju_leader_get(cmd: List[str]):
            self.assertEqual(cmd[0], "leader-get")
            return bytes(stored_data, "utf-8")

        check_output_mock = Mock(side_effect=get_database_name_using_juju_leader_get)

        with patch("subprocess.check_call", check_call_mock):  # Stubs `leader-set` call.
            with patch("subprocess.check_output", check_output_mock):  # Stubs `leader-get` call.
                self.harness.add_relation_unit(legacy_db_rel_id, "postgres/0")
                self.harness.update_relation_data(
                    legacy_db_rel_id,
                    "postgres/0",
                    {
                        "database": "livepatch-server",
                        # wokeignore:rule=master
                        "master": "host=host port=5432 dbname=livepatch-server user=username password=password",
                    },
                )

                self.assertEqual(
                    self.harness.charm._state.dsn, "postgresql://username:password@host:5432/livepatch-server"
                )

    def test_legacy_db_standby_changed(self):
        """test `_legacy_db_standby_changed event` handler."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        legacy_db_rel_id = self.harness.add_relation("database-legacy", "postgres")

        # The `ops-lib-pgsql` library calls `leader-get` and `leader-set` tools
        # from juju help-tools, so we need to mock calls that try to spawn a
        # subprocess.
        stored_data = "'{}'"

        def set_database_name_using_juju_leader_set(cmd: List[str]):
            nonlocal stored_data
            self.assertEqual(cmd[0], "leader-set")
            self.assertTrue(cmd[1].startswith("interface.pgsql="))
            stored_data = yaml.safe_dump(cmd[1].removeprefix("interface.pgsql="))

        check_call_mock = Mock(side_effect=set_database_name_using_juju_leader_set)

        def get_database_name_using_juju_leader_get(cmd: List[str]):
            self.assertEqual(cmd[0], "leader-get")
            return bytes(stored_data, "utf-8")

        check_output_mock = Mock(side_effect=get_database_name_using_juju_leader_get)

        with patch("subprocess.check_call", check_call_mock):  # Stubs `leader-set` call.
            with patch("subprocess.check_output", check_output_mock):  # Stubs `leader-get` call.
                self.harness.add_relation_unit(legacy_db_rel_id, "postgres/0")
                self.harness.update_relation_data(
                    legacy_db_rel_id,
                    "postgres/0",
                    {
                        "database": "livepatch-server",
                        "standbys": "host=standby-host port=5432 dbname=livepatch-server user=username password=password",
                    },
                )

        # Since we're not storing standby instances in the state, there's nothing
        # to assert against here. However, the event and relation data should be
        # handled without any exceptions. So, for now, it suffices for the test
        # to complete without any exceptions.

    # wokeignore:rule=master
    def test_legacy_db_relation__both_master_and_standby(self):
        """test legacy db relation handlers' function when both primary and standby units are provided."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        legacy_db_rel_id = self.harness.add_relation("database-legacy", "postgres")

        # The `ops-lib-pgsql` library calls `leader-get` and `leader-set` tools
        # from juju help-tools, so we need to mock calls that try to spawn a
        # subprocess.
        stored_data = "'{}'"

        def set_database_name_using_juju_leader_set(cmd: List[str]):
            nonlocal stored_data
            self.assertEqual(cmd[0], "leader-set")
            self.assertTrue(cmd[1].startswith("interface.pgsql="))
            stored_data = yaml.safe_dump(cmd[1].removeprefix("interface.pgsql="))

        check_call_mock = Mock(side_effect=set_database_name_using_juju_leader_set)

        def get_database_name_using_juju_leader_get(cmd: List[str]):
            self.assertEqual(cmd[0], "leader-get")
            return bytes(stored_data, "utf-8")

        check_output_mock = Mock(side_effect=get_database_name_using_juju_leader_get)

        with patch("subprocess.check_call", check_call_mock):  # Stubs `leader-set` call.
            with patch("subprocess.check_output", check_output_mock):  # Stubs `leader-get` call.
                self.harness.add_relation_unit(legacy_db_rel_id, "postgres/0")
                self.harness.update_relation_data(
                    legacy_db_rel_id,
                    "postgres/0",
                    {
                        "database": "livepatch-server",
                        # wokeignore:rule=master
                        "master": "host=host port=5432 dbname=livepatch-server user=username password=password",
                    },
                )

                self.assertEqual(
                    self.harness.charm._state.dsn, "postgresql://username:password@host:5432/livepatch-server"
                )

                self.harness.update_relation_data(
                    legacy_db_rel_id,
                    "postgres/0",
                    {
                        "database": "livepatch-server",
                        # wokeignore:rule=master
                        "master": "host=host port=5432 dbname=livepatch-server user=username password=password",
                        "standbys": "host=standby-host port=5432 dbname=livepatch-server user=username password=password",
                    },
                )

                self.assertEqual(
                    self.harness.charm._state.dsn, "postgresql://username:password@host:5432/livepatch-server"
                )

                # As mentioned in the other tests, we're not storing standby instances
                # in the state, so there's nothing to assert against for standbys.
                # However, it's important for this event to be handled without any
                # exceptions.

    def test_database_relations_are_mutually_exclusive__legacy_first(self):
        """Assure that database relations are mutually exclusive."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        legacy_db_rel_id = self.harness.add_relation("database-legacy", "postgres")

        # The `ops-lib-pgsql` library calls `leader-get` and `leader-set` tools
        # from juju help-tools, so we need to mock calls that try to spawn a
        # subprocess.
        with patch("subprocess.check_call", return_value=None):  # Stubs `leader-set` call.
            with patch("subprocess.check_output", return_value=b""):  # Stubs `leader-get` call.
                self.harness.add_relation_unit(legacy_db_rel_id, "postgres/0")
        self.harness.update_relation_data(legacy_db_rel_id, "postgres", {})

        db_rel_id = self.harness.add_relation("database", "postgres-new")
        self.harness.add_relation_unit(db_rel_id, "postgres-new/0")
        with self.assertRaises(Exception) as cm:
            self.harness.update_relation_data(
                db_rel_id,
                "postgres-new",
                {
                    "username": "some-username",
                    "password": "some-password",  # nosec
                    "endpoints": "some.database.host,some.other.database.host",
                },
            )
        self.assertEqual(
            str(cm.exception),
            "Integration with both database relations is not allowed; `database-legacy` is already activated.",
        )

    def test_database_relations_are_mutually_exclusive__standard_first(self):
        """Assure that database relations are mutually exclusive."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        db_rel_id = self.harness.add_relation("database", "postgres-new")
        self.harness.add_relation_unit(db_rel_id, "postgres-new/0")
        self.harness.update_relation_data(
            db_rel_id,
            "postgres-new",
            {
                "username": "some-username",
                "password": "some-password",  # nosec
                "endpoints": "some.database.host,some.other.database.host",
            },
        )

        legacy_db_rel_id = self.harness.add_relation("database-legacy", "postgres")

        with self.assertRaises(Exception) as cm:
            # The `ops-lib-pgsql` library calls `leader-get` and `leader-set` tools
            # from juju help-tools, so we need to mock calls that try to spawn a
            # subprocess.
            with patch("subprocess.check_call", return_value=None):  # Stubs `leader-set` call.
                with patch("subprocess.check_output", return_value=b""):  # Stubs `leader-get` call.
                    self.harness.add_relation_unit(legacy_db_rel_id, "postgres/0")

        self.assertEqual(
            str(cm.exception),
            "Integration with both database relations is not allowed; `database` is already activated.",
        )

    def test_standard_database_relation__success(self):
        """Test standard db relation successfully integrates with database."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        db_rel_id = self.harness.add_relation("database", "postgres-new")
        self.harness.add_relation_unit(db_rel_id, "postgres-new/0")
        self.harness.update_relation_data(
            db_rel_id,
            "postgres-new",
            {
                "username": "some-username",
                "password": "some-password",  # nosec
                "endpoints": "some.database.host,some.other.database.host",
            },
        )

        self.assertEqual(
            self.harness.charm._state.dsn,
            "postgresql://some-username:some-password@some.database.host/livepatch-server",
        )

    def test_standard_database_relation__empty_username_or_password(self):
        """Test standard db relation does not update the dsn if credentials are not set in relation data."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        db_rel_id = self.harness.add_relation("database", "postgres-new")
        self.harness.add_relation_unit(db_rel_id, "postgres-new/0")
        self.harness.update_relation_data(
            db_rel_id,
            "postgres-new",
            {
                "username": "",
                "password": "",  # nosec
                "endpoints": "some.database.host,some.other.database.host",
            },
        )

        # We should verify at this point the db_uri is not set in the state, as
        # this is perceived as an incomplete integration.
        self.assertIsNone(self.harness.charm._state.dsn)

    def test_postgres_patch_storage_config_sets_in_container(self):
        """A test for postgres patch storage config in container."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"
        self.harness.charm._state.resource_token = TEST_TOKEN

        container = self.harness.model.unit.get_container("livepatch")
        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

            self.harness.update_config(
                {
                    "patch-storage.type": "postgres",
                    "patch-storage.postgres-connection-string": "postgres://user:password@host/db",
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": True,
                }
            )
            self.harness.charm.on.config_changed.emit()

            # Emit the pebble-ready event for livepatch
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Check the that the plan was updated
        plan = self.harness.get_container_pebble_plan("livepatch")
        required_environment = {
            "LP_PATCH_STORAGE_TYPE": "postgres",
            "LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING": "postgres://user:password@host/db",
        }
        environment = plan.to_dict()["services"]["livepatch"]["environment"]
        self.assertEqual(environment, environment | required_environment)

    def test_postgres_patch_storage_config_defaults_to_database_relation(self):
        """A test for postgres patch storage config."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        db_rel_id = self.harness.add_relation("database", "postgres-new")
        self.harness.add_relation_unit(db_rel_id, "postgres-new/0")
        self.harness.update_relation_data(
            db_rel_id,
            "postgres-new",
            {
                "username": "username",
                "password": "password",  # nosec
                "endpoints": "host",
            },
        )

        container = self.harness.model.unit.get_container("livepatch")
        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

            self.harness.update_config(
                {
                    "patch-storage.type": "postgres",
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": True,
                }
            )
            self.harness.charm.on.config_changed.emit()

            # Emit the pebble-ready event for livepatch
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Check the that the plan was updated
        plan = self.harness.get_container_pebble_plan("livepatch")
        required_environment = {
            "LP_PATCH_STORAGE_TYPE": "postgres",
            "LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING": "postgresql://username:password@host/livepatch-server",
        }
        environment = plan.to_dict()["services"]["livepatch"]["environment"]
        self.assertEqual(environment, environment | required_environment)

    def test_pro_airgapped_server_relation__success(self):
        """Test pro-airgapped-server relation."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                }
            )

            pro_rel_id = self.harness.add_relation("pro-airgapped-server", "pro-airgapped-server")
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/0")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/0",
                {
                    "scheme": "scheme",
                    "hostname": "some.host.name",
                    "port": "9999",
                },
            )

        self._assert_environment_contains(
            {
                "LP_CONTRACTS_ENABLED": True,
                "LP_CONTRACTS_URL": "scheme://some.host.name:9999",
            }
        )

    def test_pro_airgapped_server__sync_enabled_when_sync_token_set(self):
        """Test pro-airgapped-server syncs is enabled when sync token is set."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                    "patch-sync.enabled": True,
                    "patch-sync.token": "AAAABBBB",
                }
            )

            pro_rel_id = self.harness.add_relation("pro-airgapped-server", "pro-airgapped-server")
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/0")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/0",
                {
                    "scheme": "scheme",
                    "hostname": "some.host.name",
                    "port": "9999",
                },
            )

        self._assert_environment_contains(
            {
                "LP_PATCH_SYNC_ENABLED": True,
                "LP_PATCH_SYNC_TOKEN": "AAAABBBB",  # nosec
                "LP_CONTRACTS_ENABLED": True,
                "LP_CONTRACTS_URL": "scheme://some.host.name:9999",
            }
        )

    def test_pro_airgapped_server__sync_disabled_when_sync_token_not_set(self):
        """Test pro-airgapped-server syncs is disabled when sync token is set."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                    "patch-sync.enabled": True,
                }
            )

            pro_rel_id = self.harness.add_relation("pro-airgapped-server", "pro-airgapped-server")
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/0")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/0",
                {
                    "scheme": "scheme",
                    "hostname": "some.host.name",
                    "port": "9999",
                },
            )

        self._assert_environment_contains(
            {
                "LP_CONTRACTS_ENABLED": True,
                "LP_CONTRACTS_URL": "scheme://some.host.name:9999",
                "LP_PATCH_SYNC_ENABLED": False,
            },
        )

    def test_pro_airgapped_server_relation__multiple_units(self):
        """Test pro-airgapped-server relation when there are multiple units."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                }
            )

            pro_rel_id = self.harness.add_relation("pro-airgapped-server", "pro-airgapped-server")
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/0")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/0",
                {
                    "scheme": "scheme",
                    "hostname": "first.host",
                    "port": "9999",
                },
            )

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "scheme://first.host:9999",
                }
            )

            # Adding another unit of `pro-airgapped-server`, but this new unit should not
            # affect the Livepatch server configuration.
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/1")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/1",
                {
                    "scheme": "scheme",
                    "hostname": "second.host",
                    "port": "9999",
                },
            )

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "scheme://first.host:9999",
                }
            )

    def test_pro_airgapped_server_relation__multiple_units_one_departs(self):
        """Test pro-airgapped-server relation when one of the relation units departs but the other one not."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                }
            )

            pro_rel_id = self.harness.add_relation("pro-airgapped-server", "pro-airgapped-server")
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/0")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/0",
                {
                    "scheme": "scheme",
                    "hostname": "first.host",
                    "port": "9999",
                },
            )

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "scheme://first.host:9999",
                }
            )

            # Adding another unit of `pro-airgapped-server`, but this new unit should not
            # affect the Livepatch server configuration.
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/1")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/1",
                {
                    "scheme": "scheme",
                    "hostname": "second.host",
                    "port": "9999",
                },
            )

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "scheme://first.host:9999",
                }
            )

            # Now we drop remove the first `pro-airgapped-server` unit. The charm should
            # use the second unit address.
            self.harness.remove_relation_unit(pro_rel_id, "pro-airgapped-server/0")

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "scheme://second.host:9999",
                }
            )

    def test_pro_airgapped_server_relation__relation_removed(self):
        """Test when pro-airgapped-server is removed."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"
        self.harness.charm._state.resource_token = TEST_TOKEN

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                }
            )

            pro_rel_id = self.harness.add_relation("pro-airgapped-server", "pro-airgapped-server")
            self.harness.add_relation_unit(pro_rel_id, "pro-airgapped-server/0")
            self.harness.update_relation_data(
                pro_rel_id,
                "pro-airgapped-server/0",
                {
                    "scheme": "scheme",
                    "hostname": "first.host",
                    "port": "9999",
                },
            )

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "scheme://first.host:9999",
                }
            )

            # Now we remove the relation. The plan should now point to the hosted contracts service.
            self.harness.remove_relation(pro_rel_id)

            self._assert_environment_contains(
                {
                    "LP_CONTRACTS_ENABLED": True,
                    "LP_CONTRACTS_URL": "https://contracts.canonical.com",
                }
            )

    def test_cve_catalog_relation__success(self):
        """Test cve-catalog relation."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"
        self.harness.charm._state.resource_token = TEST_TOKEN

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                }
            )

            cves_rel_id = self.harness.add_relation("cve-catalog", "livepatch-cve-service")
            self.harness.add_relation_unit(cves_rel_id, "livepatch-cve-service/0")
            self.harness.update_relation_data(
                cves_rel_id,
                "livepatch-cve-service",
                {
                    "url": "scheme://some.host.name:9999",
                },
            )

        self._assert_environment_contains(
            {
                "LP_CVE_LOOKUP_ENABLED": False,  # Should not get enabled automatically.
                "LP_CVE_SYNC_ENABLED": True,
                "LP_CVE_SYNC_SOURCE_URL": "scheme://some.host.name:9999",
                "LP_CVE_SYNC_INTERVAL": "1h",  # Default config value.
                "LP_CVE_SYNC_TIMEOUT": "5m",  # Default config value.
            }
        )

    def test_cve_catalog_relation__relation_removed(self):
        """Test when cve-catalog is removed."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.harness.charm._state.dsn = "postgresql://123"
        self.harness.charm._state.resource_token = TEST_TOKEN

        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config(
                {
                    "server.url-template": "http://localhost/{filename}",
                    "server.is-hosted": False,
                }
            )

            cves_rel_id = self.harness.add_relation("cve-catalog", "livepatch-cve-service")
            self.harness.add_relation_unit(cves_rel_id, "livepatch-cve-service/0")
            self.harness.update_relation_data(
                cves_rel_id,
                "livepatch-cve-service",
                {
                    "url": "scheme://some.host.name:9999",
                },
            )

            self._assert_environment_contains(
                {
                    "LP_CVE_LOOKUP_ENABLED": False,  # Should not get enabled automatically.
                    "LP_CVE_SYNC_ENABLED": True,
                    "LP_CVE_SYNC_SOURCE_URL": "scheme://some.host.name:9999",
                    "LP_CVE_SYNC_INTERVAL": "1h",  # Default config value.
                    "LP_CVE_SYNC_TIMEOUT": "5m",  # Default config value.
                }
            )

            # Now we remove the relation.
            self.harness.remove_relation(cves_rel_id)

            self._assert_environment_contains(
                {
                    "LP_CVE_LOOKUP_ENABLED": False,
                    "LP_CVE_SYNC_ENABLED": False,
                },
            )

    def _assert_environment_contains(self, contains: Dict[str, Any]):
        """Assert Pebble plan environment contains given key/value pairs."""
        plan = self.harness.get_container_pebble_plan("livepatch")
        environment = plan.to_dict()["services"]["livepatch"]["environment"]
        self.assertEqual(environment, environment | contains, "environment does not contain expected key/value pairs")


class TestIngressMethod(unittest.TestCase):
    """Ingress method tests."""

    def _start_harness(self, ingress_default: str):
        config_yaml = (
            "options:\n"
            "  ingress-method:\n"
            "    type: string\n"
            f"    default: {ingress_default!r}\n"
        )
        harness = Harness(LivepatchCharm, config=config_yaml)
        self.addCleanup(harness.cleanup)
        harness.disable_hooks()
        harness.add_oci_resource("livepatch-server-image")
        harness.add_oci_resource("livepatch-schema-upgrade-tool-image")
        harness.begin()
        return harness

    def test_ingress_default_uses_nginx_route(self):
        """assert that nginx route is used when ingress method is not set or set to 'nginx-route'."""
        with patch("src.charm.require_nginx_route") as require_nginx_route:
            harness = self._start_harness("")

        require_nginx_route.assert_called_once_with(
            charm=harness.charm,
            service_hostname=harness.charm.app.name,
            service_name=harness.charm.app.name,
            service_port=SERVER_PORT,
        )

    def test_ingress_traefik_route_uses_requirer(self):
        """assert that the charm uses IngressPerAppRequirer if ingress method is set to 'traefik-route'."""
        with patch("src.charm.require_nginx_route") as require_nginx_route:
            harness = self._start_harness("traefik-route")

        require_nginx_route.assert_not_called()
        self.assertIsInstance(harness.charm.ingress, IngressPerAppRequirer)

    def _add_database_legacy_relation(self, dsn_string: str = "postgresql://user:pass@host:5432/livepatch-server"):
        """Helper method to add and configure a legacy database relation."""
        db_rel_id = self.harness.add_relation("database-legacy", "postgresql")

        with patch("subprocess.check_call", return_value=None):
            with patch("subprocess.check_output", return_value=b""):
                self.harness.add_relation_unit(db_rel_id, "postgresql/0")

        # Set DSN as if database was connected
        self.harness.charm._state.dsn = dsn_string

        return db_rel_id

    def _start_service(self, container):
        """Start the service"""
        self.harness.charm._state.resource_token = TEST_TOKEN
        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.update_config({"server.url-template": "http://localhost/{filename}"})
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

    def test_database_legacy_relation_broken__stops_service_and_clears_dsn(self):
        """Test that database-legacy relation broken stops service and clears DSN without attempting restart."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        db_rel_id = self._add_database_legacy_relation()

        container = self.harness.model.unit.get_container("livepatch")

        self._start_service(container)

        # Verify service is running
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertTrue(service.is_running())

        # Act: Remove the database relation (triggers relation-broken)
        with patch("subprocess.check_call", return_value=None):
            with patch("subprocess.check_output", return_value=b""):
                self.harness.remove_relation(db_rel_id)

        # Assert: Service should be stopped
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertFalse(service.is_running())

        # Assert: DSN should be cleared
        self.assertIsNone(self.harness.charm._state.dsn)

        # Assert: Status should be BlockedStatus
        self.assertIsInstance(self.harness.charm.unit.status, BlockedStatus)
        self.assertEqual(self.harness.charm.unit.status.message, "Database connection removed")


    def _add_database_relation(self):
        """Helper method to add and configure a database relation."""
        db_rel_id = self.harness.add_relation("database", "postgresql-k8s")

        self.harness.add_relation_unit(db_rel_id, "postgresql-k8s/0")
        self.harness.update_relation_data(
            db_rel_id,
            "postgresql-k8s",
            {
                "username": "testuser",
                "password": "testpass",  # nosec
                "endpoints": "10.0.0.1:5432",
            },
        )

        return db_rel_id

    def test_database_relation_broken__stops_service_and_clears_dsn(self):
        """Test that database relation broken stops service and clears DSN without attempting restart."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        db_rel_id = self._add_database_relation()

        # Start the service
        container = self.harness.model.unit.get_container("livepatch")
        self._start_service(container)

        # Verify service is running and DSN is set
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertTrue(service.is_running())
        self.assertEqual(self.harness.charm._state.dsn, "postgresql://testuser:testpass@10.0.0.1:5432/livepatch-server")

        # Act: Remove the database relation (triggers relation-broken)
        self.harness.remove_relation(db_rel_id)

        # Assert: Service should be stopped
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertFalse(service.is_running())

        # Assert: DSN should be cleared
        self.assertIsNone(self.harness.charm._state.dsn)

        # Assert: Status should be BlockedStatus
        self.assertIsInstance(self.harness.charm.unit.status, BlockedStatus)
        self.assertEqual(self.harness.charm.unit.status.message, "Database connection removed")

    def test_database_legacy_relation_broken__non_leader_unit(self):
        """Test that non-leader units handle database-legacy relation broken correctly."""
        self.harness.set_leader(False)
        self.harness.enable_hooks()

        # Setup: Add database relation
        db_rel_id = self._add_database_legacy_relation()

        # Set DSN as if database was connected (even though non-leader shouldn't set it)
        self.harness.charm._state.dsn = "postgresql://user:pass@host:5432/livepatch-server"

        # Start the service
        container = self.harness.model.unit.get_container("livepatch")
        self._start_service(container)

        # Act: Remove the database relation
        with patch("subprocess.check_call", return_value=None):
            with patch("subprocess.check_output", return_value=b""):
                self.harness.remove_relation(db_rel_id)

        # Assert: Service should be stopped
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertFalse(service.is_running())

        # Assert: Status should be BlockedStatus
        self.assertIsInstance(self.harness.charm.unit.status, BlockedStatus)
        self.assertEqual(self.harness.charm.unit.status.message, "Database connection removed")

    def test_database_relation_broken__service_not_running(self):
        """Test that database relation broken handles case where service is not running."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        # Setup: Add database relation but don't start service
        db_rel_id = self._add_database_relation()

        # Act: Remove the database relation without starting service
        self.harness.remove_relation(db_rel_id)

        # Assert: Should not crash and should set appropriate status
        self.assertIsInstance(self.harness.charm.unit.status, BlockedStatus)
        self.assertEqual(self.harness.charm.unit.status.message, "Database connection removed")

        # Assert: DSN should be cleared
        self.assertIsNone(self.harness.charm._state.dsn)

    def test_database_legacy_relation_broken_then_fixed__service_recovers(self):
        """Test that service stops and DSN clears on broken legacy relation, then recovers when relation is fixed."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        # The `ops-lib-pgsql` library calls `leader-get` and `leader-set` tools
        # from juju help-tools, so we need to mock calls that try to spawn a subprocess.
        stored_data = "'{}'"

        def set_database_name_using_juju_leader_set(cmd: List[str]):
            nonlocal stored_data
            self.assertEqual(cmd[0], "leader-set")
            self.assertTrue(cmd[1].startswith("interface.pgsql="))
            stored_data = yaml.safe_dump(cmd[1].removeprefix("interface.pgsql="))

        check_call_mock = Mock(side_effect=set_database_name_using_juju_leader_set)

        def get_database_name_using_juju_leader_get(cmd: List[str]):
            self.assertEqual(cmd[0], "leader-get")
            return bytes(stored_data, "utf-8")

        check_output_mock = Mock(side_effect=get_database_name_using_juju_leader_get)

        # Setup: Add and configure legacy database relation
        dsn_string = "postgresql://username:password@host:5432/livepatch-server"
        legacy_db_rel_id = self._add_database_legacy_relation(dsn_string)

        # Verify DSN is set
        self.assertEqual(
            self.harness.charm._state.dsn, dsn_string
        )

        # Start the service
        container = self.harness.model.unit.get_container("livepatch")
        self._start_service(container)

        # Verify service is running
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertTrue(service.is_running())

        # Act 1: Remove the database relation (triggers relation-broken)
        with patch("subprocess.check_call", check_call_mock):
            with patch("subprocess.check_output", check_output_mock):
                self.harness.remove_relation(legacy_db_rel_id)

        # Assert 1: Service should be stopped and DSN cleared
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertFalse(service.is_running())
        self.assertIsNone(self.harness.charm._state.dsn)
        self.assertIsInstance(self.harness.charm.unit.status, BlockedStatus)
        self.assertEqual(self.harness.charm.unit.status.message, "Database connection removed")

        # Act 2: Re-establish the legacy database relation (fixing the broken relation)
        new_dsn_string = "postgresql://newuser:newpass@newhost:5432/livepatch-server"
        self._add_database_legacy_relation(dsn_string=new_dsn_string)

        # Verify DSN is updated with new connection details
        self.assertEqual(
            self.harness.charm._state.dsn, new_dsn_string
        )

        # Trigger pebble ready again to start the service with new relation
        with patch("src.charm.LivepatchCharm.migration_is_required") as migration:
            migration.return_value = False
            self.harness.charm.on.livepatch_pebble_ready.emit(container)

        # Assert 2: Service should be running again with new DSN
        service = container.get_service(LIVEPATCH_SERVICE_NAME)
        self.assertTrue(service.is_running())
        self.assertEqual(
            self.harness.charm._state.dsn, new_dsn_string
        )
        self.assertIsInstance(self.harness.charm.unit.status, ActiveStatus)
