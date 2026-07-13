import os
from collections.abc import Generator


os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import pytest


@pytest.fixture
def client() -> Generator:
    from starlette.testclient import TestClient

    from event_booker.main import app

    with TestClient(app) as test_client:
        yield test_client
