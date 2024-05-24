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
