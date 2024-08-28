#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import uuid

import pytest
import requests
from fixtures import deploy_package
from helpers import (
    ACTIVE_STATUS,
    APP_NAME,
    PRO_AIRGAPPED_SERVER_CHANNEL,
    PRO_AIRGAPPED_SERVER_ENDPOINT,
    PRO_AIRGAPPED_SERVER_NAME,
    ensure_model,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_airgapped_contracts_integration(ops_test: OpsTest):
    """
    Test the charm integrates with `pro-airgapped-server`.

    Since the `pro-airgapped-server` charm has no K8s version, we need to deploy
    it on a LXD cloud, and then use a cross-model relation (CMR) to integrate it
    with the Livepatch charm. Note that for a CMR to work, both models should be
    on the same controller.

    To setup the environment for this test follow these steps:
    - Have a LXD and a MicroK8s cloud on your Juju client. Normally, you don't
      need to do anything but to install/configure MicroK8s and LXD. Make sure
      `hostpath-storage` addon is enabled on MicroK8s. If not, you can do this
      by running:
        sudo microk8s enable hostpath-storage
        sudo snap restart microk8s
    - If needed, bootstrap a controller on your LXD cloud and name it `localhost-localhost`.
    - To make your MicroK8s cloud accessible to LXD controller, you need to do
      the following:

      1. Get the non-localhost address of the MicroK8s API server by running:
           microk8s.config | grep 'server: '
         Or use this command to store the IP in a variable:
           address=$(yq '.clusters[0].cluster.server' <(microk8s.config))
      2. Update the localhost IP address (e.g., https://127.0.0.1:16443) in
         `/var/snap/microk8s/current/credentials/client.config` with the above
          address you got. You can use this command if the `address` variable is
          already assigned with the right value (note that since `yq -i` may
          have permission issues, we just used simple redirection):
            cat /var/snap/microk8s/current/credentials/client.config | \
              yq ".clusters[0].cluster.server = \"$address\"" \
              > /var/snap/microk8s/current/credentials/client.config
      3. Restart MicroK8s:
           sudo snap restart microk8s
    - Add the MicroK8s cloud to the LXD controller:
        juju switch localhost-localhost
        juju add-cloud microk8s --controller localhost-localhost --credential microk8s
    - To test if things are set up correctly, try add a model to the MicroK8s
        cloud, configure storage class, deploy an application, and then destroy
        the whole model:
          juju add-model foo microk8s
          juju model-config workload-storage='microk8s-hostpath'
          juju deploy hello-kubecon
          juju destroy-model foo --release-storage --no-prompt

    To revert the above changes, do the following:
    - Remove the MicroK8s cloud from the LXD controller:
        juju remove-cloud microk8s -c localhost-localhost
        juju remove-credential microk8s microk8s -c localhost-localhost
    - You don't actually need to revert the changes you made to MicroK8s
      `client.config`. Anyway, you can replace the changed address to the
       original value (i.e., `https://127.0.0.1:16443`) and restart MicroK8s.
    """

    test_name = "airgapped"
    await deploy_package(ops_test, test_name)

    k8s_model_name = await ensure_model(test_name, ops_test, cloud_name="microk8s", cloud_type="k8s")
    lxd_model_name = await ensure_model(test_name, ops_test, cloud_name="localhost", cloud_type="lxd")

    with ops_test.model_context(lxd_model_name):
        jammy = "ubuntu@22.04"
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                PRO_AIRGAPPED_SERVER_NAME,
                base=jammy,
                channel=PRO_AIRGAPPED_SERVER_CHANNEL,
                application_name=PRO_AIRGAPPED_SERVER_NAME,
                num_units=1,
                config={
                    # Since we don't have a fake yet valid Pro token, we use the following
                    # override mechanism to bypass the validation step. The config below is
                    # taken from happy-path tests of the underlying project.
                    "manual-server-config": "QzE0TFpDQXh6MzZ3Nk5oNUVRRHVENmNtTkt0d1duOgogIGFjY291bnRJbmZvOgogICAgY3JlYXRlZEF0OiAiMjAyMi0wNS0xMlQwNjoyNzowM1oiCiAgICBpZDogYUFQWXc3M3hHCiAgICBuYW1lOiBhLmJAZXhhbXBsZS5jb20KICAgIHR5cGU6IHBlcnNvbmFsCiAgY29udHJhY3RJbmZvOgogICAgYWxsb3dhbmNlczoKICAgIC0gbWV0cmljOiB1bml0cwogICAgICB2YWx1ZTogMwogICAgY3JlYXRlZEF0OiAiMjAyMi0wNS0xMlQwNjoyNzowNFoiCiAgICBjcmVhdGVkQnk6ICIiCiAgICBlZmZlY3RpdmVGcm9tOiAiMjAyMi0wNS0xMlQwNjoyNzowNFoiCiAgICBlZmZlY3RpdmVUbzogIjk5OTktMTItMzFUMDA6MDA6MDBaIgogICAgaWQ6IGNBWC0tb05kCiAgICBpdGVtczoKICAgIC0gY29udHJhY3RJRDogY0FYLS1vTmQKICAgICAgY3JlYXRlZDogIjIwMjItMDUtMTJUMDY6Mjc6MDRaIgogICAgICBlZmZlY3RpdmVGcm9tOiAiMjAyMi0wNS0xMlQwNjoyNzowNFoiCiAgICAgIGVmZmVjdGl2ZVRvOiAiOTk5OS0xMi0zMVQwMDowMDowMFoiCiAgICAgIGV4dGVybmFsSURzOiBudWxsCiAgICAgIGlkOiAzOTYyOTAKICAgICAgbGFzdE1vZGlmaWVkOiAiMjAyMi0wNS0xMlQwNjoyNzowNFoiCiAgICAgIG1ldHJpYzogdW5pdHMKICAgICAgcmVhc29uOiBjb250cmFjdF9jcmVhdGVkCiAgICAgIHZhbHVlOiAzCiAgICBuYW1lOiBhLmJAZXhhbXBsZS5jb20KICAgIG9yaWdpbjogZnJlZQogICAgcHJvZHVjdHM6CiAgICAtIGZyZWUKICAgIHJlc291cmNlRW50aXRsZW1lbnRzOgogICAgLSBhZmZvcmRhbmNlczoKICAgICAgICBhcmNoaXRlY3R1cmVzOgogICAgICAgIC0gYW1kNjQKICAgICAgICAtIHg4Nl82NAogICAgICAgIHNlcmllczoKICAgICAgICAtIHhlbmlhbAogICAgICAgIC0gYmlvbmljCiAgICAgIGRpcmVjdGl2ZXM6CiAgICAgICAgYWRkaXRpb25hbFBhY2thZ2VzOgogICAgICAgIC0gdWJ1bnR1LWNvbW1vbmNyaXRlcmlhCiAgICAgICAgYXB0S2V5OiA5RjkxMkRBREQ5OUVFMUNDNkJGRkZGMjQzQTE4NkU3MzNGNDkxQzQ2CiAgICAgICAgYXB0VVJMOiBodHRwczovL2VzbS51YnVudHUuY29tL2NjCiAgICAgICAgc3VpdGVzOgogICAgICAgIC0geGVuaWFsCiAgICAgICAgLSBiaW9uaWMKICAgICAgZW50aXRsZWQ6IHRydWUKICAgICAgb2JsaWdhdGlvbnM6CiAgICAgICAgZW5hYmxlQnlEZWZhdWx0OiBmYWxzZQogICAgICB0eXBlOiBjYy1lYWw=",  # noqa: E501
                },
            )

            logger.info(f"Waiting for {PRO_AIRGAPPED_SERVER_NAME}")
            await ops_test.model.wait_for_idle(
                apps=[PRO_AIRGAPPED_SERVER_NAME], status=ACTIVE_STATUS, raise_on_blocked=False, timeout=600
            )

            offer_name = f"offer-{uuid.uuid4().hex[0:4]}"
            logger.info("Creating application offer for cross-model relation: %s", offer_name)
            await ops_test.model.create_offer(
                endpoint=PRO_AIRGAPPED_SERVER_ENDPOINT,
                application_name=PRO_AIRGAPPED_SERVER_NAME,
                offer_name=offer_name,
            )

    with ops_test.model_context(k8s_model_name):
        async with ops_test.fast_forward():
            logger.info("Creating SaaS")
            # When using the `ops_test.model.consume` method like this:
            #
            #   await ops_test.model.consume(endpoint=f"{lxd_model_name}.{offer_name}")
            #
            # We get a `KeyError` saying `ops_test.model.info` has no `username`
            # key. To make it work we have to specify the username maunually by
            # picking one of the entries from the `ops_test.model.info.users`
            # list, which could result in unexpected behavior if there were more
            # than one.
            #
            # Another option is to use the Juju client's `consume` command which
            # handles this by defaulting to the current user.
            await ops_test.juju("consume", f"{lxd_model_name}.{offer_name}")
            logger.info("Integrating Livepatch and pro-airgapped-server")
            await ops_test.model.integrate(APP_NAME, offer_name)
            logger.info("Waiting for Livepatch")
            await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", raise_on_blocked=False, timeout=600)

    with ops_test.model_context(lxd_model_name):
        async with ops_test.fast_forward():
            logger.info(f"Waiting for {PRO_AIRGAPPED_SERVER_NAME}")
            await ops_test.model.wait_for_idle(
                apps=[PRO_AIRGAPPED_SERVER_NAME], status=ACTIVE_STATUS, raise_on_blocked=False, timeout=600
            )

    with ops_test.model_context(k8s_model_name):
        logger.info("Getting Livepatch model status")
        status = await ops_test.model.get_status()  # noqa: F821
        logger.info(f"Status: {status}")
        assert ops_test.model.applications[APP_NAME].status == ACTIVE_STATUS

        app_address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
        url = f"http://{app_address}:8080/debug/status"
        logger.info("Querying app address: %s", url)
        r = requests.get(url, timeout=2.0)
        assert r.status_code == 200
        body = r.json()
        logger.info("Output = %s", body)
        assert body["contracts"]["status"] == "OK"
