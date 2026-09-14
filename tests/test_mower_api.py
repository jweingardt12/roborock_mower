"""Unit tests for RockMow remote_pb payloads and write gating.

The production command API accepts a V1 RPC channel, so these tests inject an
AsyncMock channel and never connect to Home Assistant or a mower.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


_ROOT = Path(__file__).resolve().parent.parent


def _install_roborock_test_stubs() -> None:
    """Provide only the production imports needed by the command module."""

    roborock = types.ModuleType("roborock")
    exceptions = types.ModuleType("roborock.exceptions")

    class RoborockException(Exception):
        """Test stand-in for the library's RPC exception."""

    exceptions.RoborockException = RoborockException
    protocols = types.ModuleType("roborock.protocols")
    v1_protocol = types.ModuleType("roborock.protocols.v1_protocol")
    v1_protocol.V1RpcChannel = object
    protocols.v1_protocol = v1_protocol
    roborock.exceptions = exceptions
    roborock.protocols = protocols
    sys.modules.update(
        {
            "roborock": roborock,
            "roborock.exceptions": exceptions,
            "roborock.protocols": protocols,
            "roborock.protocols.v1_protocol": v1_protocol,
        }
    )


_install_roborock_test_stubs()


# The production control module imports constants from its package. Provide a
# tiny package/const stub so this test remains independent of Home Assistant.
_test_package = types.ModuleType("roborock_mower_test_package")
_test_package.__path__ = []
_test_const = types.ModuleType("roborock_mower_test_package.const")
setattr(_test_const, "CONF_ENABLE_CONTROLS", "enable_controls")
setattr(_test_const, "ROCKMOW_Z1_MODEL", "roborock.mower.a235")
sys.modules.update(
    {
        "roborock_mower_test_package": _test_package,
        "roborock_mower_test_package.const": _test_const,
    }
)


def _load_module(name: str, relative_path: str) -> types.ModuleType:
    """Load a component module without executing the Home Assistant package."""

    path = _ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mower_api = _load_module(
    "roborock_mower_test_mower_api",
    "custom_components/roborock_mower/mower_api.py",
)
control = _load_module(
    "roborock_mower_test_package.control",
    "custom_components/roborock_mower/control.py",
)
RoborockException = sys.modules["roborock.exceptions"].RoborockException


