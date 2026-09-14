"""Optional explicit write buttons for Roborock mower devices."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from roborock.exceptions import RoborockException

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RoborockMowerCoordinator, RoborockMowerDevice, mower_device_id
from .mower_api import MowerApi


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up optional mower action buttons."""

    coordinator: RoborockMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[RoborockMowerButton] = []
    for mower_id in coordinator.data:
        if not coordinator.write_controls_enabled_for(mower_id):
            continue
        entities.extend(
            (
                RoborockEdgeCutButton(coordinator, mower_id),
                RoborockStopButton(coordinator, mower_id),
            )
        )
    async_add_entities(entities)


class RoborockMowerButton(CoordinatorEntity[RoborockMowerCoordinator], ButtonEntity):
    """Base class for an explicitly enabled mower command button."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RoborockMowerCoordinator, mower_id: str) -> None:
        """Initialize a mower command button."""

        super().__init__(coordinator)
        self._mower_id = mower_id

    @property
    def _mower(self) -> RoborockMowerDevice | None:
        """Return the mower represented by this entity."""

        return self.coordinator.data.get(self._mower_id)

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device registry information."""

        if self._mower is None:
            return None
        device = self._mower.device
        product = self._mower.product
        return DeviceInfo(
            identifiers={(DOMAIN, mower_device_id(device))},
            manufacturer="Roborock",
            name=device.name,
            model=product.model,
            sw_version=device.fv,
            serial_number=device.sn,
        )

    @property
    def available(self) -> bool:
        """Only make a button available when its RPC channel is ready."""

        return super().available and self.coordinator.mower_api_for(self._mower_id) is not None

    async def _async_press(self, action_name: str, action: Callable[[MowerApi], Awaitable[Any]]) -> None:
        """Run a command and expose RPC failures as Home Assistant errors."""

        api = self.coordinator.mower_api_for(self._mower_id)
        if api is None:
            raise HomeAssistantError("Roborock mower controls are not available")
        try:
            await action(api)
        except RoborockException as err:
            raise HomeAssistantError(f"Roborock mower {action_name} failed: {err}") from err


class RoborockEdgeCutButton(RoborockMowerButton):
    """Start an edge/perimeter cut via ``remote_pb``."""

    _attr_translation_key = "edge_cut"
    _attr_icon = "mdi:vector-square"

    def __init__(self, coordinator: RoborockMowerCoordinator, mower_id: str) -> None:
        """Initialize the edge-cut button."""

        super().__init__(coordinator, mower_id)
        self._attr_unique_id = f"{mower_id}_edge_cut"

    async def async_press(self) -> None:
        """Start an edge cut."""

        await self._async_press("edge cut", lambda api: api.edge_cut())


class RoborockStopButton(RoborockMowerButton):
    """Stop/end the current mowing task via ``remote_pb``."""

    _attr_translation_key = "stop"
    _attr_icon = "mdi:stop"

    def __init__(self, coordinator: RoborockMowerCoordinator, mower_id: str) -> None:
        """Initialize the stop button."""

        super().__init__(coordinator, mower_id)
        self._attr_unique_id = f"{mower_id}_stop"

    async def async_press(self) -> None:
        """Stop/end the current mowing task."""

        await self._async_press("stop", lambda api: api.stop())
