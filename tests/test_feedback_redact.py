"""Tests for :mod:`clickcast.feedback.redact`."""

from __future__ import annotations

from clickcast.feedback.redact import redact_report


def test_url_top_level_redacted() -> None:
    out = redact_report({"url": "https://internal.acme.com/dash/tenant?token=xyz"})
    assert out["url"].startswith("https://")
    assert "acme.example" in out["url"]
    assert "token" not in out["url"]
    assert "/*/*" in out["url"]


def test_url_after_key_redacted() -> None:
    out = redact_report({"url_after": "https://foo.bar.co.uk/a/b/c"})
    assert out["url_after"].endswith("/*/*/*")
    assert "foo.bar.co.example" in out["url_after"]


def test_selector_text_replaced_structure_kept() -> None:
    out = redact_report({"selector": 'button[aria-label="Sign in"] > span'})
    assert "«redacted»" in out["selector"]
    assert out["selector"].startswith("button[aria-label=")
    assert out["selector"].endswith("] > span")


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
    assert "x.example" in out["page_state"]["url_after"]


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


def test_ip_and_no_dot_host_still_sanitized() -> None:
    out = redact_report({"url": "http://localhost:8080/x"})
    assert "example" in out["url"]
    assert out["url"].endswith("/*")
