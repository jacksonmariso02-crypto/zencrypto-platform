import pytest

from paper_risk import PaperOrderGuard, PaperOrderLimitExceeded


def test_order_notional_cap_uses_decimal_arithmetic():
    guard = PaperOrderGuard(max_notional="100.00")
    guard.check("client-a", "0.1", "1000")
    with pytest.raises(PaperOrderLimitExceeded, match="configured maximum"):
        guard.check("client-a", "0.1000001", "1000")


def test_sliding_window_rate_limit_and_retry_after():
    guard = PaperOrderGuard(max_notional="1000", max_requests=2, window_seconds=10)
    guard.check("client-a", "1", "10", now=100)
    guard.check("client-a", "1", "10", now=101)
    with pytest.raises(PaperOrderLimitExceeded, match="rate limit") as exc:
        guard.check("client-a", "1", "10", now=102)
    assert exc.value.retry_after == 8

    # Expired requests are removed from the window.
    guard.check("client-a", "1", "10", now=111)


def test_rate_limits_are_isolated_by_client_key():
    guard = PaperOrderGuard(max_notional="1000", max_requests=1, window_seconds=60)
    guard.check("client-a", "1", "10", now=1)
    guard.check("client-b", "1", "10", now=1)
    with pytest.raises(PaperOrderLimitExceeded):
        guard.check("client-a", "1", "10", now=2)


def test_invalid_guard_configuration_is_rejected():
    with pytest.raises(ValueError):
        PaperOrderGuard(max_notional="NaN")
    with pytest.raises(ValueError):
        PaperOrderGuard(max_requests=0)


def test_paper_order_api_enforces_notional_and_rate_limits(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import main
    from paper_engine import PaperMatchingEngine

    monkeypatch.setattr(main, "ADMIN_TOKEN", "paper-test-token")
    monkeypatch.setattr(
        main, "PAPER_ENGINE", PaperMatchingEngine(str(tmp_path / "guarded.sqlite3"))
    )
    monkeypatch.setattr(
        main, "PAPER_ORDER_GUARD",
        PaperOrderGuard(max_notional="50", max_requests=1, window_seconds=60),
    )
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer paper-test-token"}
    payload = {
        "symbol": "BTC/USDT", "side": "buy", "quantity": "1",
        "price": "10", "client_order_id": "guarded-order-001",
    }

    oversized = {**payload, "price": "100", "client_order_id": "guarded-order-002"}
    response = client.post("/api/v1/paper/orders", headers=headers, json=oversized)
    assert response.status_code == 422

    accepted = client.post("/api/v1/paper/orders", headers=headers, json=payload)
    assert accepted.status_code == 200

    limited = {**payload, "client_order_id": "guarded-order-003"}
    response = client.post("/api/v1/paper/orders", headers=headers, json=limited)
    assert response.status_code == 429
    assert response.headers["retry-after"]
