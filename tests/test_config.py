from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from clickcast.cli import app
from clickcast.config import (
    Config,
    get_effective_value,
    load,
    project_config_path,
    set_user_value,
    user_config_path,
)
from clickcast.config.config import _coerce_string, _read_toml

runner = CliRunner()


# ------------------------------------------------------------------
# Paths — resolve to something plausible
# ------------------------------------------------------------------


class TestPaths:
    def test_user_config_path_ends_in_config_toml(self) -> None:
        p = user_config_path()
        assert p.name == "config.toml"
        assert "clickcast" in str(p)

    def test_project_config_path_default_is_cwd(self, tmp_path: Path) -> None:
        assert project_config_path(tmp_path) == tmp_path / "clickcast.toml"


# ------------------------------------------------------------------
# Precedence pairs (roadmap acceptance)
# ------------------------------------------------------------------


class TestPrecedence:
    def test_defaults_apply_when_no_layers_set(self, tmp_path: Path) -> None:
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.engine == "chromium"
        assert cfg.viewport == "1280x800"

    def test_user_toml_beats_default(self, tmp_path: Path) -> None:
        user = tmp_path / "user.toml"
        user.write_text('engine = "firefox"\n')
        cfg = load(project_toml=tmp_path / "missing.toml", user_toml=user)
        assert cfg.engine == "firefox"

    def test_project_toml_beats_user_toml(self, tmp_path: Path) -> None:
        user = tmp_path / "user.toml"
        user.write_text('engine = "firefox"\n')
        proj = tmp_path / "clickcast.toml"
        proj.write_text('engine = "webkit"\n')
        cfg = load(project_toml=proj, user_toml=user)
        assert cfg.engine == "webkit"

    def test_env_beats_project_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        proj = tmp_path / "clickcast.toml"
        proj.write_text('engine = "webkit"\n')
        monkeypatch.setenv("CLICKCAST_ENGINE", "firefox")
        cfg = load(project_toml=proj, user_toml=tmp_path / "missing.toml")
        assert cfg.engine == "firefox"

    def test_cli_flag_beats_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKCAST_ENGINE", "firefox")
        cfg = load(
            project_toml=tmp_path / "p.toml",
            user_toml=tmp_path / "u.toml",
            engine="chromium",
        )
        assert cfg.engine == "chromium"

    def test_bool_env_var_coerced(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKCAST_HEADFUL", "true")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.headful is True


# ------------------------------------------------------------------
# TOML files: support both flat and `[defaults]`-wrapped
# ------------------------------------------------------------------


class TestTomlShapes:
    def test_flat_toml_loads(self, tmp_path: Path) -> None:
        f = tmp_path / "clickcast.toml"
        f.write_text('engine = "webkit"\nfps = 24\n')
        cfg = load(project_toml=f, user_toml=tmp_path / "u.toml")
        assert cfg.engine == "webkit"
        assert cfg.fps == 24

    def test_defaults_wrapped_toml_loads(self, tmp_path: Path) -> None:
        f = tmp_path / "clickcast.toml"
        f.write_text('[defaults]\nengine = "webkit"\nfps = 24\n')
        cfg = load(project_toml=f, user_toml=tmp_path / "u.toml")
        assert cfg.engine == "webkit"
        assert cfg.fps == 24

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        # Per #151 (PERF-3): _read_toml now propagates decode errors so the
        # CLI can render a visible ⚠ line. `warnings.warn` was silent by
        # default and hid user-typo bugs. The CLI catches this at the
        # `_config_default_map` boundary; direct API callers of `load()`
        # get an actionable exception instead of silent default-fallback.
        f = tmp_path / "clickcast.toml"
        f.write_text('engine = "webkit\n')  # missing closing quote
        with pytest.raises(tomllib.TOMLDecodeError):
            load(project_toml=f, user_toml=tmp_path / "u.toml")


# ------------------------------------------------------------------
# Coercion + TOML round-trip for `config set`
# ------------------------------------------------------------------


class TestCoercion:
    @pytest.mark.parametrize(
        ("raw", "field", "expected"),
        [
            ("true", "headful", True),
            ("false", "headful", False),
            ("yes", "dark", True),
            ("no", "dark", False),
            ("24", "fps", 24),
            ("1.5", "dwell", 1.5),
            ("webkit", "engine", "webkit"),
            ("http://proxy", "proxy", "http://proxy"),
        ],
    )
    def test_coerce_from_string(self, raw: str, field: str, expected: object) -> None:
        annotation = Config.model_fields[field].annotation
        assert _coerce_string(raw, annotation) == expected

    def test_coerce_bad_bool_raises(self) -> None:
        annotation = Config.model_fields["headful"].annotation
        with pytest.raises(ValueError, match="bool"):
            _coerce_string("nope", annotation)


class TestSetUserValue:
    def test_writes_and_round_trips(self, tmp_path: Path) -> None:
        user = tmp_path / "user.toml"
        set_user_value("engine", "firefox", user_toml=user)
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=user)
        assert cfg.engine == "firefox"

    def test_writing_preserves_existing_keys(self, tmp_path: Path) -> None:
        user = tmp_path / "user.toml"
        set_user_value("engine", "firefox", user_toml=user)
        set_user_value("fps", "24", user_toml=user)
        data = _read_toml(user)
        assert data == {"engine": "firefox", "fps": 24}

    def test_unknown_key_raises(self, tmp_path: Path) -> None:
        with pytest.raises(KeyError, match="unknown"):
            set_user_value("nonsense", "x", user_toml=tmp_path / "u.toml")

    def test_preserves_user_comments_and_order(self, tmp_path: Path) -> None:
        # Regression: hand-rolled `_dump_toml` rewrote the whole file, losing
        # comments and reordering keys. tomlkit preserves both.
        user = tmp_path / "user.toml"
        user.write_text(
            "# Comment above engine\n"
            'engine = "chromium"\n'
            "\n"
            "# Group: rendering\n"
            "fps = 12\n"
            "dwell = 1.0\n"
        )
        set_user_value("engine", "firefox", user_toml=user)
        text = user.read_text()
        assert "# Comment above engine" in text
        assert "# Group: rendering" in text
        assert 'engine = "firefox"' in text
        # Order preserved: engine before fps before dwell
        assert text.index("engine") < text.index("fps") < text.index("dwell")

    def test_preserves_defaults_wrapping(self, tmp_path: Path) -> None:
        # Regression: files using the `[defaults]` shape were silently
        # rewritten as flat, losing the wrapper.
        user = tmp_path / "user.toml"
        user.write_text('[defaults]\nengine = "chromium"\nfps = 12\n')
        set_user_value("engine", "webkit", user_toml=user)
        text = user.read_text()
        assert "[defaults]" in text, "wrapper table dropped"
        assert 'engine = "webkit"' in text
        # And the value is still readable through the layered loader.
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=user)
        assert cfg.engine == "webkit"


