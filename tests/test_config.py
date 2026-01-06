import os
import pytest
from flask import Flask

from bmgr import get_int_param, get_bool_param
from bmgr.server import load_jinja_customs

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TEST_INT_PARAM"] = 42
    app.config["TEST_BOOL_PARAM"] = True
    return app

def test_get_int_param_with_env_var(app, monkeypatch):
    monkeypatch.setenv("TEST_INT_PARAM", "10")

    assert get_int_param(app, "TEST_INT_PARAM", 99) == 10

def test_get_int_param_with_app_config(app, monkeypatch):
    monkeypatch.delenv("TEST_INT_PARAM", raising=False)

    assert get_int_param(app, "TEST_INT_PARAM", 99) == 42

def test_get_int_param_with_default(app, monkeypatch):
    monkeypatch.delenv("TEST_INT_PARAM", raising=False)
    app.config.pop("TEST_INT_PARAM", None)

    assert get_int_param(app, "TEST_INT_PARAM", 99) == 99

def test_get_bool_param_with_env_var(app, monkeypatch):
    monkeypatch.setenv("TEST_BOOL_PARAM", "false")

    assert get_bool_param(app, "TEST_BOOL_PARAM", True) == False

def test_get_bool_param_with_app_config(app, monkeypatch):
    monkeypatch.delenv("TEST_BOOL_PARAM", raising=False)

    assert get_bool_param(app, "TEST_BOOL_PARAM", False) == True

def test_get_bool_param_with_default(app, monkeypatch):
    monkeypatch.delenv("TEST_BOOL_PARAM", raising=False)
    app.config.pop("TEST_BOOL_PARAM", None)

    assert get_bool_param(app, "TEST_BOOL_PARAM", False) == False

def test_load_filters_from_subdirectory(tmp_path):
    # create customs subdirectory structure
    base = tmp_path / "customs"
    sub = base / "additional"
    sub.mkdir(parents=True)

    filters_py = sub / "filters.py"
    filters_py.write_text(
        """
def upper(value):
    return value.upper()

FILTERS = {
    "upper": upper,
}
"""
    )

    filters, globals_ = load_jinja_customs(base)

    assert "upper" in filters
    assert filters["upper"]("hello") == "HELLO"
