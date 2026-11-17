"""Tests for :mod:`clickcast.feedback.redact`."""

from __future__ import annotations

import re

from clickcast.feedback.redact import apply_patterns, redact_report, strip_query_strings


def test_url_top_level_redacted() -> None:
    out = redact_report({"url": "https://internal.acme.com/dash/tenant?token=xyz"})
    assert out["url"] == "https://redacted.example/*/*?…"
    assert "internal.acme.com" not in out["url"]
    assert "token" not in out["url"]


def test_url_after_key_redacted() -> None:
    out = redact_report({"url_after": "https://foo.bar.co.uk/a/b/c"})
    assert out["url_after"] == "https://redacted.example/*/*/*"
    assert "foo.bar.co.uk" not in out["url_after"]


def test_selector_text_replaced_structure_kept() -> None:
    out = redact_report({"selector": 'button[aria-label="Sign in"] > span'})
    assert "«redacted»" in out["selector"]
    assert out["selector"].startswith("button[aria-label=")
    assert out["selector"].endswith("] > span")


def test_failed_step_args_redacted() -> None:
    """Field names that appear in step ``args`` must not leak payloads."""
    inp = {
        "args": {
            "expression": "localStorage.getItem('authToken')",
            "into": 'input[placeholder="Search email"]',
            "value": "secret typed text",
            "key": "Enter",
        }
    }
    out = redact_report(inp)
    args = out["args"]
    assert args["expression"] == "«redacted, 33 chars»"
    # ``into`` follows selector rules: structure kept, quoted text replaced.
    assert args["into"].startswith("input[placeholder=")
    assert "«redacted»" in args["into"]
    assert "Search email" not in args["into"]
    assert args["value"] == "«redacted, 17 chars»"
    assert args["key"] == "«redacted, 5 chars»"


def test_visible_text_length_preserved_content_gone() -> None:
    out = redact_report({"text": "Compare plans and pricing"})
    assert out["text"] == "«redacted, 25 chars»"


def test_title_field_redacted() -> None:
    out = redact_report({"title": "Acme Dashboard — Tenant Foo"})
    assert out["title"].startswith("«redacted, ")
    assert out["title"].endswith(" chars»")


def test_structural_fields_pass_through() -> None:
    inp = {
        "role": "button",
        "bbox": [10, 20, 100, 30],
        "status": "ok",
        "duration_ms": 421.7,
        "index": 3,
    }
    out = redact_report(inp)
    assert out == inp


def test_nested_dicts_walked() -> None:
    inp = {"page_state": {"title": "hello", "url_after": "https://x.com/"}}
    out = redact_report(inp)
    assert out["page_state"]["title"].startswith("«redacted,")
    assert out["page_state"]["url_after"] == "https://redacted.example"


def test_lists_of_dicts_walked() -> None:
    inp = {
        "discovered_elements": [
            {"text": "Buy now", "role": "button"},
            {"text": "Pricing", "role": "link"},
        ]
    }
    out = redact_report(inp)
    assert all(el["text"].startswith("«redacted,") for el in out["discovered_elements"])
    assert [el["role"] for el in out["discovered_elements"]] == ["button", "link"]


def test_empty_string_untouched() -> None:
    out = redact_report({"text": "", "url": "", "selector": ""})
    assert out == {"text": "", "url": "", "selector": ""}


def test_ip_and_no_dot_host_still_sanitized_and_port_preserved() -> None:
    out = redact_report({"url": "http://localhost:8080/x"})
    assert out["url"] == "http://redacted.example:8080/*"
    assert "localhost" not in out["url"]


def test_nested_sidecar_diagnostics_and_accessibility_names_redacted() -> None:
    inp = {
        "steps": [
            {
                "status": "failed",
                "error": "GET https://internal/app?token=STEPSECRET failed",
                "page_state": {
                    "console_errors": ["token=CONSOLESECRET"],
                    "page_errors": ["customer PAGESECRET"],
                    "network_failed": ["https://internal/api?token=NETSECRET"],
                },
            }
        ],
        "errors": ["tour ERRORSECRET"],
        "warnings": ["tenant WARNINGSECRET"],
        "discovered_elements": [
            {
                "role": "button",
                "accessibility": {"name": "Pay customer NAMESECRET"},
            }
        ],
    }
    out = redact_report(inp)
    dumped = str(out)
    for secret in (
        "STEPSECRET",
        "CONSOLESECRET",
        "PAGESECRET",
        "NETSECRET",
        "ERRORSECRET",
        "WARNINGSECRET",
        "NAMESECRET",
    ):
        assert secret not in dumped
    assert out["steps"][0]["status"] == "failed"
    assert out["steps"][0]["error"].startswith("«redacted, ")
    assert all(
        item.startswith("«redacted, ")
        for key in ("console_errors", "page_errors", "network_failed")
        for item in out["steps"][0]["page_state"][key]
    )
    assert out["discovered_elements"][0]["role"] == "button"
    assert out["discovered_elements"][0]["accessibility"]["name"].startswith("«redacted, ")


def test_media_path_is_not_treated_as_url() -> None:
    path = "/home/tenant/project/reel.gif?token=filesystem-name"
    out = redact_report({"media": {"path": path}})
    assert out["media"]["path"] == path
    assert strip_query_strings({"media": {"path": path}})["media"]["path"] == path

    patterned = apply_patterns({"media": {"path": path}}, [re.compile("filesystem-name")])
    assert patterned["media"]["path"] == "/home/tenant/project/reel.gif?token=«redacted»"
