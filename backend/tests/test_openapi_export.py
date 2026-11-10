from app.openapi_export import OUT, REGEN, render


def test_committed_openapi_matches_live_schema():
    assert OUT.exists() and OUT.read_text(encoding="utf-8") == render(), (
        f"backend/openapi.json is stale. Regenerate with: {REGEN}"
    )
