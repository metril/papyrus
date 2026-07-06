"""Tests for CORS origin parsing (`Settings.cors_origins_list`) and its
effect on `app.main`'s middleware stack: CORSMiddleware must be absent
entirely when no origins are configured (the previous `allow_origins=["*"]`
+ `allow_credentials=True` combination was spec-invalid), and present with
the explicit origin list when configured.
"""
from fastapi.middleware.cors import CORSMiddleware

import app.main as main_module
from app.config import Settings


def test_cors_origins_list_is_empty_by_default():
    assert Settings(cors_origins="").cors_origins_list == []


def test_cors_origins_list_parses_and_trims_comma_separated_values():
    settings = Settings(cors_origins="https://a.example, https://b.example ,,")
    assert settings.cors_origins_list == ["https://a.example", "https://b.example"]


def test_cors_middleware_is_absent_from_the_app_when_no_origins_configured():
    # app.main.settings is built from the process environment at import
    # time; the test environment never sets PAPYRUS_CORS_ORIGINS, so this
    # exercises the real app's middleware stack under the default config.
    assert main_module.settings.cors_origins_list == []
    classes = [mw.cls for mw in main_module.app.user_middleware]
    assert CORSMiddleware not in classes
