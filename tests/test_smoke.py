"""Smoke tests to verify project setup."""

from perekup_helper import __version__


def test_version() -> None:
    assert __version__ == "0.1.0"
