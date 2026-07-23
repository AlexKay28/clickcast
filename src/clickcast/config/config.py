"""Layered config: CLI kwargs → env vars → project TOML → user TOML → defaults.

Public API:

- :class:`Config` — pydantic settings with every knob any command needs.
- :func:`load` — resolve all layers and return a fully-populated ``Config``.
- :func:`user_config_path` / :func:`project_config_path` — path resolvers.
- :func:`set_user_value` — write a single key to the user TOML.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from platformdirs import user_config_dir
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

__all__ = [
    "Config",
    "get_effective_value",
    "load",
    "project_config_path",
    "set_user_value",
    "user_config_path",
]

_APP_NAME = "clickcast"


def user_config_path() -> Path:
    """Platform-appropriate user config path (``~/.config/clickcast/config.toml`` on Linux)."""
    return Path(user_config_dir(_APP_NAME)) / "config.toml"


def project_config_path(root: Path | None = None) -> Path:
    """``./clickcast.toml`` relative to the CWD (or ``root`` if supplied)."""
    return (root or Path.cwd()) / "clickcast.toml"


# --------------------------------------------------------------------------
# TOML settings source (custom — pydantic-settings' built-in fixes the path
# at class time; we need dynamic paths per call).
# --------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return {}
    # Accept both flat and `[defaults]`-wrapped TOML files.
    if isinstance(data.get("defaults"), dict):
        return dict(data["defaults"])
    return dict(data)


class _TomlSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self._data = _read_toml(path)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self.settings_cls.model_fields:
            field = self.settings_cls.model_fields[name]
            value, key, _ = self.get_field_value(field, name)
            if value is not None:
                out[key] = self.prepare_field_value(name, field, value, False)
        return out


# --------------------------------------------------------------------------
# The Config model itself
# --------------------------------------------------------------------------


class Config(BaseSettings):
    """Layered clickcast configuration.

    Precedence (first wins):

    1. Constructor kwargs (CLI flags)
    2. ``CLICKCAST_*`` environment variables
    3. Project ``./clickcast.toml``
    4. User ``<platform>/clickcast/config.toml`` (via ``platformdirs``)
    5. Defaults defined here
    """

    model_config = SettingsConfigDict(
        env_prefix="CLICKCAST_",
        extra="ignore",
    )

    engine: str = "chromium"
    viewport: str = "1280x800"
    device: str | None = None
    headful: bool = False
    slowmo: int = 0
    lang: str | None = None
    dark: bool = False
    proxy: str | None = None
    fps: int = 12
    dwell: float = 1.0
    format: str = "gif"
    quality: int = 8
    loop: int = 0


# --------------------------------------------------------------------------
# Public loader
# --------------------------------------------------------------------------


def load(
    *,
    project_toml: Path | None = None,
    user_toml: Path | None = None,
    **overrides: Any,
) -> Config:
    """Return a :class:`Config` with every layer applied.

    ``project_toml`` / ``user_toml`` override the auto-resolved paths; useful
    for tests. Extra kwargs become the highest-priority layer (they act like
    CLI flags).
    """
    project_path = project_toml if project_toml is not None else project_config_path()
    user_path = user_toml if user_toml is not None else user_config_path()

    class _LoadedConfig(Config):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            return (
                init_settings,
                env_settings,
                _TomlSettingsSource(settings_cls, project_path),
                _TomlSettingsSource(settings_cls, user_path),
            )

    return _LoadedConfig(**overrides)


# --------------------------------------------------------------------------
# Writing single keys back to the user TOML  (`clickcast config set`)
# --------------------------------------------------------------------------


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        return non_none[0] if non_none else str
    return annotation


def _coerce_string(value: str, annotation: Any) -> Any:
    annotation = _unwrap_optional(annotation)
    if annotation is bool:
        low = value.lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"cannot coerce {value!r} to bool")
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    return value


def _dump_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for k, v in data.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines) + "\n"


def set_user_value(
    key: str,
    value: str,
    *,
    user_toml: Path | None = None,
) -> Path:
    """Coerce ``value`` to the field's type and write it to the user TOML."""
    field = Config.model_fields.get(key)
    if field is None:
        raise KeyError(f"unknown config key: {key}")
    coerced = _coerce_string(value, field.annotation)

    path = user_toml if user_toml is not None else user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_toml(path)
    data[key] = coerced
    path.write_text(_dump_toml(data))
    return path


def get_effective_value(
    key: str,
    *,
    project_toml: Path | None = None,
    user_toml: Path | None = None,
) -> Any:
    """Return the current effective value of ``key`` after all precedence layers."""
    if key not in Config.model_fields:
        raise KeyError(f"unknown config key: {key}")
    cfg = load(project_toml=project_toml, user_toml=user_toml)
    return getattr(cfg, key)
