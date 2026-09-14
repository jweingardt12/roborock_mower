"""Mock-only tests for the inch-based cutting-height control."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from typing import Generic, TypeVar
from unittest.mock import AsyncMock


_ROOT = Path(__file__).resolve().parent.parent

homeassistant = types.ModuleType("homeassistant")
components = types.ModuleType("homeassistant.components")
number_component = types.ModuleType("homeassistant.components.number")
config_entries = types.ModuleType("homeassistant.config_entries")
const = types.ModuleType("homeassistant.const")
core = types.ModuleType("homeassistant.core")
exceptions = types.ModuleType("homeassistant.exceptions")
helpers = types.ModuleType("homeassistant.helpers")
device_registry = types.ModuleType("homeassistant.helpers.device_registry")
entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")


class NumberEntity:
    """Minimal number entity base."""


class NumberMode:
    SLIDER = "slider"


class EntityCategory:
    CONFIG = "config"


class HomeAssistantError(Exception):
    """Test stand-in for Home Assistant action errors."""


@dataclass(frozen=True)
class DeviceInfo:
    """Minimal device-info stand-in."""


CoordinatorType = TypeVar("CoordinatorType")


class CoordinatorEntity(Generic[CoordinatorType]):
    """Minimal coordinator entity base."""

    def __init__(self, coordinator: object) -> None:
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return True


number_component.NumberEntity = NumberEntity
number_component.NumberMode = NumberMode
config_entries.ConfigEntry = object
const.EntityCategory = EntityCategory
core.HomeAssistant = object
exceptions.HomeAssistantError = HomeAssistantError
device_registry.DeviceInfo = DeviceInfo
entity_platform.AddEntitiesCallback = object
update_coordinator.CoordinatorEntity = CoordinatorEntity
components.number = number_component
helpers.device_registry = device_registry
helpers.entity_platform = entity_platform
helpers.update_coordinator = update_coordinator
homeassistant.components = components
homeassistant.config_entries = config_entries
homeassistant.const = const
homeassistant.core = core
homeassistant.exceptions = exceptions
homeassistant.helpers = helpers
sys.modules.update(
    {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.number": number_component,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_platform": entity_platform,
        "homeassistant.helpers.update_coordinator": update_coordinator,
    }
)

_package_name = "roborock_mower_test_number_package"
_package = types.ModuleType(_package_name)
_package.__path__ = []
_const = types.ModuleType(f"{_package_name}.const")
_const.DOMAIN = "roborock_mower"
_const.STATUS_MOW_HEIGHT = "134"
_coordinator = types.ModuleType(f"{_package_name}.coordinator")
_coordinator.RoborockMowerCoordinator = object
_coordinator.RoborockMowerDevice = object
_coordinator.mower_device_id = lambda device: device.duid
_coordinator.status_value = lambda device, status_id: device.device_status.get(status_id)
_mower_api = types.ModuleType(f"{_package_name}.mower_api")
_mower_api.MOW_HEIGHT_MIN = 20
_mower_api.MOW_HEIGHT_MAX = 70
sys.modules.update(
    {
        _package_name: _package,
        f"{_package_name}.const": _const,
        f"{_package_name}.coordinator": _coordinator,
        f"{_package_name}.mower_api": _mower_api,
    }
)

_spec = importlib.util.spec_from_file_location(
    f"{_package_name}.number",
    _ROOT / "custom_components/roborock_mower/number.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Could not load number.py")
number = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = number
_spec.loader.exec_module(number)


class FakeApi:
    """Mock API for the number entity."""

    def __init__(self) -> None:
        self.set_mow_height = AsyncMock()


class FakeCoordinator:
    """Mock coordinator with controls enabled."""

    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.data = {}

    def write_controls_enabled_for(self, _mower_id: str) -> bool:
        return True

    def mower_api_for(self, _mower_id: str) -> FakeApi:
        return self.api


class CuttingHeightTests(unittest.IsolatedAsyncioTestCase):
    """Verify inch UI values become integer-millimetre commands."""

    def test_entity_uses_inches(self) -> None:
        self.assertEqual(number.RoborockMowHeightNumber._attr_native_min_value, 0.79)
        self.assertEqual(number.RoborockMowHeightNumber._attr_native_max_value, 2.76)
        self.assertEqual(number.RoborockMowHeightNumber._attr_native_step, 0.01)
        self.assertEqual(number.RoborockMowHeightNumber._attr_native_unit_of_measurement, "in")

    def test_conversion_rounds_to_wire_millimetres(self) -> None:
        self.assertEqual(number.inches_to_millimeters(0.79), 20)
        self.assertEqual(number.inches_to_millimeters(1.5), 38)
        self.assertEqual(number.inches_to_millimeters(2.76), 70)
        for value in (0.78, 2.77, float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    number.inches_to_millimeters(value)

    async def test_entity_sends_converted_value(self) -> None:
        api = FakeApi()
        entity = number.RoborockMowHeightNumber(FakeCoordinator(api), "mower")
        await entity.async_set_native_value(1.5)
        api.set_mow_height.assert_awaited_once_with(38)


if __name__ == "__main__":
    unittest.main()
