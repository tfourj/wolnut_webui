from wolnut.providers._http import post_json, validate_http_url


def send(
    server_url: str,
    topic: str,
    token: str,
    title: str,
    message: str,
    priority: int = 3,
) -> None:
    url = validate_http_url(server_url, "ntfy server URL").rstrip("/")
    topic = topic.strip()
    if not topic:
        raise ValueError("ntfy topic is required")

    headers = {}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    post_json(
        url,
        {
            "topic": topic,
            "title": title,
            "message": message,
            "priority": priority,
        },
        headers=headers,
    )
