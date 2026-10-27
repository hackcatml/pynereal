from urllib.parse import urlsplit, urlunsplit


def configure_ccxt_ws(exchange) -> None:
    """Migrate legacy OKX WebSocket URLs without changing REST or proxy URLs."""
    if exchange.id != "okx":
        return

    # Patch both environments before set_sandbox_mode() copies the test URLs.
    # https://www.okx.com/docs-v5/log_en/#upcoming-changes-websocket-port-8443-discontinuation
    for environment in ("api", "test"):
        urls = exchange.urls.get(environment)
        if not isinstance(urls, dict) or not isinstance(urls.get("ws"), str):
            continue
        parts = urlsplit(urls["ws"])
        host = parts.hostname or ""
        if (
            parts.scheme == "wss"
            and (host == "okx.com" or host.endswith(".okx.com"))
            and parts.port == 8443
        ):
            urls["ws"] = urlunsplit(parts._replace(netloc=parts.netloc.rsplit(":", 1)[0]))
