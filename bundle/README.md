# Livepatch server on-premises bundle

[![CharmHub Badge](https://charmhub.io/canonical-livepatch-onprem/badge.svg)](https://charmhub.io/canonical-livepatch-onprem)

This bundle deploys a [Livepatch](https://ubuntu.com/security/livepatch)
on-prem server for serving patches to machines running Livepatch client.

## Bundled applications

The Livepatch server on-prem model consists of the following applications:

 - [Nginx ingress integrator](https://charmhub.io/nginx-ingress-integrator) to create an `ingress` resource in K8s cluster to forward incoming HTTP requests to the Livepatch server instance.
 - [Livepatch](https://charmhub.io/canonical-livepatch-server) as the core Livepatch service.
 - [PostgreSQL](https://charmhub.io/postgresql) as database for patch data and machine reports.

## Deployment

For more detailed steps on using this bundle, please see the [tutorials](https://ubuntu.com/security/livepatch/docs/livepatch_on_prem/tutorial) on the Livepatch website.
