"""HTTP client for the chatbot backend under test (§7.3, §7.4).

The client speaks the ``{ session_id, message, history[] }`` protocol exposed
by ``hcag-server``'s ``POST /chat``. It's deliberately kept dependency-light —
just ``httpx`` — so the ``eval`` runtime doesn't drag in the whole hcag
runtime stack when it's talking to a remote or third-party chatbot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import httpx


ROLE_USER = "user"
ROLE_BOT = "bot"


@dataclass
class ChatTurn:
    role: Literal["user", "bot"]
    text: str


@dataclass
class ChatResponse:
    text: str
    elapsed_ms: float
    http_status: int
    error: str = ""

    def ok(self) -> bool:
        return not self.error and self.http_status < 400


@dataclass
class ChatSession:
    """Multi-turn state for one conversation with the backend."""

    session_id: str
    history: list[ChatTurn] = field(default_factory=list)


class BackendClient:
    """Thin wrapper around ``POST <url><chat_path>`` with retries + timeouts."""

    def __init__(
        self,
        url: str,
        chat_path: str = "/chat",
        request_timeout: float = 60.0,
        retries: int = 2,
    ) -> None:
        self.url = url.rstrip("/")
        self.chat_path = chat_path if chat_path.startswith("/") else "/" + chat_path
        self.request_timeout = request_timeout
        self.retries = retries

    def health(self) -> tuple[bool, str]:
        """One-shot ``GET /health`` probe used at run start (§7.10)."""
        try:
            r = httpx.get(f"{self.url}/health", timeout=self.request_timeout)
            r.raise_for_status()
            return True, ""
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    def chat(self, session: ChatSession, message: str) -> ChatResponse:
        """Send one user message. Retries on 5xx or transport errors."""
        payload = {
            "session_id": session.session_id,
            "message": message,
            "history": [{"role": t.role, "text": t.text} for t in session.history],
        }
        last_err = ""
        last_status = 0
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            try:
                r = httpx.post(
                    f"{self.url}{self.chat_path}",
                    json=payload,
                    timeout=self.request_timeout,
                )
                last_status = r.status_code
                if r.status_code >= 500:
                    last_err = f"http_{r.status_code}: {r.text[:200]}"
                    continue
                if r.status_code >= 400:
                    # 4xx is a hard failure — no retry.
                    return ChatResponse(
                        text="",
                        elapsed_ms=(time.monotonic() - started) * 1000.0,
                        http_status=r.status_code,
                        error=f"http_{r.status_code}: {r.text[:200]}",
                    )
                body = r.json()
                text = body.get("text") or body.get("message") or ""
                elapsed_ms = (time.monotonic() - started) * 1000.0
                session.history.append(ChatTurn(role=ROLE_USER, text=message))
                session.history.append(ChatTurn(role=ROLE_BOT, text=text))
                return ChatResponse(text=text, elapsed_ms=elapsed_ms, http_status=r.status_code)
            except httpx.TimeoutException as e:
                last_err = f"timeout: {e}"
                last_status = 0
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                last_status = 0
            time.sleep(min(2**attempt, 4))  # 1s, 2s, 4s cap

        return ChatResponse(
            text="",
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            http_status=last_status,
            error=last_err or "unknown_error",
        )
