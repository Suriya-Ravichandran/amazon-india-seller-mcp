"""Guards on the published distribution.

A version mismatch between the metadata and the package is the kind of thing that
only shows up after an upload, and a PyPI version number cannot be reused.
"""

from __future__ import annotations

import pathlib
import tomllib

import amazon_india_seller_mcp

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_version_matches_pyproject():
    assert amazon_india_seller_mcp.__version__ == _pyproject()["project"]["version"]


def test_console_script_points_at_a_real_callable():
    scripts = _pyproject()["project"]["scripts"]
    target = scripts["amazon-india-seller-mcp"]
    assert target == "amazon_india_seller_mcp.server:main"

    from amazon_india_seller_mcp.server import main

    assert callable(main)


def test_only_the_package_namespace_is_shipped():
    """Top-level names like `tools` or `config` would collide in site-packages."""
    packages = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["amazon_india_seller_mcp"]


def test_package_exposes_the_server_entry_points():
    assert callable(amazon_india_seller_mcp.create_server)
    assert callable(amazon_india_seller_mcp.main)
