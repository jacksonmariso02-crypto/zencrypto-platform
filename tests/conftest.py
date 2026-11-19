import pytest

@pytest.fixture(autouse=True)
def enable_paper_api_for_legacy_tests(monkeypatch):
    # The legacy paper-trading tests explicitly opt into the sandbox.
    # Production defaults remain closed.
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "true")
