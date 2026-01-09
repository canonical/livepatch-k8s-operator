# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


"""Grafana constants module."""

import logging

LOGGER = logging.getLogger(__name__)
WORKLOAD_CONTAINER = "livepatch"
SCHEMA_UPGRADE_CONTAINER = "livepatch-schema-upgrade"

class PgIsReadyStates:
    """Postgres states."""

    CONNECTED = 0
    REJECTED = 1
    NO_RESPONSE = 2
    NO_ATTEMPT = 3

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
# Additional config map for enabling certain features based on if a value is set.
ADDITIONAL_CONFIG_MAP = {
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

# Values we override with new names, i.e file -> filesystem
OVERRIDE_VALUES = {
    "patch-storage.type": ("file", "filesystem"),
}
