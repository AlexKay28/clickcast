"""Config precedence: CLI kwargs → env vars → project TOML → user TOML → defaults."""

from clickcast.config.config import (
    Config,
    get_effective_value,
    load,
    project_config_path,
    set_user_value,
    user_config_path,
)

__all__ = [
    "Config",
    "get_effective_value",
    "load",
    "project_config_path",
    "set_user_value",
    "user_config_path",
]
