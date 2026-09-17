# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for utils.resolve_secret_config and utils.map_config_to_env_vars."""

import unittest
from unittest.mock import MagicMock

from ops.model import ModelError

import utils

APP_NAME = "canonical-livepatch-server-k8s"


def _make_charm(secrets: dict) -> MagicMock:
    """Return a mock charm whose `model.get_secret(id=...)` resolves from `secrets`.

    `secrets` maps secret ID -> content dict. IDs not present raise ModelError,
    simulating an inaccessible/not-found secret.
    """
    charm = MagicMock()
    charm.app.name = APP_NAME

    def get_secret(id):  # noqa: A002 - matches ops.Model.get_secret's kwarg name
        if id not in secrets:
            raise ModelError(f"secret owner does not exist: {id}")
        secret = MagicMock()
        secret.get_content.return_value = secrets[id]
        return secret

    charm.model.get_secret.side_effect = get_secret
    return charm


class TestStandaloneSecrets(unittest.TestCase):
    """Tests for standalone `<key>-secret` resolution (SECRET_BACKED_CONFIG_KEYS)."""

    def test_plaintext_used_when_no_secret_set(self):
        charm = _make_charm({})
        config = {"patch-sync.token": "plaintext-token"}

        resolved = utils.resolve_secret_config(charm, config)

        self.assertEqual(resolved["patch-sync.token"], "plaintext-token")

    def test_secret_overrides_plaintext(self):
        charm = _make_charm({"secret:1": {"value": "from-secret"}})
        config = {
            "patch-sync.token": "plaintext-token",
            "patch-sync.token-secret": "secret:1",
        }

        resolved = utils.resolve_secret_config(charm, config)

        self.assertEqual(resolved["patch-sync.token"], "from-secret")

    def test_secret_option_stripped_from_result(self):
        charm = _make_charm({"secret:1": {"value": "from-secret"}})
        config = {"patch-sync.token-secret": "secret:1"}

        resolved = utils.resolve_secret_config(charm, config)

        self.assertNotIn("patch-sync.token-secret", resolved)

    def test_secret_missing_value_key_raises(self):
        charm = _make_charm({"secret:1": {"not-value": "oops"}})
        config = {
            "patch-sync.token": "plaintext-token",
            "patch-sync.token-secret": "secret:1",
        }

        with self.assertRaises(utils.CharmConfigInvalidError) as ctx:
            utils.resolve_secret_config(charm, config)

        self.assertIn("patch-sync.token-secret", str(ctx.exception))
        self.assertIn("value", str(ctx.exception))

    def test_inaccessible_secret_raises(self):
        charm = _make_charm({})
        config = {
            "patch-sync.token": "plaintext-token",
            "patch-sync.token-secret": "secret:unknown",
        }

        with self.assertRaises(utils.CharmConfigInvalidError) as ctx:
            utils.resolve_secret_config(charm, config)

        self.assertIn("patch-sync.token-secret", str(ctx.exception))
        self.assertIn(f"juju grant-secret <secret> {APP_NAME}", str(ctx.exception))

    def test_every_standalone_key_is_resolved(self):
        """Every key in SECRET_BACKED_CONFIG_KEYS actually gets its `-secret` sibling honoured."""
        for key in utils.SECRET_BACKED_CONFIG_KEYS:
            with self.subTest(key=key):
                charm = _make_charm({"secret:1": {"value": "from-secret"}})
                config = {key: "plaintext", f"{key}-secret": "secret:1"}

                resolved = utils.resolve_secret_config(charm, config)

                self.assertEqual(resolved[key], "from-secret")
                self.assertNotIn(f"{key}-secret", resolved)


class TestCredentialGroups(unittest.TestCase):
    """Tests for grouped `<group>.credentials-secret` resolution (CREDENTIAL_GROUPS)."""

    def test_plaintext_used_when_group_secret_not_set(self):
        charm = _make_charm({})
        config = {
            "contracts.user": "plain-user",
            "contracts.password": "plain-pass",
            "contracts.ca": "plain-ca",
        }

        resolved = utils.resolve_secret_config(charm, config)

        self.assertEqual(resolved["contracts.user"], "plain-user")
        self.assertEqual(resolved["contracts.password"], "plain-pass")
        self.assertEqual(resolved["contracts.ca"], "plain-ca")

    def test_group_secret_overrides_all_provided_keys(self):
        charm = _make_charm(
            {"secret:group": {"user": "secret-user", "password": "secret-pass", "ca-cert": "secret-ca"}}
        )
        config = {
            "contracts.user": "plain-user",
            "contracts.password": "plain-pass",
            "contracts.ca": "plain-ca",
            "contracts.credentials-secret": "secret:group",
        }

        resolved = utils.resolve_secret_config(charm, config)

        self.assertEqual(resolved["contracts.user"], "secret-user")
        self.assertEqual(resolved["contracts.password"], "secret-pass")
        self.assertEqual(resolved["contracts.ca"], "secret-ca")
        self.assertNotIn("contracts.credentials-secret", resolved)

    def test_group_secret_falls_back_to_plaintext_for_missing_keys(self):
        """Keys the secret doesn't provide keep their plaintext value, not an error."""
        charm = _make_charm({"secret:group": {"user": "secret-user"}})
        config = {
            "contracts.user": "plain-user",
            "contracts.password": "plain-pass",
            "contracts.ca": "plain-ca",
            "contracts.credentials-secret": "secret:group",
        }

        resolved = utils.resolve_secret_config(charm, config)

        self.assertEqual(resolved["contracts.user"], "secret-user")
        self.assertEqual(resolved["contracts.password"], "plain-pass")
        self.assertEqual(resolved["contracts.ca"], "plain-ca")

    def test_inaccessible_group_secret_raises(self):
        charm = _make_charm({})
        config = {
            "contracts.user": "plain-user",
            "contracts.credentials-secret": "secret:unknown",
        }

        with self.assertRaises(utils.CharmConfigInvalidError) as ctx:
            utils.resolve_secret_config(charm, config)

        self.assertIn("contracts.credentials-secret", str(ctx.exception))

    def test_every_group_is_resolved(self):
        """Every group in CREDENTIAL_GROUPS honours its secret for every declared field."""
        for group_key, field_map in utils.CREDENTIAL_GROUPS.items():
            with self.subTest(group=group_key):
                secret_content = {content_key: f"secret-{content_key}" for content_key in field_map}
                charm = _make_charm({"secret:group": secret_content})
                config = {target_key: "plaintext" for target_key in field_map.values()}
                config[group_key] = "secret:group"

                resolved = utils.resolve_secret_config(charm, config)

                for content_key, target_key in field_map.items():
                    self.assertEqual(resolved[target_key], f"secret-{content_key}")
                self.assertNotIn(group_key, resolved)


class TestMapConfigToEnvVars(unittest.TestCase):
    """Tests for map_config_to_env_vars."""

    def test_maps_dotted_and_dashed_keys(self):
        env = utils.map_config_to_env_vars({"contracts.user": "u", "patch-storage.type": "s3"}, is_leader=True)

        self.assertEqual(env["LP_CONTRACTS_USER"], "u")
        self.assertEqual(env["LP_PATCH_STORAGE_TYPE"], "s3")

    def test_sets_leader_flag(self):
        env = utils.map_config_to_env_vars({}, is_leader=False)

        self.assertFalse(env["LP_SERVER_IS_LEADER"])

    def test_additional_env_merged(self):
        env = utils.map_config_to_env_vars({}, is_leader=True, LP_EXTRA="value")

        self.assertEqual(env["LP_EXTRA"], "value")


if __name__ == "__main__":
    unittest.main()
