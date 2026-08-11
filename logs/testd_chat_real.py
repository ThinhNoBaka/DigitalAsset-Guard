"""Feedback #3 — Verify chatbot "Tại sao Risk cao?" trên data THẬT từ Neo4j/GDS.
Chạy pipeline full (GRAPH_SOURCE=neo4j) cho demo case, rồi hỏi chatbot, capture câu trả lời."""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["GRAPH_SOURCE"] = "neo4j"

out = open("logs/testd_chat_real.txt", "w", encoding="utf-8")


def w(s):
    out.write(str(s) + "\n")


try:
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)

    # 1. Login
    r = client.post("/api/auth/login", json={"username": "nhanvien1", "password": "123456789"})
    w("LOGIN=" + str(r.status_code))
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Run pipeline (Neo4j thật — demo case được seed từ testd_neo4j_real.py)
    payload = {
        "tx_hash": "4376436de18c2528001f19befe3f1b23",
        "wallet_from": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "wallet_to": "0x000000000000000000000000000000000000dEaD",
        "amount_vnd": 200000000.0,
        "fullname": "Nguyen Van A",
        "id_number": "012345678901",
        "account_number": "1000000001",
    }
    r = client.post("/api/pipeline/run", json=payload, headers=headers)
    w("PIPELINE=" + str(r.status_code))
    tx_hash = r.json()["tx_hash"]
    w("TX_HASH=" + tx_hash)

    # 3. Chat: "Tại sao Risk cao?"
    r = client.post(
        f"/api/pipeline/{tx_hash}/chat",
        json={"question": "Tại sao Risk cao? Lý do là gì, dùng số liệu nào?"},
        headers=headers,
    )
    w("CHAT_STATUS=" + str(r.status_code))
    if r.status_code == 200:
        w("CHAT_ANSWER=" + r.json()["answer"])
    else:
        w("CHAT_ERROR=" + r.text[:2000])

except Exception as e:
    w("TC_FAILED: " + repr(e))
    w(traceback.format_exc())

out.close()
print("SEE logs/testd_chat_real.txt")