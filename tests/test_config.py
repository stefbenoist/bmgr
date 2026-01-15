import pytest
from flask import Flask

from bmgr import get_int_param

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
