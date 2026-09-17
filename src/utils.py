# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utils module."""

import csv
import json
import os
import platform
import tempfile
import typing as t

import requests
from ops.model import ModelError

DEFAULT_CONTRACTS_URL = "https://contracts.canonical.com"
RESOURCE_NAME = "livepatch-onprem"

# The key under which a `type: secret` config option's value is expected to be
# stored in the secret's content, for options that hold a single, standalone
# value (as opposed to a credentials group secret's several named keys).
SECRET_VALUE_KEY = "value"  # nosec B105

# Suffix for a Juju secret holding the same value as a single sensitive config
# option, under the SECRET_VALUE_KEY key (e.g. "patch-sync.token-secret").
SECRET_SUFFIX = "-secret"


class CharmConfigInvalidError(Exception):
    """Raised when a secret-backed config option is set but cannot be resolved."""


# Standalone config options with a preferred, Juju-secret-backed "<key>-secret"
# sibling: the secret (if set) takes priority over the deprecated plain-text
# option. Keys that belong to a CREDENTIAL_GROUPS entry below are not listed
# here; use the group instead.
SECRET_BACKED_CONFIG_KEYS = (
    "auth.basic.users",
    "patch-storage.swift-api-key",
    "patch-storage.gcs-credentials-json",
    "patch-storage.ibm-api-key",
    "patch-storage.postgres-connection-string",
    "patch-sync.token",
    "influx.token",
)

# Config options that bundle several related, deprecated plain-text options
# into a single preferred "<group>.credentials-secret" Juju secret, keyed by
# short field names, so operators only have to manage one secret per group.
# Any key set in the secret takes priority over its plain-text counterpart;
# keys the secret doesn't provide fall back to their plain-text option.
CREDENTIAL_GROUPS = {
    "contracts.credentials-secret": {
        "user": "contracts.user",
        "password": "contracts.password",
        "ca": "contracts.ca",
    },
    "patch-storage.s3-credentials-secret": {
        "access-key": "patch-storage.s3-access-key",
        "secret-key": "patch-storage.s3-secret-key",
    },
    "patch-storage.azure-credentials-secret": {
        "account-key": "patch-storage.azure-account-key",
        "connection-string": "patch-storage.azure-connection-string",
        "client-secret": "patch-storage.azure-client-secret",
    },
}


def _get_secret_content(charm, secret_id: str, key: str) -> dict:
    """Fetch the content of the Juju secret with the given ID.

    Raises:
        CharmConfigInvalidError: if the secret is inaccessible (e.g. not granted).
    """
    try:
        return charm.model.get_secret(id=secret_id).get_content(refresh=True)
    except ModelError as e:
        raise CharmConfigInvalidError(
            f"could not access the secret configured for `{key}`; run "
            f"`juju grant-secret <secret> {charm.app.name}` and try again."
        ) from e


def resolve_secret_config(charm, config: dict) -> dict:
    """
    Resolve sensitive config values from their Juju secret equivalents, if set.

    For each key in SECRET_BACKED_CONFIG_KEYS, "<key>-secret" (a secret URI holding
    the value under SECRET_VALUE_KEY) takes precedence over the plaintext key when
    set. For each group in CREDENTIAL_GROUPS, any key set in the grouped secret
    takes precedence over its plaintext counterpart; keys it doesn't provide fall
    back to their plaintext option.

    The helper-only "-secret" options are stripped from the returned dict, which
    otherwise mirrors `config`.

    Raises:
        CharmConfigInvalidError: if a secret is set but inaccessible, or a
            standalone secret doesn't provide a SECRET_VALUE_KEY key.
    """
    resolved = dict(config)

    for key in SECRET_BACKED_CONFIG_KEYS:
        secret_key = f"{key}{SECRET_SUFFIX}"
        secret_id = resolved.pop(secret_key, None)
        if not secret_id:
            continue
        content = _get_secret_content(charm, secret_id, secret_key)
        value = content.get(SECRET_VALUE_KEY)
        if value is None:
            raise CharmConfigInvalidError(f"the secret configured for `{secret_key}` must have a `{SECRET_VALUE_KEY}` key")
        resolved[key] = value

    for group_key, field_map in CREDENTIAL_GROUPS.items():
        secret_id = resolved.pop(group_key, None)
        if not secret_id:
            continue
        content = _get_secret_content(charm, secret_id, group_key)
        for content_key, target_key in field_map.items():
            value = content.get(content_key)
            if value is not None:
                resolved[target_key] = value

    return resolved


