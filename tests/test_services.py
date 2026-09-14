"""Mock-only tests for the multi-zone mower service validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock


_ROOT = Path(__file__).resolve().parent.parent


# Keep the service test independent of Home Assistant and python-roborock.
roborock = types.ModuleType("roborock")
roborock_exceptions = types.ModuleType("roborock.exceptions")


class RoborockException(Exception):
    """Test stand-in for the library exception."""


roborock_exceptions.RoborockException = RoborockException
roborock.exceptions = roborock_exceptions
sys.modules.update(
    {"roborock": roborock, "roborock.exceptions": roborock_exceptions}
)

homeassistant = types.ModuleType("homeassistant")
config_entries = types.ModuleType("homeassistant.config_entries")
core = types.ModuleType("homeassistant.core")
exceptions = types.ModuleType("homeassistant.exceptions")
helpers = types.ModuleType("homeassistant.helpers")
device_registry = types.ModuleType("homeassistant.helpers.device_registry")


class HomeAssistant:
    """Type-only Home Assistant stand-in."""


class ServiceCall:
    """Type-only service-call stand-in."""


class ConfigEntry:
    """Type-only config-entry stand-in."""


class HomeAssistantError(Exception):
    """Test stand-in for Home Assistant action errors."""


def async_get(_hass: object) -> object:
    raise AssertionError("test registry was not installed")


config_entries.ConfigEntry = ConfigEntry
core.HomeAssistant = HomeAssistant
core.ServiceCall = ServiceCall
exceptions.HomeAssistantError = HomeAssistantError
device_registry.async_get = async_get
helpers.device_registry = device_registry
homeassistant.config_entries = config_entries
homeassistant.core = core
homeassistant.exceptions = exceptions
homeassistant.helpers = helpers
sys.modules.update(
    {
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": device_registry,
    }
)

_package_name = "roborock_mower_test_services_package"
_package = types.ModuleType(_package_name)
_package.__path__ = []
_const = types.ModuleType(f"{_package_name}.const")
_const.DOMAIN = "roborock_mower"
_const.PLATFORMS = []
_coordinator = types.ModuleType(f"{_package_name}.coordinator")


class RoborockMowerCoordinator:
    """Type-only coordinator stand-in."""


_coordinator.RoborockMowerCoordinator = RoborockMowerCoordinator
sys.modules.update(
    {
        _package_name: _package,
        f"{_package_name}.const": _const,
        f"{_package_name}.coordinator": _coordinator,
    }
)

_spec = importlib.util.spec_from_file_location(
    f"{_package_name}.__init__",
    _ROOT / "custom_components/roborock_mower/__init__.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Could not load integration __init__.py")
services = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = services
_spec.loader.exec_module(services)


class FakeDevice:
    """Device-registry object with one mower identifier."""

    identifiers = {("roborock_mower", "mower-id")}


class FakeRegistry:
    """Minimal device registry."""

    def __init__(self, device: FakeDevice | None) -> None:
        self.device = device

    def async_get(self, _device_id: str) -> FakeDevice | None:
        return self.device


class FakeApi:
    """Mock API that records read and write calls."""

    def __init__(self, areas: list[dict[str, object]]) -> None:
        self.areas = areas
        self.get_areas = AsyncMock(return_value=areas)
        self.start_area_mow = AsyncMock()


class FakeCoordinator:
    """Mock coordinator exposing one exact mower and gate."""

    def __init__(self, api: FakeApi | None, enabled: bool = True) -> None:
        self.data = {"mower-id": object()}
        self.api = api
        self.enabled = enabled

    def write_controls_enabled_for(self, _mower_id: str) -> bool:
        return self.enabled

    def mower_api_for(self, _mower_id: str) -> FakeApi | None:
        return self.api


class FakeHass:
    """Minimal Home Assistant data container."""

    def __init__(self, coordinator: FakeCoordinator) -> None:
        self.data = {"roborock_mower": {"entry": coordinator}}


class FakeCall:
    """Service call with validated target and area IDs."""

    def __init__(self, data: dict[str, object]) -> None:
        self.data = data


class ServiceValidationTests(unittest.IsolatedAsyncioTestCase):
    """Verify the service validates all input before one write."""

    def setUp(self) -> None:
        self.api = FakeApi(
            [
                {"id": 2, "name": "Front"},
                {"id": 3, "name": "Back"},
            ]
        )
        self.coordinator = FakeCoordinator(self.api)
        self.hass = FakeHass(self.coordinator)
        device_registry.async_get = lambda _hass: FakeRegistry(FakeDevice())

    def test_schema_accepts_resolved_device_target(self) -> None:
        self.assertEqual(
            services._MOW_AREAS_SCHEMA(
                {"device_id": "device-registry-id", "area_ids": [2]}
            ),
            {"device_id": "device-registry-id", "area_ids": [2]},
        )
        self.assertEqual(
            services._MOW_AREAS_SCHEMA(
                {"device_id": ["device-registry-id"], "area_ids": [2]}
            ),
            {"device_id": ["device-registry-id"], "area_ids": [2]},
        )

    async def test_multiple_ids_send_one_canonical_mow_select(self) -> None:
        await services._async_mow_areas(
            self.hass,
            FakeCall(
                {
                    "device_id": "device-registry-id",
                    "area_ids": [2, 3],
                }
            ),
        )

        self.api.get_areas.assert_awaited_once_with()
        self.api.start_area_mow.assert_awaited_once_with(
            [
                {"id": 2, "name": "Front"},
                {"id": 3, "name": "Back"},
            ]
        )

    async def test_unknown_area_is_rejected_before_write(self) -> None:
        with self.assertRaisesRegex(Exception, "Unknown or stale"):
            await services._async_mow_areas(
                self.hass,
                FakeCall(
                    {
                        "device_id": "device-registry-id",
                        "area_ids": [99],
                    }
                ),
            )
        self.api.start_area_mow.assert_not_awaited()

    async def test_duplicate_area_ids_are_rejected_before_discovery(self) -> None:
        with self.assertRaisesRegex(Exception, "duplicates"):
            await services._async_mow_areas(
                self.hass,
                FakeCall(
                    {
                        "device_id": "device-registry-id",
                        "area_ids": [2, "2"],
                    }
                ),
            )
        self.api.get_areas.assert_not_awaited()
        self.api.start_area_mow.assert_not_awaited()

    async def test_disabled_controls_are_rejected_before_discovery(self) -> None:
        self.coordinator.enabled = False
        with self.assertRaisesRegex(Exception, "not enabled"):
            await services._async_mow_areas(
                self.hass,
                FakeCall(
                    {
                        "device_id": "device-registry-id",
                        "area_ids": [2],
                    }
                ),
            )
        self.api.get_areas.assert_not_awaited()
        self.api.start_area_mow.assert_not_awaited()

    async def test_multiple_device_targets_are_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "exactly one device"):
            await services._async_mow_areas(
                self.hass,
                FakeCall(
                    {
                        "device_id": ["one", "two"],
                        "area_ids": [2],
                    }
                ),
            )
        self.api.get_areas.assert_not_awaited()
        self.api.start_area_mow.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
