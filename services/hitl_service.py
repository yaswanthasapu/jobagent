import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

class HITLManager:
    """
    Coordinates Human-In-The-Loop (HITL) requests across CLI, Desktop Browser, and Web Dashboard.
    Ensures that when JobAgent pauses for human intervention (validation errors, new fields, or submit review),
    the Web UI displays interactive controls and can submit responses back to the automation loop.
    """
    _current_request: Optional[Dict[str, Any]] = None
    _response_future: Optional[asyncio.Future] = None

    @classmethod
    def request_human_input(
        cls,
        hitl_type: str,          # "validation_error", "field_input", "submit_approval"
        title: str,
        message: str,
        field_label: str = "",
        options: Optional[List[str]] = None,
        suggested_value: Optional[str] = None,
        timeout_sec: int = 120
    ) -> asyncio.Future:
        """
        Registers an active HITL request and returns a Future that resolves when the
        user responds via Web UI, terminal, or browser.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        now = time.time()
        cls._current_request = {
            "type": hitl_type,
            "title": title,
            "message": message,
            "field_label": field_label,
            "options": options or [],
            "suggested_value": suggested_value or "",
            "timeout_sec": timeout_sec,
            "created_at": now,
            "expires_at": now + timeout_sec
        }
        cls._response_future = loop.create_future()
        return cls._response_future

    @classmethod
    def get_current_request(cls) -> Optional[Dict[str, Any]]:
        """Returns the active HITL request if one is currently waiting, or None if expired."""
        if cls._current_request:
            if time.time() > cls._current_request.get("expires_at", 0):
                cls.clear()
                return None
            rem = max(0, int(cls._current_request.get("expires_at", 0) - time.time()))
            cls._current_request["remaining_sec"] = rem
        return cls._current_request

    @classmethod
    def resolve_request(cls, action: str, value: Optional[str] = None) -> bool:
        """
        Resolves the active HITL request with:
        - action: 'resolved' (resolved in browser), 'answer' (value provided), 'skip' (skip job), 'submit' (approved)
        - value: string value if applicable
        """
        if cls._response_future and not cls._response_future.done():
            cls._response_future.set_result({"action": action, "value": value})
            cls._current_request = None
            return True
        return False

    @classmethod
    def clear(cls) -> None:
        """Clears active HITL request."""
        cls._current_request = None
        if cls._response_future and not cls._response_future.done():
            cls._response_future.cancel()
        cls._response_future = None
