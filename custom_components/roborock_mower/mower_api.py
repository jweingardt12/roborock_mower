"""Write commands for Roborock RockMow devices.

RockMow mowing actions are sent through the V1 ``remote_pb`` RPC.  This module
keeps the wire payload construction independent of Home Assistant so it can be
unit-tested with a mocked RPC channel.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from roborock.exceptions import RoborockException
from roborock.protocols.v1_protocol import V1RpcChannel

_LOGGER = logging.getLogger(__name__)

REMOTE_PB_METHOD = "remote_pb"
TYPE_APP_BUTTON = "APP_BUTTON"

BUTTON_MOW_GLOBAL = "MOW_GLOBAL"
BUTTON_MOW_EDGE = "MOW_EDGE"
BUTTON_MOW_PAUSE = "MOW_PAUSE"
BUTTON_MOW_RESUME = "MOW_RESUME"
BUTTON_MOW_END = "MOW_END"
BUTTON_CHARGE = "CHARGE"

PAUSED_MOW_STATES = frozenset({2, 58})


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
        except RoborockException:
            _LOGGER.warning("[%s] RockMow remote_pb command failed", self._duid, exc_info=True)
            raise

    async def _send_button(self, app_button: str) -> Any:
        """Send an app-button command."""

        return await self._send_remote_msg(
            {"type": TYPE_APP_BUTTON, "app_button": app_button}
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

    async def stop(self) -> Any:
        """Stop/end the current mowing task."""

        return await self._send_button(BUTTON_MOW_END)