def map_config_to_env_vars(config: dict, is_leader: bool, **additional_env):
    """
    Map the resolved config values into environment variables.

    `config` should already have sensitive values resolved from their
    "-secret"/"-credentials" Juju secret equivalents (see `resolve_secret_config`).
    After that, the vars can be passed directly to the pebble layer.
    Variables must match the form LP_<Key1>_<key2>_<key3>...
    """
    env_mapped_config = {"LP_" + k.replace("-", "_").replace(".", "_").upper(): v for k, v in config.items()}

    env_mapped_config["LP_SERVER_IS_LEADER"] = is_leader

    return {**env_mapped_config, **additional_env}


def get_proxy_dict(cfg) -> t.Optional[dict]:
    """Generate an http proxy server configuration dictionary."""
    d = {
        "http_proxy": cfg.get("http_proxy", "") or os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
        "https_proxy": cfg.get("https_proxy", "") or os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
        "no_proxy": cfg.get("no_proxy", "") or os.environ.get("JUJU_CHARM_NO_PROXY", ""),
    }
    if all(v == "" for v in d.values()):
        return None
    return d


def get_machine_token(
    contract_token: str, contracts_url=DEFAULT_CONTRACTS_URL, proxies=None, ca_certificate=None
) -> t.Optional[str]:
    """Retrieve a resource token for the livepatch-onprem resource."""
    if proxies is not None:
        os.environ["http_proxy"] = proxies.get("http_proxy", "")
        os.environ["https_proxy"] = proxies.get("https_proxy", "")
        os.environ["no_proxy"] = proxies.get("no_proxy", "")

    system_information = get_system_information()
    payload = {
        "architecture": system_information.get("architecture", ""),
        "hostType": "container",
        "machineId": "livepatch-onprem",
        "os": {
            "distribution": system_information.get("version", ""),
            "kernel": system_information.get("kernel-version", ""),
            "release": system_information.get("version_id", ""),
            "series": system_information.get("version_codename", ""),
            "type": "Linux",
        },
    }

    headers = {
        "Authorization": f"Bearer {contract_token}",
        "Content-Type": "application/json",
    }

    with tempfile.NamedTemporaryFile(prefix="ca", suffix="cert", delete=False) as ca_tempfile:
        ca_filename = None
        if ca_certificate is not None:
            ca_tempfile.write(ca_certificate)
            ca_tempfile.close()
            ca_filename = ca_tempfile.name
        try:
            data = make_request(
                "POST",
                f"{contracts_url}/v1/context/machines/token",
                data=json.dumps(payload),
                headers=headers,
                timeout=60,
                verify=ca_filename,
            )
            return data.get("machineToken", "")
        except Exception:
            return None
        finally:
            os.unlink(ca_tempfile.name)


def get_resource_token(machine_token, contracts_url=DEFAULT_CONTRACTS_URL, proxies=None, ca_certificate=None):
    """Retrieve a resource token for the livepatch-onprem resource."""
    if proxies is not None:
        os.environ["http_proxy"] = proxies.get("http_proxy", "")
        os.environ["https_proxy"] = proxies.get("https_proxy", "")
        os.environ["no_proxy"] = proxies.get("no_proxy", "")

    headers = {"Authorization": f"Bearer {machine_token}"}
    with tempfile.NamedTemporaryFile(prefix="ca", suffix="cert", delete=False) as ca_tempfile:
        ca_filename = None
        if ca_certificate is not None:
            ca_tempfile.write(ca_certificate)
            ca_tempfile.close()
            ca_filename = ca_tempfile.name

        try:
            data = make_request(
                "GET",
                f"{contracts_url}/v1/resources/{RESOURCE_NAME}/context/machines/livepatch-onprem",
                headers=headers,
                timeout=60,
                verify=ca_filename,
            )
            return data.get("resourceToken", "")
        except Exception:
            return None
        finally:
            os.unlink(ca_tempfile.name)


def make_request(method: str, url: str, *args, **kwargs):
    """
    Wrap HTTP request calls to be safely patched when testing.

    The signature of this function is the same as the `requests` library's
    `request` function.

    Note that we don't want to patch the entire `requests` library methods, since
    it might be used by other dependencies used in this charm.
    """
    response = requests.request(method, url, *args, **kwargs)
    return response.json()


def get_system_information() -> dict:
    """Fetch system information: kernel version, architecture, os, etc."""
    system_information = {}
    with open("/etc/os-release") as f:
        reader = csv.reader(f, delimiter="=")
        for row in reader:
            if row:
                system_information[row[0].lower()] = row[1]
    system_information["kernel-version"] = platform.uname().release
    system_information["architecture"] = platform.machine()
    return system_information
