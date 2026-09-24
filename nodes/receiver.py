"""One Telegram update per workflow run; ComfyUI Instant mode owns requeueing.

Inspired by AKharytonchyk/ComfyUI-telegram-bot-node (MIT). Uses the suite's
httpx transport instead of a second polling/event-loop framework.
"""

import atexit
import hashlib
import heapq
import logging
import math
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import httpx
from comfy_execution.graph import ExecutionBlocker
from comfy.model_management import throw_exception_if_processing_interrupted

from .client import TelegramClient, TelegramException, validate_token
from .utils import audio_from_bytes, image_from_bytes

log = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 10
MAX_PENDING_MESSAGES = 1000
RECEIVER_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0, 2.0, 5.0)
# Consecutive outage budget, including requests and backoff. A successful poll
# resets it; an idle workflow rerun does not.
RECEIVER_RETRY_TIMEOUT_SECONDS = 90.0
MEDIA_MAX_ATTEMPTS = 5


def _retry_delay(failures, retry_after=None):
    delay = RECEIVER_RETRY_DELAYS_SECONDS[min(failures - 1, len(RECEIVER_RETRY_DELAYS_SECONDS) - 1)]
    try:
        value = float(retry_after)
        if math.isfinite(value):
            delay = max(delay, value)
    except (TypeError, ValueError):
        pass
    return delay


def _parse_chat_filter(access_mode, chat_ids):
    mode = str(access_mode).strip().lower()
    if mode not in {"whitelist", "blacklist"}:
        raise ValueError("access_mode must be whitelist or blacklist.")
    ids = set()
    for value in re.split(r"[\s,;]+", str(chat_ids).strip()):
        if not value:
            continue
        if not re.fullmatch(r"-?[0-9]+", value):
            # Fail closed: silently ignoring a malformed blacklist could allow
            # somebody the owner intended to block. Validate before consuming.
            raise ValueError("chat_ids must contain numeric ChatIDs separated by spaces, commas, semicolons or newlines.")
        ids.add(int(value))
    return mode, ids


def _message_kind(message):
    if message.get("photo"):
        return "image", max(message["photo"], key=lambda p: p.get("width", 0) * p.get("height", 0))
    for name, kind in (("video", "video"), ("animation", "video"), ("audio", "audio"), ("voice", "audio")):
        if message.get(name):
            return kind, message[name]
    document = message.get("document") or {}
    mime = document.get("mime_type", "")
    if mime.startswith("image/"):
        return "image", document
    if mime.startswith("video/"):
        return "video", document
    if mime.startswith("audio/"):
        return "audio", document
    if message.get("text") is not None:
        return "text", None
    if message.get("caption"):
        return "text", None
    return None, None


