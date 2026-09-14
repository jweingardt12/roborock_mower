# Roborock Mower

![Roborock Mower unofficial integration logo](../../assets/roborock_mower_logo.svg)

Read-only by default Home Assistant custom integration for Roborock RockMow Z1 / Z115. Optional write controls are disabled unless explicitly enabled in the integration options.

Version 0.5 adds opt-in cutting-height, efficiency-mode, and multi-zone actions, plus expanded read-only DPS status sensors. It uses Roborock MQTT/DPS push as the primary status source and keeps `get_home_data_v3(user_data)` as a slow cloud fallback.

## Supported device

The integration looks for Roborock devices where:

- `product.category` is `roborock.mower`
- tested model: `roborock.mower.a235`
- product name: `RockMow Z1`

Other mower models are not blocked, but they are not tested.

## Install

1. Copy `custom_components/roborock_mower` to your Home Assistant config folder:

   ```text
   <config>/custom_components/roborock_mower
   ```

2. Restart Home Assistant.
3. Go to **Settings** -> **Devices & services** -> **Add integration**.
4. Search for **Roborock Mower**.
5. Enter the Roborock account e-mail address.
6. Enter the Roborock e-mail code.

The Roborock e-mail code normally expires after 15 minutes. The integration stores the returned session/token data, so a new code is only needed if Home Assistant starts a reauth flow.

## Entities

The integration creates one main entity per mower:

- `lawn_mower.<device>`

It also creates these read-only status entities:

- `sensor.<device>_battery`
- `sensor.<device>_mow_state`
- `sensor.<device>_charge_state`
- `sensor.<device>_mow_progress`
- `sensor.<device>_mow_height`
- `sensor.<device>_gps_raw` (redacted and disabled by default)
- additional diagnostic sensors for mower, mapping, charging, OTA, network, blade, and error status
- `binary_sensor.<device>_online`

All entities are linked to the same Home Assistant device. Unique IDs use DUID, with SN as fallback.

## Optional mower controls

The main `lawn_mower` entity is read-only by default. To expose start/resume, pause, and dock actions, open the integration's **Configure** dialog and enable **Mower controls**. The option defaults to disabled and is stored in the config entry options.

When controls are enabled, only the exact tested model `roborock.mower.a235` receives actuator writes. The existing lawn-mower controls and buttons remain available:

- `button.<device>_edge_cut`
- `button.<device>_stop` (stop/end task)

They send `APP_BUTTON` values through `remote_pb`: `MOW_GLOBAL`, `MOW_RESUME`, `MOW_PAUSE`, `CHARGE`, `MOW_EDGE`, and `MOW_END`. Start/resume chooses `MOW_RESUME` when the mower reports a paused state; otherwise it sends `MOW_GLOBAL`.

Two additional setting entities are exposed:

- `number.<device>_cutting_height` — approximately 0.79–2.76 in, with 0.01 in steps; values are rounded to whole millimetres on the wire
- `select.<device>_mow_eff_mode` — `Daily`, `Efficient`, or `Manicure`

Cutting height uses `MAIN_CUTTER_HEIGHT` and may physically actuate the cutter mechanism. Efficiency mode reads the complete `preference_config.global`, changes only `effective`, and refuses to write if the complete preference cannot be read. Neither setting should start mowing, but both are experimental and unverified on the exact a235 device.

When controls are enabled and the mower returns valid saved areas, a `select.<device>_mow_area` entity is also exposed. Its options are the unambiguous saved area names returned by the read-only `GET_MOW_PREFERENCE_CONFIG` query. Setup and discovery do not start mowing; only an explicit selection sends `MOW_SELECT` with the selected area's `modify_map.boundaries` payload. Invalid, missing-ID, or duplicate-name entries are omitted.

For multiple areas, call `roborock_mower.mow_areas` with one mower device target and a non-empty `area_ids` list, for example `[2, 3]`. The service reads a fresh saved-area snapshot, rejects duplicate or stale IDs, resolves canonical names, and sends one `MOW_SELECT` command. This multi-zone command protocol is **not live-verified on the exact a235 RockMow**.

The command payload and action mapping were ported from the RockNeo preview to the python-roborock 7.x V1 RPC channel. Enabling controls is experimental and may cause unexpected behavior; keep the option disabled unless you intentionally accept that risk.

The `gps_raw` sensor never exposes or decodes GPS bytes. Its state is unavailable and its raw-value attribute contains only a redaction marker; its entity is disabled by default.

## Status source

MQTT protocol `102` DPS updates are applied immediately. Cloud polling runs every 15 minutes as fallback/resynchronization.

MQTT protocol `500` online/offline messages are treated as weak hints. Offline is delayed for 5 minutes because RockMow can briefly report false/true when Wi-Fi coverage is poor. Any fresh DPS update marks the mower online again.

Binary/map-like MQTT protocols `301` and `702` are logged at debug level only. They are not decoded in this integration yet.

## Known RockMow DPS

| ID | Field |
| --- | --- |
| 120 | `error_code` |
| 121 | `battery` |
| 122 | `mow_type` |
| 123 | `mow_state` |
| 124 | `mapping_type` |
| 125 | `mapping_state` |
| 126 | `ota_state` |
| 127 | `charge_state` |
| 128 | `dock_state` |
| 129 | `charge_type` |
| 130 | `pend_type` |
| 131 | `remote_state` |
| 132 | `mow_start_type` |
| 133 | `mow_eff_mode` |
| 134 | `mow_height` |
| 135 | `mow_direction_angle` |
| 136 | `mow_pattern` |
| 138 | `offline_status` |
| 139 | `mow_progress` |
| 140 | `blade_lifespan` |
| 142 | `gps_coordinate` (redacted) |
| 143 | `off_dock_no_task_status` |
| 144 | `afs_status` |
| 145 | `network_channel` |

Known `mow_state` mappings:

- `0` = `idle`
- `51` = `resuming`
- `55` = `area_mowing`
- `56` = `edge_mowing`
- `57` = `moving_to_destination`
- `58` = `paused`
- `61` = `returning_to_charge_low_battery`
- `76` = `transit`

Observed notes:

- During edge cutting, the mower can briefly report `57` when moving to another destination before continuing.
- At the end of a job, the mower may drive over an area/path on the way back to the dock without exposing that area name as a plain DPS value.

Unknown values are preserved as `unknown_<code>` and the raw value is available in entity attributes.

## Debug logging

```yaml
logger:
  logs:
    custom_components.roborock_mower: debug
```

Useful attributes include:

- `last_mqtt_update`
- `last_mqtt_protocol`
- `last_mqtt_seen`
- `last_mqtt_online_hint`
- `last_mqtt_payload`
- `mqtt_connected`
- `mqtt_subscribed`
- `last_mqtt_error`
- `last_cloud_update`
- `last_rate_limit`
- GPS payloads are redacted; no latitude/longitude is exposed

If the standalone MQTT probe sees activity but Home Assistant does not update, check these attributes first. `mqtt_subscribed` should be true for the mower, `last_mqtt_update` should move when DPS messages arrive, and `last_mqtt_error` should be empty.

Diagnostics redact sensitive values such as `localKey`, `duid`, `sn`, `token`, and `rriot`.

## Not implemented

Map geometry and full-map decoding are not implemented. Saved-area discovery and multi-zone validation are read-only until the final `MOW_SELECT` command; that command, cutting-height command, and efficiency preference write remain unverified on a235 until live testing is explicitly authorized.
