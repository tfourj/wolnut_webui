import json
from urllib import error

from wolnut.config import (
    DiscordNotificationConfig,
    GotifyNotificationConfig,
    NotificationsConfig,
)
from wolnut.notifications import NotificationService, send_discord, send_gotify


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None


def test_send_discord_posts_embed(mocker):
    urlopen = mocker.patch(
        "wolnut.notifications.request.urlopen",
        return_value=FakeResponse(),
    )

    send_discord("https://discord.com/api/webhooks/1/token", "Wake sent", "server")

    req = urlopen.call_args.args[0]
    payload = json.loads(req.data)
    assert req.full_url == "https://discord.com/api/webhooks/1/token"
    assert payload["embeds"][0]["title"] == "Wake sent"
    assert payload["embeds"][0]["description"] == "server"


def test_send_gotify_posts_message_and_token(mocker):
    urlopen = mocker.patch(
        "wolnut.notifications.request.urlopen",
        return_value=FakeResponse(),
    )

    send_gotify("https://gotify.example.com/", "secret token", "Error", "Failed", 8)

    req = urlopen.call_args.args[0]
    payload = json.loads(req.data)
    assert req.full_url == "https://gotify.example.com/message?token=secret+token"
    assert payload == {"title": "Error", "message": "Failed", "priority": 8}


def test_service_sends_to_enabled_providers(mocker):
    discord = mocker.patch("wolnut.notifications.send_discord")
    gotify = mocker.patch("wolnut.notifications.send_gotify")
    config = NotificationsConfig(
        discord=DiscordNotificationConfig(
            enabled=True,
            webhook_url="https://discord.example/hook",
        ),
        gotify=GotifyNotificationConfig(
            enabled=True,
            url="https://gotify.example",
            token="token",
            priority=7,
        ),
    )

    results = NotificationService(config).send("wake_sent", "Wake sent", "server")

    assert all(result.success for result in results)
    discord.assert_called_once_with("https://discord.example/hook", "Wake sent", "server")
    gotify.assert_called_once_with(
        "https://gotify.example",
        "token",
        "Wake sent",
        "server",
        7,
    )


def test_service_skips_disabled_event(mocker):
    discord = mocker.patch("wolnut.notifications.send_discord")
    config = NotificationsConfig(
        discord=DiscordNotificationConfig(
            enabled=True,
            webhook_url="https://discord.example/hook",
        ),
    )
    config.events.errors = False

    results = NotificationService(config).send("errors", "Error", "Failed")

    assert results == []
    discord.assert_not_called()


def test_provider_failures_do_not_stop_other_providers(mocker):
    mocker.patch(
        "wolnut.notifications.send_discord",
        side_effect=RuntimeError("discord unavailable"),
    )
    gotify = mocker.patch("wolnut.notifications.send_gotify")
    config = NotificationsConfig(
        discord=DiscordNotificationConfig(
            enabled=True,
            webhook_url="https://discord.example/hook",
        ),
        gotify=GotifyNotificationConfig(
            enabled=True,
            url="https://gotify.example",
            token="token",
        ),
    )

    results = NotificationService(config).send("errors", "Error", "Failed")

    assert results[0].success is False
    assert results[0].error == "discord unavailable"
    assert results[1].success is True
    gotify.assert_called_once()


def test_http_errors_do_not_expose_provider_credentials(mocker):
    url = "https://discord.example/webhook-secret"
    mocker.patch(
        "wolnut.notifications.request.urlopen",
        side_effect=error.HTTPError(url, 401, "Unauthorized", {}, None),
    )

    config = NotificationsConfig(
        discord=DiscordNotificationConfig(enabled=True, webhook_url=url),
    )
    result = NotificationService(config).send_test("discord")

    assert result.success is False
    assert result.error == "notification provider returned HTTP 401"
    assert "webhook-secret" not in result.error
