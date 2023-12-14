.PHONY: microk8s-push operator-prod-k8s deploy-onprem-k8s

# Re-tag and push to microk8s for testing locally
# Note: When pushing, should share many layers between each other
microk8s-push: docker-prod docker-admin-tool docker-schema-tool
	docker tag livepatch 			    localhost:32000/livepatch-server:latest
	docker tag livepatch-schema-tool 	localhost:32000/livepatch-schema-tool:latest
	docker tag livepatch-admin-tool 	localhost:32000/livepatch-admin-tool:latest

	docker push localhost:32000/livepatch-server:latest
	docker push localhost:32000/livepatch-schema-tool:latest
	docker push localhost:32000/livepatch-admin-tool:latest

# Builds the prod operator charm, can be used with hosted or onprem images
operator-prod-k8s:
	rm -f *.charm
	sudo -E charmcraft pack -p ./charms/operator-k8s


# NOTE: For local use only
# Requires the schema-tool (docker-schema-tool), livepatch prod (docker), and charm (operator-prod-k8s) to be run first.
deploy-onprem-k8s: operator-prod-k8s microk8s-push
	juju deploy ./livepatch_ubuntu-20.04-amd64.charm \
		--resource livepatch-schema-upgrade-tool-image=localhost:32000/livepatch-schema-tool:latest \
		--resource livepatch-server-image=localhost:32000/livepatch-onprem:latest

