"""Optional cutting-height control for exact RockMow a235 devices."""

from __future__ import annotations

import math
from typing import Any

from roborock.exceptions import RoborockException

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATUS_MOW_HEIGHT
from .coordinator import (
    RoborockMowerCoordinator,
    RoborockMowerDevice,
    mower_device_id,
    status_value,
)
from .mower_api import MOW_HEIGHT_MAX, MOW_HEIGHT_MIN

MILLIMETERS_PER_INCH = 25.4
CUTTING_HEIGHT_MIN_INCHES = round(MOW_HEIGHT_MIN / MILLIMETERS_PER_INCH, 2)
CUTTING_HEIGHT_MAX_INCHES = round(MOW_HEIGHT_MAX / MILLIMETERS_PER_INCH, 2)
CUTTING_HEIGHT_STEP_INCHES = 0.01


def inches_to_millimeters(value: float) -> int:
    """Convert a finite inch value to the nearest whole millimetre."""

    if not math.isfinite(value):
        raise ValueError("cutting height must be a finite number")
    if value < CUTTING_HEIGHT_MIN_INCHES or value > CUTTING_HEIGHT_MAX_INCHES:
        raise ValueError(
            f"cutting height must be between {CUTTING_HEIGHT_MIN_INCHES} and "
            f"{CUTTING_HEIGHT_MAX_INCHES} inches"
        )
    return round(value * MILLIMETERS_PER_INCH)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the opt-in cutting-height control without querying or writing."""

    coordinator: RoborockMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoborockMowHeightNumber(coordinator, mower_id)
        for mower_id in coordinator.data
        if coordinator.write_controls_enabled_for(mower_id)
    )


class RoborockMowHeightNumber(CoordinatorEntity[RoborockMowerCoordinator], NumberEntity):
    """Set cutting height through the app's remote_pb command."""

    _attr_has_entity_name = True
    _attr_translation_key = "cutting_height"
    _attr_icon = "mdi:arrow-expand-vertical"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = CUTTING_HEIGHT_MIN_INCHES
    _attr_native_max_value = CUTTING_HEIGHT_MAX_INCHES
    _attr_native_step = CUTTING_HEIGHT_STEP_INCHES
    _attr_native_unit_of_measurement = "in"

    def __init__(self, coordinator: RoborockMowerCoordinator, mower_id: str) -> None:
        """Initialize the cutting-height entity."""

        super().__init__(coordinator)
        self._mower_id = mower_id
        self._attr_unique_id = f"{mower_id}_cutting_height"

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
        """Make the actuator available only while its gate and RPC are ready."""

        return (
            super().available
            and self.coordinator.write_controls_enabled_for(self._mower_id)
            and self.coordinator.mower_api_for(self._mower_id) is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return the numeric DPS height, or no value for an unknown reading."""

        if self._mower is None:
            return None
        value: Any = status_value(self._mower.device, STATUS_MOW_HEIGHT)
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return round(result / MILLIMETERS_PER_INCH, 2)

    async def async_set_native_value(self, value: float) -> None:
        """Send one cutting-height actuator command after rechecking the gate."""

        if not self.coordinator.write_controls_enabled_for(self._mower_id):
            raise HomeAssistantError("Roborock mower controls are not enabled")
        api = self.coordinator.mower_api_for(self._mower_id)
        if api is None:
            raise HomeAssistantError("Roborock mower controls are not available")
        try:
            height_mm = inches_to_millimeters(value)
            await api.set_mow_height(height_mm)
        except (RoborockException, ValueError) as err:
            raise HomeAssistantError(f"Roborock mower cutting height failed: {err}") from err