class TestSetProjectValue:
    """#177: peer of :func:`set_user_value` that targets ``./clickcast.toml``."""

    def test_writes_and_round_trips(self, tmp_path: Path) -> None:
        from clickcast.config import set_project_value

        project = tmp_path / "clickcast.toml"
        set_project_value("engine", "webkit", project_toml=project)
        cfg = load(project_toml=project, user_toml=tmp_path / "u.toml")
        assert cfg.engine == "webkit"

    def test_project_beats_user_in_precedence_stack(self, tmp_path: Path) -> None:
        """Precedence check that #177's whole motivation depends on: a
        project-scope write outranks a user-scope value for the same key.
        If this ever reversed, `--scope project` becomes a no-op for teams
        whose members already have a user default set."""
        from clickcast.config import set_project_value

        user = tmp_path / "user.toml"
        project = tmp_path / "clickcast.toml"
        set_user_value("engine", "firefox", user_toml=user)
        set_project_value("engine", "webkit", project_toml=project)
        cfg = load(project_toml=project, user_toml=user)
        assert cfg.engine == "webkit"

    def test_unknown_key_raises(self, tmp_path: Path) -> None:
        from clickcast.config import set_project_value

        with pytest.raises(KeyError, match="unknown"):
            set_project_value("nonsense", "x", project_toml=tmp_path / "p.toml")


