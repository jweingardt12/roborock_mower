"""Mock-only tests for the gated saved-area select platform."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from typing import Generic, TypeVar


_ROOT = Path(__file__).resolve().parent.parent


def _install_roborock_stub_if_needed() -> None:
    """Provide the exception import when tests run without python-roborock."""

    try:
        import roborock.exceptions  # noqa: F401
    except ModuleNotFoundError:
        roborock = types.ModuleType("roborock")
        exceptions = types.ModuleType("roborock.exceptions")

        class RoborockException(Exception):
            """Test stand-in for the library exception."""

        exceptions.RoborockException = RoborockException
        roborock.exceptions = exceptions
        sys.modules.update(
            {
                "roborock": roborock,
                "roborock.exceptions": exceptions,
            }
        )


def _install_home_assistant_stubs() -> None:
    """Provide the small Home Assistant surface used by select.py."""

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    select_component = types.ModuleType("homeassistant.components.select")
    config_entries = types.ModuleType("homeassistant.config_entries")
    core = types.ModuleType("homeassistant.core")
    exceptions = types.ModuleType("homeassistant.exceptions")
    helpers = types.ModuleType("homeassistant.helpers")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class SelectEntity:
        """Minimal select entity base."""

        @property
        def options(self) -> list[str]:
            return getattr(self, "_attr_options", [])

    CoordinatorType = TypeVar("CoordinatorType")

    class CoordinatorEntity(Generic[CoordinatorType]):
        """Minimal coordinator entity base."""

        def __init__(self, coordinator: CoordinatorType) -> None:
            self.coordinator = coordinator

        @property
        def available(self) -> bool:
            return True

    class DeviceInfo(dict[str, object]):
        """Minimal device-info stand-in."""

        def __init__(self, **kwargs: object) -> None:
            super().__init__(kwargs)

    class HomeAssistant:
        """Type-only Home Assistant stand-in."""

    class ConfigEntry:
        """Type-only config-entry stand-in."""

    class HomeAssistantError(Exception):
        """Test stand-in for a Home Assistant action error."""

    select_component.SelectEntity = SelectEntity
    config_entries.ConfigEntry = ConfigEntry
    core.HomeAssistant = HomeAssistant
    exceptions.HomeAssistantError = HomeAssistantError
    device_registry.DeviceInfo = DeviceInfo
    entity_platform.AddEntitiesCallback = Callable
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    components.select = select_component
    helpers.device_registry = device_registry
    helpers.entity_platform = entity_platform
    helpers.update_coordinator = update_coordinator
    homeassistant.components = components
    homeassistant.config_entries = config_entries
    homeassistant.core = core
    homeassistant.exceptions = exceptions
    homeassistant.helpers = helpers
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.select": select_component,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.exceptions": exceptions,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.entity_platform": entity_platform,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )


_install_roborock_stub_if_needed()
_install_home_assistant_stubs()


_package_name = "roborock_mower_test_select_package"
_package = types.ModuleType(_package_name)
_package.__path__ = []
_const = types.ModuleType(f"{_package_name}.const")
_const.DOMAIN = "roborock_mower"
_coordinator = types.ModuleType(f"{_package_name}.coordinator")
_coordinator.RoborockMowerCoordinator = object
_coordinator.RoborockMowerDevice = object
_coordinator.mower_device_id = lambda device: device.duid
sys.modules.update(
    {
        _package_name: _package,
        f"{_package_name}.const": _const,
        f"{_package_name}.coordinator": _coordinator,
    }
)

_spec = importlib.util.spec_from_file_location(
    f"{_package_name}.select",
    _ROOT / "custom_components/roborock_mower/select.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Could not load select.py")
select = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = select
_spec.loader.exec_module(select)


class FakeApi:
    """Mock mower API that records discovery and write calls."""

    def __init__(self, areas: list[dict[str, object]]) -> None:
        self.areas = areas
        self.discovery_calls = 0
        self.start_area_mow_calls: list[list[dict[str, object]]] = []

    async def get_areas(self) -> list[dict[str, object]]:
        self.discovery_calls += 1
        return self.areas

    async def get_saved_areas(self) -> list[dict[str, object]]:
        return await self.get_areas()

    async def start_area_mow(self, areas: list[dict[str, object]]) -> None:
        self.start_area_mow_calls.append(areas)


class FakeCoordinator:
    """Mock coordinator exposing the production gating boundary."""

    def __init__(
        self, enabled: dict[str, bool], apis: dict[str, FakeApi | None]
    ) -> None:
        self.data = {mower_id: object() for mower_id in enabled}
        self._enabled = enabled
        self._apis = apis

    def write_controls_enabled_for(self, mower_id: str) -> bool:
        return self._enabled[mower_id]

    def mower_api_for(self, mower_id: str) -> FakeApi | None:
        return self._apis[mower_id]


class SavedAreaSelectTests(unittest.IsolatedAsyncioTestCase):
    """Verify discovery and selection are separate, gated operations."""

    async def test_setup_discovers_only_enabled_mowers_and_does_not_start_mowing(
        self,
    ) -> None:
        enabled = {"a235": True, "other-model": False, "no-api": True}
        good_api = FakeApi([{"id": 2, "name": "Front"}])
        other_api = FakeApi([{"id": 3, "name": "Back"}])
        no_api = None
        coordinator = FakeCoordinator(
            enabled,
            {"a235": good_api, "other-model": other_api, "no-api": no_api},
        )
        entry = types.SimpleNamespace(entry_id="entry")
        hass = types.SimpleNamespace(data={"roborock_mower": {"entry": coordinator}})
        entities: list[object] = []

        await select.async_setup_entry(hass, entry, entities.extend)

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.options, ["Front"])
        self.assertIsNone(entity.current_option)
        self.assertEqual(good_api.discovery_calls, 1)
        self.assertEqual(other_api.discovery_calls, 0)
        self.assertEqual(good_api.start_area_mow_calls, [])

        await entity.async_select_option("Front")

        self.assertEqual(
            good_api.start_area_mow_calls,
            [[{"id": 2, "name": "Front"}]],
        )

    async def test_selection_fails_closed_if_gate_is_disabled_after_setup(self) -> None:
        api = FakeApi([{"id": 2, "name": "Front"}])
        coordinator = FakeCoordinator({"a235": True}, {"a235": api})
        entry = types.SimpleNamespace(entry_id="entry")
        hass = types.SimpleNamespace(data={"roborock_mower": {"entry": coordinator}})
        entities: list[object] = []
        await select.async_setup_entry(hass, entry, entities.extend)

        coordinator._enabled["a235"] = False
        await entities[0].async_select_option("Front")

        self.assertEqual(api.start_area_mow_calls, [])

    def test_duplicate_names_and_missing_ids_are_not_selectable(self) -> None:
        self.assertEqual(
            select.selectable_area_map(
                [
                    {"id": 1, "name": "Front"},
                    {"id": 2, "name": "front"},
                    {"name": "Missing ID"},
                    {"id": None, "name": "Missing ID"},
                    {"id": 3, "name": "Back"},
                ]
            ),
            {"Back": {"id": 3, "name": "Back"}},
        )


if __name__ == "__main__":
    unittest.main()
