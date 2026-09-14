"""Read-only saved-area discovery and write commands for RockMow devices.

RockMow mowing actions are sent through the V1 ``remote_pb`` RPC.  This module
keeps the wire payload construction independent of Home Assistant so it can be
unit-tested with a mocked RPC channel.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from typing import Any

from roborock.exceptions import RoborockException
from roborock.protocols.v1_protocol import V1RpcChannel

_LOGGER = logging.getLogger(__name__)

REMOTE_PB_METHOD = "remote_pb"
TYPE_APP_BUTTON = "APP_BUTTON"
TYPE_GET_MOW_PREFERENCE_CONFIG = "GET_MOW_PREFERENCE_CONFIG"
TYPE_REMOTE_CMD = "REMOTE_CMD"
TYPE_SET_MOW_PREFERENCE = "SET_MOW_PREFERENCE"

BUTTON_MOW_GLOBAL = "MOW_GLOBAL"
BUTTON_MOW_EDGE = "MOW_EDGE"
BUTTON_MOW_SELECT = "MOW_SELECT"
BUTTON_MOW_PAUSE = "MOW_PAUSE"
BUTTON_MOW_RESUME = "MOW_RESUME"
BUTTON_MOW_END = "MOW_END"
BUTTON_CHARGE = "CHARGE"

EFF_MODE_LABELS: dict[int, str] = {1: "Daily", 2: "Efficient", 3: "Manicure"}
EFF_MODE_REVERSE: dict[str, int] = {label: code for code, label in EFF_MODE_LABELS.items()}
EFF_MODE_WIRE: dict[int, str] = {1: "DAILY", 2: "EFFICIENT", 3: "MANICURE"}
MOW_HEIGHT_MIN = 20
MOW_HEIGHT_MAX = 70

PAUSED_MOW_STATES = frozenset({2, 58})
_UNEXPECTED_RESULT_PREFIX = "Unexpected API Result: "


def build_remote_message(payload: dict[str, Any], *, request_id: int | str | None = None) -> dict[str, Any]:
    """Build the protobufjs ``RemoteMsg.toJSON`` object sent to ``remote_pb``."""

    if request_id is None:
        request_id = int(time.time() * 1000)
    return {"id": str(request_id), **payload}


def start_button_for_state(mow_state: Any) -> str:
    """Return the app button used by lawn-mower start/resume."""

    try:
        state = int(mow_state)
    except (TypeError, ValueError):
        state = None
    return BUTTON_MOW_RESUME if state in PAUSED_MOW_STATES else BUTTON_MOW_GLOBAL


def _preference_config(response: Any) -> dict[str, Any] | None:
    """Return the preference config object from a mower response."""

    if not isinstance(response, dict):
        return None
    for key in ("preference_config", "mow_preference_config"):
        if key in response:
            config = response[key]
            return config if isinstance(config, dict) else None
    return response


def _area_id_key(area_id: Any) -> str | None:
    """Return a stable comparison key for a supported saved-area ID."""

    if isinstance(area_id, bool) or not isinstance(area_id, int | str):
        return None
    if isinstance(area_id, str) and not area_id.strip():
        return None
    return str(area_id).strip()


def parse_saved_areas(response: Any) -> list[dict[str, Any]]:
    """Parse safe, named saved areas from GET_MOW_PREFERENCE_CONFIG data.

    The reference app response stores saved areas under ``custom`` as
    ``area_id``/``area_name`` pairs. Entries with missing or unsupported IDs,
    blank names, duplicate IDs, or duplicate display names are omitted rather
    than guessing which option a user intended to select.
    """

    config = _preference_config(response)
    custom = config.get("custom") if config is not None else None
    if not isinstance(custom, list):
        return []

    candidates: list[dict[str, Any]] = []
    for raw_area in custom:
        if not isinstance(raw_area, dict):
            continue
        area_id = raw_area.get("area_id")
        id_key = _area_id_key(area_id)
        name = raw_area.get("area_name")
        if id_key is None or not isinstance(name, str) or not name.strip():
            continue
        candidates.append({"id": area_id, "name": name})

    id_counts: dict[str, int] = {}
    name_counts: dict[str, int] = {}
    for area in candidates:
        id_key = _area_id_key(area["id"])
        name_key = str(area["name"]).strip().casefold()
        if id_key is not None:
            id_counts[id_key] = id_counts.get(id_key, 0) + 1
        name_counts[name_key] = name_counts.get(name_key, 0) + 1

    safe_areas: list[dict[str, Any]] = []
    for area in candidates:
        id_key = _area_id_key(area["id"])
        name_key = str(area["name"]).strip().casefold()
        if id_key is not None and id_counts.get(id_key, 0) == 1 and name_counts.get(name_key, 0) == 1:
            safe_areas.append(area)
    return safe_areas


def _boundaries_payload(areas: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the ``modify_map`` payload used by area mowing."""

    return {
        "boundaries": [
            {"id": area["id"], "name": area.get("name", "")} for area in areas
        ]
    }


