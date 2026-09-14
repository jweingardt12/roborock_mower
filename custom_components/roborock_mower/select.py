"""Optional saved-area select entity for Roborock RockMow devices."""

from __future__ import annotations

import logging
from typing import Any

from roborock.exceptions import RoborockException

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RoborockMowerCoordinator, RoborockMowerDevice, mower_device_id

_LOGGER = logging.getLogger(__name__)


def _area_id_key(area_id: Any) -> str | None:
    """Return a comparison key for a supported area ID, or None if unsafe."""

    if isinstance(area_id, bool) or not isinstance(area_id, int | str):
        return None
    if isinstance(area_id, str) and not area_id.strip():
        return None
    return str(area_id).strip()


def selectable_area_map(areas: Any) -> dict[str, dict[str, Any]]:
    """Return only unambiguous named areas suitable for a select entity."""

    if not isinstance(areas, list):
        return {}

    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for area in areas:
        if not isinstance(area, dict):
            continue
        area_id = area.get("id")
        id_key = _area_id_key(area_id)
        name = area.get("name")
        if id_key is None or not isinstance(name, str) or not name.strip():
            continue
        candidates.append(
            (id_key, name.strip().casefold(), {"id": area_id, "name": name})
        )

    id_counts: dict[str, int] = {}
    name_counts: dict[str, int] = {}
    for id_key, name_key, _area in candidates:
        id_counts[id_key] = id_counts.get(id_key, 0) + 1
        name_counts[name_key] = name_counts.get(name_key, 0) + 1

    return {
        area["name"]: area
        for id_key, name_key, area in candidates
        if id_counts[id_key] == 1 and name_counts[name_key] == 1
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover saved areas and expose them only behind the existing write gate."""

    coordinator: RoborockMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[RoborockMowAreaSelect] = []
    for mower_id in coordinator.data:
        if not coordinator.write_controls_enabled_for(mower_id):
            continue
        api = coordinator.mower_api_for(mower_id)
        if api is None:
            continue
        try:
            areas = await api.get_areas()
        except RoborockException as err:
            _LOGGER.debug("Could not discover saved areas for %s: %s", mower_id, err)
            continue
        safe_areas = selectable_area_map(areas)
        if safe_areas:
            entities.append(RoborockMowAreaSelect(coordinator, mower_id, safe_areas))
    async_add_entities(entities)


class RoborockMowAreaSelect(CoordinatorEntity[RoborockMowerCoordinator], SelectEntity):
    """Action-style selector where choosing a saved area starts area mowing."""

    _attr_has_entity_name = True
    _attr_translation_key = "mow_area"
    _attr_icon = "mdi:select-marker"

    def __init__(
        self,
        coordinator: RoborockMowerCoordinator,
        mower_id: str,
        areas: dict[str, dict[str, Any]],
    ) -> None:
        """Initialize a saved-area selector."""

        super().__init__(coordinator)
        self._mower_id = mower_id
        self._areas = areas
        self._attr_options = list(areas)
        self._attr_unique_id = f"{mower_id}_mow_area"

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
        """Only make the selector available while the explicit gate is active."""

        return (
            super().available
            and self.coordinator.write_controls_enabled_for(self._mower_id)
            and self.coordinator.mower_api_for(self._mower_id) is not None
        )

    @property
    def current_option(self) -> str | None:
        """Return no persistent value because selecting an area is an action."""

        return None

    async def async_select_option(self, option: str) -> None:
        """Start mowing the explicitly selected saved area."""

        area = self._areas.get(option)
        if area is None or not self.coordinator.write_controls_enabled_for(
            self._mower_id
        ):
            return
        api = self.coordinator.mower_api_for(self._mower_id)
        if api is None:
            raise HomeAssistantError("Roborock mower controls are not available")
        try:
            await api.start_area_mow([area])
        except RoborockException as err:
            raise HomeAssistantError(f"Roborock mower area mow failed: {err}") from err
