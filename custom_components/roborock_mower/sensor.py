"""Read-only DPS sensors for the Roborock Mower integration."""

from __future__ import annotations

import json
from dataclasses import dataclass
import logging
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CHARGE_STATE_MAP,
    DOMAIN,
    MOW_STATE_MAP,
    STATUS_AFS_STATUS,
    STATUS_BLADE_LIFESPAN,
    STATUS_BATTERY,
    STATUS_CHARGE_STATE,
    STATUS_CHARGE_TYPE,
    STATUS_DOCK_STATE,
    STATUS_ERROR_CODE,
    STATUS_GPS_COORDINATE,
    STATUS_MAPPING_STATE,
    STATUS_MAPPING_TYPE,
    STATUS_MOW_DIRECTION_ANGLE,
    STATUS_MOW_EFF_MODE,
    STATUS_MOW_HEIGHT,
    STATUS_MOW_PATTERN,
    STATUS_MOW_PROGRESS,
    STATUS_MOW_START_TYPE,
    STATUS_MOW_STATE,
    STATUS_MOW_TYPE,
    STATUS_NETWORK_CHANNEL,
    STATUS_OFF_DOCK_NO_TASK_STATUS,
    STATUS_OFFLINE_STATUS,
    STATUS_OTA_STATE,
    STATUS_PEND_TYPE,
    STATUS_REMOTE_STATE,
)
from .coordinator import (
    RoborockMowerCoordinator,
    RoborockMowerDevice,
    mower_device_id,
    redact_gps_data,
    status_value,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN_CODES_LOGGED: set[tuple[str, int]] = set()


def decode_gps_coordinate(_value: Any) -> dict[str, Any]:
    """Return only a redaction marker; GPS bytes are never decoded or exposed."""

    return {"gps_redacted": True}


def mapped_state(value: Any, mapping: dict[int, str], state_name: str) -> str | None:
    """Return a readable state while preserving unknown raw codes."""

    if value is None:
        return None
    try:
        code = int(value)
    except (TypeError, ValueError):
        return str(value)
    if code in mapping:
        return mapping[code]

    log_key = (state_name, code)
    if log_key not in _UNKNOWN_CODES_LOGGED:
        _UNKNOWN_CODES_LOGGED.add(log_key)
        _LOGGER.warning(
            "Roborock mower reported unknown %s code %s; exposing state as unknown_%s",
            state_name,
            code,
            code,
        )
    return f"unknown_{code}"


def _safe_sensor_value(value: Any) -> Any:
    """Keep scalar DPS values intact and serialize unexpected values safely."""

    value = redact_gps_data(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True, kw_only=True)
class RoborockMowerSensorEntityDescription(SensorEntityDescription):
    """Describes a raw or conservatively mapped Roborock mower DPS sensor."""

    status_id: str
    value_fn: Callable[[Any], Any] = lambda value: value


_DIAGNOSTIC = {
    "entity_category": EntityCategory.DIAGNOSTIC,
    "entity_registry_enabled_default": False,
}


SENSORS: tuple[RoborockMowerSensorEntityDescription, ...] = (
    RoborockMowerSensorEntityDescription(
        key="battery",
        name="Battery",
        status_id=STATUS_BATTERY,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_state",
        name="Mow state",
        status_id=STATUS_MOW_STATE,
        value_fn=lambda value: mapped_state(value, MOW_STATE_MAP, "mow_state"),
    ),
    RoborockMowerSensorEntityDescription(
        key="charge_state",
        name="Charge state",
        status_id=STATUS_CHARGE_STATE,
        value_fn=lambda value: mapped_state(value, CHARGE_STATE_MAP, "charge_state"),
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_progress",
        name="Mow progress",
        status_id=STATUS_MOW_PROGRESS,
        native_unit_of_measurement=PERCENTAGE,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_height",
        name="Mow height",
        status_id=STATUS_MOW_HEIGHT,
    ),
    RoborockMowerSensorEntityDescription(
        key="gps_raw",
        name="GPS raw",
        status_id=STATUS_GPS_COORDINATE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="error_code",
        name="Error code",
        status_id=STATUS_ERROR_CODE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_type",
        name="Mow type",
        status_id=STATUS_MOW_TYPE,
    ),
    RoborockMowerSensorEntityDescription(
        key="mapping_type",
        name="Mapping type",
        status_id=STATUS_MAPPING_TYPE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="mapping_state",
        name="Mapping state",
        status_id=STATUS_MAPPING_STATE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="ota_state",
        name="OTA state",
        status_id=STATUS_OTA_STATE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="dock_state",
        name="Dock state",
        status_id=STATUS_DOCK_STATE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="charge_type",
        name="Charge type",
        status_id=STATUS_CHARGE_TYPE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="pend_type",
        name="Pause type",
        status_id=STATUS_PEND_TYPE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="remote_state",
        name="Remote state",
        status_id=STATUS_REMOTE_STATE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_start_type",
        name="Mow start type",
        status_id=STATUS_MOW_START_TYPE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_eff_mode",
        name="Mow efficiency mode",
        status_id=STATUS_MOW_EFF_MODE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_direction_angle",
        name="Mow direction angle",
        status_id=STATUS_MOW_DIRECTION_ANGLE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="mow_pattern",
        name="Mow pattern",
        status_id=STATUS_MOW_PATTERN,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="offline_status",
        name="Offline status",
        status_id=STATUS_OFFLINE_STATUS,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="blade_lifespan",
        name="Blade lifespan",
        status_id=STATUS_BLADE_LIFESPAN,
        native_unit_of_measurement=PERCENTAGE,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="off_dock_no_task_status",
        name="Off-dock status",
        status_id=STATUS_OFF_DOCK_NO_TASK_STATUS,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="afs_status",
        name="AFS status",
        status_id=STATUS_AFS_STATUS,
        **_DIAGNOSTIC,
    ),
    RoborockMowerSensorEntityDescription(
        key="network_channel",
        name="Network channel",
        status_id=STATUS_NETWORK_CHANNEL,
        **_DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roborock mower sensors."""

    coordinator: RoborockMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoborockMowerSensor(coordinator, mower_id, description)
        for mower_id in coordinator.data
        for description in SENSORS
    )


class RoborockMowerSensor(CoordinatorEntity[RoborockMowerCoordinator], SensorEntity):
    """A read-only Roborock mower sensor."""

    entity_description: RoborockMowerSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RoborockMowerCoordinator,
        mower_id: str,
        description: RoborockMowerSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(coordinator)
        self._mower_id = mower_id
        self.entity_description = description
        self._attr_unique_id = f"{mower_id}_{description.key}"

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
    def native_value(self) -> Any:
        """Return the current sensor value without exposing GPS bytes."""

        if self._mower is None:
            return None
        value = status_value(self._mower.device, self.entity_description.status_id)
        if self.entity_description.status_id == STATUS_GPS_COORDINATE:
            return None
        return _safe_sensor_value(self.entity_description.value_fn(value))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe diagnostic attributes for this sensor."""

        if self._mower is None:
            return {}
        raw_value = status_value(self._mower.device, self.entity_description.status_id)
        safe_raw_value = (
            {"gps_redacted": True}
            if self.entity_description.status_id == STATUS_GPS_COORDINATE
            else redact_gps_data(raw_value)
        )
        return {
            "status_id": self.entity_description.status_id,
            "raw_value": safe_raw_value,
            "last_cloud_update": self.coordinator.last_cloud_update,
            "last_mqtt_update": self.coordinator.last_mqtt_update,
            "last_mqtt_protocol": self.coordinator.last_mqtt_protocol,
            "last_mqtt_seen": self.coordinator.last_mqtt_seen.get(self._mower_id),
            "last_mqtt_online_hint": self.coordinator.last_mqtt_online_hint.get(self._mower_id),
            "last_mqtt_payload": redact_gps_data(
                self.coordinator.last_mqtt_payload.get(self._mower_id)
            ),
            "mqtt_connected": self.coordinator.mqtt_connected,
            "mqtt_subscribed": self.coordinator.mqtt_subscribed.get(self._mower_id),
            "last_mqtt_error": self.coordinator.last_mqtt_error,
            "last_update_attempt": self.coordinator.last_update_attempt,
            "last_rate_limit": self.coordinator.last_rate_limit,
            "last_status_change": self.coordinator.last_status_change.get(self._mower_id),
            "last_static_status_update": self.coordinator.last_static_status_update.get(self._mower_id),
            "product_name": self._mower.product.name,
            "product_model": self._mower.product.model,
        }
