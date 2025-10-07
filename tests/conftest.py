"""
Pytest configuration and fixtures for Jararaca tests.
"""

import asyncio
from typing import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Create a test client for HTTP testing."""
    from jararaca import HttpMicroservice, Microservice, create_http_server

    app = Microservice(providers=[], controllers=[], interceptors=[])
    http_app = create_http_server(HttpMicroservice(app))

    with TestClient(http_app) as client:
        yield client
