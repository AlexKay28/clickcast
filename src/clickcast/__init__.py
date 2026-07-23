"""clickcast — drive a browser, return a reel + AI-readable feedback sidecar."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clickcast")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
