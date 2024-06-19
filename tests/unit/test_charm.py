# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from typing import List
from unittest.mock import Mock, patch

import yaml
from ops import pebble
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import ActionFailed, Harness

from src.charm import LivepatchCharm
from src.state import State

APP_NAME = "canonical-livepatch-server-k8s"

TEST_TOKEN = "test-token"  # nosec
TEST_CA_CERT = "VGVzdCBDQSBDZXJ0Cg=="
TEST_CA_CERT_1 = "TmV3IFRlc3QgQ0EgQ2VydAo="


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
    if cmd == "/usr/local/bin/livepatch-schema-tool upgrade /usr/src/livepatch/schema-upgrades":
        return MockOutput("", "")
    return MockOutput("", "unexpected command")


# pylint: disable=too-many-public-methods
class TestCharm(unittest.TestCase):
    """A wrapper class for charm unit tests."""

    def setUp(self):
        self.harness = Harness(LivepatchCharm)
        self.addCleanup(self.harness.cleanup)

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
                    "/etc/livepatch/schema-upgrades",
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
                    "/etc/livepatch/schema-upgrades",
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
                    "/etc/livepatch/schema-upgrades",
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
                    "/etc/livepatch/schema-upgrades",
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
                    "/etc/livepatch/schema-upgrades",
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

    def test_get_resource_token_action__failure__non_leader_unit(self):
        """Test the scenario where `get-resource-token` action fails because unit is not leader."""
        self.harness.enable_hooks()

        self.start_container()

        output = self.harness.run_action("get-resource-token", {"contract-token": "some-token"})

        self.assertEqual(output.results, {"error": "cannot fetch the resource token: unit is not the leader"})

    def test_get_resource_token_action__failure__empty_contract_token(self):
        """Test the scenario where `get-resource-token` action fails because contract token is empty."""
        self.harness.set_leader(True)
        self.harness.enable_hooks()

        self.start_container()

        output = self.harness.run_action("get-resource-token", {"contract-token": ""})

        self.assertEqual(output.results, {"error": "cannot fetch the resource token: no contract token provided"})

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
                    "password": "some-password",
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
                "password": "some-password",
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
                "password": "some-password",
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
                "password": "",
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
                "password": "password",
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
