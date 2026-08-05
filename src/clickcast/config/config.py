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
import warnings
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomlkit
from platformdirs import user_config_dir
from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
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
    """Return the flat dict shape of a clickcast TOML file (or ``{}`` if missing).

    A missing file is normal — every user starts with no config. A malformed
    TOML file, on the other hand, is a user typo we must not swallow: it
    used to emit a :func:`warnings.warn` (silent by default, so users saw
    zero indication that their settings were being ignored). Now we re-raise
    :class:`tomllib.TOMLDecodeError` with the file path prepended to the
    message so the CLI layer can print a visible ``⚠`` line pointing at
    the offending file — see #151 (PERF-3).
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        # Preserve the exception type so callers can `except TOMLDecodeError`;
        # prepend the file path so the message identifies the source. The
        # `from e` chain keeps the original position info for developers.
        raise tomllib.TOMLDecodeError(f"{path}: {e}") from e
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
    pace: str = "natural"  # fast | natural | slow | onboarding — sets fps + dwell in auto
    format: str = "gif"
    quality: int = 8
    loop: int = 0
    # #166: internal / SSO-protected sites.
    insecure: bool = False
    # ``NoDecode`` tells pydantic-settings to hand the raw env-var string to
    # our field_validator below instead of trying to JSON-decode it first
    # (which fails on the friendlier ``"Name: value"`` scalar form).
    header: Annotated[list[str], NoDecode] = []
    header_host: str | None = None

    @field_validator("header", mode="before")
    @classmethod
    def _parse_header(cls, value: Any) -> Any:
        """Accept several friendly env-var shapes for ``CLICKCAST_HEADER``.

        The pydantic-settings default for a ``list[str]`` env var is a JSON
        array (``'["A: b"]'``), which is awful to type in a shell. This
        validator accepts:

        - a real list (kwargs / TOML): passed through
        - a JSON array string: parsed as JSON
        - a plain scalar string: split on newlines or ``;`` — whichever
          the user prefers; matches how ``curl -H`` is chained in scripts

        So all four forms work:

            CLICKCAST_HEADER='["Authorization: Bearer x"]'
            CLICKCAST_HEADER='Authorization: Bearer x'
            CLICKCAST_HEADER='Authorization: Bearer x;X-Trace: 1'
            CLICKCAST_HEADER=$'Authorization: Bearer x\\nX-Trace: 1'
        """
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            # Split on newlines first (least ambiguous with header values
            # that may legitimately contain commas), fall back to ``;``.
            parts = stripped.splitlines() if "\n" in stripped else stripped.split(";")
            return [p.strip() for p in parts if p.strip()]
        return value


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
    # #166: `list[str]` (currently: `header`) accepts the same friendly
    # shapes as the env-var validator — split on newlines or `;` — so
    # `clickcast config set header "Authorization: Bearer x"` just works.
    if get_origin(annotation) is list:
        parts = value.splitlines() if "\n" in value else value.split(";")
        return [p.strip() for p in parts if p.strip()]
    return value


def _load_tomlkit_document(path: Path) -> tomlkit.TOMLDocument:
    """Load ``path`` as a structure-preserving TOMLDocument, or an empty one."""
    if not path.exists():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text())
    except tomlkit.exceptions.TOMLKitError as e:
        warnings.warn(
            f"clickcast: existing {path} could not be parsed ({e}). "
            "The updated key will be written to a fresh document — "
            "your prior comments/keys are preserved only if you back the file up first.",
            stacklevel=2,
        )
        return tomlkit.document()


def set_user_value(
    key: str,
    value: str,
    *,
    user_toml: Path | None = None,
) -> Path:
    """Coerce ``value`` to the field's type and write it to the user TOML.

    Comments, whitespace, and key order in the existing file are preserved
    (via ``tomlkit``). If the file uses a top-level ``[defaults]`` table,
    the new key is written INSIDE that table so its shape survives the round
    trip.
    """
    field = Config.model_fields.get(key)
    if field is None:
        raise KeyError(f"unknown config key: {key}")
    coerced = _coerce_string(value, field.annotation)

    path = user_toml if user_toml is not None else user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = _load_tomlkit_document(path)
    defaults = doc.get("defaults")
    if isinstance(defaults, dict):
        # Existing [defaults] wrapper — write inside it so shape survives.
        defaults[key] = coerced
    else:
        doc[key] = coerced

    path.write_text(tomlkit.dumps(doc))
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
