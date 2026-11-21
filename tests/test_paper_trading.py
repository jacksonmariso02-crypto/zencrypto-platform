import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


def test_user_can_fund_trade_and_sell_paper_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "test.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        reg = client.post("/auth/register", json={"email":"paper-user@example.test","password":"CorrectHorseBattery12"})
        assert reg.status_code == 201
        login = client.post("/auth/login", json={"email":"paper-user@example.test","password":"CorrectHorseBattery12"})
        assert login.status_code == 200
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        faucet = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"test-faucet-0001"},
                             json={"amount":"1000","currency":"USD"})
        assert faucet.status_code == 201
        buy = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"test-buy-000001"},
                          json={"symbol":"BTC","side":"buy","quote_amount":"100","reference_price":"10000"})
        assert buy.status_code == 201, buy.text
        assert buy.json()["status"] == "filled"
        assert buy.json()["fee"] == "0.10"
        sell = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"test-sell-00001"},
                           json={"symbol":"BTC","side":"sell","quote_amount":"50","reference_price":"10000"})
        assert sell.status_code == 201, sell.text
        orders = client.get("/trade/orders", headers=headers).json()["orders"]
        assert len(orders) == 2
        balances = {x["currency"]: float(x["amount"]) for x in client.get("/account/balances",headers=headers).json()["balances"]}
        assert balances["USD"] > 900
        assert balances["BTC"] > 0
        assert client.post("/deposits/request",headers=headers,json={"currency":"USD","amount":"10"}).status_code == 403
        assert client.post("/withdrawals/request",headers=headers,json={"currency":"USD","amount":"10","destination":"bank"}).status_code == 403


def test_idempotency_and_user_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "isolation.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        tokens=[]
        for email in ("one@example.test","two@example.test"):
            client.post("/auth/register",json={"email":email,"password":"CorrectHorseBattery12"})
            response=client.post("/auth/login",json={"email":email,"password":"CorrectHorseBattery12"})
            tokens.append({"Authorization":"Bearer "+response.json()["access_token"]})
        h1,h2=tokens
        first=client.post("/sandbox/faucet",headers={**h1,"Idempotency-Key":"same-key-00001"},json={"amount":"1"})
        repeat=client.post("/sandbox/faucet",headers={**h1,"Idempotency-Key":"same-key-00001"},json={"amount":"1"})
        assert first.status_code==201 and repeat.json()==first.json()
        other=client.get("/account/balances",headers=h2).json()["balances"]
        assert all(float(x["amount"])==0 for x in other)



def test_ledger_pagination_currency_filter_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "ledger.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"ledger-user@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"ledger-user@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        funded = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"ledger-faucet-001"}, json={"amount":"100"})
        assert funded.status_code == 201
        bought = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"ledger-buy-00001"},
                             json={"symbol":"BTC","side":"buy","quote_amount":"25","reference_price":"50000"})
        assert bought.status_code == 201
        page = client.get("/account/ledger?limit=2&offset=0", headers=headers)
        assert page.status_code == 200
        data = page.json()
        assert len(data["entries"]) == 2
        assert data["pagination"]["total"] > 2
        assert data["pagination"]["has_more"] is True
        usd = client.get("/account/ledger?currency=usd&limit=20", headers=headers).json()
        assert usd["filters"]["currency"] == "USD"
        assert all(row["currency"] == "USD" for row in usd["entries"])
        assert client.get("/account/ledger?limit=201", headers=headers).status_code == 422
        assert client.get("/account/ledger?offset=-1", headers=headers).status_code == 422
        assert client.get("/account/ledger?currency=USD%3BSELECT", headers=headers).status_code == 422


def test_order_history_filters_pagination_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "orders-page.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"orders-page@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"orders-page@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"orders-faucet-001"}, json={"amount":"500"})
        for key, side, symbol in [("orders-buy-0001","buy","BTC"),("orders-buy-0002","buy","ETH"),("orders-sell-001","sell","BTC")]:
            response = client.post("/trade/orders", headers={**headers,"Idempotency-Key":key},
                                   json={"symbol":symbol,"side":side,"quote_amount":"20","reference_price":"100"})
            assert response.status_code == 201, response.text
        page = client.get("/trade/orders?limit=2&offset=0", headers=headers)
        assert page.status_code == 200
        assert len(page.json()["orders"]) == 2
        assert page.json()["pagination"]["total"] == 3
        assert page.json()["pagination"]["has_more"] is True
        btc_buys = client.get("/trade/orders?symbol=btc&side=BUY", headers=headers).json()
        assert len(btc_buys["orders"]) == 1
        assert btc_buys["orders"][0]["symbol"] == "BTC"
        assert btc_buys["orders"][0]["side"] == "buy"
        assert client.get("/trade/orders?status=unknown", headers=headers).status_code == 422
        assert client.get("/trade/orders?side=short", headers=headers).status_code == 422
        assert client.get("/trade/orders?limit=201", headers=headers).status_code == 422


