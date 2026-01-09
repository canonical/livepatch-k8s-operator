# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utils module."""

import csv
import json
import os
import platform
import tempfile
import typing as t
import argparse
import json
import sys
import yaml


import requests

DEFAULT_CONTRACTS_URL = "https://contracts.canonical.com"
RESOURCE_NAME = "livepatch-onprem"

# Config map for converting from old reactive charm configs to the modern config format.
CONFIG_MAP = {
    # Authentication
    "auth_basic_users": "auth.basic.users",
    "auth_lp_teams": "auth.sso.teams",
    "auth_sso_location": "auth.sso.url",
    "auth_sso_public_key": "auth.sso.public-key",

    # Server
    "burst_limit": "server.burst-limit",
    "concurrency_limit": "server.concurrency-limit",
    "log_level": "server.log-level",
    "url_template": "server.url-template",

    # Database / connection pool
    "dbconn_max_lifetime": "database.connection-lifetime-max",
    "dbconn_max": "database.connection-pool-max",
    "psql_dbname": None,
    "psql_roles": None,

    # Patch sync
    "patch_sync_enabled": "patch-sync.enabled",
    "sync_flavors": "patch-sync.flavors",
    "sync_interval": "patch-sync.interval",
    "sync_minimum_kernel_version": "patch-sync.minimum-kernel-version",
    "sync_architectures": "patch-sync.architectures",
    "sync_token": "patch-sync.token",
    "sync_upstream": "patch-sync.upstream-url",
    "sync_send_machine_reports": "patch-sync.send-machine-reports",
    "sync_identity": None,
    "sync_tier": None,
    "sync_upstream_tier": None,

    # Patch sync proxies
    "http_proxy": "patch-sync.proxy.http",
    "https_proxy": "patch-sync.proxy.https",
    "no_proxy": "patch-sync.proxy.no-proxy",

    # Patch storage
    "patchstore": "patch-storage.type",
    "storage_path": "patch-storage.filesystem-path",
    "s3_access_key_id": "patch-storage.s3-access-key",
    "s3_bucket": "patch-storage.s3-bucket",
    "s3_endpoint": "patch-storage.s3-endpoint",
    "s3_region": "patch-storage.s3-region",
    "s3_secret_key": "patch-storage.s3-secret-key",
    "s3_secure": "patch-storage.s3-secure",
    "swift_apikey": "patch-storage.swift-api-key",
    "swift_auth_url": "patch-storage.swift-auth-url",
    "swift_container_name": "patch-storage.swift-container",
    "swift_domain_name": "patch-storage.swift-domain",
    "swift_region_name": "patch-storage.swift-region",
    "swift_tenant_name": "patch-storage.swift-tenant",
    "swift_username": "patch-storage.swift-username",

    # Patch cache
    "patch_cache_on": "patch-cache.enabled",
    "patch_cache_size": "patch-cache.cache-size",
    "patch_cache_ttl": "patch-cache.cache-ttl",

    # Blocklist cache
    "blocklist_cache_refresh": "patch-blocklist.refresh-interval",

    # Machine reports
    "event_bus_brokers": "machine-reports.event-bus.brokers",
    "event_bus_ca_cert": "machine-reports.event-bus.ca-cert",
    "event_bus_client_cert": "machine-reports.event-bus.client-cert",
    "event_bus_client_key": "machine-reports.event-bus.client-key",
    "report_cleanup_interval": "machine-reports.database.cleanup-interval",
    "report_cleanup_row_limit": "machine-reports.database.cleanup-row-limit",
    "report_retention": "machine-reports.database.retention-days",

    # KPI reports
    "kpi_reports": "kpi-reports.interval",

    # Contracts
    "contract_server_password": "contracts.password",
    "contract_server_url": "contracts.url",
    "contract_server_user": "contracts.user",

    # Profiler settings
    "profiler_block_profile_rate": "profiler.block_profile_rate",
    "profiler_enabled": "profiler.enabled",
    "profiler_hostname": "profiler.hostname",
    "profiler_mutex_profile_fraction": "profiler.mutex_profile_fraction",
    "profiler_profile_allocations": "profiler.profile_allocations",
    "profiler_profile_blocks": "profiler.profile_blocks",
    "profiler_profile_goroutines": "profiler.profile_goroutines",
    "profiler_profile_inuse": "profiler.profile_inuse",
    "profiler_profile_mutexes": "profiler.profile_mutexes",
    "profiler_sample_rate": "profiler.sample_rate",
    "profiler_server_address": "profiler.server_address",
    "profiler_upload_rate": "profiler.upload_rate",

    # Cloud delay (no longer used)
    "cloud_delay_default_delay_hours": None,
    "is_cloud_delay_enabled": None,
}

