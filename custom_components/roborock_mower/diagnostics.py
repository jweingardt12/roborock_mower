"""Diagnostics for the Roborock Mower integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SENSITIVE_DIAGNOSTIC_KEYS
from .coordinator import redact_gps_data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    mowers: list[dict[str, Any]] = []

    if coordinator and coordinator.data:
        for mower in coordinator.data.values():
            device = redact_gps_data(mower.device.as_dict())
            product = mower.product.as_dict()
            mowers.append(
                {
                    "device": {
                        "name": device.get("name"),
                        "duid": device.get("duid"),
                        "sn": device.get("sn"),
                        "localKey": device.get("localKey"),
                        "online": device.get("online"),
                        "pv": device.get("pv"),
                        "fv": device.get("fv"),
                        "deviceStatus": device.get("deviceStatus"),
                    },
                    "product": {
                        "name": product.get("name"),
                        "model": product.get("model"),
                        "category": product.get("category"),
                    },
                }
            )

    return async_redact_data(
        {
            "entry": redact_gps_data(entry.as_dict()),
            "mqtt": {
                "last_mqtt_update": getattr(coordinator, "last_mqtt_update", None),
                "last_mqtt_protocol": getattr(coordinator, "last_mqtt_protocol", None),
                "last_mqtt_seen": getattr(coordinator, "last_mqtt_seen", {}),
                "last_mqtt_online_hint": getattr(coordinator, "last_mqtt_online_hint", {}),
                "last_mqtt_payload": redact_gps_data(
                    getattr(coordinator, "last_mqtt_payload", {})
                ),
                "mqtt_connected": getattr(coordinator, "mqtt_connected", None),
                "mqtt_subscribed": getattr(coordinator, "mqtt_subscribed", {}),
                "last_mqtt_error": getattr(coordinator, "last_mqtt_error", None),
            },
            "mowers": mowers,
        },
        SENSITIVE_DIAGNOSTIC_KEYS,
    )
