"""Write (or check) the committed OpenAPI schema: ``python -m app.openapi_export [--check]``."""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "openapi.json"
REGEN = "cd backend && python -m app.openapi_export && cd ../frontend && npm run gen:api"


def render() -> str:
    try:
        import cups  # noqa: F401
    except ImportError:  # schema generation never talks to CUPS
        from unittest.mock import MagicMock

        sys.modules["cups"] = MagicMock()
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.name} is out of date. Regenerate with: {REGEN}", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
