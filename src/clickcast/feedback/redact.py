"""Redaction pass for :mod:`clickcast.feedback` reports.

Two related-but-distinct entry points live here:

1. :func:`redact_report` — sanitizes URLs, selectors, and visible text for
   sharing a sidecar publicly (used by ``clickcast report-bug --redact``).
   The SHAPE of the bug (element counts, role distribution, step ordering,
   timing) is preserved because that's what actually helps triage.

2. :func:`apply_patterns` / :func:`strip_query_strings` — the "token-leak
   footgun" fix for auth-bypassed previews (#110). Sidecars from Vercel
   Deployment Protection, Cloudflare Access, Netlify password-protected
   sites bake the bypass token into every recorded URL. These functions
   let a caller scrub matched patterns (with ``«redacted»``) and/or drop
   query strings entirely, applied over the FULL sidecar dict just before
   serialization. Unlike :func:`redact_report` they do not restructure
   anything else — non-matching values pass through byte-identical.

:func:`redact_report` rules:

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
from urllib.parse import urlparse, urlunparse

__all__ = ["apply_patterns", "redact_report", "strip_query_strings"]

REDACTED = "«redacted»"


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


# ---------------------------------------------------------------------------
# Pattern-based redaction (#110) — the token-leak footgun fix.
#
# Applied over ANY string in the sidecar dict, not just recognized URL fields.
# Rationale: an auth-bypass token can leak into places we don't statically
# know about — a page_state.title that shows the URL, a debug string in an
# error message, a screenshot filename with the query in it. Better to walk
# the whole payload once than to try to enumerate every leak site.
# ---------------------------------------------------------------------------


def apply_patterns(payload: Any, patterns: list[re.Pattern[str]]) -> Any:
    """Recursively walk ``payload`` and replace every regex match with ``«redacted»``.

    Returns a deep-copied structure with the same shape. Non-string leaves and
    strings with no matches are returned unchanged (identity for scalars, new
    container instances for dict/list). Empty ``patterns`` is a no-op fast path.

    Intended to run over ``report.model_dump(mode=\"json\")`` immediately before
    it hits disk — see :meth:`clickcast.reel.Reel.save`.
    """
    if not patterns:
        return payload
    return _walk_patterns(payload, patterns)


def _walk_patterns(value: Any, patterns: list[re.Pattern[str]]) -> Any:
    if isinstance(value, dict):
        return {k: _walk_patterns(v, patterns) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_patterns(v, patterns) for v in value]
    if isinstance(value, str):
        out = value
        for pat in patterns:
            out = pat.sub(REDACTED, out)
        return out
    return value


def strip_query_strings(payload: Any) -> Any:
    """Recursively walk ``payload`` and drop the query string from every recorded URL.

    Only string values under recognized URL-shaped keys (``url``, ``url_after``)
    are touched — arbitrary strings that happen to contain ``?`` are left alone.
    This is intentionally narrower than :func:`apply_patterns`: dropping ``?...``
    from a random error message would corrupt it, but dropping it from a URL
    field is exactly the sanitization we want.

    Non-URL fields, empty strings, and non-URL-shaped strings pass through
    unchanged.
    """
    return _walk_strip_query(payload)


def _walk_strip_query(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_query_field(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_strip_query(v) for v in value]
    return value


def _strip_query_field(key: str, value: Any) -> Any:
    # Only strip on URL-shaped fields; nested structures still get walked
    # so ``steps[i].args.url`` and ``steps[i].page_state.url_after`` are
    # both reached.
    if isinstance(value, str) and key in _URL_KEYS and value:
        return _strip_url_query(value)
    return _walk_strip_query(value)


def _strip_url_query(raw: str) -> str:
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    if not parsed.scheme:
        # Not a URL we recognize (e.g. a relative path in a non-url_after
        # slot); leave it alone rather than mangle it.
        return raw
    return urlunparse(parsed._replace(query="", fragment=""))
