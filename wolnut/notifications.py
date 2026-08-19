import json
import logging
from dataclasses import dataclass
from typing import Callable
from urllib import parse, request

from wolnut.config import NotificationsConfig

logger = logging.getLogger("wolnut")

NotificationSender = Callable[[str, str], None]


@dataclass
class NotificationResult:
    provider: str
    success: bool
    error: str | None = None


def _validate_http_url(value: str, field_name: str) -> str:
    value = value.strip()
    parsed = parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid http or https URL")
    return value


def _post_json(url: str, payload: dict, timeout: int = 10) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Wolnut"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise RuntimeError(f"notification provider returned HTTP {status}")


def send_discord(webhook_url: str, title: str, message: str) -> None:
    url = _validate_http_url(webhook_url, "Discord webhook URL")
    _post_json(
        url,
        {
            "username": "Wolnut",
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": 5213439,
                }
            ],
        },
    )


def send_gotify(
    server_url: str,
    token: str,
    title: str,
    message: str,
    priority: int = 5,
) -> None:
    base_url = _validate_http_url(server_url, "Gotify server URL").rstrip("/")
    if not token.strip():
        raise ValueError("Gotify app token is required")
    endpoint = f"{base_url}/message?{parse.urlencode({'token': token.strip()})}"
    _post_json(
        endpoint,
        {
            "title": title,
            "message": message,
            "priority": priority,
        },
    )


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
