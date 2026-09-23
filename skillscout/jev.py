"""Client TypeSafe Jev : une relance, disjoncteur. Repris de jev-guard
(Hermes), réécrit sur la bibliothèque standard. Ne journalise jamais la clé."""
from __future__ import annotations

import os
import threading
import time

from . import net

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"        # figé : les seuils de verdict.py sont calibrés pour lui
TIMEOUT = 15.0              # Task 0, S2
BREAKER_THRESHOLD = 3       # échecs consécutifs (chacun après une relance)
BREAKER_COOLDOWN_S = 300.0
ENV_KEY = "TYPESAFE_API_KEY"


class JevClient:
    """Appels sûrs en parallèle : l'état du disjoncteur est protégé par un verrou."""

    def __init__(self, api_key: str, *, timeout: float = TIMEOUT, now=time.monotonic):
        self._key = api_key
        self._timeout = timeout
        self._now = now
        self._lock = threading.Lock()
        self._fails = 0
        self._open_until = 0.0
        self.key_rejected = False
        self.last_error = ""
        self.calls = 0

    def __repr__(self) -> str:
        return f"JevClient(model={MODEL!r}, available={self.available})"

    @classmethod
    def from_env(cls, environ=None) -> "JevClient | None":
        environ = os.environ if environ is None else environ
        key = (environ.get(ENV_KEY) or "").strip()
        return cls(key) if key else None

    @property
    def available(self) -> bool:
        with self._lock:
            return not self.key_rejected and self._now() >= self._open_until

    def _fail(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def _trip(self) -> None:
        with self._lock:
            self._fails += 1
            if self._fails >= BREAKER_THRESHOLD:
                self._open_until = self._now() + BREAKER_COOLDOWN_S
                self._fails = 0

    def classify(self, state: dict, questions: dict) -> dict | None:
        """Les réponses (`answers`) de Jev, ou None en cas d'échec."""
        if not self.available:
            return None
        body = {"model": MODEL, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self._key}"}
        for _attempt in (1, 2):
            with self._lock:
                self.calls += 1
            try:
                status, payload = net.post_json(ENDPOINT, body, headers, self._timeout)
            except OSError as e:
                # Le nom de l'exception seulement : son message pourrait
                # reprendre un en-tête, donc la clé.
                self._fail(f"Jev injoignable ({type(e).__name__})")
                continue
            if status in (401, 403):
                with self._lock:
                    self.key_rejected = True
                    self.last_error = "clé TYPESAFE_API_KEY refusée"
                return None
            answers = payload.get("answers") if isinstance(payload, dict) else None
            if status == 200 and isinstance(answers, dict):
                with self._lock:
                    self._fails = 0
                return answers
            self._fail(f"HTTP {status}" if status != 200 else "réponse Jev illisible")
        self._trip()
        return None
