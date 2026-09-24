"""Small synchronous Telegram transport, using the suite's existing httpx dependency."""

import atexit
import hashlib
import json
import logging
import re
import threading

import httpx

DEFAULT_API_URL = "https://api.telegram.org"

# Nodes are reconstructed between runs. Keep connections outside node instances,
# and give long polling, uploads and downloads independent connection pools.
_HTTP_CLIENTS = {}
_HTTP_CLIENTS_LOCK = threading.Lock()


def _get_http_client(api_url, token, role):
    key = (api_url, hashlib.sha256(token.encode()).digest(), role)
    with _HTTP_CLIENTS_LOCK:
        client = _HTTP_CLIENTS.get(key)
        if client is None or client.is_closed:
            client = httpx.Client(limits=httpx.Limits(
                max_connections=8, max_keepalive_connections=8, keepalive_expiry=60.0,
            ))
            _HTTP_CLIENTS[key] = client
        return client


def close_http_clients():
    with _HTTP_CLIENTS_LOCK:
        clients = list(_HTTP_CLIENTS.values())
        _HTTP_CLIENTS.clear()
    for client in clients:
        client.close()


atexit.register(close_http_clients)


class _TelegramLogFilter(logging.Filter):
    def filter(self, record):
        text = record.getMessage()
        safe = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot<hidden>", text)
        if safe != text:
            record.msg, record.args = safe, ()
        return True


# httpx INFO request logs otherwise expose the token embedded in the API URL.
logging.getLogger("httpx").addFilter(_TelegramLogFilter())


class TelegramException(RuntimeError):
    """An API error whose public message never contains the bot token or URL."""

    def __init__(self, message, code=None, retry_after=None, transient=False):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        self.transient = transient


def validate_token(token):
    token = str(token).strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Enter a valid Telegram bot_token from BotFather (digits:token).")
    return token


class TelegramClient:
    def __init__(self, token, api_url=DEFAULT_API_URL, *, role="send"):
        self.token = validate_token(token)
        self.api_url = api_url.rstrip("/")
        self.base_url = f"{self.api_url}/bot{self.token}"
        self.role = role

    def _safe_text(self, value):
        return str(value).replace(self.token, "<hidden>").replace(self.base_url, "<telegram>")

    def __call__(self, method_name, params=None, files=None, *, timeout=None):
        # Preserve False, zero and the empty string: an empty caption clears it.
        payload = {
            key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if isinstance(value, (dict, list, bool)) else value
            for key, value in (params or {}).items()
            if value is not None
        }
        request_timeout = timeout or httpx.Timeout(15.0, connect=3.0, write=60.0, pool=3.0)
        try:
            response = _get_http_client(self.api_url, self.token, self.role).post(
                f"{self.base_url}/{method_name}",
                data=payload or None, files=files or None, timeout=request_timeout,
            )
        except httpx.RequestError as exc:
            # Do not chain HTTP exceptions: their URLs include the secret token.
            # Retry policy belongs to the caller; this transport does one attempt.
            raise TelegramException(
                f"Telegram {method_name}: network request failed ({type(exc).__name__}).",
                transient=True,
            ) from None
        try:
            result = response.json()
        except (ValueError, UnicodeError):
            raise TelegramException(
                f"Telegram {method_name}: invalid API response (HTTP {response.status_code}).",
                code=response.status_code,
                transient=response.status_code in {200, 408, 429} or response.status_code >= 500,
                retry_after=response.headers.get("Retry-After"),
            ) from None
        if not isinstance(result, dict) or not result.get("ok"):
            body = result if isinstance(result, dict) else {}
            code = body.get("error_code", response.status_code)
            if not isinstance(code, int):
                code = response.status_code
            description = self._safe_text(body.get("description", "request rejected"))
            parameters = body.get("parameters")
            parameters = parameters if isinstance(parameters, dict) else {}
            raise TelegramException(
                f"Telegram {method_name}: {description} (code {code}).",
                code=code, retry_after=parameters.get("retry_after", response.headers.get("Retry-After")),
                transient=code in {200, 408, 429} or code >= 500,
            )
        if "result" not in result:
            raise TelegramException(f"Telegram {method_name}: incomplete API response.", transient=True)
        return result["result"]

    def download(self, file_id):
        info = self("getFile", {"file_id": file_id})
        if not isinstance(info, dict):
            raise TelegramException("Telegram getFile: malformed file response.", transient=True)
        file_path = info.get("file_path")
        if not file_path:
            raise TelegramException("Telegram did not provide a downloadable file path.")
        try:
            response = _get_http_client(self.api_url, self.token, "download").get(
                f"{self.api_url}/file/bot{self.token}/{file_path}",
                timeout=httpx.Timeout(60.0, connect=5.0, pool=3.0),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise TelegramException(
                f"Telegram file download failed (HTTP {code}).", code=code,
                transient=code in {408, 429} or code >= 500,
                retry_after=exc.response.headers.get("Retry-After"),
            ) from None
        except httpx.RequestError as exc:
            raise TelegramException(
                f"Telegram file download failed ({type(exc).__name__}).", transient=True,
            ) from None
        return response.content, file_path
