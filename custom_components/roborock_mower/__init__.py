"""The Roborock Mower integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from roborock.exceptions import RoborockException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    PLATFORMS,
)
from .coordinator import RoborockMowerCoordinator

SERVICE_MOW_AREAS = "mow_areas"
ATTR_DEVICE_ID = "device_id"
ATTR_AREA_IDS = "area_ids"
_ALLOWED_MOW_AREAS_KEYS = frozenset({ATTR_DEVICE_ID, ATTR_AREA_IDS})


def _area_id_key(area_id: Any) -> str | None:
    """Return the stable key used for saved-area ID comparisons."""

    if isinstance(area_id, bool) or not isinstance(area_id, int | str):
        return None
    if isinstance(area_id, str) and not area_id.strip():
        return None
    return str(area_id).strip()


def _validate_area_ids(value: Any) -> list[int | str]:
    """Validate a non-empty list of saved-area IDs without coercing values."""

    if not isinstance(value, list) or not value:
        raise vol.Invalid("area_ids must be a non-empty list")
    seen: set[str] = set()
    for area_id in value:
        key = _area_id_key(area_id)
        if key is None:
            raise vol.Invalid("area_ids must contain only non-empty strings or integers")
        if key in seen:
            raise vol.Invalid("area_ids must not contain duplicates")
        seen.add(key)
    return value


_MOW_AREAS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_AREA_IDS): _validate_area_ids,
        vol.Optional(ATTR_DEVICE_ID): vol.Any(str, [str]),
    }
)


def _single_device_target(data: Mapping[str, Any]) -> str:
    """Return exactly one device target from a resolved Home Assistant call."""

    target = data.get(ATTR_DEVICE_ID)
    if isinstance(target, str):
        targets: list[Any] = [target]
    elif isinstance(target, list):
        targets = target
    else:
        raise HomeAssistantError("mow_areas requires one device target")
    if len(targets) != 1 or not isinstance(targets[0], str) or not targets[0].strip():
        raise HomeAssistantError("mow_areas requires exactly one device target")
    return targets[0]


def _resolve_mower_target(
    hass: HomeAssistant, device_id: str
) -> tuple[RoborockMowerCoordinator, str]:
    """Resolve one HA device target to one loaded mower and its mower ID."""

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Unknown device: {device_id}")
    mower_ids = {
        identifier[1]
        for identifier in device.identifiers
        if isinstance(identifier, tuple)
        and len(identifier) == 2
        and identifier[0] == DOMAIN
        and isinstance(identifier[1], str)
    }
    if not mower_ids:
        raise HomeAssistantError(f"Device {device_id} is not a Roborock mower")

    matches: list[tuple[RoborockMowerCoordinator, str]] = []
    for coordinator in hass.data.get(DOMAIN, {}).values():
        for mower_id in mower_ids:
            if mower_id in coordinator.data:
                matches.append((coordinator, mower_id))
    if len(matches) != 1:
        raise HomeAssistantError(f"Mower for device {device_id} is not loaded uniquely")
    return matches[0]


def _canonical_areas_for_ids(
    area_ids: list[int | str], saved_areas: Any
) -> list[dict[str, Any]]:
    """Validate IDs against a fresh snapshot and return its canonical names."""

    _validate_area_ids(area_ids)
    if not isinstance(saved_areas, list):
        raise HomeAssistantError("Roborock mower saved-area snapshot is unavailable")

    by_id: dict[str, dict[str, Any]] = {}
    for area in saved_areas:
        if not isinstance(area, dict):
            continue
        area_key = _area_id_key(area.get("id"))
        name = area.get("name")
        if area_key is None or not isinstance(name, str) or not name.strip():
            continue
        if area_key in by_id:
            raise HomeAssistantError("Roborock mower saved-area snapshot has duplicate IDs")
        by_id[area_key] = {"id": area["id"], "name": name}

    canonical: list[dict[str, Any]] = []
    for area_id in area_ids:
        area_key = _area_id_key(area_id)
        if area_key is None or area_key not in by_id:
            raise HomeAssistantError(f"Unknown or stale Roborock mower area ID: {area_id}")
        canonical.append(dict(by_id[area_key]))
    return canonical


async def _async_mow_areas(hass: HomeAssistant, call: ServiceCall) -> None:
    """Validate one target and send exactly one multi-area mow command."""

    data = call.data
    unexpected = set(data) - _ALLOWED_MOW_AREAS_KEYS
    if unexpected:
        raise HomeAssistantError(
            "mow_areas accepts only a device target and area_ids"
        )
    device_id = _single_device_target(data)
    area_ids = _validate_area_ids(data.get(ATTR_AREA_IDS))
    coordinator, mower_id = _resolve_mower_target(hass, device_id)

    if not coordinator.write_controls_enabled_for(mower_id):
        raise HomeAssistantError("Roborock mower controls are not enabled for this model")
    api = coordinator.mower_api_for(mower_id)
    if api is None:
        raise HomeAssistantError("Roborock mower controls are not available")

    try:
        saved_areas = await api.get_areas()
    except RoborockException as err:
        raise HomeAssistantError(f"Roborock mower saved-area read failed: {err}") from err
    canonical_areas = _canonical_areas_for_ids(area_ids, saved_areas)
    try:
        await api.start_area_mow(canonical_areas)
    except RoborockException as err:
        raise HomeAssistantError(f"Roborock mower area mow failed: {err}") from err


def _register_services(hass: HomeAssistant) -> None:
    """Register the multi-area service once for all loaded entries."""

    if hass.services.has_service(DOMAIN, SERVICE_MOW_AREAS):
        return
    async def _handle_mow_areas(call: ServiceCall) -> None:
        await _async_mow_areas(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_MOW_AREAS,
        _handle_mow_areas,
        schema=_MOW_AREAS_SCHEMA,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Roborock Mower from a config entry."""

    coordinator = RoborockMowerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_mqtt()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_update_listener))
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entities when the explicit controls option changes."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and remove services only after the last entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    coordinator: RoborockMowerCoordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id)
    await coordinator.async_stop_mqtt()
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data:
        hass.data.pop(DOMAIN, None)
        if hass.services.has_service(DOMAIN, SERVICE_MOW_AREAS):
            hass.services.async_remove(DOMAIN, SERVICE_MOW_AREAS)
    return True
