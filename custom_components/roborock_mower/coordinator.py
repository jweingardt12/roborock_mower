"""Data coordinator for the Roborock Mower integration."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from roborock.devices.cache import DeviceCache, NoCache
from roborock.devices.rpc.v1_channel import create_v1_channel
from roborock.devices.transport.mqtt_channel import MqttChannel
from roborock.data import HomeData, HomeDataDevice, HomeDataProduct, UserData
from roborock.exceptions import RoborockException, RoborockInvalidCredentials, RoborockRateLimit
from roborock.mqtt.roborock_session import create_lazy_mqtt_session
from roborock.protocol import create_mqtt_params
from roborock.roborock_message import RoborockMessage
from roborock.web_api import RoborockApiClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HOME_DATA,
    CONF_USER_DATA,
    DOMAIN,
    MQTT_BINARY_MAP_PROTOCOL,
    MQTT_MAP_PROTOCOL,
    MQTT_ONLINE_PROTOCOL,
    MQTT_STATUS_PROTOCOL,
    MOWER_CATEGORY,
    ONLINE_GRACE_PERIOD,
    ROCKMOW_Z1_MODEL,
    SCAN_INTERVAL,
    STATUS_GPS_COORDINATE,
)
from .control import write_controls_enabled
from .mower_api import MowerApi

_LOGGER = logging.getLogger(__name__)
MQTT_RESTART_DELAY = timedelta(seconds=30)

AUTH_REFRESH_METHODS = (
    "refresh_user_data",
    "refresh_token",
    "refresh_login",
    "refresh_auth",
)


@dataclass(frozen=True)
class RoborockMowerDevice:
    """A Roborock mower and its product metadata."""

    device: HomeDataDevice
    product: HomeDataProduct


def _category_value(product: HomeDataProduct) -> str:
    category = product.category
    return getattr(category, "value", str(category))


def mower_device_id(device: HomeDataDevice) -> str:
    """Return the stable unique identifier for a mower."""

    return device.duid or device.sn


def find_mower_devices(home_data: HomeData) -> dict[str, RoborockMowerDevice]:
    """Return Roborock mower devices from home data."""

    mowers: dict[str, RoborockMowerDevice] = {}
    for device, product in home_data.device_products.values():
        if _category_value(product) != MOWER_CATEGORY:
            continue
        device_id = mower_device_id(device)
        if not device_id:
            continue
        if product.model != ROCKMOW_Z1_MODEL:
            _LOGGER.debug(
                "Found Roborock mower with untested model %s; exposing read-only status sensors",
                product.model,
            )
        mowers[device_id] = RoborockMowerDevice(device=device, product=product)
    return mowers


def status_value(device: HomeDataDevice, status_id: str) -> Any:
    """Return a value from device.device_status."""

    if not device.device_status:
        return None
    return device.device_status.get(status_id)


_GPS_REDACTED = "<gps redacted>"
_GPS_KEYS = {
    str(STATUS_GPS_COORDINATE),
    "gps_coordinate",
    "gpsCoordinate",
}


def redact_gps_data(value: Any) -> Any:
    """Return a copy of data with GPS values removed from outward surfaces."""

    if isinstance(value, dict):
        return {
            key: _GPS_REDACTED if str(key) in _GPS_KEYS else redact_gps_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_gps_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_gps_data(item) for item in value)
    return value


class RoborockMowerCoordinator(DataUpdateCoordinator[dict[str, RoborockMowerDevice]]):
    """Poll Roborock Cloud for mower data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""

        self.entry = entry
        self.email = entry.data["email"]
        self.user_data = UserData.from_dict(entry.data[CONF_USER_DATA])
        self.last_cloud_update: datetime | None = None
        self.last_mqtt_update: datetime | None = None
        self.last_mqtt_protocol: int | None = None
        self.last_update_attempt: datetime | None = None
        self.last_rate_limit: datetime | None = None
        self.last_status_change: dict[str, datetime] = {}
        self.last_static_status_update: dict[str, datetime] = {}
        self.last_mqtt_seen: dict[str, datetime] = {}
        self.last_mqtt_online_hint: dict[str, bool] = {}
        self.last_mqtt_payload: dict[str, dict[str, Any]] = {}
        self.mqtt_connected = False
        self.mqtt_subscribed: dict[str, bool] = {}
        self.last_mqtt_error: str | None = None
        self._last_device_status: dict[str, dict[str, Any]] = {}
        self._mqtt_session: Any | None = None
        self._mqtt_tasks: list[Any] = []
        self._command_channels: dict[str, Any] = {}
        self._command_unsubscribes: dict[str, Any] = {}
        self._mower_apis: dict[str, MowerApi] = {}
        self._offline_callbacks: dict[str, Any] = {}
        self._stopping_mqtt = False
        self.client = RoborockApiClient(
            username=self.email,
            session=async_get_clientsession(hass),
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

        if home_data := entry.data.get(CONF_HOME_DATA):
            self.data = find_mower_devices(HomeData.from_dict(home_data))
            for mower_id, mower in self.data.items():
                self._last_device_status[mower_id] = dict(mower.device.device_status or {})

    def write_controls_enabled_for(self, mower_id: str) -> bool:
        """Return whether writes are explicitly enabled for this exact mower model."""

        mower = self.data.get(mower_id)
        return mower is not None and write_controls_enabled(self.entry.options, mower.product.model)

    def mower_api_for(self, mower_id: str) -> MowerApi | None:
        """Return the command API prepared for a mower, if writes are available."""

        return self._mower_apis.get(mower_id)

    async def async_start_mqtt(self) -> None:
        """Start listening for MQTT DPS updates."""

        if not self.data or self._mqtt_session is not None:
            return

        try:
            mqtt_params = create_mqtt_params(self.user_data.rriot)
            self._mqtt_session = await create_lazy_mqtt_session(mqtt_params)
        except (AttributeError, RoborockException) as err:
            _LOGGER.warning(
                "Could not prepare Roborock MQTT session; cloud fallback remains active: %s",
                err,
            )
            self._mqtt_session = None
            return

        for mower_id, mower in self.data.items():
            try:
                channel = MqttChannel(
                    self._mqtt_session,
                    mower.device.duid,
                    mower.device.local_key,
                    self.user_data.rriot,
                    mqtt_params,
                )
            except RoborockException as err:
                _LOGGER.warning(
                    "Could not create Roborock MQTT channel for mower %s; cloud fallback remains active: %s",
                    mower.device.name,
                    err,
                )
                continue

            if self.write_controls_enabled_for(mower_id):
                try:
                    command_channel = create_v1_channel(
                        self.user_data,
                        mqtt_params,
                        self._mqtt_session,
                        mower.device,
                        DeviceCache(mower.device.duid, NoCache()),
                    )
                    # V1Channel connects its local/MQTT RPC transports from
                    # subscribe(). Keep a no-op subscription alive for the
                    # lifetime of the coordinator so command RPCs have an
                    # active transport without changing the status path.
                    command_unsubscribe = await command_channel.subscribe(lambda _message: None)
                except RoborockException as err:
                    _LOGGER.warning(
                        "Could not prepare write channel for mower %s; controls will be unavailable: %s",
                        mower.device.name,
                        err,
                    )
                else:
                    self._command_channels[mower_id] = command_channel
                    self._command_unsubscribes[mower_id] = command_unsubscribe
                    self._mower_apis[mower_id] = MowerApi(
                        command_channel.rpc_channel,
                        mower.device.duid,
                    )

            self.mqtt_subscribed[mower_id] = False
            task = self.hass.async_create_task(
                self._async_mqtt_watch_loop(mower_id, mower.device.name, channel)
            )
            self._mqtt_tasks.append(task)
            _LOGGER.info("Starting Roborock MQTT watch loop for mower %s", mower.device.name)

    async def async_stop_mqtt(self) -> None:
        """Stop MQTT listeners."""

        self._stopping_mqtt = True
        for task in self._mqtt_tasks:
            task.cancel()
        self._mqtt_tasks.clear()

        for cancel in self._offline_callbacks.values():
            cancel()
        self._offline_callbacks.clear()
        for unsubscribe in self._command_unsubscribes.values():
            unsubscribe()
        self._command_unsubscribes.clear()
        self._command_channels.clear()
        self._mower_apis.clear()

        if self._mqtt_session is not None:
            await self._mqtt_session.close()
            self._mqtt_session = None
        self.mqtt_connected = False

    async def _async_mqtt_watch_loop(self, mower_id: str, mower_name: str, channel: MqttChannel) -> None:
        """Watch the Roborock MQTT stream and apply mower updates."""

        while not self._stopping_mqtt:
            try:
                _LOGGER.info("Subscribing to Roborock MQTT stream for mower %s", mower_name)
                self.mqtt_subscribed[mower_id] = True
                self.last_mqtt_error = None
                async for message in channel.subscribe_stream():
                    self.mqtt_connected = channel.is_connected
                    self._handle_mqtt_message(mower_id, message)
            except Exception as err:  # noqa: BLE001 - keep MQTT fallback loop alive.
                if self._stopping_mqtt:
                    return
                self.mqtt_connected = False
                self.mqtt_subscribed[mower_id] = False
                self.last_mqtt_error = str(err)
                _LOGGER.warning(
                    "Roborock MQTT watch loop for mower %s stopped; retrying in %.0f seconds: %s",
                    mower_name,
                    MQTT_RESTART_DELAY.total_seconds(),
                    err,
                )
                await asyncio.sleep(MQTT_RESTART_DELAY.total_seconds())
            else:
                if not self._stopping_mqtt:
                    self.mqtt_connected = False
                    self.mqtt_subscribed[mower_id] = False
                    _LOGGER.warning(
                        "Roborock MQTT watch loop for mower %s ended; retrying in %.0f seconds",
                        mower_name,
                        MQTT_RESTART_DELAY.total_seconds(),
                    )
                    await asyncio.sleep(MQTT_RESTART_DELAY.total_seconds())

    async def _async_update_data(self) -> dict[str, RoborockMowerDevice]:
        """Fetch fresh mower data from Roborock Cloud."""

        self.last_update_attempt = dt_util.utcnow()
        try:
            home_data = await self.client.get_home_data_v3(self.user_data)
        except RoborockInvalidCredentials as err:
            if await self._async_refresh_auth():
                try:
                    home_data = await self.client.get_home_data_v3(self.user_data)
                except RoborockInvalidCredentials as refresh_err:
                    raise ConfigEntryAuthFailed(
                        "Roborock authentication expired; reauthentication required"
                    ) from refresh_err
            else:
                raise ConfigEntryAuthFailed(
                    "Roborock authentication expired; reauthentication required"
                ) from err
        except RoborockRateLimit:
            self.last_rate_limit = dt_util.utcnow()
            if self.data:
                _LOGGER.info("Roborock home data rate limit reached; keeping last known mower data")
                return self.data
            raise UpdateFailed("Roborock home data rate limit reached and no cached data is available")
        except RoborockException as err:
            raise UpdateFailed(str(err)) from err

        mowers = find_mower_devices(home_data)
        if not mowers:
            raise UpdateFailed("No Roborock mower found in Roborock Cloud home data")
        self.last_cloud_update = dt_util.utcnow()
        self._log_status_updates(mowers)
        return mowers

    def _handle_mqtt_message(self, mower_id: str, message: RoborockMessage) -> None:
        """Handle a decoded MQTT message from Roborock."""

        try:
            protocol = int(message.protocol)
        except (TypeError, ValueError):
            protocol = int(getattr(message.protocol, "value", 0))

        now = dt_util.utcnow()
        self.last_mqtt_update = now
        self.last_mqtt_protocol = protocol
        self.last_mqtt_seen[mower_id] = now

        if protocol == MQTT_STATUS_PROTOCOL:
            self._handle_status_payload(mower_id, message.payload)
            return

        if protocol == MQTT_ONLINE_PROTOCOL:
            self._handle_online_payload(mower_id, message.payload)
            return

        if protocol in (MQTT_MAP_PROTOCOL, MQTT_BINARY_MAP_PROTOCOL):
            payload_size = len(message.payload or b"")
            _LOGGER.debug(
                "Roborock mower MQTT binary/map protocol=%s seq=%s payload_bytes=%s",
                protocol,
                message.seq,
                payload_size,
            )
            return

        _LOGGER.debug(
            "Roborock mower MQTT protocol=%s seq=%s payload_bytes=%s",
            protocol,
            message.seq,
            len(message.payload or b""),
        )

    def _handle_status_payload(self, mower_id: str, payload: bytes | None) -> None:
        """Apply DPS status values from MQTT protocol 102."""

        payload_json = _decode_json_payload(payload)
        if not isinstance(payload_json, dict):
            _LOGGER.debug("Roborock mower MQTT status payload was not JSON: %r", payload)
            return

        raw_dps = payload_json.get("dps")
        if not isinstance(raw_dps, dict):
            _LOGGER.debug("Roborock mower MQTT status payload had no DPS object: %s", payload_json)
            return

        mower = self.data.get(mower_id)
        if mower is None:
            return

        device_status = dict(mower.device.device_status or {})
        changed: dict[str, dict[str, Any]] = {}
        for raw_key, value in raw_dps.items():
            key = str(raw_key)
            previous = device_status.get(key)
            device_status[key] = value
            if previous != value:
                changed[key] = {"old": previous, "new": value}

        mower.device.device_status = device_status
        mower.device.online = True
        self.last_mqtt_online_hint[mower_id] = True
        safe_raw_dps = redact_gps_data(raw_dps)
        self.last_mqtt_payload[mower_id] = safe_raw_dps

        if changed:
            self.last_status_change[mower_id] = self.last_mqtt_update or dt_util.utcnow()
            self._last_device_status[mower_id] = device_status
            _LOGGER.info(
                "Roborock mower %s status changed from MQTT: %s",
                mower.device.name,
                redact_gps_data(changed),
            )
        else:
            self.last_static_status_update[mower_id] = self.last_mqtt_update or dt_util.utcnow()
            _LOGGER.debug(
                "Roborock mower %s status unchanged from MQTT: %s",
                mower.device.name,
                safe_raw_dps,
            )

        self._cancel_pending_offline(mower_id)
        self.async_set_updated_data(dict(self.data))

    def _handle_online_payload(self, mower_id: str, payload: bytes | None) -> None:
        """Handle Roborock MQTT online/offline hints."""

        payload_json = _decode_json_payload(payload)
        if not isinstance(payload_json, dict) or "online" not in payload_json:
            _LOGGER.debug("Roborock mower MQTT online payload was not understood: %r", payload)
            return

        online = bool(payload_json["online"])
        self.last_mqtt_online_hint[mower_id] = online
        mower = self.data.get(mower_id)
        if mower is None:
            return

        if online:
            mower.device.online = True
            self._cancel_pending_offline(mower_id)
            self.async_set_updated_data(dict(self.data))
            return

        _LOGGER.debug(
            "Roborock mower %s reported offline over MQTT; waiting %s before marking offline",
            mower.device.name,
            ONLINE_GRACE_PERIOD,
        )
        self._schedule_offline_if_stale(mower_id)
        self.async_set_updated_data(dict(self.data))

    def is_mower_online(self, mower_id: str) -> bool | None:
        """Return debounced online status for a mower."""

        mower = self.data.get(mower_id)
        if mower is None:
            return None

        if self.last_mqtt_online_hint.get(mower_id) is True:
            return True

        if last_seen := self.last_mqtt_seen.get(mower_id):
            if dt_util.utcnow() - last_seen < ONLINE_GRACE_PERIOD:
                return True

        return mower.device.online

    def _schedule_offline_if_stale(self, mower_id: str) -> None:
        """Mark a mower offline later if no fresh MQTT messages arrive."""

        self._cancel_pending_offline(mower_id)

        def _mark_offline(_now: datetime) -> None:
            mower = self.data.get(mower_id)
            if mower is None:
                return
            last_seen = self.last_mqtt_seen.get(mower_id)
            if last_seen and dt_util.utcnow() - last_seen < ONLINE_GRACE_PERIOD:
                return
            if self.last_mqtt_online_hint.get(mower_id) is False:
                mower.device.online = False
                self.async_set_updated_data(dict(self.data))

        self._offline_callbacks[mower_id] = async_call_later(
            self.hass,
            ONLINE_GRACE_PERIOD.total_seconds(),
            _mark_offline,
        )

    def _cancel_pending_offline(self, mower_id: str) -> None:
        """Cancel any delayed offline marker."""

        if cancel := self._offline_callbacks.pop(mower_id, None):
            cancel()

    def _log_status_updates(self, mowers: dict[str, RoborockMowerDevice]) -> None:
        """Log whether Roborock Cloud returned changed or static mower status."""

        for mower_id, mower in mowers.items():
            status = dict(mower.device.device_status or {})
            previous_status = self._last_device_status.get(mower_id)

            if previous_status is None:
                self.last_status_change[mower_id] = self.last_cloud_update or dt_util.utcnow()
                self._last_device_status[mower_id] = status
                _LOGGER.info(
                    "Roborock mower %s initial status from cloud: %s",
                    mower.device.name,
                    redact_gps_data(status),
                )
                continue

            if status == previous_status:
                self.last_static_status_update[mower_id] = self.last_cloud_update or dt_util.utcnow()
                _LOGGER.debug(
                    "Roborock mower %s status unchanged from cloud: %s",
                    mower.device.name,
                    status,
                )
                continue

            changed = {
                key: {
                    "old": redact_gps_data(previous_status.get(key)),
                    "new": redact_gps_data(value),
                }
                for key, value in status.items()
                if previous_status.get(key) != value
            }
            removed = {
                key: redact_gps_data(previous_status[key])
                for key in previous_status.keys() - status.keys()
            }
            self.last_status_change[mower_id] = self.last_cloud_update or dt_util.utcnow()
            self._last_device_status[mower_id] = status
            _LOGGER.info(
                "Roborock mower %s status changed: changed=%s removed=%s",
                mower.device.name,
                changed,
                removed,
            )

    async def _async_refresh_auth(self) -> bool:
        """Refresh Roborock auth data if the installed library supports it."""

        for method_name in AUTH_REFRESH_METHODS:
            method = getattr(self.client, method_name, None)
            if method is None:
                continue

            _LOGGER.debug("Trying Roborock auth refresh method %s", method_name)
            try:
                result = method(self.user_data) if _call_accepts_user_data(method) else method()
                if inspect.isawaitable(result):
                    result = await result
            except (RoborockException, TypeError):
                _LOGGER.exception("Roborock auth refresh method %s failed", method_name)
                return False

            if result is None:
                updated_user_data = self.user_data
            elif isinstance(result, UserData):
                updated_user_data = result
            elif isinstance(result, dict):
                updated_user_data = UserData.from_dict(result)
            else:
                _LOGGER.debug(
                    "Roborock auth refresh method %s returned unsupported type %s",
                    method_name,
                    type(result),
                )
                return False

            self.user_data = updated_user_data
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_USER_DATA: updated_user_data.as_dict(),
                },
            )
            _LOGGER.info("Roborock auth data refreshed")
            return True

        _LOGGER.debug("Installed python-roborock does not expose an auth refresh method")
        return False


def _call_accepts_user_data(method: Any) -> bool:
    """Return whether a bound refresh method appears to accept one argument."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True

    required_params = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    return bool(required_params)


def _decode_json_payload(payload: bytes | None) -> Any:
    """Decode a JSON MQTT payload."""

    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
