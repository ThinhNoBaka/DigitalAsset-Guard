"""TEST D — gửi request demo case qua API thật (/api/pipeline/run) với GRAPH_SOURCE=neo4j."""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["GRAPH_SOURCE"] = "neo4j"

out = open("logs/testd_api_real.txt", "w", encoding="utf-8")


def w(s):
    out.write(str(s) + "\n")


try:
    from fastapi.testclient import TestClient

    # Import sau khi set GRAPH_SOURCE để resolve đúng production provider
    from api.main import app

    client = TestClient(app)

    # 1. Login
    r = client.post("/api/auth/login", json={"username": "nhanvien1", "password": "123456789"})
    w("LOGIN_STATUS=" + str(r.status_code))
    if r.status_code != 200:
        w("LOGIN_BODY=" + r.text)
        out.close()
        sys.exit(0)
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Pipeline run — demo case
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
    w("PIPELINE_STATUS=" + str(r.status_code))
    if r.status_code == 200:
        state = r.json()["state"]
        w("state.graph_analysis_status=" + str(state.get("graph_analysis_status")))
        w("state.graph_data_available=" + str(state.get("graph_data_available")))
        w("state.sanction_path_found=" + str(state.get("sanction_path_found")))
        w("state.graph_score=" + str(state.get("graph_score")))
        w("state.hop_distance_to_blacklist=" + str(state.get("hop_distance_to_blacklist")))
        w("state.suspicious_path=" + str(state.get("suspicious_path")))
        w("state.fan_out=" + str(state.get("fan_out")))
        w("state.community_id=" + str(state.get("community_id")))
        w("state.decision=" + str(state.get("decision")))
        w("state.case_status=" + str(state.get("case_status")))
        w("state.insufficient_data=" + str(state.get("insufficient_data")))
        w("state.mock_graph_edges_present=" + str("mock_graph_edges" in state))
        w("state.mock_blacklisted_present=" + str("mock_blacklisted_wallets" in state))
        w("STATE_DUMP=" + str(state))
    else:
        w("PIPELINE_BODY=" + r.text[:3000])
except Exception as e:
    w("TC_FAILED: " + repr(e))
    w(traceback.format_exc())

out.close()
print("SEE logs/testd_api_real.txt")