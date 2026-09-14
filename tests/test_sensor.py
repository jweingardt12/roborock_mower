"""Mock-only tests for sensor conversion and GPS redaction."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from typing import Any, Generic, TypeVar


_ROOT = Path(__file__).resolve().parent.parent


def _install_stubs() -> None:
    """Provide only the Home Assistant surface needed by sensor.py."""

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor_component = types.ModuleType("homeassistant.components.sensor")
    config_entries = types.ModuleType("homeassistant.config_entries")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class SensorDeviceClass:
        BATTERY = "battery"

    class SensorEntity:
        """Minimal sensor entity base."""

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        """Minimal dataclass-compatible sensor description."""

        key: str
        name: str | None = None
        device_class: str | None = None
        native_unit_of_measurement: str | None = None
        entity_category: object | None = None
        entity_registry_enabled_default: bool = True

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"

    CoordinatorType = TypeVar("CoordinatorType")

    class CoordinatorEntity(Generic[CoordinatorType]):
        """Minimal coordinator entity base."""

        def __init__(self, coordinator: CoordinatorType) -> None:
            self.coordinator = coordinator

    class DeviceInfo(dict[str, object]):
        """Minimal device-info stand-in."""

        def __init__(self, **kwargs: object) -> None:
            super().__init__(kwargs)

    sensor_component.SensorDeviceClass = SensorDeviceClass
    sensor_component.SensorEntity = SensorEntity
    sensor_component.SensorEntityDescription = SensorEntityDescription
    config_entries.ConfigEntry = object
    const.PERCENTAGE = "%"
    const.EntityCategory = EntityCategory
    core.HomeAssistant = object
    entity_platform.AddEntitiesCallback = object
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    device_registry.DeviceInfo = DeviceInfo
    components.sensor = sensor_component
    helpers.device_registry = device_registry
    helpers.entity_platform = entity_platform
    helpers.update_coordinator = update_coordinator
    homeassistant.components = components
    homeassistant.config_entries = config_entries
    homeassistant.const = const
    homeassistant.core = core
    homeassistant.helpers = helpers
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor_component,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.entity_platform": entity_platform,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )


_install_stubs()

_package_name = "roborock_mower_test_sensor_package"
_package = types.ModuleType(_package_name)
_package.__path__ = []
_const = types.ModuleType(f"{_package_name}.const")
for _name, _value in {
    "CHARGE_STATE_MAP": {0: "not_charging"},
    "DOMAIN": "roborock_mower",
    "MOW_STATE_MAP": {0: "idle"},
    "STATUS_AFS_STATUS": "144",
    "STATUS_BLADE_LIFESPAN": "140",
    "STATUS_BATTERY": "121",
    "STATUS_CHARGE_STATE": "127",
    "STATUS_CHARGE_TYPE": "129",
    "STATUS_DOCK_STATE": "128",
    "STATUS_ERROR_CODE": "120",
    "STATUS_GPS_COORDINATE": "142",
    "STATUS_MAPPING_STATE": "125",
    "STATUS_MAPPING_TYPE": "124",
    "STATUS_MOW_DIRECTION_ANGLE": "135",
    "STATUS_MOW_EFF_MODE": "133",
    "STATUS_MOW_HEIGHT": "134",
    "STATUS_MOW_PATTERN": "136",
    "STATUS_MOW_PROGRESS": "139",
    "STATUS_MOW_START_TYPE": "132",
    "STATUS_MOW_STATE": "123",
    "STATUS_MOW_TYPE": "122",
    "STATUS_NETWORK_CHANNEL": "145",
    "STATUS_OFF_DOCK_NO_TASK_STATUS": "143",
    "STATUS_OFFLINE_STATUS": "138",
    "STATUS_OTA_STATE": "126",
    "STATUS_PEND_TYPE": "130",
    "STATUS_REMOTE_STATE": "131",
}.items():
    setattr(_const, _name, _value)
_coordinator = types.ModuleType(f"{_package_name}.coordinator")


class Coordinator:
    """Type-only coordinator stand-in."""


class Mower:
    """Type-only mower stand-in."""


def _status_value(device: Any, status_id: str) -> Any:
    return device.device_status.get(status_id)


def _redact_gps_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<gps redacted>" if str(key) == "142" else _redact_gps_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_gps_data(item) for item in value]
    return value


_coordinator.RoborockMowerCoordinator = Coordinator
_coordinator.RoborockMowerDevice = Mower
_coordinator.mower_device_id = lambda device: device.duid
_coordinator.redact_gps_data = _redact_gps_data
_coordinator.status_value = _status_value
sys.modules.update(
    {
        _package_name: _package,
        f"{_package_name}.const": _const,
        f"{_package_name}.coordinator": _coordinator,
    }
)

_spec = importlib.util.spec_from_file_location(
    f"{_package_name}.sensor",
    _ROOT / "custom_components/roborock_mower/sensor.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Could not load sensor.py")
sensor = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sensor
_spec.loader.exec_module(sensor)


class SensorHelperTests(unittest.TestCase):
    """Verify conservative sensor conversion and GPS handling."""

    def test_unknown_state_is_preserved(self) -> None:
        self.assertEqual(sensor.mapped_state("999", {0: "idle"}, "test"), "unknown_999")
        self.assertEqual(sensor.mapped_state("not-an-int", {0: "idle"}, "test"), "not-an-int")

    def test_gps_decoder_returns_redaction_marker(self) -> None:
        self.assertEqual(sensor.decode_gps_coordinate("secret-gps"), {"gps_redacted": True})

    def test_gps_sensor_state_and_attribute_do_not_expose_raw_value(self) -> None:
        device = types.SimpleNamespace(
            duid="duid",
            sn="sn",
            name="Mower",
            fv="1",
            device_status={"142": "secret-gps", "121": 80},
        )
        product = types.SimpleNamespace(name="RockMow", model="roborock.mower.a235")
        mower = types.SimpleNamespace(device=device, product=product)
        coordinator = types.SimpleNamespace(
            data={"duid": mower},
            last_cloud_update=None,
            last_mqtt_update=None,
            last_mqtt_protocol=None,
            last_mqtt_seen={},
            last_mqtt_online_hint={},
            last_mqtt_payload={"duid": {"dps": {"142": "secret-gps"}}},
            mqtt_connected=True,
            mqtt_subscribed={"duid": True},
            last_mqtt_error=None,
            last_update_attempt=None,
            last_rate_limit=None,
            last_status_change={},
            last_static_status_update={},
        )
        description = sensor.RoborockMowerSensorEntityDescription(
            key="gps_raw", name="GPS raw", status_id="142"
        )
        entity = sensor.RoborockMowerSensor(coordinator, "duid", description)

        self.assertIsNone(entity.native_value)
        attrs = entity.extra_state_attributes
        self.assertEqual(attrs["raw_value"], {"gps_redacted": True})
        self.assertNotIn("secret-gps", str(attrs))


if __name__ == "__main__":
    unittest.main()
