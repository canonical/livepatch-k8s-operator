# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common pytest configuration and fixtures for integration tests."""

import juju.utils


def _patch_libjuju_series_map() -> None:
    """Patch python-libjuju's series map to include newer Ubuntu releases not yet in its lookup table."""
    missing = {
        "resolute": "26.04",
    }
    for series, version in missing.items():
        if series not in juju.utils.UBUNTU_SERIES:
            juju.utils.UBUNTU_SERIES[series] = version
        if series not in juju.utils.ALL_SERIES_VERSIONS:
            juju.utils.ALL_SERIES_VERSIONS[series] = version


def pytest_configure(config):
    _patch_libjuju_series_map()
