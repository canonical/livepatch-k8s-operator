# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import uuid
from pathlib import Path
from typing import Literal, Union

import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
POSTGRESQL_NAME = "postgresql-k8s"
POSTGRESQL_CHANNEL = "14/stable"
PRO_AIRGAPPED_SERVER_NAME = "pro-airgapped-server"
PRO_AIRGAPPED_SERVER_CHANNEL = "latest/stable"
PRO_AIRGAPPED_SERVER_ENDPOINT = "livepatch-server"
NGINX_INGRESS_CHARM_NAME = "nginx-ingress-integrator"
ACTIVE_STATUS = ActiveStatus.name
BLOCKED_STATUS = BlockedStatus.name
WAITING_STATUS = WaitingStatus.name


# Unique instance ID to be used for safe naming of models.
__INSTANCE_ID = uuid.uuid4().hex


async def ensure_model(
    test_name: str,
    ops_test: OpsTest,
    cloud_name: str,
    cloud_type: Literal["k8s", "lxd"],
) -> Union[str, None]:
    """
    Get (or create) the model on the given cloud and return its name.

    This function is meant to be used only in multi-cloud test cases where
    multiple models on different clouds has to be set up before proceeding with
    deploying charms on them.
    """
    model_name = f"livepatch-test-{test_name}-{cloud_name}-{__INSTANCE_ID[:4]}"
    for _, v in ops_test.models.items():
        if v.model_name == model_name:
            return model_name

    # Although `OpsTest.track_model` can create a new model, it results in an
    # authorization error when adding a model to a MicroK8s cloud. So, we have
    # to directly use the Juju controller to create a model and then call the
    # `track_model` method to ensure it'll be destroyed at the end of the test.
    await (await ops_test.model.get_controller()).add_model(
        model_name=model_name,
        cloud_name=cloud_name,
        credential_name=cloud_name,
    )

    model = await ops_test.track_model(
        alias=model_name,
        model_name=model_name,
        cloud_name=cloud_name,
        use_existing=True,
        keep=ops_test._init_keep_model or False,  # To respect the `--keep-models` option, if provided.
    )

    # When adding a K8s cloud to a LXD controller, we need to update the
    # workload storage parameter to a storage class defined in the K8s cluster,
    # otherwise the model in unable to deploy charms. Check out these bug
    # reports for more context:
    #   - https://bugs.launchpad.net/juju/+bug/2031216
    #   - https://bugs.launchpad.net/juju/+bug/2077426
    if cloud_type == "k8s":
        controller_cloud = await (await model.get_controller()).cloud()
        if controller_cloud.cloud.type_ == "lxd":
            # You can get a list of available storage classes on your MicroK8s
            # cluster by:
            #   microk8s kubectl get storageclass
            exit_code, stdout, stderr = await ops_test.juju(
                "model-config", "-m", model.name, "workload-storage=microk8s-hostpath"
            )
            if exit_code != 0:
                logger.error(f"running `juju model-config` failed:\n\nstdout:\n{stdout}\n\nstderr:\n{stderr}")
                raise RuntimeError("running `juju model-config` failed")

    return model.name


async def get_unit_url(ops_test: OpsTest, application, unit, port, protocol="http"):
    """Returns unit URL from the model.

    Args:
        ops_test: PyTest object.
        application: Name of the application.
        unit: Number of the unit.
        port: Port number of the URL.
        protocol: Transfer protocol (default: http).

    Returns:
        Unit URL of the form {protocol}://{address}:{port}
    """
    # Sometimes get_unit_address returns a None, no clue why.
    url = await ops_test.model.applications[application].units[unit].get_public_address()
    return f"{protocol}://{url}:{port}"


def oci_image(metadata_file: str, image_name: str) -> str:
    """Find upstream source for a container image.

    Args:
        metadata_file: string path of metadata YAML file relative
            to top level charm directory
        image_name: OCI container image string name as defined in
            metadata.yaml file

    Returns:
        upstream image source

    Raises:
        FileNotFoundError: if metadata_file path is invalid
        ValueError: if upstream source for image name can not be found
    """
    metadata = yaml.safe_load(Path(metadata_file).read_text())

    resources = metadata.get("resources", {})
    if not resources:
        raise ValueError("No resources found")

    image = resources.get(image_name, {})
    if not image:
        raise ValueError(f"{image_name} image not found")

    upstream_source = image.get("upstream-source", "")
    if not upstream_source:
        raise ValueError("Upstream source not found")

    return upstream_source
