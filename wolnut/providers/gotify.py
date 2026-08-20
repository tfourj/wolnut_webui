from urllib import parse

from wolnut.providers._http import post_json, validate_http_url


def send(
    server_url: str,
    token: str,
    title: str,
    message: str,
    priority: int = 5,
) -> None:
    base_url = validate_http_url(server_url, "Gotify server URL").rstrip("/")
    if not token.strip():
        raise ValueError("Gotify app token is required")
    endpoint = f"{base_url}/message?{parse.urlencode({'token': token.strip()})}"
    post_json(
        endpoint,
        {
            "title": title,
            "message": message,
            "priority": priority,
        },
    )
