"""Tests for :class:`clickcast.core.viewport.Viewport`."""

from __future__ import annotations

import pytest

from clickcast.core.viewport import Viewport


class TestParse:
    def test_parse_string_lowercase(self) -> None:
        assert Viewport.parse("1280x800") == Viewport(1280, 800)

    def test_parse_string_uppercase_x(self) -> None:
        """The old ad-hoc parsers all lowercased the input first — preserve that."""
        assert Viewport.parse("1280X800") == Viewport(1280, 800)

    def test_parse_tuple(self) -> None:
        assert Viewport.parse((1440, 900)) == Viewport(1440, 900)

    def test_parse_tuple_of_strs_coerces(self) -> None:
        """A few call sites (auto config from JSON) hand in stringified ints
        via a tuple — mirror the ``int(...)`` coercion the old code did."""
        assert Viewport.parse(("1024", "768")) == Viewport(1024, 768)  # type: ignore[arg-type]

    def test_parse_viewport_is_identity(self) -> None:
        vp = Viewport(1280, 800)
        assert Viewport.parse(vp) is vp

    def test_parse_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid viewport"):
            Viewport.parse("")

    def test_parse_malformed_string_raises_with_named_value(self) -> None:
        """Error message must include the offending value so users can debug."""
        with pytest.raises(ValueError, match="'nonsense'"):
            Viewport.parse("nonsense")

    def test_parse_wrong_tuple_arity_raises(self) -> None:
        with pytest.raises(ValueError, match=r"tuple must be \(width, height\)"):
            Viewport.parse((1, 2, 3))  # type: ignore[arg-type]

    def test_parse_wrong_type_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="must be str, tuple"):
            Viewport.parse(1280)  # type: ignore[arg-type]


class TestSerialization:
    def test_str_canonical(self) -> None:
        assert str(Viewport(1280, 800)) == "1280x800"

    def test_str_round_trips_through_parse(self) -> None:
        vp = Viewport(1440, 900)
        assert Viewport.parse(str(vp)) == vp

    def test_as_tuple(self) -> None:
        assert Viewport(1280, 800).as_tuple() == (1280, 800)

    def test_as_list(self) -> None:
        assert Viewport(1280, 800).as_list() == [1280, 800]


class TestImmutability:
    def test_frozen(self) -> None:
        vp = Viewport(1280, 800)
        with pytest.raises(Exception):  # noqa: B017 - dataclasses raises FrozenInstanceError
            vp.width = 640  # type: ignore[misc]

    def test_hashable(self) -> None:
        """Frozen + slots means Viewport is hashable — useful for cache keys."""
        assert {Viewport(1280, 800), Viewport(1280, 800)} == {Viewport(1280, 800)}
