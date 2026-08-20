import logging
from dataclasses import dataclass
from typing import Callable

from wolnut.config import NotificationsConfig
from wolnut.providers import send_discord, send_gotify, send_ntfy

logger = logging.getLogger("wolnut")

NotificationSender = Callable[[str, str], None]


@dataclass
class NotificationResult:
    provider: str
    success: bool
    error: str | None = None


class NotificationService:
    def __init__(self, config: NotificationsConfig):
        self.config = config

    def _provider_senders(self) -> dict[str, NotificationSender]:
        senders: dict[str, NotificationSender] = {}
        if self.config.discord.enabled:
            senders["discord"] = lambda title, message: send_discord(
                self.config.discord.webhook_url,
                title,
                message,
            )
        if self.config.gotify.enabled:
            senders["gotify"] = lambda title, message: send_gotify(
                self.config.gotify.url,
                self.config.gotify.token,
                title,
                message,
                self.config.gotify.priority,
            )
        if self.config.ntfy.enabled:
            senders["ntfy"] = lambda title, message: send_ntfy(
                self.config.ntfy.url,
                self.config.ntfy.topic,
                self.config.ntfy.token,
                title,
                message,
                self.config.ntfy.priority,
            )
        return senders

    def send(
        self,
        event: str,
        title: str,
        message: str,
    ) -> list[NotificationResult]:
        if not getattr(self.config.events, event, False):
            return []
        return self._send_with(self._provider_senders(), title, message)

    def send_test(self, provider: str) -> NotificationResult:
        senders = self._provider_senders()
        if provider not in senders:
            return NotificationResult(
                provider=provider,
                success=False,
                error=f"{provider.capitalize()} is not enabled",
            )
        return self._send_with(
            {provider: senders[provider]},
            "Wolnut test notification",
            "Your notification provider is configured correctly.",
        )[0]

    @staticmethod
    def _send_with(
        senders: dict[str, NotificationSender],
        title: str,
        message: str,
    ) -> list[NotificationResult]:
        results = []
        for provider, sender in senders.items():
            try:
                sender(title, message)
                results.append(NotificationResult(provider=provider, success=True))
            except Exception as exc:
                logger.error("Failed to send %s notification: %s", provider, exc)
                results.append(
                    NotificationResult(
                        provider=provider,
                        success=False,
                        error=str(exc),
                    )
                )
        return results
