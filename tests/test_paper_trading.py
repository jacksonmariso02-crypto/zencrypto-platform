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
