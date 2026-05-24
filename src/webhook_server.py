"""Local webhook receiver for phone SMS/call forwarders.

The phone posts incoming SMS and call events to this server. Providers read
those events from thread-safe queues and feed the existing parser flow.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger


def _first(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", {}, []):
            return value
    return default


_RU_DT_RX = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?")
_FORWARD_SMS_FOOTER_RX = re.compile(
    r"(?P<body>.*?)"
    r"(?:\r?\n|\s{2,})"
    r"@(?P<sender>[A-Za-zА-Яа-я0-9._-]{2,80})"
    r"(?:\s+#(?P<tag>\S+))?"
    r"(?:\s+\((?P<dt>\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)\))?"
    r"\s*$",
    re.DOTALL,
)
_PHONE_RX = re.compile(r"\+?\d(?:[\s().-]*\d){8,14}")


def _local_tz() -> ZoneInfo:
    name = os.getenv("PARSER_TIMEZONE", "Europe/Moscow")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Moscow")


def _to_local_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(_local_tz()).replace(tzinfo=None)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        # Mobile forwarders often send milliseconds.
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=_local_tz()).replace(tzinfo=None)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.isdigit():
            return _parse_dt(int(raw))
        m = _RU_DT_RX.search(raw)
        if m:
            day, month, year, hour, minute, second = m.groups()
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
                int(second or 0),
            )
        try:
            return _to_local_naive(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            pass
    return datetime.now(_local_tz()).replace(tzinfo=None)


def _flatten_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested webhook JSON and make key lookup tolerant."""
    flat: dict[str, Any] = {}

    def walk(obj: Any, prefix: str = "") -> None:
        if not isinstance(obj, dict):
            return
        for key, value in obj.items():
            key_s = str(key)
            aliases = {key_s, key_s.lower()}
            if prefix:
                aliases.add(f"{prefix}.{key_s}")
                aliases.add(f"{prefix}.{key_s}".lower())
            for alias in aliases:
                flat.setdefault(alias, value)
            if isinstance(value, dict):
                walk(value, f"{prefix}.{key_s}" if prefix else key_s)

    walk(data)
    return flat


def _try_parse_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return None


def _extract_forward_sms_footer(text: str) -> tuple[str, str | None, str | None]:
    """Forward SMS on iPhone can append: @alpha #code (23.05.2026 21:38)."""
    m = _FORWARD_SMS_FOOTER_RX.match(text or "")
    if not m:
        return text, None, None
    body = (m.group("body") or "").strip()
    sender = (m.group("sender") or "").strip() or None
    dt = (m.group("dt") or "").strip() or None
    return body or text, sender, dt


def _is_generic_sender(sender: str) -> bool:
    s = (sender or "").strip().lower()
    return (
        not s
        or s == "unknown"
        or s.startswith("sim ")
        or s in {"sms", "iphone", "forward sms", "forwarded sms"}
    )


def _parse_duration(value: Any) -> int:
    if isinstance(value, str):
        raw = value.strip()
        if ":" in raw:
            parts = [p for p in raw.split(":") if p.strip().isdigit()]
            if len(parts) == 3:
                h, m, s = [int(p) for p in parts]
                return h * 3600 + m * 60 + s
            if len(parts) == 2:
                m, s = [int(p) for p in parts]
                return m * 60 + s
    try:
        seconds = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return seconds


def _parse_duration_millis(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)) // 1000)
    except (TypeError, ValueError):
        return 0


