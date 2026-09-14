"""Lawn mower entity for the Roborock Mower integration."""

from __future__ import annotations

from typing import Any

from roborock.exceptions import RoborockException

from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATUS_CHARGE_STATE, STATUS_ERROR_CODE, STATUS_MOW_STATE
from .coordinator import RoborockMowerCoordinator, RoborockMowerDevice, mower_device_id, status_value
from .mower_api import MowerApi


MOWING_STATES = {1, 51, 55, 56, 57, 76}
RETURNING_STATES = {3, 61}
PAUSED_STATES = {2, 58}
ERROR_STATES = {5}
DOCKED_CHARGE_STATES = {1, 2}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roborock mower lawn mower entities."""

    coordinator: RoborockMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoborockMowerEntity(coordinator, mower_id) for mower_id in coordinator.data
    )


class RoborockMowerEntity(CoordinatorEntity[RoborockMowerCoordinator], LawnMowerEntity):
    """Represent a Roborock mower, with optional explicitly enabled controls."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: RoborockMowerCoordinator, mower_id: str) -> None:
        """Initialize the lawn mower entity."""

        super().__init__(coordinator)
        self._mower_id = mower_id
        self._attr_unique_id = mower_id
        self._attr_supported_features = (
            LawnMowerEntityFeature.START_MOWING
            | LawnMowerEntityFeature.PAUSE
            | LawnMowerEntityFeature.DOCK
            if coordinator.write_controls_enabled_for(mower_id)
            else LawnMowerEntityFeature(0)
        )

    @property
    def _mower(self) -> RoborockMowerDevice | None:
        return self.coordinator.data.get(self._mower_id)

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device registry information."""

        if self._mower is None:
            return None
        device = self._mower.device
        product = self._mower.product
        identifier = mower_device_id(device)
        return DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer="Roborock",
            name=device.name,
            model=product.model,
            sw_version=device.fv,
            serial_number=device.sn,
        )

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return the current mower activity."""

        if self._mower is None:
            return None

        error_code = status_value(self._mower.device, STATUS_ERROR_CODE)
        if error_code not in (None, 0, "0"):
            return LawnMowerActivity.ERROR

        mow_state = _int_or_none(status_value(self._mower.device, STATUS_MOW_STATE))
        if mow_state in ERROR_STATES:
            return LawnMowerActivity.ERROR
        if mow_state in RETURNING_STATES:
            return LawnMowerActivity.RETURNING
        if mow_state in PAUSED_STATES:
            return LawnMowerActivity.PAUSED
        if mow_state in MOWING_STATES:
            return LawnMowerActivity.MOWING

        charge_state = _int_or_none(status_value(self._mower.device, STATUS_CHARGE_STATE))
        if charge_state in DOCKED_CHARGE_STATES:
            return LawnMowerActivity.DOCKED

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return debug attributes for the main mower entity."""

        if self._mower is None:
            return {}
        return {
            "mow_state": status_value(self._mower.device, STATUS_MOW_STATE),
            "charge_state": status_value(self._mower.device, STATUS_CHARGE_STATE),
            "last_cloud_update": self.coordinator.last_cloud_update,
            "last_mqtt_update": self.coordinator.last_mqtt_update,
            "last_mqtt_protocol": self.coordinator.last_mqtt_protocol,
            "last_mqtt_seen": self.coordinator.last_mqtt_seen.get(self._mower_id),
            "last_mqtt_online_hint": self.coordinator.last_mqtt_online_hint.get(self._mower_id),
            "mqtt_connected": self.coordinator.mqtt_connected,
            "mqtt_subscribed": self.coordinator.mqtt_subscribed.get(self._mower_id),
            "last_mqtt_error": self.coordinator.last_mqtt_error,
        }

    def _command_api(self) -> MowerApi:
        """Return the command API or fail closed when writes are unavailable."""

        api = self.coordinator.mower_api_for(self._mower_id)
        if api is None:
            raise HomeAssistantError("Roborock mower controls are not available")
        return api

    async def async_start_mowing(self) -> None:
        """Start a full mow or resume a paused task."""

        try:
            await self._command_api().start_or_resume(
                status_value(self._mower.device, STATUS_MOW_STATE) if self._mower else None
            )
        except RoborockException as err:
            raise HomeAssistantError(f"Roborock mower start failed: {err}") from err

    async def async_pause(self) -> None:
        """Pause the current mowing task."""

        try:
            await self._command_api().pause()
        except RoborockException as err:
            raise HomeAssistantError(f"Roborock mower pause failed: {err}") from err

    async def async_dock(self) -> None:
        """Return the mower to its dock."""

        try:
            await self._command_api().dock()
        except RoborockException as err:
            raise HomeAssistantError(f"Roborock mower dock failed: {err}") from err


def _int_or_none(value: Any) -> int | None:
    """Return value as int if possible."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return None
