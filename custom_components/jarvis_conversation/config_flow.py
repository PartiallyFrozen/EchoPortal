"""Config flow: point Home Assistant at the PC agent."""
from __future__ import annotations

from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_TIMEOUT, CONF_TOKEN, CONF_URL, DEFAULT_TIMEOUT, DOMAIN


def _normalize_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise vol.Invalid("invalid_url")
    return url


async def _verify(url: str, token: str) -> str | None:
    """Return an error key on failure, else None."""
    try:
        timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
        headers = {"X-EchoPortal-Token": token}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url + "/api/status") as response:
                if response.status == 401:
                    return "invalid_auth"
                if response.status != 200:
                    return "cannot_connect"
                body = await response.json(content_type=None)
                if not isinstance(body, dict) or not body.get("ready"):
                    return "not_ready"
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return "cannot_connect"
    return None


class JarvisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id("jarvis_conversation")
            self._abort_if_unique_id_configured()
            url = user_input[CONF_URL]
            token = user_input[CONF_TOKEN]
            errors_raw = await _verify(url, token)
            if errors_raw:
                errors["base"] = errors_raw
            else:
                return self.async_create_entry(title="Jarvis", data={
                    CONF_URL: url, CONF_TOKEN: token,
                    CONF_TIMEOUT: int(user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
                })
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_URL): str,
            vol.Required(CONF_TOKEN): str,
            vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=120)),
        }), errors=errors)
