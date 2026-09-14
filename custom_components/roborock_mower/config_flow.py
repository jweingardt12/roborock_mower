"""Config flow for the Roborock Mower integration."""

from __future__ import annotations

import logging
from typing import Any

from roborock.exceptions import (
    RoborockAccountDoesNotExist,
    RoborockException,
    RoborockInvalidCode,
    RoborockRateLimit,
    RoborockTooFrequentCodeRequests,
)
from roborock.web_api import RoborockApiClient
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CODE, CONF_EMAIL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOME_DATA, CONF_USER_DATA, DOMAIN, ROCKMOW_Z1_NAME
from .control import CONF_ENABLE_CONTROLS
from .coordinator import find_mower_devices

_LOGGER = logging.getLogger(__name__)

CONF_REQUEST_NEW_CODE = "request_new_code"


class RoborockMowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Roborock Mower."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow for the explicit mower controls setting."""

        return RoborockMowerOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the flow."""

        self._email: str | None = None
        self._client: RoborockApiClient | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for email and request a Roborock login code."""

        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            self._email = email
            self._client = RoborockApiClient(
                username=email,
                session=async_get_clientsession(self.hass),
            )

            if await self._async_request_code(errors):
                return await self.async_step_code()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate the login code and create the config entry."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if self._client is None or self._email is None:
                return await self.async_step_user()

            if user_input.get(CONF_REQUEST_NEW_CODE):
                if await self._async_request_code(errors):
                    errors["base"] = "code_resent"
                return self._show_code_form(errors)

            code = str(user_input.get(CONF_CODE, "")).strip()
            if not code:
                errors[CONF_CODE] = "required"
                return self._show_code_form(errors)

            try:
                user_data = await self._client.code_login(code)
                home_data = await self._client.get_home_data_v3(user_data)
            except RoborockInvalidCode:
                errors["base"] = "invalid_code"
            except RoborockRateLimit:
                errors["base"] = "rate_limited"
            except RoborockException:
                _LOGGER.exception("Failed to log in to Roborock")
                errors["base"] = "cannot_connect"
            else:
                mowers = find_mower_devices(home_data)
                if not mowers:
                    errors["base"] = "no_mower_found"
                else:
                    title = next(iter(mowers.values())).device.name or ROCKMOW_Z1_NAME
                    return self.async_create_entry(
                        title=title,
                        data={
                            "email": self._email,
                            CONF_USER_DATA: user_data.as_dict(),
                            CONF_HOME_DATA: home_data.as_dict(),
                        },
                    )

        return self._show_code_form(errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reauthentication after Home Assistant detects expired auth."""

        entry_id = self.context.get("entry_id")
        if entry_id is None:
            return self.async_abort(reason="reauth_entry_not_found")

        self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_not_found")

        self._email = self._reauth_entry.data.get("email") or entry_data.get("email")
        if self._email is None:
            return self.async_abort(reason="reauth_entry_not_found")

        await self.async_set_unique_id(self._email)
        self._client = RoborockApiClient(
            username=self._email,
            session=async_get_clientsession(self.hass),
        )

        errors: dict[str, str] = {}
        await self._async_request_code(errors)

        return self._show_reauth_code_form(errors)

    async def async_step_reauth_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate a reauth code and update the existing config entry."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if self._client is None or self._email is None or self._reauth_entry is None:
                return self.async_abort(reason="reauth_entry_not_found")

            if user_input.get(CONF_REQUEST_NEW_CODE):
                if await self._async_request_code(errors):
                    errors["base"] = "code_resent"
                return self._show_reauth_code_form(errors)

            code = str(user_input.get(CONF_CODE, "")).strip()
            if not code:
                errors[CONF_CODE] = "required"
                return self._show_reauth_code_form(errors)

            try:
                user_data = await self._client.code_login(code)
                home_data = await self._client.get_home_data_v3(user_data)
            except RoborockInvalidCode:
                errors["base"] = "invalid_code"
            except RoborockRateLimit:
                errors["base"] = "rate_limited"
            except RoborockException:
                _LOGGER.exception("Failed to reauthenticate with Roborock")
                errors["base"] = "cannot_connect"
            else:
                mowers = find_mower_devices(home_data)
                if not mowers:
                    errors["base"] = "no_mower_found"
                else:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data={
                            **self._reauth_entry.data,
                            "email": self._email,
                            CONF_USER_DATA: user_data.as_dict(),
                            CONF_HOME_DATA: home_data.as_dict(),
                        },
                    )
                    await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self._show_reauth_code_form(errors)

    async def _async_request_code(self, errors: dict[str, str]) -> bool:
        """Request a Roborock e-mail code and fill errors on failure."""

        if self._client is None:
            errors["base"] = "cannot_connect"
            return False

        try:
            await self._client.request_code()
        except RoborockAccountDoesNotExist:
            errors["base"] = "account_not_found"
        except RoborockTooFrequentCodeRequests:
            errors["base"] = "too_many_requests"
        except RoborockRateLimit:
            errors["base"] = "rate_limited"
        except RoborockException:
            _LOGGER.exception("Failed to request Roborock login code")
            errors["base"] = "cannot_connect"
        else:
            return True

        return False

    def _show_code_form(self, errors: dict[str, str]) -> config_entries.ConfigFlowResult:
        """Show the code entry form."""

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CODE): str,
                    vol.Optional(CONF_REQUEST_NEW_CODE, default=False): bool,
                }
            ),
            errors=errors,
        )

    def _show_reauth_code_form(self, errors: dict[str, str]) -> config_entries.ConfigFlowResult:
        """Show the reauth code entry form."""

        return self.async_show_form(
            step_id="reauth_code",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CODE): str,
                    vol.Optional(CONF_REQUEST_NEW_CODE, default=False): bool,
                }
            ),
            errors=errors,
        )


class RoborockMowerOptionsFlow(config_entries.OptionsFlow):
    """Configure optional Roborock mower write controls."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""

        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enable or disable command entities and actions."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENABLE_CONTROLS,
                        default=bool(self._config_entry.options.get(CONF_ENABLE_CONTROLS, False)),
                    ): bool
                }
            ),
        )
