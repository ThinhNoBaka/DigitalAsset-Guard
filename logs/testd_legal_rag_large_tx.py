"""TEST 3b — case amount >= 400 triệu, KHÔNG có bằng chứng graph/sanction.

Thiết kế case để cô lập quy định "giá trị lớn":
- wallet_from=0x28C6c... (ví thật, fetch Etherscan được — classifier ~0.736 > theta)
- scenario="smurfing": edges override KHÔNG chứa wallet này -> hop_distance=None,
  sanction_path_found=False (KHÔNG có graph/sanction evidence)
- account_number="0123456789": structuring thật (4 khoản ~480-495tr) -> REPORT
  qua Rule 2 (evidence chứa "structuring", KHÔNG chứa "graph exposure"/"sanctioned")
- amount=450.000.000: >= ngưỡng 400.000.000 theo QĐ 11/2023/QĐ-TTg

Kỳ vọng: Điều 6 (giá trị lớn) phải được trích với lý do ĐÚNG vì 450tr >= 400tr.
Vì KHÔNG có graph/sanction evidence, không bắt buộc chọn STR làm căn cứ chính.
"""
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

# 2. Pipeline run — amount 450.000.000 (>= ngưỡng 400.000.000 QĐ 11/2023),
# scenario="smurfing" để KHÔNG có graph/sanction evidence,
# account_number="0123456789" để structuring REPORT (Rule 2).
payload = {
    "wallet_from": "0x28C6c06298d514Db089934071355E5743bf21d60",
    "wallet_to": "0x000000000000000000000000000000000000dEaD",
    "amount_vnd": 450000000,
    "fullname": "Nguyen Van A",
    "id_number": "012345678901",
    "account_number": "0123456789",
    "scenario": "smurfing",
}
r = client.post("/api/pipeline/run", json=payload, headers=headers)
print("PIPELINE_STATUS=" + str(r.status_code))
if r.status_code != 200:
    print("PIPELINE_BODY=" + r.text[:3000])
    sys.exit(1)

state = r.json()["state"]
print("DECISION=" + str(state.get("decision")))
print("DECISION_REASON=" + str(state.get("decision_reason")))
print("DECISION_EVIDENCE=" + json.dumps(state.get("decision_evidence"), ensure_ascii=False, indent=2))
print("HOP_DISTANCE=" + str(state.get("hop_distance_to_blacklist")))
print("SANCTION_PATH_FOUND=" + str(state.get("sanction_path_found")))
print("LEGAL_SOURCES_RETRIEVED=" + json.dumps(state.get("legal_sources_retrieved"), ensure_ascii=False))

# 3. In NGUYÊN VĂN legal_citations
print("=== LEGAL_CITATIONS_RAW ===")
print(json.dumps(state.get("legal_citations"), ensure_ascii=False, indent=2, default=str))