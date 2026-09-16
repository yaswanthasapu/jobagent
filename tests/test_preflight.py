import pytest
import sys
from pathlib import Path
from services.preflight import get_playwright_browsers_dir, is_chromium_installed, run_preflight_checks

def test_get_playwright_browsers_dir():
    b_dir = get_playwright_browsers_dir()
    assert isinstance(b_dir, Path)
    assert "ms-playwright" in str(b_dir).lower()

def test_is_chromium_installed_returns_bool():
    res = is_chromium_installed()
    assert isinstance(res, bool)

def test_run_preflight_checks_success(monkeypatch):
    # Current Python is >= 3.10
    assert sys.version_info >= (3, 10)
    assert run_preflight_checks() is True

def test_run_preflight_checks_unsupported_python(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 9, 0))
    assert run_preflight_checks() is False
