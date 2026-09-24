"""Retry outgoing API requests without turning an outage into a queue error.

Only senders, editors, chat actions and API Method use this client and guard.
Successful sends have no artificial delay. Early retries are fast, with more
time allowed on later attempts and for media uploads/server processing.
"""

import functools
import inspect
import logging
import math
import time

import httpx
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy_execution.graph import ExecutionBlocker

from .client import TelegramClient, TelegramException

log = logging.getLogger(__name__)

# Includes the first request: 5 attempts = 1 initial request + 4 retries.
SENDER_MAX_ATTEMPTS = 5
SENDER_CONNECT_TIMEOUT_SECONDS = (3.0, 5.0, 10.0, 10.0, 10.0)
SENDER_READ_TIMEOUT_SECONDS = (10.0, 15.0, 30.0, 30.0, 30.0)
SENDER_MEDIA_READ_TIMEOUT_SECONDS = 120.0
SENDER_WRITE_TIMEOUT_SECONDS = 120.0
SENDER_POOL_TIMEOUT_SECONDS = 3.0
SENDER_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0, 2.0)
_MEDIA_METHODS = {
    "sendPhoto", "sendVideo", "sendAnimation", "sendDocument", "sendAudio",
    "sendVoice", "sendVideoNote", "sendMediaGroup", "editMessageMedia",
    "sendSticker", "sendLivePhoto",
}


class SenderRetriesExhausted(TelegramException):
    """Handled at the node boundary; never exposed as a ComfyUI queue error."""


def _wait_before_retry(seconds):
    deadline = time.monotonic() + seconds
    while True:
        throw_exception_if_processing_interrupted()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _retry_delay(attempt, retry_after):
    delay = SENDER_RETRY_DELAYS_SECONDS[min(attempt - 1, len(SENDER_RETRY_DELAYS_SECONDS) - 1)]
    try:
        telegram_delay = float(retry_after)
        if math.isfinite(telegram_delay):
            delay = max(delay, telegram_delay)
    except (TypeError, ValueError):
        pass
    return delay


class RetryingTelegramClient(TelegramClient):
    def __call__(self, method_name, params=None, files=None, *, timeout=None):
        # Retry the current HTTP request, not the whole node. In particular, an
        # individually sent image batch must not resend earlier successful items.
        for attempt in range(1, SENDER_MAX_ATTEMPTS + 1):
            throw_exception_if_processing_interrupted()
            request_timeout = timeout or httpx.Timeout(
                connect=SENDER_CONNECT_TIMEOUT_SECONDS[min(attempt - 1, len(SENDER_CONNECT_TIMEOUT_SECONDS) - 1)],
                read=SENDER_MEDIA_READ_TIMEOUT_SECONDS if files or method_name in _MEDIA_METHODS
                else SENDER_READ_TIMEOUT_SECONDS[min(attempt - 1, len(SENDER_READ_TIMEOUT_SECONDS) - 1)],
                write=SENDER_WRITE_TIMEOUT_SECONDS,
                pool=SENDER_POOL_TIMEOUT_SECONDS,
            )
            try:
                return super().__call__(method_name, params=params, files=files, timeout=request_timeout)
            except TelegramException as exc:
                throw_exception_if_processing_interrupted()
                if not exc.transient:
                    raise
                if attempt == SENDER_MAX_ATTEMPTS:
                    raise SenderRetriesExhausted(
                        f"{method_name} failed after {attempt} attempts. Last failure: {exc}",
                        code=exc.code, transient=True,
                    ) from None
                delay = _retry_delay(attempt, exc.retry_after)
                log.warning(
                    "[Telegram Autonomous] %s (attempt %d/%d); retrying in %g seconds.",
                    exc, attempt, SENDER_MAX_ATTEMPTS, delay,
                )
                _wait_before_retry(delay)


def sender_network_guard(function):
    """Keep failure results honest, and keep a supplied trigger moving forward."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def guarded(self, *args, **kwargs):
        arguments = signature.bind(self, *args, **kwargs).arguments
        try:
            return function(self, *args, **kwargs)
        except SenderRetriesExhausted as exc:
            # Logging alone does not set ComfyUI's lastExecutionError. A silent
            # blocker prevents downstream edits from using an invalid message ID.
            log.warning(
                "[Telegram Autonomous] %s: %s Skipping the failed request; continuing workflow.",
                type(self).__name__, exc,
            )
            return tuple(
                arguments.get("trigger") if name == "trigger"
                else False if kind in {"BOOL", "BOOLEAN"}
                else ExecutionBlocker(None)
                for name, kind in zip(self.RETURN_NAMES, self.RETURN_TYPES)
            )

    return guarded
