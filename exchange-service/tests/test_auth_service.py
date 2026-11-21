import pytest

from auth_service import AuthService, AuthenticationError, UsernameTaken


def test_register_login_and_expired_or_tampered_tokens(tmp_path):
    service = AuthService(str(tmp_path / "auth.sqlite3"), "s" * 48, token_ttl_seconds=900)
    registered = service.register("Trader_One", "correct horse battery 123")
    assert registered["username"] == "trader_one"
    assert registered["account_id"].startswith("u_")
    assert service.authenticate(registered["access_token"]) == {
        "user_id": registered["user_id"],
        "username": "trader_one",
        "account_id": registered["account_id"],
    }

    logged_in = service.login("TRADER_ONE", "correct horse battery 123")
    assert logged_in["account_id"] == registered["account_id"]

    with pytest.raises(AuthenticationError):
        service.login("trader_one", "wrong password 123")
    with pytest.raises(AuthenticationError):
        service.authenticate(registered["access_token"] + "tampered")


def test_registration_creates_unique_user_and_ledger_account_atomically(tmp_path):
    from paper_ledger import PaperLedger

    path = str(tmp_path / "same-db.sqlite3")
    PaperLedger(path)
    service = AuthService(path, "s" * 48)
    first = service.register("trader_one", "strong password 1234")
    with pytest.raises(UsernameTaken):
        service.register("TRADER_ONE", "another password 1234")

    balances = PaperLedger(path).get_balances(first["account_id"])
    assert balances == []


def test_password_and_signing_secret_policy(tmp_path):
    service = AuthService(str(tmp_path / "policy.sqlite3"), "s" * 48)
    with pytest.raises(ValueError):
        service.register("ab", "short1")
    with pytest.raises(ValueError):
        service.register("valid_name", "allletterswithoutnumbers")
    unconfigured = AuthService(str(tmp_path / "no-secret.sqlite3"), "short")
    with pytest.raises(RuntimeError, match="AUTH_TOKEN_SECRET"):
        unconfigured.register("new_user", "strong password 1234")


def test_disabled_user_token_is_rejected(tmp_path):
    service = AuthService(str(tmp_path / "disabled.sqlite3"), "s" * 48)
    user = service.register("disabled_user", "strong password 1234")
    import sqlite3
    with sqlite3.connect(str(tmp_path / "disabled.sqlite3")) as db:
        db.execute("UPDATE paper_users SET disabled=1 WHERE user_id=?", (user["user_id"],))
    with pytest.raises(AuthenticationError):
        service.authenticate(user["access_token"])


def test_self_service_paper_api_enforces_account_ownership(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import main
    from auth_service import AuthService
    from paper_engine import PaperMatchingEngine
    from paper_ledger import PaperLedger
    from paper_risk import PaperOrderGuard

    path = str(tmp_path / "self-service.sqlite3")
    monkeypatch.setattr(main, "ADMIN_TOKEN", "admin-test-token")
    monkeypatch.setattr(main, "PAPER_ENGINE", PaperMatchingEngine(path))
    monkeypatch.setattr(main, "PAPER_LEDGER", PaperLedger(path))
    monkeypatch.setattr(main, "AUTH_SERVICE", AuthService(path, "a" * 48))
    monkeypatch.setattr(main, "AUTH_ATTEMPT_GUARD", PaperOrderGuard("10", 100, 60))
    monkeypatch.setattr(main, "PAPER_ORDER_GUARD", PaperOrderGuard("10000", 100, 60))
    client = TestClient(main.app)

    alice_response = client.post("/api/v1/auth/register", json={
        "username": "alice_user", "password": "alice password 1234"
    })
    bob_response = client.post("/api/v1/auth/register", json={
        "username": "bob_user", "password": "bob password 1234"
    })
    assert alice_response.status_code == 200
    assert bob_response.status_code == 200
    alice = alice_response.json()
    bob = bob_response.json()
    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
    bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
    admin_headers = {"Authorization": "Bearer admin-test-token"}

    assert alice["account_id"] != bob["account_id"]
    assert client.get("/api/v1/me", headers=alice_headers).json()["account_id"] == alice["account_id"]
    assert client.get("/api/v1/me/balances").status_code == 401

    # Administrator-only test credit is used to seed simulated balances.
    for account_id, asset, amount, key in (
        (alice["account_id"], "BTC", "1", "alice-btc-credit-01"),
        (bob["account_id"], "USDT", "500", "bob-usdt-credit-001"),
    ):
        response = client.post(
            f"/api/v1/paper/accounts/{account_id}/credit",
            headers=admin_headers,
            json={"asset": asset, "amount": amount, "idempotency_key": key},
        )
        assert response.status_code == 200

    ask = client.post("/api/v1/me/orders", headers=alice_headers, json={
        "symbol": "BTC/USDT", "side": "sell", "quantity": "1",
        "price": "100", "client_order_id": "alice-ask-0001",
    })
    bid = client.post("/api/v1/me/orders", headers=bob_headers, json={
        "symbol": "BTC/USDT", "side": "buy", "quantity": "1",
        "price": "110", "client_order_id": "bob-bid-000001",
    })
    assert ask.status_code == 200
    assert bid.status_code == 200
    assert bid.json()["status"] == "filled"

    alice_orders = client.get("/api/v1/me/orders", headers=alice_headers).json()
    bob_orders = client.get("/api/v1/me/orders", headers=bob_headers).json()
    assert [order["client_order_id"] for order in alice_orders] == ["alice-ask-0001"]
    assert [order["client_order_id"] for order in bob_orders] == ["bob-bid-000001"]

    # User tokens cannot access administrator account APIs or another user's account.
    assert client.get(
        f"/api/v1/paper/accounts/{alice['account_id']}/balances", headers=bob_headers
    ).status_code == 401
    assert client.get("/api/v1/me/balances", headers=alice_headers).json() == [
        {"asset": "USDT", "available": "100", "locked": "0"}
    ]
    assert client.get("/api/v1/me/balances", headers=bob_headers).json() == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "400", "locked": "0"},
    ]
