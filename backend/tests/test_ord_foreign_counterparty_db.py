"""C30: иностранный контрагент через API — хранение реквизитов и upsert (требует DATABASE_URL)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import db
from tests.test_ord_chain_db import _app_as
from tests.test_ord_flow_db import _admin, _conn, _Env


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    db._pool = None
    yield
    db._pool = None


def test_foreign_counterparty_roundtrip(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            client = _app_as(_admin(e))
            body = {"name": "Foreign LLP", "inn": "990000000001", "legal_type": "foreign", "role": "agency",
                    "region_number": "398", "tax_id": "990000000001"}

            r = await client.post("/ord/counterparties", json={**body, "region_number": None})
            assert r.status_code == 422, r.text

            r = await client.post("/ord/counterparties", json=body)
            assert r.status_code == 201, r.text
            cp = r.json()
            assert (cp["region_number"], cp["tax_id"], cp["registration_number"]) == ("398", "990000000001", None)

            r = await client.post(f"/ord/counterparties/{cp['id']}/register")
            assert r.status_code == 200, r.text
            assert r.json()["ord_status"] == "registered"

            # Повтор той же карточки (двойная отправка формы) не сбрасывает статус ОРД.
            r = await client.post("/ord/counterparties", json=body)
            assert r.status_code == 201, r.text
            again = r.json()
            assert again["id"] == cp["id"] and again["ord_status"] == "registered"

            # Смена реквизита — карточку нужно переотправить в ОРД.
            r = await client.post("/ord/counterparties", json={**body, "registration_number": "BIN-1"})
            assert r.json()["registration_number"] == "BIN-1" and r.json()["ord_status"] == "pending"

            with pytest.raises(Exception):
                await conn.execute("UPDATE counterparties SET region_number = 'KZ' WHERE id = $1", cp["id"])
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(scenario())