def _normalize_phoneish(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    for start, ch in enumerate(raw):
        if not (ch == "+" or ch.isdigit()):
            continue
        digits = ""
        for cur in raw[start:]:
            if cur.isdigit():
                digits += cur
                if len(digits) == 11 and digits[0] in "78":
                    return "+" + ("7" + digits[1:] if digits[0] == "8" else digits)
                if len(digits) == 10 and digits[0] == "9":
                    return "+7" + digits
            elif cur in "+ ().-":
                continue
            else:
                break
    matches = list(_PHONE_RX.finditer(raw))
    if not matches:
        return raw
    preferred = None
    for match in matches:
        digits = re.sub(r"\D", "", match.group(0))
        if 10 <= len(digits) <= 11 and digits[0] in "789":
            preferred = match.group(0)
            break
    if preferred is None:
        preferred = matches[0].group(0)
    digits = re.sub(r"\D", "", preferred)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return f"+{digits}" if len(digits) >= 10 else raw


def _call_summary(data: dict[str, Any], status: str, direction: str) -> str:
    pieces: list[str] = []
    if direction:
        pieces.append(direction)
    if status:
        pieces.append(status)
    raw_text = str(_first(
        data,
        "text",
        "message",
        "body",
        "content",
        "title",
        "subtitle",
        default="",
    ) or "").strip()
    if raw_text and raw_text not in pieces:
        pieces.append(raw_text[:140])
    return " | ".join(pieces)


def _normalize_android_call_type(value: str) -> tuple[str, str]:
    raw = (value or "").strip().lower()
    mapping = {
        "1": ("incoming", "answered"),
        "incoming": ("incoming", "answered"),
        "incoming_type": ("incoming", "answered"),
        "2": ("outgoing", "answered"),
        "outgoing": ("outgoing", "answered"),
        "outgoing_type": ("outgoing", "answered"),
        "3": ("incoming", "missed"),
        "missed": ("incoming", "missed"),
        "missed_type": ("incoming", "missed"),
        "5": ("incoming", "rejected"),
        "rejected": ("incoming", "rejected"),
        "6": ("incoming", "blocked"),
        "blocked": ("incoming", "blocked"),
    }
    return mapping.get(raw, (value, ""))


@dataclass(frozen=True)
class WebhookConfig:
    host: str
    port: int
    token: str = ""


class WebhookReceiver:
    def __init__(self, config: WebhookConfig) -> None:
        self.config = config
        self.sms_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.call_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def public_base_url(self) -> str:
        host = self.config.host
        if host in ("0.0.0.0", "::"):
            host = _local_ip()
        return f"http://{host}:{self.config.port}"

    def start(self) -> None:
        if self._server:
            return

        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib API
                if self.path.startswith("/health"):
                    self._send_json(200, {"ok": True})
                    return
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "sms": "/sms",
                        "call": "/call",
                    },
                )

            def do_POST(self) -> None:  # noqa: N802 - stdlib API
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                data = self._parse_body(body)
                query = parse_qs(parsed.query)

                if receiver.config.token:
                    token = (
                        self.headers.get("X-Webhook-Token")
                        or data.get("token")
                        or (query.get("token") or [""])[0]
                    )
                    if token != receiver.config.token:
                        self._send_json(401, {"ok": False, "error": "bad token"})
                        return

                if parsed.path == "/sms":
                    event = normalize_sms_payload(data)
                    receiver.sms_queue.put(event)
                    logger.info(
                        f"Webhook SMS: {event['sender']} — {event['text'][:80]}"
                    )
                    self._send_json(200, {"ok": True, "type": "sms"})
                    return

                if parsed.path == "/call":
                    event = normalize_call_payload(data)
                    receiver.call_queue.put(event)
                    logger.info(
                        f"Webhook CALL: {event['caller']} ({event['duration_sec']}s)"
                    )
                    self._send_json(200, {"ok": True, "type": "call"})
                    return

                self._send_json(404, {"ok": False, "error": "unknown path"})

            def log_message(self, fmt: str, *args: Any) -> None:
                logger.debug("webhook: " + (fmt % args))

            def _parse_body(self, body: bytes) -> dict[str, Any]:
                if not body:
                    return {}
                content_type = self.headers.get("Content-Type", "")
                raw = body.decode("utf-8", errors="replace")
                parsed_json = _try_parse_json(raw)
                if parsed_json is not None:
                    return parsed_json
                if "application/json" in content_type:
                    return {"text": raw}
                parsed_form = {k: v[-1] for k, v in parse_qs(raw).items()}
                for key in ("payload", "data", "json", "body"):
                    nested = parsed_form.get(key)
                    if isinstance(nested, str):
                        nested_json = _try_parse_json(nested)
                        if nested_json is not None:
                            parsed_form.update(nested_json)
                return parsed_form or {"text": raw}

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(
            (self.config.host, self.config.port),
            Handler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mfo-webhook",
            daemon=True,
        )
        self._thread.start()
        logger.success(
            f"Webhook слушает {self.public_base_url} "
            f"(SMS: /sms, звонки: /call)"
        )

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None