class _Inbox:
    """One background long poller and a bounded, synchronized inbox per token.

    Polling continues while the workflow processes a message. Network requests
    never hold the queue lock, so an idle receiver remains easy to cancel.
    """

    def __init__(self, client):
        self.client = client
        self.offset = None
        self.pending = deque()
        self.deferred = []
        self._sequence = 0
        self.condition = threading.Condition()
        self.stopping = threading.Event()
        self.thread = None
        self.fatal_error = None
        self._error_reported = False
        self.retry_at = 0.0
        self.last_warning = -math.inf

    def start(self):
        with self.condition:
            if (self.thread is None or not self.thread.is_alive()) and not self.stopping.is_set():
                # Deliver a background failure once before permitting a restart.
                # Otherwise the next automatic Instant run could silently reset
                # the outage budget and retry forever again.
                if self.fatal_error is not None and not self._error_reported:
                    return
                # A permanent API/configuration error stops Instant visibly.
                # After fixing it, pressing Run again should recover without
                # requiring the entire ComfyUI process to be restarted.
                self.fatal_error = None
                self._error_reported = False
                self.last_warning = -math.inf
                self.thread = threading.Thread(target=self._run, name="TelegramInbox", daemon=True)
                self.thread.start()

    def close(self):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()

    def _run(self):
        failures = 0
        failure_started = None

        def retry_budget():
            if failure_started is None:
                return math.inf
            remaining = RECEIVER_RETRY_TIMEOUT_SECONDS - (time.monotonic() - failure_started)
            if remaining <= 0:
                error = TelegramException(
                    f"Telegram polling stopped after {RECEIVER_RETRY_TIMEOUT_SECONDS:g} seconds "
                    "of connection/API failures. Restore the connection and press Run to "
                    "restart the listener; restarting ComfyUI is not necessary."
                )
                log.warning("Telegram receiver: %s", error)
                raise error
            return remaining

        try:
            while not self.stopping.is_set():
                remaining_budget = retry_budget()
                with self.condition:
                    capacity = MAX_PENDING_MESSAGES - len(self.pending) - len(self.deferred)
                    if capacity <= 0:
                        # Backpressure, not silent eviction. Leave additional
                        # updates on Telegram until there is queue space.
                        self.condition.wait(0.1)
                        continue
                started = time.monotonic()
                try:
                    # Bound all four HTTP phases by the remaining outage budget.
                    # Shorten Telegram's server wait too, so it fits the read.
                    phase_limit = remaining_budget / 4
                    read_timeout = min(POLL_TIMEOUT_SECONDS + 5.0, phase_limit)
                    updates = self.client("getUpdates", {
                        "offset": self.offset,
                        "timeout": min(POLL_TIMEOUT_SECONDS, max(0, int(read_timeout - 1))),
                        "limit": min(100, capacity), "allowed_updates": ["message"],
                    }, timeout=httpx.Timeout(
                        read=read_timeout,
                        connect=min(10.0, 3.0 + 2.0 * failures, phase_limit),
                        write=min(10.0, phase_limit), pool=min(3.0, phase_limit),
                    ))
                    if not isinstance(updates, list) or any(
                        not isinstance(item, dict) or not isinstance(item.get("update_id"), int)
                        for item in updates
                    ):
                        raise TelegramException("Telegram getUpdates: malformed update batch.", transient=True)
                except TelegramException as exc:
                    if exc.code == 409:
                        raise TelegramException(
                            "Telegram polling conflict: use one listener per bot and stop other "
                            "getUpdates clients. If this bot has a webhook, remove it first "
                            "with deleteWebhook (drop_pending_updates=false).", code=409,
                        ) from None
                    if not exc.transient:
                        raise
                    if failure_started is None:
                        failure_started = started
                    failures += 1
                    # A long retry_after never triggers an early retry: if it
                    # exceeds the budget, wait only until the budget expires.
                    delay = min(_retry_delay(failures, exc.retry_after), retry_budget())
                    self.retry_at = time.monotonic() + delay
                    if time.monotonic() - self.last_warning >= 30:
                        log.warning("Telegram receiver: %s; retrying within the %g-second outage limit.",
                                    exc, RECEIVER_RETRY_TIMEOUT_SECONDS)
                        self.last_warning = time.monotonic()
                    if self.stopping.wait(delay):
                        return
                    continue
                failures = 0
                failure_started = None
                self.retry_at = 0.0
                with self.condition:
                    for update in sorted(updates, key=lambda item: item["update_id"]):
                        update_id = update["update_id"]
                        if self.offset is not None and update_id < self.offset:
                            continue
                        message = update.get("message")
                        if message and _message_kind(message)[0] is not None:
                            self.pending.append(message)
                        # Store first, then acknowledge with the next poll.
                        self.offset = update_id + 1
                    self.condition.notify_all()
                # A real long poll sleeps at the server. Guard against a broken
                # endpoint returning empty responses immediately, without adding
                # a delay when any messages were returned.
                if not updates:
                    self.stopping.wait(max(0, 0.05 - (time.monotonic() - started)))
        except Exception as exc:
            with self.condition:
                self.fatal_error = exc if isinstance(exc, TelegramException) else TelegramException(
                    f"Telegram receiver stopped ({type(exc).__name__}); check the configuration and press Run again."
                )
                self._error_reported = False
                self.retry_at = 0.0
                self.condition.notify_all()

    def defer(self, message, delay):
        """Keep a failed download without blocking other users behind it."""
        with self.condition:
            self._sequence += 1
            heapq.heappush(self.deferred, (time.monotonic() + delay, self._sequence, message))
            self.condition.notify_all()

    def receive(self, timeout_time):
        throw_exception_if_processing_interrupted()
        self.start()
        deadline = time.monotonic() + timeout_time
        with self.condition:
            while True:
                throw_exception_if_processing_interrupted()
                if self.fatal_error is not None:
                    self._error_reported = True
                    raise self.fatal_error
                # Retry ready downloads ahead of fresh work, but never wait for
                # a delayed attachment while another user's message is ready.
                if self.deferred and self.deferred[0][0] <= time.monotonic():
                    message = heapq.heappop(self.deferred)[2]
                    self.condition.notify_all()
                    return message
                if self.pending:
                    message = self.pending.popleft()
                    self.condition.notify_all()
                    return message
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.stopping.is_set():
                    return None
                if self.deferred:
                    remaining = min(remaining, max(0, self.deferred[0][0] - time.monotonic()))
                self.condition.wait(min(0.1, remaining))


_INBOXES = {}
_INBOXES_LOCK = threading.Lock()


def _get_inbox(bot_token):
    token = validate_token(bot_token)
    key = hashlib.sha256(token.encode()).hexdigest()
    with _INBOXES_LOCK:
        if key not in _INBOXES:
            _INBOXES[key] = _Inbox(TelegramClient(token, role="poll"))
        return _INBOXES[key]