class MowerApi:
    """Send RockMow commands through a python-roborock V1 RPC channel."""

    def __init__(self, rpc_channel: V1RpcChannel, duid: str) -> None:
        """Initialize the command API with an already configured RPC channel."""

        self._rpc_channel = rpc_channel
        self._duid = duid

    async def _send_remote_msg(self, payload: dict[str, Any]) -> Any:
        """Send one exact ``remote_pb`` JSON payload and return the RPC result."""

        message = build_remote_message(payload)
        _LOGGER.debug("[%s] Sending RockMow remote_pb command %s", self._duid, payload.get("type"))
        try:
            return await self._rpc_channel.send_command(REMOTE_PB_METHOD, params=message)
        except RoborockException as err:
            if not str(err).startswith(_UNEXPECTED_RESULT_PREFIX):
                _LOGGER.warning(
                    "[%s] RockMow remote_pb command failed", self._duid, exc_info=True
                )
            raise

    async def _query(self, payload: dict[str, Any]) -> Any:
        """Send a read-only remote_pb query and decode its JSON result."""

        try:
            result = await self._send_remote_msg(payload)
        except RoborockException as err:
            text = str(err)
            marker = text.find(_UNEXPECTED_RESULT_PREFIX)
            if marker == -1:
                raise
            raw = text[marker + len(_UNEXPECTED_RESULT_PREFIX) :]
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise err from None
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        return result

    async def _send_button(self, app_button: str, **extra: Any) -> Any:
        """Send an app-button command."""

        return await self._send_remote_msg(
            {"type": TYPE_APP_BUTTON, "app_button": app_button, **extra}
        )

    async def start(self) -> Any:
        """Start a full-lawn mow."""

        return await self._send_button(BUTTON_MOW_GLOBAL)

    async def start_or_resume(self, mow_state: Any) -> Any:
        """Resume a paused task, otherwise start a full-lawn mow."""

        return await self._send_button(start_button_for_state(mow_state))

    async def pause(self) -> Any:
        """Pause the current mowing task."""

        return await self._send_button(BUTTON_MOW_PAUSE)

    async def resume(self) -> Any:
        """Resume a paused mowing task."""

        return await self._send_button(BUTTON_MOW_RESUME)

    async def dock(self) -> Any:
        """Return to the dock and charge."""

        return await self._send_button(BUTTON_CHARGE)

    async def edge_cut(self) -> Any:
        """Start an edge/perimeter cut for the current map."""

        return await self._send_button(BUTTON_MOW_EDGE)

    async def start_area_mow(self, areas: list[dict[str, Any]]) -> Any:
        """Start a saved-area mow with the app's MOW_SELECT payload."""

        if not areas:
            raise ValueError("start_area_mow requires at least one area")
        return await self._send_button(
            BUTTON_MOW_SELECT,
            modify_map=_boundaries_payload(areas),
        )

    async def stop(self) -> Any:
        """Stop/end the current mowing task."""

        return await self._send_button(BUTTON_MOW_END)

    async def get_mow_preference_config(self) -> dict[str, Any] | None:
        """Read the mower's saved mowing preferences without sending a write."""

        try:
            response = await self._query({"type": TYPE_GET_MOW_PREFERENCE_CONFIG})
        except RoborockException as err:
            _LOGGER.debug(
                "[%s] GET_MOW_PREFERENCE_CONFIG failed: %s",
                self._duid,
                err,
            )
            return None
        return _preference_config(response)

    async def get_areas(self) -> list[dict[str, Any]]:
        """Return safe named areas from the read-only preference query."""

        config = await self.get_mow_preference_config()
        return parse_saved_areas(config)

    async def get_saved_areas(self) -> list[dict[str, Any]]:
        """Compatibility alias for the saved-area discovery method."""

        return await self.get_areas()

    async def set_mow_height(self, height: int | float) -> Any:
        """Set the cutter height through the app's ``MAIN_CUTTER_HEIGHT`` RPC.

        The integration deliberately does not persist this value in the mowing
        preference.  The UI exposes a provisional 20--70 mm range, while the
        device-reported motor limits are not available in the DPS snapshot.
        """

        if isinstance(height, bool):
            raise ValueError("cutting height must be an integer")
        if isinstance(height, float) and not height.is_integer():
            raise ValueError("cutting height must be an integer")
        try:
            height_value = int(height)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("cutting height must be an integer") from err
        if height_value < MOW_HEIGHT_MIN or height_value > MOW_HEIGHT_MAX:
            raise ValueError(
                f"cutting height must be between {MOW_HEIGHT_MIN} and {MOW_HEIGHT_MAX} mm"
            )
        return await self._send_remote_msg(
            {
                "type": TYPE_REMOTE_CMD,
                "remote_cmd": {
                    "type": "MAIN_CUTTER_HEIGHT",
                    "main_cutter_height": height_value,
                },
            }
        )

    async def _get_global_mow_preference(self) -> dict[str, Any] | None:
        """Return a copy of the complete global preference, if readable."""

        config = await self.get_mow_preference_config()
        global_preference = config.get("global") if isinstance(config, dict) else None
        if not isinstance(global_preference, dict):
            return None
        return copy.deepcopy(global_preference)

    async def set_mow_eff_mode(self, mode: int | str) -> Any:
        """Read-modify-write the complete global efficiency preference.

        A missing preference is a hard failure: sending a partial preference
        could reset unrelated mowing settings, so this method never synthesizes
        a fallback payload.
        """

        if isinstance(mode, str):
            mode_code = EFF_MODE_REVERSE.get(mode)
        elif isinstance(mode, bool):
            mode_code = None
        else:
            try:
                mode_code = int(mode)
            except (TypeError, ValueError, OverflowError):
                mode_code = None
        effective = EFF_MODE_WIRE.get(mode_code) if mode_code is not None else None
        if effective is None:
            raise ValueError(f"Unknown efficiency mode: {mode}")

        global_preference = await self._get_global_mow_preference()
        if global_preference is None:
            raise RoborockException(
                "Roborock mower global preference could not be read; refusing partial write"
            )
        global_preference["effective"] = effective
        return await self._send_remote_msg(
            {
                "type": TYPE_SET_MOW_PREFERENCE,
                "mow_preference": global_preference,
            }
        )