def normalize_sms_payload(data: dict[str, Any]) -> dict[str, Any]:
    flat = _flatten_payload(data)
    sender = str(_first(
        flat,
        "sender",
        "from",
        "address",
        "origin",
        "sender_name",
        "sendername",
        "sender_address",
        "senderaddress",
        "senderAddress",
        "alpha",
        "alpha_name",
        "sms_alpha",
        "sim",
        default="unknown",
    ))
    text = str(_first(
        flat,
        "text",
        "message.text",
        "message.body",
        "message",
        "body",
        "sms.text",
        "sms.body",
        "sms",
        "content",
        default="",
    ))
    text, footer_sender, footer_dt = _extract_forward_sms_footer(text)
    if footer_sender and _is_generic_sender(sender):
        sender = footer_sender
    received_at = _parse_dt(
        _first(
            flat,
            "received_at",
            "receivedat",
            "date",
            "time",
            "timestamp",
            "date_ms",
            "datems",
            "dateMillis",
            "datemillis",
            "received_time",
            "receivedtime",
            "receivedTime",
            "sent_at",
            "sentat",
            "sent_date",
            "sentdate",
            "dateSent",
            "datesent",
            default=footer_dt or text or "",
        )
    )
    return {
        "sender": sender,
        "text": text,
        "received_at": received_at,
        "raw": data,
    }


def normalize_call_payload(data: dict[str, Any]) -> dict[str, Any]:
    flat = _flatten_payload(data)
    raw_caller = _first(
        flat,
        "caller",
        "caller_id",
        "callerid",
        "phone_number",
        "phonenumber",
        "phoneNumber",
        "call_number",
        "callnumber",
        "callNumber",
        "incoming_number",
        "incomingnumber",
        "incomingNumber",
        "remote_number",
        "remotenumber",
        "remoteNumber",
        "from",
        "number",
        "phone",
        "address",
        "contact",
        "name",
        "cached_name",
        "cachedname",
        "cachedName",
        default="unknown",
    )
    if raw_caller == "unknown":
        raw_caller = _first(
            flat,
            "text",
            "message",
            "body",
            "content",
            "title",
            "subtitle",
            default="unknown",
        )
    caller = _normalize_phoneish(raw_caller)
    duration_ms = _first(
        flat,
        "duration_ms",
        "durationms",
        "durationMillis",
        "duration_millis",
        default=None,
    )
    if duration_ms not in (None, ""):
        duration_sec = _parse_duration_millis(duration_ms)
    else:
        duration = _first(
            flat,
            "duration_sec",
            "duration_seconds",
            "duration",
            "call_duration",
            "callduration",
            "callDuration",
            default=0,
        )
        duration_sec = _parse_duration(duration)
    status = str(_first(
        flat,
        "status",
        "state",
        "call_status",
        "callstatus",
        "disposition",
        "result",
        default="",
    ) or "").strip()
    raw_direction = str(_first(
        flat,
        "direction",
        "call_type",
        "calltype",
        "callType",
        "type",
        "event",
        default="incoming",
    ) or "").strip()
    direction, status_from_type = _normalize_android_call_type(raw_direction)
    if not status:
        status = status_from_type
    started_at = _parse_dt(
        _first(
            flat,
            "started_at",
            "startedat",
            "received_at",
            "receivedat",
            "date",
            "time",
        "timestamp",
        "date_ms",
        "datems",
        "dateMillis",
        "datemillis",
        "call_date",
        "calldate",
        "callDate",
        "call_time",
        "calltime",
        "callTime",
        default=_first(flat, "text", "message", "body", "content", default=""),
    )
    )
    return {
        "caller": caller,
        "duration_sec": duration_sec,
        "started_at": started_at,
        "status": status,
        "direction": direction,
        "summary": _call_summary(flat, status, direction),
        "recording_url": _first(
            flat,
            "recording_url",
            "recordingurl",
            "record_url",
            "recordurl",
            "url",
            default=None,
        ),
        "raw": data,
    }


def _local_ip() -> str:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


_receiver: WebhookReceiver | None = None
_lock = threading.Lock()


def get_webhook_receiver(host: str, port: int, token: str = "") -> WebhookReceiver:
    global _receiver
    with _lock:
        if _receiver is None:
            _receiver = WebhookReceiver(WebhookConfig(host=host, port=port, token=token))
        elif (
            _receiver.config.host != host
            or _receiver.config.port != port
            or _receiver.config.token != token
        ):
            raise RuntimeError("Webhook уже запущен с другими настройками")
        _receiver.start()
        return _receiver