def test_trade_summary_reports_only_users_paper_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "trade-summary.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        headers_list = []
        for email in ("summary-one@example.test", "summary-two@example.test"):
            client.post("/auth/register", json={"email":email,"password":"CorrectHorseBattery12"})
            login = client.post("/auth/login", json={"email":email,"password":"CorrectHorseBattery12"})
            headers_list.append({"Authorization":"Bearer " + login.json()["access_token"]})
        h1, h2 = headers_list
        client.post("/sandbox/faucet", headers={**h1,"Idempotency-Key":"summary-faucet-01"}, json={"amount":"200"})
        order = client.post("/trade/orders", headers={**h1,"Idempotency-Key":"summary-buy-0001"},
                            json={"symbol":"BTC","side":"buy","quote_amount":"50","reference_price":"50000"})
        assert order.status_code == 201
        summary = client.get("/trade/summary", headers=h1)
        assert summary.status_code == 200
        data = summary.json()["summary"]
        assert data["order_count"] == 1
        assert data["filled_count"] == 1
        assert data["buy_count"] == 1
        assert data["sell_count"] == 0
        assert data["filled_notional"] == "50.00"
        assert data["fees"] == "0.05"
        other = client.get("/trade/summary", headers=h2).json()["summary"]
        assert other["order_count"] == 0


def test_cors_preflight_allows_production_pages_origin():
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        response = client.options(
            "/auth/login",
            headers={
                "Origin": "https://jacksonmariso02-crypto.github.io",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://jacksonmariso02-crypto.github.io"
        allowed_headers = response.headers.get("access-control-allow-headers", "").lower()
        assert "authorization" in allowed_headers
        assert "content-type" in allowed_headers
        assert "idempotency-key" in allowed_headers


def test_cors_does_not_allow_untrusted_origin():
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        response = client.options(
            "/auth/login",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "https://untrusted.example"


def test_journal_balances_per_currency_and_is_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "journal-integrity.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"integrity@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"integrity@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        funded = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"integrity-faucet-01"}, json={"amount":"250"})
        assert funded.status_code == 201
        bought = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"integrity-buy-0001"}, json={"symbol":"BTC","side":"buy","quote_amount":"75","reference_price":"50000"})
        assert bought.status_code == 201, bought.text
        with main.db() as conn:
            bad = conn.execute("SELECT j.id,a.currency,SUM(p.amount_minor) total FROM journal j JOIN postings p ON p.journal_id=j.id JOIN accounts a ON a.id=p.account_id GROUP BY j.id,a.currency HAVING SUM(p.amount_minor)<>0").fetchall()
            assert len(bad) == 0
            journal_id = conn.execute("SELECT id FROM journal ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()["id"]
            update_blocked = False
            delete_blocked = False
            try:
                conn.execute("UPDATE postings SET amount_minor=amount_minor+1 WHERE journal_id=?", (journal_id,))
            except Exception:
                update_blocked = True
            try:
                conn.execute("DELETE FROM journal WHERE id=?", (journal_id,))
            except Exception:
                delete_blocked = True
            assert update_blocked and delete_blocked


def test_ledger_integrity_endpoint_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "ledger-admin.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"ledger-admin@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"ledger-admin@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        assert client.get("/admin/ledger/integrity", headers=headers).status_code == 403
        with main.db() as conn:
            conn.execute("UPDATE users SET role='admin' WHERE email=?", ("ledger-admin@example.test",))
        result = client.get("/admin/ledger/integrity", headers=headers)
        assert result.status_code == 200
        assert result.json()["balanced"] is True
        assert result.json()["mode"] == "PAPER_ONLY"


def test_order_rejects_silent_rounding_and_excess_price_precision(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "order-precision.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"precision@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"precision@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        faucet = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"precision-faucet-1"}, json={"amount":"100"})
        assert faucet.status_code == 201
        bad_amount = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"precision-order-1"}, json={"symbol":"BTC","side":"buy","quote_amount":"10.009","reference_price":"50000"})
        assert bad_amount.status_code == 422
        bad_price = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"precision-order-2"}, json={"symbol":"BTC","side":"buy","quote_amount":"10.00","reference_price":"50000.1234567890123"})
        assert bad_price.status_code == 422
        valid = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"precision-order-3"}, json={"symbol":"BTC","side":"buy","quote_amount":"10.01","reference_price":"50000.123456789012"})
        assert valid.status_code == 201, valid.text
        assert valid.json()["notional"] == "10.01"


