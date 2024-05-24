# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

.PHONY: microk8s-push operator-prod-k8s deploy-onprem-k8s

# Builds the prod operator charm, can be used with hosted or onprem images
operator-prod-k8s:
	rm -f *.charm
	charmcraft pack


# NOTE: For local use only
# Requires the schema-tool (docker-schema-tool), livepatch prod (docker), and charm (operator-prod-k8s) to be run first.
deploy-onprem-k8s: operator-prod-k8s microk8s-push
	juju deploy ./canonical-livepatch-server-k8s_ubuntu-20.04-amd64_ubuntu-22.04-amd64.charm \
		--resource livepatch-schema-upgrade-tool-image=localhost:32000/livepatch-schema-tool:latest \
		--resource livepatch-server-image=localhost:32000/livepatch-server:latest