class TestMalformedTomlRaises:
    """Per #151 (PERF-3): a malformed TOML file must not fall back silently.

    The old behaviour warned via :func:`warnings.warn` (silent unless the
    user ran with ``-W all``), leaving typos invisible. Now the CLI layer
    turns the raised :class:`tomllib.TOMLDecodeError` into a visible ⚠
    line with the file path (see ``clickcast.cli._config_default_map``);
    direct API callers of ``load()`` get an actionable exception.
    """

    def test_read_toml_raises_on_decode_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.toml"
        bad.write_text('engine = "unterminated\n')  # missing closing quote
        with pytest.raises(tomllib.TOMLDecodeError):
            _read_toml(bad)

    def test_load_raises_when_toml_broken(self, tmp_path: Path) -> None:
        user = tmp_path / "user.toml"
        user.write_text('engine = "unterminated\n')
        with pytest.raises(tomllib.TOMLDecodeError):
            load(project_toml=tmp_path / "p.toml", user_toml=user)


# ------------------------------------------------------------------
# CLI wiring
# ------------------------------------------------------------------


class TestCliCommands:
    def test_config_path(self) -> None:
        r = runner.invoke(app, ["config", "path"])
        assert r.exit_code == 0
        assert "config.toml" in r.stdout

    def test_config_list_prints_every_field(self) -> None:
        r = runner.invoke(app, ["config", "list"])
        assert r.exit_code == 0
        for field in ("engine", "viewport", "headful", "fps"):
            assert field in r.stdout

    def test_config_get_effective_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKCAST_ENGINE", "webkit")
        r = runner.invoke(app, ["config", "get", "engine"])
        assert r.exit_code == 0
        assert "webkit" in r.stdout

    def test_config_get_unknown_key(self) -> None:
        r = runner.invoke(app, ["config", "get", "not_a_real_key"])
        assert r.exit_code == 1

    def test_config_set_writes_to_user_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `set_user_value` calls `user_config_path()` from its own module;
        # patch there so the write lands in a tmp file.
        target = tmp_path / "config.toml"
        monkeypatch.setattr("clickcast.config.config.user_config_path", lambda: target)
        r = runner.invoke(app, ["config", "set", "engine", "firefox"])
        assert r.exit_code == 0, r.output
        assert 'engine = "firefox"' in target.read_text()

    # ------------------------------------------------------------------
    # #177: `--scope user|project` selects which TOML the write lands in.
    # ------------------------------------------------------------------

    def test_config_set_scope_project_writes_to_project_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--scope project` writes to ./clickcast.toml, not the user TOML.

        Verifies both (a) the target file is the project path from
        ``project_config_path()``, and (b) the user TOML is untouched — so
        a project-scope write can't accidentally leak into user defaults.
        """
        project_target = tmp_path / "clickcast.toml"
        user_target = tmp_path / "user.toml"
        monkeypatch.setattr("clickcast.config.config.project_config_path", lambda: project_target)
        monkeypatch.setattr("clickcast.config.config.user_config_path", lambda: user_target)
        r = runner.invoke(app, ["config", "set", "engine", "firefox", "--scope", "project"])
        assert r.exit_code == 0, r.output
        assert 'engine = "firefox"' in project_target.read_text()
        assert not user_target.exists(), "user TOML must stay untouched on project-scope write"

    def test_config_set_scope_project_round_trips_via_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A project-scope write must be readable through the precedence
        stack — i.e. `load(project_toml=...)` sees the value we set."""
        from clickcast.config import load as load_config

        project_target = tmp_path / "clickcast.toml"
        monkeypatch.setattr("clickcast.config.config.project_config_path", lambda: project_target)
        monkeypatch.setattr("clickcast.config.config.user_config_path", lambda: tmp_path / "u.toml")
        r = runner.invoke(app, ["config", "set", "engine", "webkit", "--scope", "project"])
        assert r.exit_code == 0, r.output
        cfg = load_config(project_toml=project_target, user_toml=tmp_path / "u.toml")
        assert cfg.engine == "webkit"

    def test_config_set_scope_defaults_to_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Omitting `--scope` writes to the user TOML — preserves the
        backwards-compat behaviour every existing caller expects."""
        project_target = tmp_path / "clickcast.toml"
        user_target = tmp_path / "user.toml"
        monkeypatch.setattr("clickcast.config.config.project_config_path", lambda: project_target)
        monkeypatch.setattr("clickcast.config.config.user_config_path", lambda: user_target)
        r = runner.invoke(app, ["config", "set", "fps", "24"])
        assert r.exit_code == 0, r.output
        assert "fps = 24" in user_target.read_text()
        assert not project_target.exists()

    def test_config_set_scope_invalid_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unknown scope value fails cleanly (nonzero exit + red message),
        rather than silently defaulting to user or crashing on an AttributeError."""
        monkeypatch.setattr("clickcast.config.config.user_config_path", lambda: tmp_path / "u.toml")
        monkeypatch.setattr(
            "clickcast.config.config.project_config_path", lambda: tmp_path / "p.toml"
        )
        r = runner.invoke(app, ["config", "set", "engine", "firefox", "--scope", "garbage"])
        assert r.exit_code != 0
        assert "scope" in (r.output + (r.stderr or "")).lower()

    # ------------------------------------------------------------------
    # #175: `config list` formatting — no Python repr for list / None,
    # aligned columns regardless of field-name length.
    # ------------------------------------------------------------------

    def test_config_list_formats_list_with_semicolons_no_brackets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A populated list-typed field renders as ``"; "``-joined values,
        not Python's ``['a', 'b']`` repr. This matches the friendlier env-var
        syntax that ``_parse_header`` accepts for ``CLICKCAST_HEADER``."""
        monkeypatch.setenv("CLICKCAST_HEADER", "Authorization: Bearer x; X-Trace: 1")
        r = runner.invoke(app, ["config", "list"])
        assert r.exit_code == 0
        assert "Authorization: Bearer x; X-Trace: 1" in r.stdout
        # The buggy code printed the Python repr — guard against regression.
        assert "['Authorization" not in r.stdout
        assert "'X-Trace: 1'" not in r.stdout
        assert "[" not in _header_line(r.stdout)
        assert "]" not in _header_line(r.stdout)

    def test_config_list_formats_empty_list_as_none_marker(self) -> None:
        """The default ``header = []`` should render as ``(none)``, not
        the bare ``[]`` that looks like a bug in the output."""
        r = runner.invoke(app, ["config", "list"])
        assert r.exit_code == 0
        line = _header_line(r.stdout)
        assert "(none)" in line
        assert "[]" not in line

    def test_config_list_formats_unset_optional_as_unset_marker(self) -> None:
        """Unset ``Optional`` fields (``lang: str | None = None``) should
        render as ``(unset)``, not the bare ``None`` string."""
        r = runner.invoke(app, ["config", "list"])
        assert r.exit_code == 0
        lang_line = next(ln for ln in r.stdout.splitlines() if ln.strip().startswith("lang "))
        assert "(unset)" in lang_line
        # Bare "None" as the value is the buggy shape — guard against it.
        # Split on the field name to isolate the value column.
        value = lang_line.split("lang", 1)[1].strip()
        assert value == "(unset)"

    def test_config_list_columns_align_across_field_name_lengths(self) -> None:
        """Short (``fps``), medium (``header``) and long (``header_host``)
        field names must line up in the value column. The bug hardcoded
        width 12, which was tight for ``header_host`` (11 chars) and
        misaligned any field longer than that."""
        r = runner.invoke(app, ["config", "list"])
        assert r.exit_code == 0
        lines = {
            name: next(ln for ln in r.stdout.splitlines() if ln.strip().startswith(f"{name} "))
            for name in ("fps", "header", "header_host")
        }
        # Value column = first char after the run of spaces that follows the key.
        starts = {
            name: len(ln) - len(ln.lstrip(" ")) + len(name) + _count_trailing_spaces(ln, name)
            for name, ln in lines.items()
        }
        assert starts["fps"] == starts["header"] == starts["header_host"], (
            f"value columns misaligned: {starts!r}\nlines: {lines!r}"
        )


def _header_line(stdout: str) -> str:
    """Return the ``header`` row from ``clickcast config list`` output.

    Matches ``header`` but not ``header_host`` — key/value output uses a
    single space between key and value column, so we anchor on the trailing
    space.
    """
    for ln in stdout.splitlines():
        if ln.strip().startswith("header ") and not ln.strip().startswith("header_host"):
            return ln
    raise AssertionError(f"no `header` line in output:\n{stdout}")

    # ------------------------------------------------------------------
    # #177: `config` is a Typer sub-app — each subcommand has its own --help.
    # ------------------------------------------------------------------

    def test_config_help_lists_all_subcommands(self) -> None:
        r = runner.invoke(app, ["config", "--help"])
        assert r.exit_code == 0, r.output
        for sub in ("path", "list", "get", "set"):
            assert sub in r.stdout, f"expected {sub!r} in `config --help` output"

    def test_config_get_help_shows_key_argument(self) -> None:
        r = runner.invoke(app, ["config", "get", "--help"])
        assert r.exit_code == 0, r.output
        assert "key" in r.stdout.lower()
        assert "Config key" in r.stdout


def _header_line(stdout: str) -> str:
    """Return the ``header`` row from ``clickcast config list`` output.

    Matches ``header`` but not ``header_host`` — key/value output uses a
    single space between key and value column, so we anchor on the trailing
    space.
    """
    for ln in stdout.splitlines():
        if ln.strip().startswith("header ") and not ln.strip().startswith("header_host"):
            return ln
    raise AssertionError(f"no `header` line in output:\n{stdout}")


def _count_trailing_spaces(line: str, name: str) -> int:
    """Number of padding spaces between the key ``name`` and the value column."""
    after = line.split(name, 1)[1]
    return len(after) - len(after.lstrip(" "))


# ------------------------------------------------------------------
# get_effective_value: sanity around all-layers behaviour
# ------------------------------------------------------------------


class TestInsecureHeadersConfig:
    """#166: new Config fields for internal / SSO-protected hosts."""

    def test_defaults(self, tmp_path: Path) -> None:
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.insecure is False
        assert cfg.header == []
        assert cfg.header_host is None

    def test_insecure_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKCAST_INSECURE", "true")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.insecure is True

    def test_header_host_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKCAST_HEADER_HOST", "internal.example.com")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.header_host == "internal.example.com"

    def test_header_env_var_scalar(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Common single-header case: user types a bare `Name: value`."""
        monkeypatch.setenv("CLICKCAST_HEADER", "Authorization: Bearer secret")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.header == ["Authorization: Bearer secret"]

    def test_header_env_var_semicolon_separated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLICKCAST_HEADER", "Authorization: Bearer x; X-Trace: 1")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.header == ["Authorization: Bearer x", "X-Trace: 1"]

    def test_header_env_var_newline_separated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLICKCAST_HEADER", "Authorization: Bearer x\nX-Trace: 1")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.header == ["Authorization: Bearer x", "X-Trace: 1"]

    def test_header_env_var_json_list_still_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The JSON-array form is documented as the pydantic-settings native
        shape — keep it working for scripts / agents that already use it."""
        monkeypatch.setenv("CLICKCAST_HEADER", '["Authorization: Bearer x", "X-Trace: 1"]')
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.header == ["Authorization: Bearer x", "X-Trace: 1"]

    def test_header_config_set_round_trip(self, tmp_path: Path) -> None:
        """`clickcast config set header "A: b; C: d"` round-trips via the
        list-aware coercer, so users can persist headers to the TOML file
        the same way they persist any other field."""
        user = tmp_path / "user.toml"
        set_user_value("header", "Authorization: Bearer x; X-Trace: 1", user_toml=user)
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=user)
        assert cfg.header == ["Authorization: Bearer x", "X-Trace: 1"]


class TestGetEffectiveValue:
    def test_matches_load_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLICKCAST_ENGINE", "webkit")
        v = get_effective_value(
            "engine",
            project_toml=tmp_path / "p.toml",
            user_toml=tmp_path / "u.toml",
        )
        assert v == "webkit"

    def test_unknown_key_raises(self, tmp_path: Path) -> None:
        with pytest.raises(KeyError):
            get_effective_value(
                "bogus",
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
            )
