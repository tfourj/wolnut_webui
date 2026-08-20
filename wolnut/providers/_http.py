import json
from urllib import error, parse, request


def validate_http_url(value: str, field_name: str) -> str:
    value = value.strip()
    parsed = parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid http or https URL")
    return value


def post_json(
    url: str,
    payload: dict,
    timeout: int = 10,
    headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Wolnut",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise RuntimeError(f"notification provider returned HTTP {status}")
    except error.HTTPError as exc:
        raise RuntimeError(
            f"notification provider returned HTTP {exc.code}"
        ) from None
    except error.URLError as exc:
        raise RuntimeError(f"notification request failed: {exc.reason}") from None
