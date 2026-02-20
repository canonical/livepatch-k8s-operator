# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""gateway-route interface library.

## Getting Started

To get started using the library, you just need to fetch the library using `charmcraft`.

```shell
cd some-charm
charmcraft fetch-lib charms.gateway_api_integrator.v0.gateway_route
```

In the `metadata.yaml` of the charm, add the following:

```yaml
requires:
    gateway-route:
        interface: gateway-route
        limit: 1
```

Then, to initialise the library:

```python
from charms.gateway_api_integrator.v0.gateway_route import GatewayRouteRequirer

class SomeCharm(CharmBase):
def __init__(self, *args):
    # ...

    # There are 2 ways you can use the requirer implementation:
    # 1. To initialize the requirer with parameters:
    self.gateway_route_requirer = GatewayRouteRequirer(self,
        relation_name=<required>,
        name=<optional>,
        model=<optional>,
        port=<optional>,
        hostname=<optional>,
        paths=<optional>,
    )

    # 2.To initialize the requirer with no parameters, i.e
    # self.gateway_route_requirer = GatewayRouteRequirer(self)
    # This will simply initialize the requirer class and it won't perform any action.

    # Afterwards regardless of how you initialized the requirer you can call the
    # provide_gateway_route_requirements method anywhere in your charm to update the requirer data.
    # The method takes the same number of parameters as the requirer class.
    # provide_gateway_route_requirements(name=, model=, ...)

    self.framework.observe(
        self.framework.on.config_changed, self._on_config_changed
    )
    self.framework.observe(
        self.gateway_route_requirer.on.ready, self._on_endpoints_ready
    )
    self.framework.observe(
        self.gateway_route_requirer.on.removed, self._on_endpoints_removed
    )

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        self.gateway_route_requirer.provide_gateway_route_requirements(...)

    def _on_endpoints_ready(self, _: EventBase) -> None:
        # Handle endpoints ready event
        ...

    def _on_endpoints_removed(self, _: EventBase) -> None:
        # Handle endpoints removed event
        ...
"""

import json
import logging
from typing import Annotated, Any, MutableMapping, Optional, cast

from ops import CharmBase, ModelError, RelationBrokenEvent
from ops.charm import CharmEvents
from ops.framework import EventBase, EventSource, Object
from ops.model import Relation
from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from pydantic.dataclasses import dataclass
from validators import domain

# The unique Charmhub library identifier, never change it
LIBID = "53fdf90019a7406695064ed1e3d2708f"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2

logger = logging.getLogger(__name__)
GATEWAY_ROUTE_RELATION_NAME = "gateway-route"


class DataValidationError(Exception):
    """Raised when data validation fails."""


class GatewayRouteInvalidRelationDataError(Exception):
    """Raised when data validation of the gateway-route relation fails."""


class _DatabagModel(BaseModel):
    """Base databag model.

    Attrs:
        model_config: pydantic model configuration.
    """

    model_config = ConfigDict(
        # tolerate additional keys in databag
        extra="ignore",
        # Allow instantiating this class by field name (instead of forcing alias).
        populate_by_name=True,
        # Custom config key: whether to nest the whole datastructure (as json)
        # under a field or spread it out at the toplevel.
        _NEST_UNDER=None,
    )  # type: ignore
    """Pydantic config."""

    @classmethod
    def load(cls, databag: MutableMapping) -> "_DatabagModel":
        """Load this model from a Juju json databag.

        Args:
            databag: Databag content.

        Raises:
            DataValidationError: When model validation failed.

        Returns:
            _DatabagModel: The validated model.
        """
        nest_under = cls.model_config.get("_NEST_UNDER")
        if nest_under:
            return cls.model_validate(json.loads(databag[nest_under]))

        try:
            data = {
                k: json.loads(v)
                for k, v in databag.items()
                # Don't attempt to parse model-external values
                if k in {(f.alias or n) for n, f in cls.model_fields.items()}
            }
        except json.JSONDecodeError as e:
            msg = f"invalid databag contents: expecting json. {databag}"
            logger.error(msg)
            raise DataValidationError(msg) from e

        try:
            return cls.model_validate_json(json.dumps(data))
        except ValidationError as e:
            msg = f"failed to validate databag: {databag}"
            logger.error(str(e), exc_info=True)
            raise DataValidationError(msg) from e

    @classmethod
    def from_dict(cls, values: dict) -> "_DatabagModel":
        """Load this model from a dict.

        Args:
            values: Dict values.

        Raises:
            DataValidationError: When model validation failed.

        Returns:
            _DatabagModel: The validated model.
        """
        try:
            logger.info("Loading values from dictionary: %s", values)
            return cls.model_validate(values)
        except ValidationError as e:
            msg = f"failed to validate: {values}"
            logger.debug(msg, exc_info=True)
            raise DataValidationError(msg) from e

    def dump(
        self, databag: Optional[MutableMapping] = None, clear: bool = True
    ) -> Optional[MutableMapping]:
        """Write the contents of this model to Juju databag.

        Args:
            databag: The databag to write to.
            clear: Whether to clear the databag before writing.

        Returns:
            MutableMapping: The databag.
        """
        if clear and databag:
            databag.clear()

        if databag is None:
            databag = {}
        nest_under = self.model_config.get("_NEST_UNDER")
        if nest_under:
            databag[nest_under] = self.model_dump_json(
                by_alias=True,
                # skip keys whose values are default
                exclude_defaults=True,
            )
            return databag

        dct = self.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        databag.update({k: json.dumps(v) for k, v in dct.items()})
        return databag


def valid_fqdn(value: str) -> str:
    """Validate if value is a valid fqdn. TLDs are not allowed.

    Raises:
        ValueError: When value is not a valid domain.

    Args:
        value: The value to validate.
    """
    fqdn = value[2:] if value.startswith("*.") else value
    if not bool(domain(fqdn)):
        raise ValueError(f"Invalid domain: {value}")
    return value


class RequirerApplicationData(_DatabagModel):
    """Configuration model for Gateway route requirer application data.

    Attributes:
        hostname: Optional: The hostname of this service.
        paths: List of URL paths to route to this service. Defaults to an empty list.
        model: The model the application is in.
        name: Name of the app requesting gateway route.
        port: The port number on which the service is listening.
    """

    hostname: Annotated[str, BeforeValidator(valid_fqdn)] | None = Field(
        description="Hostname of this service."
    )
    paths: list[str] = Field(description="The list of paths to route to this service.", default=[])
    model: str = Field(description="The model the application is in.")
    name: str = Field(description="The name of the app requesting gateway route.")
    port: int = Field(
        description="The port number on which the service is listening.", ge=1, le=65535
    )


class GatewayRouteProviderAppData(_DatabagModel):
    """gateway-route provider databag schema.

    Attributes:
        endpoints: The endpoints that maps to the backend.
    """

    endpoints: list[AnyHttpUrl]


@dataclass
class GatewayRouteRequirerData:
    """gateway-route requirer data.

    Attributes:
        relation_id: Id of the relation.
        application_data: Application data.
    """

    relation_id: int
    application_data: RequirerApplicationData


class GatewayRouteDataAvailableEvent(EventBase):
    """GatewayRouteDataAvailableEvent custom event.

    This event indicates that the requirers data are available.
    """


class GatewayRouteDataRemovedEvent(EventBase):
    """GatewayRouteDataRemovedEvent custom event.

    This event indicates that one of the endpoints was removed.
    """


class GatewayRouteProviderEvents(CharmEvents):
    """List of events that the gateway-route requirer charm can leverage.

    Attributes:
        data_available: This event indicates that
            the gateway-route endpoints are available.
        data_removed: This event indicates that one of the endpoints was removed.
    """

    data_available = EventSource(GatewayRouteDataAvailableEvent)
    data_removed = EventSource(GatewayRouteDataRemovedEvent)


class GatewayRouteProvider(Object):
    """Gateway-route interface provider implementation.

    Attributes:
        on: Custom events of the provider.
        relation: Related applications.
    """

    on = GatewayRouteProviderEvents()  # type: ignore

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = GATEWAY_ROUTE_RELATION_NAME,
        raise_on_validation_error: bool = False,
    ) -> None:
        """Initialize the GatewayRouteProvider.

        Args:
            charm: The charm that is instantiating the library.
            relation_name: The name of the relation.
            raise_on_validation_error: Whether the library should raise
                GatewayRouteInvalidRelationDataError when requirer data validation fails.
                If this is set to True the provider charm needs to also catch and handle the
                thrown exception.
        """
        super().__init__(charm, relation_name)

        self._relation_name = relation_name
        self.charm = charm
        self.raise_on_validation_error = raise_on_validation_error
        on = self.charm.on
        self.framework.observe(on[self._relation_name].relation_created, self._configure)
        self.framework.observe(on[self._relation_name].relation_changed, self._configure)
        self.framework.observe(on[self._relation_name].relation_broken, self._on_endpoint_removed)
        self.framework.observe(
            on[self._relation_name].relation_departed, self._on_endpoint_removed
        )

    @property
    def relation(self) -> Relation | None:
        """The list of Relation instances associated with this endpoint."""
        return self.charm.model.get_relation(self._relation_name)

    def _configure(self, _event: EventBase) -> None:
        """Handle relation events."""
        if self.relation:
            # Only for data validation
            _ = self.get_data(self.relation)
            self.on.data_available.emit()

    def _on_endpoint_removed(self, _: EventBase) -> None:
        """Handle relation broken/departed events."""
        self.on.data_removed.emit()

    def get_data(self, relation: Relation) -> Optional[GatewayRouteRequirerData]:
        """Fetch requirer data.

        Args:
            relation: The relation instance to fetch data from.

        Raises:
            GatewayRouteInvalidRelationDataError: When requirer data validation fails.

        Returns:
            GatewayRouteRequirerData: Validated data from the gateway-route requirer.
        """
        requirer_data: Optional[GatewayRouteRequirerData] = None
        if relation:
            try:
                application_data = self._get_requirer_application_data(relation)
                requirer_data = GatewayRouteRequirerData(
                    relation_id=relation.id,
                    application_data=application_data,
                )
            except DataValidationError as exc:
                if self.raise_on_validation_error:
                    logger.error(
                        "gateway-route data validation failed for relation %s: %s",
                        relation,
                        str(exc),
                    )
                    raise GatewayRouteInvalidRelationDataError(
                        f"gateway-route data validation failed for relation: {relation}"
                    ) from exc
        return requirer_data

    def _get_requirer_application_data(self, relation: Relation) -> RequirerApplicationData:
        """Fetch and validate the requirer's application databag.

        Args:
            relation: The relation to fetch application data from.

        Raises:
            DataValidationError: When requirer application data validation fails.

        Returns:
            RequirerApplicationData: Validated application data from the requirer.
        """
        try:
            return cast(
                RequirerApplicationData,
                RequirerApplicationData.load(relation.data.get(relation.app, {})),
            )
        except DataValidationError:
            logger.error("Invalid requirer application data for %s", relation.app.name)
            raise

    def publish_endpoints(self, endpoints: list[AnyHttpUrl], relation: Relation) -> None:
        """Publish to the app databag the proxied endpoints.

        Args:
            endpoints: The list of proxied endpoints to publish.
            relation: The relation with the requirer application.
        """
        GatewayRouteProviderAppData(endpoints=cast(list[AnyHttpUrl], endpoints)).dump(
            relation.data.get(self.charm.app), clear=True
        )


class GatewayRouteEnpointsReadyEvent(EventBase):
    """GatewayRouteEnpointsReadyEvent custom event."""


class GatewayRouteEndpointsRemovedEvent(EventBase):
    """GatewayRouteEndpointsRemovedEvent custom event."""


class GatewayRouteRequirerEvents(CharmEvents):
    """List of events that the gateway-route requirer charm can leverage.

    Attributes:
        ready: when the provider proxied endpoints are ready.
        removed: when the provider removes or withdraws the proxied endpoints, or the
            relation with the provider is removed/broken.
    """

    ready = EventSource(GatewayRouteEnpointsReadyEvent)
    removed = EventSource(GatewayRouteEndpointsRemovedEvent)


class GatewayRouteRequirer(Object):
    """gateway-route interface requirer implementation.

    Attributes:
        on: Custom events of the requirer.
    """

    on = GatewayRouteRequirerEvents()  # type: ignore

    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        name: str | None = None,
        model: str | None = None,
        port: int | None = None,
        hostname: str | None = None,
        paths: Optional[list[str]] = None,
    ) -> None:
        """Initialize the GatewayRouteRequirer.

        Args:
            charm: The charm that is instantiating the library.
            relation_name: The name of the relation to bind to.
            name: The name of the service to route traffic to.
            model: The model of the service to route traffic to.
            port: The port the service is listening on.
            hostname: Hostname of this service.
            paths: List of URL paths to route to this service.
        """
        super().__init__(charm, relation_name)

        self._relation_name = relation_name
        self.charm = charm
        self.relation = self.model.get_relation(self._relation_name)

        if name and model and port and hostname:
            # If all required parameters are provided, immediately provide the requirements
            self.provide_gateway_route_requirements(
                name,
                model,
                port,
                hostname,
                paths,
            )
        else:
            self._application_data = self._generate_application_data()

        on = self.charm.on
        self.framework.observe(on[self._relation_name].relation_created, self._configure)
        self.framework.observe(on[self._relation_name].relation_changed, self._configure)
        self.framework.observe(on[self._relation_name].relation_broken, self._on_relation_broken)

    def _configure(self, _: EventBase) -> None:
        """Handle relation events."""
        self.update_relation_data()
        if self.relation and self.get_routed_endpoints():
            # This event is only emitted when the provider databag changes
            # which only happens when relevant changes happened
            # Additionally this event is purely informational and it's up to the requirer to
            # fetch the routed endpoints in their code using get_routed_endpoints
            self.on.ready.emit()

    def _on_relation_broken(self, _: RelationBrokenEvent) -> None:
        """Handle relation broken event."""
        self.on.removed.emit()

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def provide_gateway_route_requirements(
        self,
        name: str,
        model: str,
        port: int,
        hostname: str,
        paths: Optional[list[str]] = None,
    ) -> None:
        """Update gateway-route requirements data in the relation.

        Args:
            name: The name of the service to route traffic to.
            model: The model of the service to route traffic to.
            port: The port the service is listening on.
            hostname: Hostname of this service.
            paths: List of URL paths to route to this service.
        """
        self._application_data = self._generate_application_data(
            name,
            model,
            port,
            hostname,
            paths,
        )
        self.update_relation_data()

    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    def _generate_application_data(
        self,
        name: Optional[str] = None,
        model: Optional[str] = None,
        port: Optional[int] = None,
        hostname: Optional[str] = None,
        paths: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Generate the complete application data structure.

        Args:
            name: The name of the service to route traffic to.
            model: The model of the service to route traffic to.
            port: The port the service is listening on.
            paths: List of URL paths to route to this service.
            hostname: Hostname of this service.

        Returns:
            dict: A dictionary containing the complete application data structure.
        """
        # Apply default value to list parameters to avoid problems with mutable default args.
        if not paths:
            paths = []

        application_data: dict[str, Any] = {
            "hostname": hostname,
            "model": model,
            "name": name,
            "paths": paths,
            "port": port,
        }

        return application_data

    def update_relation_data(self) -> None:
        """Update the application data in the relation."""
        if not self._application_data.get("hostname") and not self._application_data.get("port"):
            logger.warning("Required field(s) are missing, skipping update of the relation data.")
            return

        if relation := self.relation:
            self._update_application_data(relation)

    def _update_application_data(self, relation: Relation) -> None:
        """Update application data in the relation databag.

        Args:
            relation: The relation instance.
        """
        if self.charm.unit.is_leader():
            application_data = self._prepare_application_data()
            application_data.dump(relation.data.get(self.charm.app), clear=True)

    def _prepare_application_data(self) -> RequirerApplicationData:
        """Prepare and validate the application data.

        Raises:
            DataValidationError: When validation of application data fails.

        Returns:
            RequirerApplicationData: The validated application data model.
        """
        try:
            return cast(
                RequirerApplicationData, RequirerApplicationData.from_dict(self._application_data)
            )
        except ValidationError as exc:
            logger.error("Validation error when preparing requirer application data.")
            raise DataValidationError(
                "Validation error when preparing requirer application data."
            ) from exc

    def get_routed_endpoints(self) -> list[AnyHttpUrl]:
        """The full ingress URL to reach the current unit.

        Returns:
            The provider URLs or an empty list if the URLs aren't available yet or are not valid.
        """
        relation = self.relation
        if not relation or not relation.app:
            return []

        # Fetch the provider's app databag
        try:
            databag = relation.data.get(relation.app)
        except ModelError:
            logger.exception("Error reading remote app data.")
            return []

        if not databag:  # not ready yet
            return []

        try:
            provider_data = cast(
                GatewayRouteProviderAppData, GatewayRouteProviderAppData.load(databag)
            )
            return provider_data.endpoints
        except DataValidationError:
            logger.exception("Invalid provider url.")
            return []
