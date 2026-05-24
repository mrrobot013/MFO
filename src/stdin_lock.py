"""Единый lock для чтения stdin (форма 403 + manual SMS)."""
from __future__ import annotations

import threading

STDIN_LOCK = threading.Lock()
