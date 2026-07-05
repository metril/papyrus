"""Shared pytest fixtures/setup.

The local dev venv does not have pycups installed (host lacks CUPS headers),
but Docker/CI images do. Stub the `cups` module so any code importing it is
still importable/testable locally.
"""
import sys
from unittest.mock import MagicMock

try:
    import cups  # noqa: F401
except ImportError:
    sys.modules["cups"] = MagicMock()
