"""Tests for :mod:`clickcast.core.opts` and the Meta flat-vs-nested shim."""

from __future__ import annotations

from clickcast.core.opts import BrowserOpts, RenderOpts
from clickcast.core.viewport import Viewport
from clickcast.scenario import Meta, loads


class TestBrowserOpts:
    def test_defaults(self) -> None:
        opts = BrowserOpts()
        assert opts.engine == "chromium"
        assert opts.viewport == Viewport(1280, 800)
        assert opts.device is None
        assert opts.headful is False
        assert opts.lang is None
        assert opts.dark is False
        assert opts.slowmo == 0
        assert opts.proxy is None

    def test_to_session_kwargs_shape(self) -> None:
        opts = BrowserOpts(
            engine="firefox",
            viewport=Viewport(1024, 768),
            device="iPhone 15",
            headful=True,
            lang="en-US",
            dark=True,
            slowmo=100,
        )
        assert opts.to_session_kwargs() == {
            "engine": "firefox",
            "viewport": (1024, 768),
            "device": "iPhone 15",
            "headful": True,
            "lang": "en-US",
            "dark": True,
            "slowmo": 100,
        }

    def test_viewport_field_uses_value_type(self) -> None:
        """Field is :class:`Viewport`, not a raw string / tuple."""
        opts = BrowserOpts(viewport=Viewport(1440, 900))
        assert isinstance(opts.viewport, Viewport)
        # to_session_kwargs coerces to tuple for Session.
        assert opts.to_session_kwargs()["viewport"] == (1440, 900)


class TestRenderOpts:
    def test_defaults(self) -> None:
        opts = RenderOpts()
        assert opts.fps == 12
        assert opts.quality == 8
        assert opts.loop == 0
        assert opts.format == "gif"


class TestMetaMigrationShim:
    """Meta's model_validator accepts BOTH the old flat YAML shape AND
    the new nested `browser: {...}` / `render: {...}` shape."""

    def test_flat_yaml_still_loads(self) -> None:
        """Legacy scenario YAML that predates #97."""
        scenario = loads(
            """
            meta:
              engine: firefox
              viewport: 1024x768
              device: iPhone 15
              headful: true
              lang: en-US
              dark: true
              slowmo: 250
              fps: 15
              quality: 10
              loop: 2
              format: mp4
              dwell: 2.5
              out: reel.mp4
            steps: []
            """
        )
        m = scenario.meta
        # Nested fields exist and are populated.
        assert m.browser.engine == "firefox"
        assert m.browser.viewport == Viewport(1024, 768)
        assert m.browser.device == "iPhone 15"
        assert m.browser.headful is True
        assert m.browser.lang == "en-US"
        assert m.browser.dark is True
        assert m.browser.slowmo == 250
        assert m.render.fps == 15
        assert m.render.quality == 10
        assert m.render.loop == 2
        assert m.render.format == "mp4"
        # Meta-only fields intact.
        assert m.dwell == 2.5
        assert m.out == "reel.mp4"

    def test_nested_yaml_loads(self) -> None:
        """New scenarios written in nested shape."""
        scenario = loads(
            """
            meta:
              browser:
                engine: webkit
                viewport: 800x600
                headful: true
              render:
                fps: 24
                format: gif
              dwell: 1.5
            steps: []
            """
        )
        m = scenario.meta
        assert m.browser.engine == "webkit"
        assert m.browser.viewport == Viewport(800, 600)
        assert m.browser.headful is True
        assert m.render.fps == 24
        assert m.render.format == "gif"
        assert m.dwell == 1.5

    def test_mixed_flat_and_nested_flat_wins(self) -> None:
        """If a scenario specifies both flat AND nested for the same
        field, the flat key wins — matches natural user expectation
        (\"the more specific one I just typed wins\")."""
        scenario = loads(
            """
            meta:
              browser:
                engine: chromium
              engine: firefox
            steps: []
            """
        )
        assert scenario.meta.browser.engine == "firefox"

    def test_flat_properties_still_readable(self) -> None:
        """Backwards-compat: `meta.engine` still works even though the
        field really lives at `meta.browser.engine`. Keeps existing call
        sites happy without a codebase-wide rename."""
        scenario = loads(
            """
            meta:
              engine: firefox
              viewport: 1024x768
              fps: 20
              format: mp4
            steps: []
            """
        )
        m = scenario.meta
        assert m.engine == "firefox"
        assert m.viewport == "1024x768"
        assert m.fps == 20
        assert m.format == "mp4"

    def test_empty_meta_uses_defaults(self) -> None:
        m = Meta()
        assert m.browser == BrowserOpts()
        assert m.render == RenderOpts()
        assert m.engine == "chromium"  # via property
