from wolnut.providers._http import post_json, validate_http_url


def send(webhook_url: str, title: str, message: str) -> None:
    url = validate_http_url(webhook_url, "Discord webhook URL")
    post_json(
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