additional_config_dict = {
    "auth_basic_users": ("auth.basic.enabled", True),
    "auth_lp_teams": ("auth.sso.enabled", True),
    "auth_sso_public_key": ("auth.sso.enabled", True),
    "blocklist_cache_refresh": ("patch-blocklist.enabled", True),
    "contract_server_password": ("contracts.enabled", True),
    "contract_server_user": ("contracts.enabled", True),
    "event_bus_brokers": ("machine-reports.event-bus.enabled", True),
    "event_bus_ca_cert": ("machine-reports.event-bus.enabled", True),
    "event_bus_client_cert": ("machine-reports.event-bus.enabled", True),
    "event_bus_client_key": ("machine-reports.event-bus.enabled", True),
    "filebacked": ("patch-storage.type", "filesystem"),
    "http_proxy": ("patch-sync.proxy.enabled", True),
    "https_proxy": ("patch-sync.proxy.enabled", True),
    "kpi_reports": ("kpi-reports.enabled", True),
    "no_proxy": ("patch-sync.proxy.enabled", True),
    "sync_architectures": ("patch-sync.enabled", True),
    "sync_flavors": ("patch-sync.enabled", True),
    "sync_interval": ("patch-sync.enabled", True),
    "sync_minimum_kernel_version": ("patch-sync.enabled", True),
    "sync_token": ("patch-sync.enabled", True),
    "sync_upstream": ("patch-sync.enabled", True),
}

override_values = {
    "patch-storage.type": ("file", "filesystem"),
}


def map_config_to_env_vars(charm, **additional_env):
    """
    Map the config values provided in config.yaml into environment variables.

    After that, the vars can be passed directly to the pebble layer.
    Variables must match the form LP_<Key1>_<key2>_<key3>...
    """
    env_mapped_config = {"LP_" + k.replace("-", "_").replace(".", "_").upper(): v for k, v in charm.config.items()}

    env_mapped_config["LP_SERVER_IS_LEADER"] = charm.unit.is_leader()

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


def map_old_config_to_new_config(conf : dict) -> dict:
    settings = conf.get("options", {})
    if settings == {}:
        settings = conf.get("settings", {})
    if settings == {}:
        raise ValueError("No valid key for configuration found.")
    converted_options = {}
    removed_keys = []
    unrecognized_keys = []
    skip_count = 0
    for key, val in settings.items():
        parsed_val = val.get("value", None)
        # Set additional config entries only if the key has a non-empty value.
        if key in additional_config_dict and parsed_val not in [None, ""]:
            add_conf_key, add_conf_val = additional_config_dict[key]
            converted_options[add_conf_key] = add_conf_val

        if key in CONFIG_MAP:
            if CONFIG_MAP[key]:
                if parsed_val is None:
                    raise ValueError(f"{key} doesn't have a set value for it")
                elif parsed_val == "":
                    skip_count += 1
                    continue
                converted_options[CONFIG_MAP[key]] = parsed_val
            else:
                removed_keys.append(key)
        else:
            unrecognized_keys.append(key)
    for key, value in override_values.items():
        if key in converted_options:
            current, override = value
            if converted_options[key] == current:
                converted_options[key] = override
    # config file needs to have `canonical-livepatch-server-k8s` as the root in order to read properly.
    config = {
        "canonical-livepatch-server-k8s": converted_options
    }
    result = {
        "new-config": yaml.dump(config).strip(),
        "removed-keys": removed_keys,
        "unrecognized-keys":  unrecognized_keys,
    }
    return result
