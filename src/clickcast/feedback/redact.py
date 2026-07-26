"""Redaction pass for :mod:`clickcast.feedback` reports.

Consumed by ``clickcast report-bug --redact`` (the default). The idea is that
downstream AI-agent consumers can share a sanitized version of the sidecar
with the maintainers without leaking selectors, URLs, or on-page text. The
SHAPE of the bug (element counts, role distribution, step ordering, timing)
is preserved because that's what actually helps triage.

Rules:

- **URLs** — keep scheme + hostname (TLD replaced with ``.example`` to mark
  the URL as sanitized) + segment COUNT of the path. Query string and each
  path segment are replaced with placeholders that keep length category (so
  \"very long slug\" doesn't shrink to a single \"*\").
- **CSS/XPath selectors** — keep structure. Text values inside quotes are
  replaced with ``\"«redacted»\"``.
- **Visible text** (``DiscoveredElement.text``, ``PageState.title``) — replaced
  with ``«redacted, N chars»`` so the caller sees the original length category.
- **Structural fields** — ``role``, ``bbox``, ``status``, counts, timing,
  and every schema-defined non-text field pass through untouched.

Redaction is purely additive: the returned dict has the same keys as the
input, values transformed in place. No fields dropped, no fields added.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

__all__ = ["redact_report"]


_TEXT_KEYS = ("text", "title")
_URL_KEYS = ("url", "url_after", "path")
_SELECTOR_KEYS = ("selector",)


def redact_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied, redacted version of ``report``.

    ``report`` is expected to be the ``model_dump(mode=\"json\")`` output of a
    :class:`~clickcast.feedback.models.Report`; the function walks it
    recursively and applies the rules described in the module docstring.
    """
    walked = _walk(report)
    assert isinstance(walked, dict)
    return walked


def _walk(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact_field(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v) for v in value]
    return value


def _redact_field(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if key in _URL_KEYS:
            return _redact_url(value)
        if key in _SELECTOR_KEYS:
            return _redact_selector(value)
        if key in _TEXT_KEYS:
            return _redact_text(value)
        return value
    return _walk(value)


def _redact_url(raw: str) -> str:
    """Keep scheme + hostname (TLD replaced with .example) + path segment count."""
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return "«redacted url»"
    if not parsed.scheme:
        return "«redacted url»"
    host = parsed.hostname or "redacted"
    # Replace the TLD (last dot-segment) with `.example` so the URL is
    # visibly sanitized without losing the leaf domain (`acme.com` → `acme.example`).
    if "." in host:
        stem, _, _tld = host.rpartition(".")
        host = f"{stem}.example" if stem else "redacted.example"
    else:
        host = f"{host}.example"
    segments = [s for s in parsed.path.split("/") if s]
    scrubbed = "/".join("*" for _ in segments)
    path = f"/{scrubbed}" if segments else ""
    tail = "?…" if parsed.query else ""
    return f"{parsed.scheme}://{host}{path}{tail}"


_SELECTOR_QUOTED = re.compile(r"([\"\'])(.*?)\1")


def _redact_selector(raw: str) -> str:
    """Keep selector structure; replace quoted text values with ``«redacted»``."""
    if not raw:
        return raw
    return _SELECTOR_QUOTED.sub(lambda m: f"{m.group(1)}«redacted»{m.group(1)}", raw)


def _redact_text(raw: str) -> str:
    """Replace visible text with ``«redacted, N chars»`` — preserves length category."""
    if not raw:
        return raw
    return f"«redacted, {len(raw)} chars»"
