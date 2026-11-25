"""Conversation entity that forwards Home Assistant voice to the Jarvis brain."""
from __future__ import annotations

import aiohttp
from homeassistant.components.conversation import ConversationEntity, ConversationInput
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.intent import ConversationResult, IntentResponse

from .const import CONF_TIMEOUT, CONF_TOKEN, CONF_URL, DEFAULT_TIMEOUT


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([JarvisConversationEntity(entry)])


class JarvisConversationEntity(ConversationEntity):
    """Send user speech to the PC agent and speak its reply through HA."""

    _attr_name = "Jarvis"
    _attr_icon = "mdi:robot"

    def __init__(self, entry: ConfigEntry) -> None:
        self._url = str(entry.data[CONF_URL]).rstrip("/")
        self._token = str(entry.data[CONF_TOKEN])
        self._timeout = int(entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
        self._attr_unique_id = f"{entry.entry_id}-conversation"

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        text = str(user_input.text or "").strip()
        conversation_id = str(user_input.conversation_id or "homeassistant")
        response = IntentResponse(hass=self.hass, language=user_input.language or "en")
        if not text:
            response.async_set_answer("I didn't catch that.")
            return ConversationResult(response=response, conversation_id=conversation_id)
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            headers = {"X-EchoPortal-Token": self._token, "Content-Type": "application/json"}
            payload = {"model": "jarvis", "stream": False, "user": conversation_id,
                       "messages": [{"role": "user", "content": text}]}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.post(self._url + "/v1/chat/completions", json=payload) as res:
                    if res.status != 200:
                        raise RuntimeError(f"agent returned HTTP {res.status}")
                    body = await res.json(content_type=None)
            choices = body.get("choices") if isinstance(body, dict) else None
            answer = ""
            if choices:
                answer = str((choices[0].get("message") or {}).get("content") or "")
            response.async_set_answer(answer or "I couldn't reach Jarvis.")
        except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError) as err:
            response.async_set_answer(f"Jarvis is unavailable: {str(err)[:120]}")
        return ConversationResult(response=response, conversation_id=conversation_id)
