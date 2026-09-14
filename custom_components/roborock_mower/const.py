"""Constants for the Roborock Mower integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "roborock_mower"
CONF_ENABLE_CONTROLS: Final = "enable_controls"
ROCKMOW_Z1_MODEL: Final = "roborock.mower.a235"
PLATFORMS: Final = [
    Platform.BUTTON,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
]

CONF_USER_DATA: Final = "user_data"
CONF_HOME_DATA: Final = "home_data"

MOWER_CATEGORY: Final = "roborock.mower"
ROCKMOW_Z1_NAME: Final = "RockMow Z1"

# python-roborock currently limits home data calls to 5 per hour.
# Polling every 15 minutes gives better visibility while staying below 5 calls per hour in normal operation.
SCAN_INTERVAL: Final = timedelta(minutes=15)
ONLINE_GRACE_PERIOD: Final = timedelta(minutes=5)

STATUS_ERROR_CODE: Final = "120"
STATUS_BATTERY: Final = "121"
STATUS_MOW_TYPE: Final = "122"
STATUS_MOW_STATE: Final = "123"
STATUS_MAPPING_TYPE: Final = "124"
STATUS_MAPPING_STATE: Final = "125"
STATUS_OTA_STATE: Final = "126"
STATUS_CHARGE_STATE: Final = "127"
STATUS_DOCK_STATE: Final = "128"
STATUS_CHARGE_TYPE: Final = "129"
STATUS_PEND_TYPE: Final = "130"
STATUS_REMOTE_STATE: Final = "131"
STATUS_MOW_START_TYPE: Final = "132"
STATUS_MOW_EFF_MODE: Final = "133"
STATUS_MOW_HEIGHT: Final = "134"
STATUS_MOW_DIRECTION_ANGLE: Final = "135"
STATUS_MOW_PATTERN: Final = "136"
STATUS_OFFLINE_STATUS: Final = "138"
STATUS_MOW_PROGRESS: Final = "139"
STATUS_BLADE_LIFESPAN: Final = "140"
STATUS_GPS_COORDINATE: Final = "142"
STATUS_OFF_DOCK_NO_TASK_STATUS: Final = "143"
STATUS_AFS_STATUS: Final = "144"
STATUS_NETWORK_CHANNEL: Final = "145"

SENSITIVE_DIAGNOSTIC_KEYS: Final = {
    "duid",
    "sn",
    "localKey",
    "local_key",
    "token",
    "rriot",
}

CHARGE_STATE_MAP: Final = {
    0: "not_charging",
    1: "charging",
    2: "charged",
    3: "charging_error",
}

MOW_STATE_MAP: Final = {
    0: "idle",
    51: "resuming",
    1: "mowing",
    2: "paused",
    3: "returning_to_dock",
    4: "docked",
    5: "error",
    55: "area_mowing",
    56: "edge_mowing",
    57: "moving_to_destination",
    58: "paused",
    61: "returning_to_charge_low_battery",
    76: "transit",
}

MQTT_STATUS_PROTOCOL: Final = 102
MQTT_MAP_PROTOCOL: Final = 301
MQTT_ONLINE_PROTOCOL: Final = 500
MQTT_BINARY_MAP_PROTOCOL: Final = 702