class RemotePbPayloadTests(unittest.IsolatedAsyncioTestCase):
    """Verify exact remote_pb JSON objects passed to the RPC channel."""

    def setUp(self) -> None:
        self.rpc_channel = AsyncMock()
        self.api = mower_api.MowerApi(self.rpc_channel, "duid-a235")

    async def test_build_remote_message_uses_string_id(self) -> None:
        with patch.object(mower_api.time, "time", return_value=1700000000.123):
            result = mower_api.build_remote_message(
                {"type": mower_api.TYPE_APP_BUTTON, "app_button": mower_api.BUTTON_MOW_GLOBAL}
            )
        self.assertEqual(
            result,
            {"id": "1700000000123", "type": "APP_BUTTON", "app_button": "MOW_GLOBAL"},
        )

    async def test_mower_actions_send_exact_app_button_payloads(self) -> None:
        actions = (
            ("start", mower_api.BUTTON_MOW_GLOBAL),
            ("resume", mower_api.BUTTON_MOW_RESUME),
            ("pause", mower_api.BUTTON_MOW_PAUSE),
            ("dock", mower_api.BUTTON_CHARGE),
            ("edge_cut", mower_api.BUTTON_MOW_EDGE),
            ("stop", mower_api.BUTTON_MOW_END),
        )
        for method_name, button in actions:
            with self.subTest(method_name=method_name):
                self.rpc_channel.reset_mock()
                with patch.object(mower_api.time, "time", return_value=1700000000.123):
                    await getattr(self.api, method_name)()
                self.rpc_channel.send_command.assert_awaited_once_with(
                    "remote_pb",
                    params={
                        "id": "1700000000123",
                        "type": "APP_BUTTON",
                        "app_button": button,
                    },
                )

    async def test_start_or_resume_maps_paused_and_active_states(self) -> None:
        for mow_state, button in ((58, "MOW_RESUME"), ("2", "MOW_RESUME"), (55, "MOW_GLOBAL"), (None, "MOW_GLOBAL")):
            with self.subTest(mow_state=mow_state):
                self.rpc_channel.reset_mock()
                with patch.object(mower_api.time, "time", return_value=1700000000.123):
                    await self.api.start_or_resume(mow_state)
                self.rpc_channel.send_command.assert_awaited_once_with(
                    "remote_pb",
                    params={
                        "id": "1700000000123",
                        "type": "APP_BUTTON",
                        "app_button": button,
                    },
                )

    async def test_rpc_error_is_propagated_without_retry(self) -> None:
        self.rpc_channel.send_command.side_effect = RoborockException("mower unavailable")
        with patch.object(mower_api.time, "time", return_value=1700000000.123):
            with self.assertRaisesRegex(RoborockException, "mower unavailable"):
                await self.api.stop()
        self.rpc_channel.send_command.assert_awaited_once_with(
            "remote_pb",
            params={
                "id": "1700000000123",
                "type": "APP_BUTTON",
                "app_button": "MOW_END",
            },
        )

    async def test_start_area_mow_sends_exact_select_payload(self) -> None:
        with patch.object(mower_api.time, "time", return_value=1700000000.123):
            await self.api.start_area_mow(
                [{"id": 2, "name": "Front"}, {"id": 3, "name": "Back"}]
            )
        self.rpc_channel.send_command.assert_awaited_once_with(
            "remote_pb",
            params={
                "id": "1700000000123",
                "type": "APP_BUTTON",
                "app_button": "MOW_SELECT",
                "modify_map": {
                    "boundaries": [
                        {"id": 2, "name": "Front"},
                        {"id": 3, "name": "Back"},
                    ]
                },
            },
        )

    async def test_start_area_mow_rejects_empty_input_before_rpc(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one area"):
            await self.api.start_area_mow([])
        self.rpc_channel.send_command.assert_not_awaited()

    async def test_get_saved_areas_uses_read_only_preference_query(self) -> None:
        response = {
            "type": "MOW_PREFERENCE_CONFIG",
            "preference_config": {
                "global": {"mode": "GLOBAL"},
                "custom": [
                    {"area_id": 2, "area_name": "Front"},
                    {"area_id": 3, "area_name": "Back"},
                ],
            },
        }
        self.rpc_channel.send_command.side_effect = RoborockException(
            f"Unexpected API Result: {json.dumps(response)}"
        )
        with patch.object(mower_api.time, "time", return_value=1700000000.123):
            areas = await self.api.get_saved_areas()
        self.assertEqual(
            areas,
            [{"id": 2, "name": "Front"}, {"id": 3, "name": "Back"}],
        )
        self.rpc_channel.send_command.assert_awaited_once_with(
            "remote_pb",
            params={
                "id": "1700000000123",
                "type": "GET_MOW_PREFERENCE_CONFIG",
            },
        )

    async def test_get_saved_areas_ignores_malformed_and_ambiguous_entries(self) -> None:
        self.rpc_channel.send_command.return_value = {
            "preference_config": {
                "custom": [
                    {"area_id": 1, "area_name": "Duplicate"},
                    {"area_id": 2, "area_name": "duplicate"},
                    {"area_id": 3, "area_name": "Unique"},
                    {"area_name": "Missing ID"},
                    {"area_id": None, "area_name": "Missing ID"},
                    {"area_id": 4, "area_name": ""},
                    "not-an-area",
                ]
            }
        }
        areas = await self.api.get_saved_areas()
        self.assertEqual(areas, [{"id": 3, "name": "Unique"}])

    async def test_get_saved_areas_returns_empty_for_missing_or_wrong_shape(self) -> None:
        for response in (
            None,
            [],
            {"preference_config": None},
            {"preference_config": {"custom": {"area_id": 1}}},
        ):
            with self.subTest(response=response):
                self.rpc_channel.reset_mock()
                self.rpc_channel.send_command.return_value = response
                self.assertEqual(await self.api.get_saved_areas(), [])
                self.rpc_channel.send_command.assert_awaited_once()


class WriteControlGatingTests(unittest.TestCase):
    """Verify controls require both the explicit option and exact model."""

    def test_controls_are_disabled_by_default(self) -> None:
        self.assertFalse(control.write_controls_enabled(None, "roborock.mower.a235"))
        self.assertFalse(control.write_controls_enabled({}, "roborock.mower.a235"))
        self.assertFalse(
            control.write_controls_enabled(
                {control.CONF_ENABLE_CONTROLS: False}, "roborock.mower.a235"
            )
        )

    def test_controls_require_exact_a235_model(self) -> None:
        self.assertTrue(
            control.write_controls_enabled(
                {control.CONF_ENABLE_CONTROLS: True}, "roborock.mower.a235"
            )
        )
        self.assertFalse(
            control.write_controls_enabled(
                {control.CONF_ENABLE_CONTROLS: True}, "roborock.mower.a222"
            )
        )
        self.assertFalse(
            control.write_controls_enabled(
                {control.CONF_ENABLE_CONTROLS: True}, "robot.vacuum.a104"
            )
        )


if __name__ == "__main__":
    unittest.main()
