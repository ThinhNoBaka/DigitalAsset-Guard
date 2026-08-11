"""VERIFY CUỐI — gửi case qua API thật và in nguyên văn legal_citations."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

# 1. Login
r = client.post("/api/auth/login", json={"username": "nhanvien1", "password": "123456789"})
print("LOGIN_STATUS=" + str(r.status_code))
assert r.status_code == 200, r.text
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Pipeline run — case demo thật (input theo task)
payload = {
    "wallet_from": "0x28C6c06298d514Db089934071355E5743bf21d60",
    "wallet_to": "0x000000000000000000000000000000000000dEaD",
    "amount_vnd": 200000000,
    "fullname": "Nguyen Van A",
    "id_number": "012345678901",
    "account_number": "1000000001",
}
r = client.post("/api/pipeline/run", json=payload, headers=headers)
print("PIPELINE_STATUS=" + str(r.status_code))
if r.status_code != 200:
    print("PIPELINE_BODY=" + r.text[:3000])
    sys.exit(1)

state = r.json()["state"]
print("DECISION=" + str(state.get("decision")))
print("CASE_STATUS=" + str(state.get("case_status")))
print("LEGAL_SOURCES_RETRIEVED=" + json.dumps(state.get("legal_sources_retrieved"), ensure_ascii=False))

# 3. In NGUYÊN VĂN legal_citations (KHÔNG paraphrase)
print("=== LEGAL_CITATIONS_RAW ===")
print(json.dumps(state.get("legal_citations"), ensure_ascii=False, indent=2, default=str))