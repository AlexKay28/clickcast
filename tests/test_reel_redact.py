"""Tests for the token-leak footgun fix (#110).

Two related fronts:

- :func:`clickcast.feedback.redact.apply_patterns` and
  :func:`clickcast.feedback.redact.strip_query_strings` — the low-level
  redactors, verified against realistic auth-bypass payloads.
- :meth:`clickcast.reel.Reel.__init__` — verifies the constructor plumbs
  ``redact_patterns`` and ``strip_query_strings`` into the sidecar writer
  via :func:`clickcast.feedback.write`.

The heavy end-to-end integration path (real chromium + real sidecar disk
write) lives in :mod:`tests.test_reel`; here we exercise the redaction
layer directly against synthetic reports so failures point at the redactor,
not the browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from clickcast import Reel
from clickcast.feedback import Media, Report, StepReport, write
from clickcast.feedback.models import PageState
from clickcast.feedback.redact import (
    REDACTED,
    apply_patterns,
    strip_query_strings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_media() -> Media:
    return Media(
        path="tour.gif",
        format="gif",
        size_bytes=1024,
        frame_count=12,
        duration_s=1.0,
        fps=12,
    )


def _report_with_urls(
    *,
    top_url: str,
    goto_url: str,
    url_after: str,
) -> Report:
    """A minimal :class:`Report` whose URLs live in the three places #110 cares
    about: ``report.url``, ``steps[i].args.url``, and ``steps[i].page_state.url_after``.
    """
    return Report(
        clickcast_version="0.1.0",
        url=top_url,
        started_at="2026-07-23T15:00:00+00:00",
        duration_s=1.0,
        media=_valid_media(),
        steps=[
            StepReport(
                index=0,
                action="goto",
                args={"url": goto_url},
                status="ok",
                duration_ms=100.0,
                frames=["frame-0000-000.png"],
                page_state=PageState(title="Dashboard", url_after=url_after),
            )
        ],
    )


# ---------------------------------------------------------------------------
# apply_patterns — pure-function tests
# ---------------------------------------------------------------------------


class TestApplyPatterns:
    def test_empty_patterns_is_identity(self) -> None:
        payload = {"url": "https://x/?token=abc", "steps": [{"error": "boom"}]}
        assert apply_patterns(payload, []) == payload

    def test_matches_replaced_with_redacted(self) -> None:
        pat = [re.compile(r"token=[^&]+")]
        out = apply_patterns({"url": "https://x/?token=abc123&foo=bar"}, pat)
        assert out["url"] == f"https://x/?{REDACTED}&foo=bar"

    def test_walks_nested_dicts_and_lists(self) -> None:
        pat = [re.compile(r"secret-\w+")]
        payload = {
            "steps": [
                {"args": {"url": "https://x/?k=secret-xyz"}, "error": "hit secret-abc"},
                {"args": {"url": "https://y/ok"}},
            ]
        }
        out = apply_patterns(payload, pat)
        assert REDACTED in out["steps"][0]["args"]["url"]
        assert out["steps"][0]["error"] == f"hit {REDACTED}"
        assert out["steps"][1]["args"]["url"] == "https://y/ok"

    def test_multiple_patterns_all_applied(self) -> None:
        pats = [re.compile(r"token=[^&]+"), re.compile(r"tenant=[^&]+")]
        out = apply_patterns({"url": "https://x/?token=aaa&tenant=bbb&x=1"}, pats)
        assert "token=aaa" not in out["url"]
        assert "tenant=bbb" not in out["url"]
        assert "x=1" in out["url"]

    def test_non_string_scalars_pass_through(self) -> None:
        pats = [re.compile(r".*")]  # would match any string
        payload = {"count": 3, "ok": True, "score": 1.5, "none": None}
        assert apply_patterns(payload, pats) == payload

    def test_returns_new_containers_not_mutating_input(self) -> None:
        pats = [re.compile(r"token=[^&]+")]
        payload = {"url": "https://x/?token=abc"}
        original = json.loads(json.dumps(payload))
        apply_patterns(payload, pats)
        # Input untouched
        assert payload == original


# ---------------------------------------------------------------------------
# strip_query_strings — pure-function tests
# ---------------------------------------------------------------------------


class TestStripQueryStrings:
    def test_top_level_url_stripped(self) -> None:
        out = strip_query_strings({"url": "https://acme.example/app?token=xyz"})
        assert out["url"] == "https://acme.example/app"

    def test_url_after_stripped(self) -> None:
        out = strip_query_strings({"url_after": "https://x/?a=1&b=2"})
        assert out["url_after"] == "https://x/"

    def test_nested_step_url_stripped(self) -> None:
        payload = {
            "steps": [
                {"args": {"url": "https://x/?token=z"}},
                {"page_state": {"url_after": "https://x/next?k=v"}},
            ]
        }
        out = strip_query_strings(payload)
        assert out["steps"][0]["args"]["url"] == "https://x/"
        assert out["steps"][1]["page_state"]["url_after"] == "https://x/next"

    def test_fragment_also_dropped(self) -> None:
        out = strip_query_strings({"url": "https://x/app?a=1#frag"})
        assert out["url"] == "https://x/app"

    def test_non_url_keys_untouched_even_if_they_look_like_urls(self) -> None:
        # The recorder never puts URLs in `error`, but a debug string with a
        # `?` must not be truncated — strip_query_strings is deliberately
        # narrow (URL-shaped keys only) precisely to avoid this class of
        # damage. Use apply_patterns for freeform strings.
        out = strip_query_strings({"error": "failed: foo?bar=1"})
        assert out["error"] == "failed: foo?bar=1"

    def test_relative_or_empty_url_left_alone(self) -> None:
        out = strip_query_strings({"url": "", "url_after": "/relative?a=1"})
        assert out["url"] == ""
        assert out["url_after"] == "/relative?a=1"

    def test_url_without_query_unchanged(self) -> None:
        payload = {"url": "https://x/app"}
        assert strip_query_strings(payload) == payload


# ---------------------------------------------------------------------------
# feedback.write() end-to-end — the seam Reel.save uses
# ---------------------------------------------------------------------------


class TestWriteWithRedaction:
    def test_pattern_hits_top_level_url_and_step_args_and_page_state(self, tmp_path: Path) -> None:
        report = _report_with_urls(
            top_url="https://acme.example/app?x-vercel-protection-bypass=SECRETTOP",
            goto_url="https://acme.example/app?x-vercel-protection-bypass=SECRETGOTO",
            url_after="https://acme.example/next?x-vercel-protection-bypass=SECRETAFTER",
        )
        path = write(
            report,
            tmp_path / "tour.gif.json",
            redact_patterns=[re.compile(r"x-vercel-protection-bypass=[^&]+")],
        )
        payload = json.loads(path.read_text())
        # None of the secrets survive
        text = json.dumps(payload)
        assert "SECRETTOP" not in text
        assert "SECRETGOTO" not in text
        assert "SECRETAFTER" not in text
        assert REDACTED in payload["url"]
        assert REDACTED in payload["steps"][0]["args"]["url"]
        assert REDACTED in payload["steps"][0]["page_state"]["url_after"]

    def test_strip_query_strings_removes_query_from_every_url(self, tmp_path: Path) -> None:
        report = _report_with_urls(
            top_url="https://acme.example/app?token=xyz",
            goto_url="https://acme.example/app?token=xyz",
            url_after="https://acme.example/next?token=xyz",
        )
        path = write(
            report,
            tmp_path / "tour.gif.json",
            strip_query_strings=True,
        )
        payload = json.loads(path.read_text())
        assert payload["url"] == "https://acme.example/app"
        assert payload["steps"][0]["args"]["url"] == "https://acme.example/app"
        assert payload["steps"][0]["page_state"]["url_after"] == "https://acme.example/next"
        # And of course the secret is gone
        assert "xyz" not in json.dumps(payload)

    def test_non_matching_values_untouched(self, tmp_path: Path) -> None:
        report = _report_with_urls(
            top_url="https://public.example/app",
            goto_url="https://public.example/app",
            url_after="https://public.example/next",
        )
        path = write(
            report,
            tmp_path / "tour.gif.json",
            redact_patterns=[re.compile(r"token=[^&]+")],
            strip_query_strings=True,
        )
        payload = json.loads(path.read_text())
        # No query strings to strip, no patterns to match — payload passes
        # through with URLs intact.
        assert payload["url"] == "https://public.example/app"
        assert payload["steps"][0]["args"]["url"] == "https://public.example/app"
        assert payload["steps"][0]["page_state"]["url_after"] == "https://public.example/next"
        assert REDACTED not in json.dumps(payload)

    def test_feedback_block_urls_are_static_and_not_redacted(self, tmp_path: Path) -> None:
        # The `--with-feedback` block URLs are constants that point at the
        # clickcast repo itself (github.com/AlexKay28/clickcast/...). A user's
        # pattern targeting *their* auth-bypass token must not accidentally
        # match those static URLs. Verify by supplying a pattern that would
        # only match user URLs (contains "acme") and confirming the feedback
        # block survives byte-identical.
        report = _report_with_urls(
            top_url="https://acme.example/app?token=SECRET",
            goto_url="https://acme.example/app?token=SECRET",
            url_after="https://acme.example/next?token=SECRET",
        )
        path = write(
            report,
            tmp_path / "tour.gif.json",
            with_feedback=True,
            redact_patterns=[re.compile(r"token=[^&]+")],
        )
        payload = json.loads(path.read_text())
        # User token gone
        assert "SECRET" not in json.dumps(payload)
        # Static feedback pointers untouched
        feedback = payload["feedback"]
        assert feedback["repo"] == "https://github.com/AlexKay28/clickcast"
        assert feedback["issues_url"] == "https://github.com/AlexKay28/clickcast/issues"
        assert feedback["report_url"].startswith(
            "https://github.com/AlexKay28/clickcast/issues/new"
        )
        assert feedback["schema_url"].startswith(
            "https://raw.githubusercontent.com/AlexKay28/clickcast/"
        )
        assert feedback["docs_url"].startswith("https://github.com/AlexKay28/clickcast/blob/main")


# ---------------------------------------------------------------------------
# Reel.__init__ wiring — the public API surface
# ---------------------------------------------------------------------------


class TestReelInit:
    def test_defaults_are_empty_and_disabled(self) -> None:
        reel = Reel("https://x")
        # Internal names — checked here because these fields are the contract
        # between the constructor and Reel.save; the alternative is a full
        # browser-driven integration test just to prove a bool round-trips.
        assert reel._redact_patterns == []
        assert reel._strip_query_strings is False

    def test_patterns_stored_as_list(self) -> None:
        pat = [re.compile(r"token=[^&]+")]
        reel = Reel("https://x", redact_patterns=pat)
        assert reel._redact_patterns == pat

    def test_strip_query_strings_stored(self) -> None:
        reel = Reel("https://x", strip_query_strings=True)
        assert reel._strip_query_strings is True
