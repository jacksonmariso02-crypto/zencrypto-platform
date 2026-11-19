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

def test_compliance_disclosure_and_real_money_routes_stay_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "compliance.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        reg = client.post("/auth/register", json={"email": "compliance@example.test", "password": "CorrectHorseBattery12"})
        assert reg.status_code == 201
        login = client.post("/auth/login", json={"email": "compliance@example.test", "password": "CorrectHorseBattery12"})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}

        status = client.get("/compliance/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["real_money_allowed"] is False
        assert status.json()["withdrawals_allowed"] is False
        assert status.json()["mode"] == "PAPER_ONLY"

        kyc = client.post("/kyc/submit", headers=headers, json={"legal_name": "Demo User", "country": "US"})
        assert kyc.status_code == 200
        assert kyc.json()["status"] == "pending_provider_verification"
        assert "no identity verification" in kyc.json()["notice"].lower()

        case = client.post("/compliance/cases", headers=headers, json={"reason": "Test review case for sandbox workflow"})
        assert case.status_code == 201
        assert case.json()["status"] == "open"

        assert client.post("/deposits/request", headers=headers, json={"currency": "USD", "amount": "10"}).status_code == 403
        assert client.post("/withdrawals/request", headers=headers, json={"currency": "USD", "amount": "10", "destination": "bank"}).status_code == 403
        assert client.get("/admin/audit", headers=headers).status_code == 403
        assert client.get("/admin/ledger/integrity", headers=headers).status_code == 403

def test_sandbox_deposit_withdrawal_reservation_and_admin_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "transfers.db"))
    main.init()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        reg = client.post("/auth/register", json={"email": "transfer-user@example.test", "password": "CorrectHorseBattery12"})
        assert reg.status_code == 201
        login = client.post("/auth/login", json={"email": "transfer-user@example.test", "password": "CorrectHorseBattery12"})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}

        deposit = client.post("/sandbox/deposits", headers={**headers, "Idempotency-Key": "deposit-test-0001"},
                              json={"amount": "100", "currency": "USD"})
        assert deposit.status_code == 201, deposit.text
        assert deposit.json()["status"] == "sandbox_credited"
        assert "No blockchain deposit" in deposit.json()["notice"]

        again = client.post("/sandbox/deposits", headers={**headers, "Idempotency-Key": "deposit-test-0001"},
                            json={"amount": "100", "currency": "USD"})
        assert again.status_code == 201
        assert again.json()["id"] == deposit.json()["id"]

        withdrawal = client.post("/sandbox/withdrawal-requests",
                                 headers={**headers, "Idempotency-Key": "withdraw-test-0001"},
                                 json={"amount": "40", "currency": "USD", "destination_label": "My test wallet"})
        assert withdrawal.status_code == 201, withdrawal.text
        assert withdrawal.json()["status"] == "pending_sandbox_review"

        balances = {x["currency"]: float(x["amount"]) for x in
                    client.get("/account/balances", headers=headers).json()["balances"]}
        assert balances["USD"] == 60.0

        history = client.get("/account/requests", headers=headers).json()["requests"]
        assert len(history) == 2
        assert any(x["kind"] == "sandbox_withdrawal" and x["status"] == "pending_sandbox_review" for x in history)

        with main.db() as conn:
            conn.execute("UPDATE users SET role='admin' WHERE email='transfer-user@example.test'")

        admin_requests = client.get("/admin/requests", headers=headers)
        assert admin_requests.status_code == 200
        resolution = client.post("/admin/requests/" + withdrawal.json()["id"] + "/resolve",
                                 headers=headers, json={"decision": "approve", "note": "Sandbox approval test"})
        assert resolution.status_code == 200, resolution.text
        assert resolution.json()["status"] == "sandbox_approved"
        assert "No external funds" in resolution.json()["notice"]

        balance_after = client.get("/account/balances", headers=headers).json()["balances"]
        usd_after = next(x for x in balance_after if x["currency"] == "USD")
        assert float(usd_after["amount"]) == 60.0

        assert client.post("/deposits/request", headers=headers,
                           json={"currency": "USD", "amount": "10"}).status_code == 403
        assert client.post("/withdrawals/request", headers=headers,
                           json={"currency": "USD", "amount": "10", "destination": "external"}).status_code == 403

def test_dex_quote_proxy_is_disabled_without_server_side_api_key(monkeypatch):
    monkeypatch.delenv("ZEROX_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        response = client.get("/dex/price", params={
            "chainId": 8453,
            "sellToken": "0x4200000000000000000000000000000000000006",
            "buyToken": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "sellAmount": "100000000000000000",
            "taker": "0x0000000000000000000000000000000000000001",
        })
        assert response.status_code == 503
        assert "ZEROX_API_KEY" in response.json()["detail"]


def test_dex_quote_proxy_rejects_unapproved_tokens_before_provider_call(monkeypatch):
    monkeypatch.setenv("ZEROX_API_KEY", "test-key")
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        response = client.get("/dex/price", params={
            "chainId": 8453,
            "sellToken": "0x0000000000000000000000000000000000000001",
            "buyToken": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "sellAmount": "1000000",
            "taker": "0x0000000000000000000000000000000000000001",
        })
        assert response.status_code == 422
        assert "curated WETH/USDC" in response.json()["detail"]

def test_legacy_paper_api_is_closed_when_feature_flag_is_off(monkeypatch):
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "false")
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        response = client.post("/auth/register", json={"email": "blocked@example.test", "password": "CorrectHorseBattery12"})
        assert response.status_code == 404
        assert "paper-trading API is disabled" in response.json()["detail"]


def test_dex_quote_response_rejects_wrong_asset_and_calldata():
    from fastapi import HTTPException
    sell = "0x4200000000000000000000000000000000000006"
    buy = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    taker = "0x0000000000000000000000000000000000000001"
    good = {
        "liquidityAvailable": True,
        "sellToken": sell, "buyToken": buy, "sellAmount": "1000000", "buyAmount": "999",
        "transaction": {"from": taker, "to": "0x0000000000000000000000000000000000000002",
                        "data": "0x12345678", "value": "0"},
    }
    def check(payload):
        return main.validate_dex_quote_response(payload, chain_id=8453, sell_token=sell,
                                                buy_token=buy, sell_amount="1000000", taker=taker)
    assert check(good) == good
    for changed in (
        {"sellToken": buy},
        {"buyToken": sell},
        {"sellAmount": "999999"},
        {"buyAmount": "0"},
        {"transaction": {**good["transaction"], "data": "0xgarbage"}},
        {"transaction": {**good["transaction"], "value": "0x1"}},
        {"transaction": {**good["transaction"], "from": "0x0000000000000000000000000000000000000003"}},
        {"issues": {"allowance": {"spender": "0x0000000000000000000000000000000000000004"}}},
    ):
        with __import__("pytest").raises(HTTPException):
            check({**good, **changed})
