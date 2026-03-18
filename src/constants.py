# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


"""Grafana constants module."""

import logging
from log_redactor import RedactingFilter

LOGGER = logging.getLogger(__name__)
# Add the redacting filter to the module logger to ensure all logs 
# from this module are scrubbed of sensitive information.
LOGGER.addFilter(RedactingFilter())
WORKLOAD_CONTAINER = "livepatch"
SCHEMA_UPGRADE_CONTAINER = "livepatch-schema-upgrade"

class PgIsReadyStates:
    """Postgres states."""

    CONNECTED = 0
    REJECTED = 1
    NO_RESPONSE = 2
    NO_ATTEMPT = 3