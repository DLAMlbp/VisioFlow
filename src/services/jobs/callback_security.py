from urllib.parse import urlsplit


class CallbackConfigurationError(ValueError):
    pass


def validate_callback_destination(
    callback_url: str,
    *,
    production: bool,
    allowed_hosts: str,
) -> None:
    parsed = urlsplit(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CallbackConfigurationError("callback_url 必须是有效的 http/https URL")
    if production and parsed.scheme != "https":
        raise CallbackConfigurationError("生产环境 callback_url 必须使用 HTTPS")
    hostname = (parsed.hostname or "").lower()
    allowed = {host.strip().lower() for host in allowed_hosts.split(",") if host.strip()}
    if allowed and hostname not in allowed:
        raise CallbackConfigurationError("callback_url 主机不在允许列表中")