def close_inboxes():
    with _INBOXES_LOCK:
        for inbox in _INBOXES.values():
            inbox.close()
        _INBOXES.clear()


atexit.register(close_inboxes)


def _save_video(data, remote_path):
    import folder_paths
    directory = Path(folder_paths.get_temp_directory()) / "telegram-autonomous"
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(remote_path).suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".gif", ".avi", ".m4v"}:
        suffix = ".mp4"
    # Never use a Telegram-provided filename as a local path.
    path = directory / f"received-{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return (False, [str(path)])


class TelegramListener:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": ("STRING", {
                    "default": "", "multiline": False,
                    "placeholder": "Enter your Telegram bot token here",
                }),
                "timeout_time": ("INT", {
                    "default": 10, "min": 1, "max": 300, "step": 1,
                    "tooltip": "Wait per run in seconds. Idle runs skip downstream nodes; Instant queues the next run.",
                }),
                "access_mode": (["blacklist", "whitelist"], {
                    "default": "blacklist",
                    "tooltip": "Blacklist rejects listed chats. Whitelist only accepts listed chats.",
                }),
                "chat_ids": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "Numeric ChatIDs, separated by newlines, spaces, commas or semicolons. Keep the minus sign for groups. Empty blacklist allows everyone; empty whitelist allows nobody.",
                }),
            },
        }

    # Preserve the original two output positions; add the requested image socket.
    RETURN_TYPES = ("STRING", "INT", "IMAGE", "VHS_FILENAMES", "AUDIO", "INT", "INT", "STRING")
    RETURN_NAMES = ("message_text", "chat_id", "message_image", "message_video", "message_audio",
                    "message_id", "message_thread_id", "message_type")
    FUNCTION = "listen_for_message"
    CATEGORY = "Autonomous Telegram Bot ◀️"
    OUTPUT_NODE = True
    NOT_IDEMPOTENT = True
    DESCRIPTION = (
        "One incoming Telegram message per run. Select Run (Instant), batch count 1. "
        "Connect chat_id directly to senders. Text includes captions and commands. "
        "Messages are buffered during generation. Filtered chats, missing media "
        "and idle timeouts quietly block their dependent branches."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # NaN is unequal to itself. ComfyUI must poll again even with identical
        # widget values, and the receiver's dependent nodes become fresh too.
        return float("nan")

    def listen_for_message(self, bot_token, timeout_time=10, access_mode="blacklist", chat_ids=""):
        mode, ids = _parse_chat_filter(access_mode, chat_ids)
        inbox = _get_inbox(bot_token)
        message = inbox.receive(max(1, min(300, int(timeout_time))))
        if message is None:
            # Do NOT raise a timeout error: Instant stops on execution errors.
            # Do NOT emit a dummy chat/image: that could trigger an unwanted send.
            return tuple(ExecutionBlocker(None) for _ in self.RETURN_TYPES)

        chat_id = int(message["chat"]["id"])
        if (chat_id in ids) == (mode == "blacklist"):
            log.warning("Telegram receiver: rejected ChatID %s (%s); discarded message, continuing workflow.", chat_id, mode)
            return tuple(ExecutionBlocker(None) for _ in self.RETURN_TYPES)

        kind, media = _message_kind(message)
        image = ExecutionBlocker(None)
        video = ExecutionBlocker(None)
        audio = ExecutionBlocker(None)
        if media:
            try:
                data, file_path = inbox.client.download(media["file_id"])
                throw_exception_if_processing_interrupted()
            except TelegramException as exc:
                attempts = message.get("_download_attempts", 0) + 1
                message["_download_attempts"] = attempts
                if exc.transient and attempts < MEDIA_MAX_ATTEMPTS:
                    inbox.defer(message, _retry_delay(attempts, exc.retry_after))
                    log.warning("Telegram receiver: download for ChatID %s failed (%s/%s); retained for retry. %s",
                                chat_id, attempts, MEDIA_MAX_ATTEMPTS, exc)
                else:
                    log.warning("Telegram receiver: skipping unavailable media for ChatID %s after %s attempt(s). %s",
                                chat_id, attempts, exc)
                return tuple(ExecutionBlocker(None) for _ in self.RETURN_TYPES)
            except BaseException:
                # A cancelled download must not consume the user's job. Do not
                # swallow ComfyUI's interruption; retain the item for a restart.
                inbox.defer(message, 0)
                raise
            if kind == "image":
                image = image_from_bytes(data)
            elif kind == "video":
                video = _save_video(data, file_path)
            elif kind == "audio":
                audio = audio_from_bytes(data)
        return (
            message.get("text") or message.get("caption") or "",
            chat_id, image, video, audio,
            int(message["message_id"]), int(message.get("message_thread_id") or -1), kind,
        )
