# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import glob
import logging
from pathlib import Path

from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


async def fetch_charm(ops_test: OpsTest) -> str:
    """
    Uses an existing charm in the directory or builds the charm
    if it doesn't exist.
    """
    logger.info("Building charm...")
    try:
        charm_path = Path(get_local_charm()).resolve()
        logger.info("Skipping charm build. Found existing charm.")
    except FileNotFoundError:
        charm_path = await ops_test.build_charm(".")
    logger.info("Charm path is: %s", charm_path)
    return str(charm_path)


def get_local_charm():
    """Search for an already built charm to save time."""
    charm = glob.glob("./*.charm")
    if len(charm) != 1:
        raise FileNotFoundError(f"Found {len(charm)} file(s) with .charm extension.")
    return charm[0]
