"""Jarvis conversation agent for Home Assistant.

Posts speech text to the PC agent's OpenAI-compatible /v1/chat/completions endpoint so
Home Assistant voice assistants can drive the same Jarvis brain as the dashboard and Spot.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

PLATFORMS = ("conversation",)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
