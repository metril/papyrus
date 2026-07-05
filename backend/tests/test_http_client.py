"""Tests for the shared, lazily-created httpx.AsyncClient.

Covers: lazy singleton identity (the same instance is returned across calls
until closed), and that closing resets state so a later call builds a fresh
client rather than reusing a closed one.
"""
import httpx
import pytest

import app.services.http_client as http_client_module
from app.services.http_client import close_http_client, get_http_client


@pytest.fixture(autouse=True)
async def _reset_client():
    """The client is a module-level singleton; isolate each test and make sure
    no test leaks a real open connection pool into the next one."""
    await close_http_client()
    yield
    await close_http_client()


async def test_get_http_client_returns_an_async_client():
    client = get_http_client()
    assert isinstance(client, httpx.AsyncClient)


async def test_get_http_client_is_a_lazy_singleton():
    first = get_http_client()
    second = get_http_client()
    assert first is second


async def test_get_http_client_uses_the_default_timeout():
    client = get_http_client()
    assert client.timeout == httpx.Timeout(http_client_module.DEFAULT_TIMEOUT)


async def test_close_http_client_closes_the_underlying_client():
    client = get_http_client()
    assert not client.is_closed

    await close_http_client()
    assert client.is_closed


async def test_close_then_get_builds_a_new_client_instance():
    first = get_http_client()
    await close_http_client()

    second = get_http_client()
    assert second is not first
    assert not second.is_closed


async def test_close_http_client_is_a_no_op_when_nothing_was_ever_created():
    # The autouse fixture already reset module state to None; closing again
    # (with no client ever constructed in this test) must not raise.
    await close_http_client()
