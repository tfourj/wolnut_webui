"""Notification provider implementations."""

from wolnut.providers.discord import send as send_discord
from wolnut.providers.gotify import send as send_gotify

__all__ = ["send_discord", "send_gotify"]
