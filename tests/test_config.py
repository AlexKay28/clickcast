from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from clickcast.cli import app
from clickcast.config import (
    Config,
    get_effective_value,
    load,
    project_config_path,
    set_user_value,
    user_config_path,
)
from clickcast.config.config import _coerce_string, _dump_toml, _read_toml

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

    def test_malformed_toml_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "clickcast.toml"
        f.write_text('engine = "webkit\n')  # missing closing quote
        cfg = load(project_toml=f, user_toml=tmp_path / "u.toml")
        # Falls back to defaults rather than blowing up.
        assert cfg.engine == "chromium"


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

    def test_dump_toml_native_types(self) -> None:
        out = _dump_toml({"engine": "webkit", "headful": True, "fps": 24})
        assert 'engine = "webkit"' in out
        assert "headful = true" in out
        assert "fps = 24" in out


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
        # Redirect user_config_path to a tmp location
        target = tmp_path / "config.toml"
        monkeypatch.setattr("clickcast.cli.user_config_path", lambda: target)
        # `set_user_value` uses config.user_config_path directly, not the CLI's
        # import — patch there too.
        monkeypatch.setattr("clickcast.config.config.user_config_path", lambda: target)
        r = runner.invoke(app, ["config", "set", "engine", "firefox"])
        assert r.exit_code == 0, r.output
        assert 'engine = "firefox"' in target.read_text()


# ------------------------------------------------------------------
# get_effective_value: sanity around all-layers behaviour
# ------------------------------------------------------------------


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