def test_order_idempotency_returns_same_fill_without_double_debit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "idempotency.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"retry@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"retry@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"retry-faucet-01"}, json={"amount":"100"})
        order_headers = {**headers,"Idempotency-Key":"same-order-key-0001"}
        payload = {"symbol":"BTC","side":"buy","quote_amount":"25.00","reference_price":"50000"}
        first = client.post("/trade/orders", headers=order_headers, json=payload)
        retry = client.post("/trade/orders", headers=order_headers, json=payload)
        assert first.status_code == 201, first.text
        assert retry.status_code == 201, retry.text
        assert retry.json()["id"] == first.json()["id"]
        assert client.get("/trade/orders", headers=headers).json()["pagination"]["total"] == 1
        usd = next(x for x in client.get("/account/balances", headers=headers).json()["balances"] if x["currency"] == "USD")
        assert usd["amount"] == "74.98"


def test_order_idempotency_key_cannot_be_reused_for_different_order(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "idempotency-conflict.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"conflict@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"conflict@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"conflict-faucet-01"}, json={"amount":"100"})
        order_headers = {**headers,"Idempotency-Key":"reused-order-key-01"}
        first = client.post("/trade/orders", headers=order_headers, json={"symbol":"BTC","side":"buy","quote_amount":"25.00","reference_price":"50000"})
        conflict = client.post("/trade/orders", headers=order_headers, json={"symbol":"ETH","side":"buy","quote_amount":"25.00","reference_price":"2500"})
        assert first.status_code == 201, first.text
        assert conflict.status_code == 409
        assert client.get("/trade/orders", headers=headers).json()["pagination"]["total"] == 1


def test_sell_fee_is_deducted_from_proceeds_not_asset_quantity(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "sell-fee.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"sell-fee@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"sell-fee@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        funded = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"sell-fee-faucet"}, json={"amount":"1000"})
        assert funded.status_code == 201
        bought = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"sell-fee-buy-001"}, json={"symbol":"BTC","side":"buy","quote_amount":"100.00","reference_price":"10000"})
        assert bought.status_code == 201, bought.text
        assert bought.json()["quantity"] == "0.01000000"
        sold = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"sell-fee-sell-01"}, json={"symbol":"BTC","side":"sell","quote_amount":"50.00","reference_price":"10000"})
        assert sold.status_code == 201, sold.text
        assert sold.json()["quantity"] == "0.00500000"
        assert sold.json()["notional"] == "50.00"
        assert sold.json()["fee"] == "0.05"
        balances = {x["currency"]: x["amount"] for x in client.get("/account/balances", headers=headers).json()["balances"]}
        assert balances["USD"] == "949.85"
        assert balances["BTC"] == "0.005"


def test_order_rejects_assets_not_supported_by_product(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "unsupported-asset.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"unsupported@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"unsupported@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"], "Idempotency-Key":"unsupported-asset-01"}
        response = client.post("/trade/orders", headers=headers, json={"symbol":"DOGE","side":"buy","quote_amount":"10.00","reference_price":"0.15"})
        assert response.status_code == 422
        assert "unsupported paper asset" in response.json()["detail"].lower()
        assert client.get("/trade/orders", headers=headers).json()["pagination"]["total"] == 0


