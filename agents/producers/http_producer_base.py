"""
agents/producers/http_producer_base.py
────────────────────────────────────────
Shared HTTP infrastructure for all real (Phase 3) producer adapters.

Provides:
  - httpx.Client with default timeouts and auth headers
  - Retry logic via tenacity (exponential back-off on 429 / 5xx)
  - Typed exception hierarchy so broker can handle partner failures cleanly
  - _get / _post / _patch helpers that raise ProducerApiError on bad responses

Subclasses only need to supply _base_url, _auth_headers(), and implement
the five ProducerAdapter abstract methods.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, Dict, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents.interfaces.producer_adapter import ProducerAdapter

logger = logging.getLogger(__name__)

# ── Exception hierarchy ───────────────────────────────────────────────────────

class ProducerApiError(Exception):
    """Non-retryable partner API error (4xx except 429)."""
    def __init__(self, msg: str, status_code: int = 0, body: str = "") -> None:
        super().__init__(msg)
        self.status_code = status_code
        self.body = body


class ProducerRateLimitError(ProducerApiError):
    """429 Too Many Requests — retryable."""


class ProducerUnavailableError(ProducerApiError):
    """5xx server error — retryable."""


class ProducerAuthError(ProducerApiError):
    """401 / 403 — bad API key or insufficient permissions."""


# ── Retry policy ─────────────────────────────────────────────────────────────

_RETRYABLE = (ProducerRateLimitError, ProducerUnavailableError)

_retry_policy = retry(
    retry=retry_if_exception_type(_RETRYABLE),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)


# ── Base class ────────────────────────────────────────────────────────────────

class HttpProducerBase(ProducerAdapter):
    """
    Abstract base for all HTTP-backed producer adapters.

    Subclasses must set:
      - _base_url   : root URL of the partner API (no trailing slash)
      - PRODUCER_ID / PRODUCER_NAME : as per ProducerAdapter contract

    And override _auth_headers() to return the partner-specific auth dict.

    Pass dry_run=True (or set env DRY_RUN=1) to skip live API health checks
    in can_fulfil — useful for CI and demos without real credentials.
    """

    _base_url: str = ""

    def __init__(self, timeout: float = 30.0, dry_run: bool = False) -> None:
        import os
        self.dry_run = dry_run or os.getenv("DRY_RUN", "").lower() in ("1", "true")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "BlackRiver/3.0 (github.com/black-river)",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpProducerBase":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @abstractmethod
    def _auth_headers(self) -> Dict[str, str]:
        """Return partner-specific authentication headers."""
        ...

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.is_success:
            return
        body = resp.text[:400]
        code = resp.status_code
        if code == 401 or code == 403:
            raise ProducerAuthError(
                f"{self.PRODUCER_NAME}: auth error {code}", code, body
            )
        if code == 429:
            raise ProducerRateLimitError(
                f"{self.PRODUCER_NAME}: rate-limited", code, body
            )
        if code >= 500:
            raise ProducerUnavailableError(
                f"{self.PRODUCER_NAME}: server error {code}", code, body
            )
        raise ProducerApiError(
            f"{self.PRODUCER_NAME}: API error {code}", code, body
        )

    @_retry_policy
    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        logger.debug("[%s] GET %s", self.PRODUCER_NAME, path)
        resp = self._client.get(path, params=params, headers=self._auth_headers())
        self._raise_for_status(resp)
        return resp.json()

    @_retry_policy
    def _post(self, path: str, payload: Optional[Dict] = None) -> Any:
        logger.debug("[%s] POST %s", self.PRODUCER_NAME, path)
        resp = self._client.post(path, json=payload or {}, headers=self._auth_headers())
        self._raise_for_status(resp)
        return resp.json()

    @_retry_policy
    def _patch(self, path: str, payload: Optional[Dict] = None) -> Any:
        logger.debug("[%s] PATCH %s", self.PRODUCER_NAME, path)
        resp = self._client.patch(path, json=payload or {}, headers=self._auth_headers())
        self._raise_for_status(resp)
        return resp.json()