def test_supported_paper_assets_endpoint_is_authenticated_and_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "supported-assets.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        assert client.get("/trade/assets").status_code == 401
        client.post("/auth/register", json={"email":"assets@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"assets@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        response = client.get("/trade/assets", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert {item["symbol"] for item in body["assets"]} == {"BTC", "ETH", "SOL", "BNB", "XRP"}
        assert all(item["quote"] == "USD" and item["mode"] == "PAPER_ONLY" for item in body["assets"])
        assert body["mode"] == "PAPER_ONLY"

 
 
def test_order_history_rejects_unsupported_asset_and_unimplemented_status_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "order-filters.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"filters@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"filters@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        unsupported = client.get("/trade/orders?symbol=DOGE", headers=headers)
        assert unsupported.status_code == 422
        open_orders = client.get("/trade/orders?status=open", headers=headers)
        assert open_orders.status_code == 422
        assert "immediate-fill" in open_orders.json()["detail"]
        filled = client.get("/trade/orders?status=filled", headers=headers)
        assert filled.status_code == 200
        assert filled.json()["orders"] == []


def test_ledger_integrity_endpoint_reports_balanced_user_journals(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "ledger-integrity.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        assert client.get("/account/ledger/verify").status_code == 401
        client.post("/auth/register", json={"email":"integrity@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"integrity@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        empty = client.get("/account/ledger/verify", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["status"] == "balanced"
        funded = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"integrity-faucet-01"}, json={"amount":"100"})
        assert funded.status_code == 201, funded.text
        bought = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"integrity-buy-0001"},
                             json={"symbol":"BTC","side":"buy","quote_amount":"25.00","reference_price":"50000"})
        assert bought.status_code == 201, bought.text
        verified = client.get("/account/ledger/verify", headers=headers)
        assert verified.status_code == 200
        assert verified.json()["status"] == "balanced"
        assert verified.json()["issue_count"] == 0
        assert verified.json()["checked_journals"] >= 2


def test_ledger_integrity_endpoint_detects_corrupted_posting(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "ledger-corruption.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"corrupt@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"corrupt@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        funded = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"corrupt-faucet-01"}, json={"amount":"100"})
        assert funded.status_code == 201, funded.text
        with main.db() as connection:
            connection.execute("DROP TRIGGER IF EXISTS postings_no_update")
            connection.execute("""
                UPDATE postings SET amount_minor=amount_minor+1
                WHERE id=(
                    SELECT p.id FROM postings p
                    JOIN accounts a ON a.id=p.account_id
                    JOIN users u ON u.id=a.user_id
                    JOIN journal j ON j.id=p.journal_id
                    WHERE u.email=? AND a.currency='USD'
                    ORDER BY j.created_at DESC LIMIT 1
                )
            """, ("corrupt@example.test",))
        verified = client.get("/account/ledger/verify", headers=headers)
        assert verified.status_code == 200
        body = verified.json()
        assert body["status"] == "integrity_issue"
        assert body["issue_count"] == 1
        assert body["issues"][0]["currency"] == "USD"


def test_paper_order_response_discloses_client_reference_price(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "price-provenance.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"provenance@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"provenance@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"provenance-faucet-01"}, json={"amount":"100"})
        order = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"provenance-buy-0001"}, json={"symbol":"BTC","side":"buy","quote_amount":"25.00","reference_price":"50000"})
        assert order.status_code == 201, order.text
        assert order.json()["execution_model"] == "IMMEDIATE_FILL_AT_CLIENT_REFERENCE_PRICE"
        assert order.json()["price_source"] == "CLIENT_SUPPLIED_REFERENCE"


def test_server_order_preview_matches_fill_math_without_writing_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "order-preview.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        assert client.post("/trade/preview", json={}).status_code == 401
        client.post("/auth/register", json={"email":"preview@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"preview@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"preview-faucet-01"}, json={"amount":"100"})
        with main.db() as connection:
            before = connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        preview = client.post("/trade/preview", headers=headers, json={"symbol":"BTC","side":"buy","quote_amount":"25.00","reference_price":"50000"})
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["quantity"] == "0.00050000"
        assert data["fee"] == "0.02"
        assert data["estimated_usd_debit"] == "25.02"
        assert data["sufficient_balance"] is True
        assert data["execution_model"] == "PREVIEW_ONLY_NO_EXECUTION"
        assert data["price_source"] == "CLIENT_SUPPLIED_REFERENCE"
        with main.db() as connection:
            after = connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        assert after == before
        order = client.post("/trade/orders", headers={**headers,"Idempotency-Key":"preview-order-01"}, json={"symbol":"BTC","side":"buy","quote_amount":"25.00","reference_price":"50000"})
        assert order.status_code == 201, order.text
        assert order.json()["quantity"] == data["quantity"]
        assert order.json()["fee"] == data["fee"]
        insufficient = client.post("/trade/preview", headers=headers, json={"symbol":"ETH","side":"buy","quote_amount":"1000.00","reference_price":"3000"})
        assert insufficient.status_code == 200
        assert insufficient.json()["sufficient_balance"] is False


def test_limit_order_reserves_and_cancel_releases_paper_usd_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "limit-reserve.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register", json={"email":"limit@example.test","password":"CorrectHorseBattery12"})
        login = client.post("/auth/login", json={"email":"limit@example.test","password":"CorrectHorseBattery12"})
        headers = {"Authorization":"Bearer " + login.json()["access_token"]}
        funded = client.post("/sandbox/faucet", headers={**headers,"Idempotency-Key":"limit-faucet-01"}, json={"amount":"1000"})
        assert funded.status_code == 201, funded.text
        before = {x["currency"]:x["amount"] for x in client.get("/account/balances",headers=headers).json()["balances"]}
        payload = {"symbol":"BTC","side":"buy","quantity":"0.01000000","limit_price":"50000"}
        order = client.post("/trade/limit-orders",headers={**headers,"Idempotency-Key":"limit-buy-0001"},json=payload)
        assert order.status_code == 201, order.text
        assert order.json()["status"] == "open"
        assert order.json()["reserved_amount"] == "500.50"
        after_reserve = {x["currency"]:x["amount"] for x in client.get("/account/balances",headers=headers).json()["balances"]}
        assert float(after_reserve["USD"]) == float(before["USD"]) - 500.50
        listed = client.get("/trade/limit-orders?status=open",headers=headers)
        assert listed.status_code == 200 and len(listed.json()["orders"]) == 1
        cancelled = client.post("/trade/limit-orders/"+order.json()["id"]+"/cancel",headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        after_cancel = {x["currency"]:x["amount"] for x in client.get("/account/balances",headers=headers).json()["balances"]}
        assert after_cancel["USD"] == before["USD"]
        repeated = client.post("/trade/limit-orders/"+order.json()["id"]+"/cancel",headers=headers)
        assert repeated.status_code == 200 and repeated.json()["already_cancelled"] is True
        assert client.get("/trade/limit-orders?status=open",headers=headers).json()["orders"] == []


def test_limit_order_rejects_insufficient_reserve_without_creating_order(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "limit-insufficient.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register",json={"email":"limit-low@example.test","password":"CorrectHorseBattery12"})
        login=client.post("/auth/login",json={"email":"limit-low@example.test","password":"CorrectHorseBattery12"})
        headers={"Authorization":"Bearer "+login.json()["access_token"]}
        order=client.post("/trade/limit-orders",headers={**headers,"Idempotency-Key":"limit-low-0001"},json={"symbol":"BTC","side":"buy","quantity":"0.10000000","limit_price":"50000"})
        assert order.status_code == 400
        assert client.get("/trade/limit-orders",headers=headers).json()["orders"] == []


def test_limit_sell_reserves_and_cancel_releases_asset_and_is_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "limit-sell.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register",json={"email":"limit-sell@example.test","password":"CorrectHorseBattery12"})
        login=client.post("/auth/login",json={"email":"limit-sell@example.test","password":"CorrectHorseBattery12"})
        headers={"Authorization":"Bearer "+login.json()["access_token"]}
        client.post("/sandbox/faucet",headers={**headers,"Idempotency-Key":"sell-faucet-0001"},json={"amount":"1000"})
        buy=client.post("/trade/orders",headers={**headers,"Idempotency-Key":"sell-seed-buy-001"},json={"symbol":"BTC","side":"buy","quote_amount":"100","reference_price":"10000"})
        assert buy.status_code==201,buy.text
        before={x["currency"]:x["amount"] for x in client.get("/account/balances",headers=headers).json()["balances"]}
        payload={"symbol":"BTC","side":"sell","quantity":"0.00500000","limit_price":"25000"}
        order=client.post("/trade/limit-orders",headers={**headers,"Idempotency-Key":"sell-limit-0001"},json=payload)
        assert order.status_code==201,order.text
        assert order.json()["reserve_currency"]=="BTC"
        assert order.json()["reserved_amount"]=="0.00500000"
        outsider=client.post("/trade/limit-orders/"+order.json()["id"]+"/cancel",headers={"Authorization":"Bearer invalid"})
        assert outsider.status_code==401
        cancelled=client.post("/trade/limit-orders/"+order.json()["id"]+"/cancel",headers=headers)
        assert cancelled.status_code==200,cancelled.text
        after={x["currency"]:x["amount"] for x in client.get("/account/balances",headers=headers).json()["balances"]}
        assert after["BTC"]==before["BTC"]


def test_limit_order_book_aggregates_open_orders_without_exposing_user_or_order_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "limit-book.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        client.post("/auth/register",json={"email":"book-one@example.test","password":"CorrectHorseBattery12"})
        login=client.post("/auth/login",json={"email":"book-one@example.test","password":"CorrectHorseBattery12"})
        headers={"Authorization":"Bearer "+login.json()["access_token"]}
        client.post("/sandbox/faucet",headers={**headers,"Idempotency-Key":"book-faucet-01"},json={"amount":"1000"})
        for key,qty in [("book-order-0001","0.00100000"),("book-order-0002","0.00200000")]:
            response=client.post("/trade/limit-orders",headers={**headers,"Idempotency-Key":key},json={"symbol":"BTC","side":"buy","quantity":qty,"limit_price":"50000"})
            assert response.status_code==201,response.text
        book=client.get("/trade/limit-order-book/BTC",headers=headers)
        assert book.status_code==200,book.text
        data=book.json()
        assert data["book_type"]=="ZENCRYPTO_USER_LIMIT_ORDERS"
        assert data["bids"]==[{"price":"50000","quantity":"0.00300000","order_count":2}]
        assert data["asks"]==[]
        assert "user_id" not in str(data) and "order_id" not in str(data)
        assert client.get("/trade/limit-order-book/DOGE",headers=headers).status_code==422


def test_limit_matching_partial_fill_settles_both_assets_and_releases_excess_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "limit-match.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        def create_user(email, faucet_key):
            client.post("/auth/register",json={"email":email,"password":"CorrectHorseBattery12"})
            login=client.post("/auth/login",json={"email":email,"password":"CorrectHorseBattery12"})
            h={"Authorization":"Bearer "+login.json()["access_token"]}
            funded=client.post("/sandbox/faucet",headers={**h,"Idempotency-Key":faucet_key},json={"amount":"1000"})
            assert funded.status_code==201,funded.text
            return h
        seller=create_user("maker@example.test","maker-faucet-001")
        buyer=create_user("taker@example.test","taker-faucet-001")
        seed=client.post("/trade/orders",headers={**seller,"Idempotency-Key":"maker-seed-buy-01"},json={"symbol":"BTC","side":"buy","quote_amount":"100","reference_price":"10000"})
        assert seed.status_code==201,seed.text
        sell=client.post("/trade/limit-orders",headers={**seller,"Idempotency-Key":"maker-limit-sell-01"},json={"symbol":"BTC","side":"sell","quantity":"0.00400000","limit_price":"49000"})
        assert sell.status_code==201,sell.text
        buy=client.post("/trade/limit-orders",headers={**buyer,"Idempotency-Key":"taker-limit-buy-001"},json={"symbol":"BTC","side":"buy","quantity":"0.01000000","limit_price":"50000"})
        assert buy.status_code==201,buy.text
        data=buy.json()
        assert data["status"]=="partially_filled"
        assert data["filled_quantity"]=="0.00400000"
        assert data["remaining_quantity"]=="0.00600000"
        assert len(data["fills"])==1
        assert data["fills"][0]["price"]=="49000"
        maker_orders=client.get("/trade/limit-orders",headers=seller).json()["orders"]
        assert maker_orders[0]["status"]=="filled"
        buyer_orders=client.get("/trade/limit-orders?status=partially_filled",headers=buyer).json()["orders"]
        assert len(buyer_orders)==1
        assert buyer_orders[0]["remaining_quantity"]=="0.00600000"
        assert buyer_orders[0]["reserved_amount"]=="300.30"
        buyer_bal={x["currency"]:x["amount"] for x in client.get("/account/balances",headers=buyer).json()["balances"]}
        seller_bal={x["currency"]:x["amount"] for x in client.get("/account/balances",headers=seller).json()["balances"]}
        assert buyer_bal["BTC"]=="0.00400000"
        assert seller_bal["USD"]=="489.51"
        assert seller_bal["BTC"]=="0.00600000"
        buyer_trades=client.get("/trade/limit-trades",headers=buyer)
        seller_trades=client.get("/trade/limit-trades",headers=seller)
        assert buyer_trades.status_code==200 and len(buyer_trades.json()["trades"])==1
        assert seller_trades.status_code==200 and seller_trades.json()["trades"][0]["side"]=="sell"
        cancelled=client.post("/trade/limit-orders/"+data["id"]+"/cancel",headers=buyer)
        assert cancelled.status_code==200,cancelled.text
        assert cancelled.json()["released_amount"]=="300.30"
        assert client.get("/trade/limit-orders?status=cancelled",headers=buyer).json()["orders"][0]["status"]=="cancelled"
