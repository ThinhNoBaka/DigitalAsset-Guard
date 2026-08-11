"""TEST B + C — GRAPH_SOURCE=neo4j TRÊN NEo4j/GDS THẬT (tạm thời, phục vụ verify).
TEST B: graph có data cho wallet nhưng KHÔNG có path tới ví sanction.
TEST C: wallet KHÔNG tồn tại trong Neo4j (NO_GRAPH_DATA).
Mục đích theo feedback #1: xác nhận semantics 2 engine (mock vs neo4j) KHÔNG lệch."""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["GRAPH_SOURCE"] = "neo4j"

out = open("logs/testd_neo4j_bc.txt", "w", encoding="utf-8")


def w(s):
    out.write(str(s) + "\n")


try:
    from neo4j import GraphDatabase

    d = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "123456789"))

    # === Seed TEST B: cụm wallets TÁCH BIỆT (không kết nối tới 0xSANCTIONED_DEMO_SOURCE) ===
    with d.session() as s:
        s.run(
            "MERGE (a:Wallet {address: $p0}) MERGE (b:Wallet {address: $p1}) "
            "MERGE (a)-[r:TRANSFER]->(b) SET r.amount = $p2",
            p0="0xB_ISOLATED_SRC", p1="0xB_TEST_WALLET", p2=150000000.0,
        )
        s.run(
            "MERGE (a:Wallet {address: $p0}) MERGE (b:Wallet {address: $p1}) "
            "MERGE (a)-[r:TRANSFER]->(b) SET r.amount = $p2",
            p0="0xB_TEST_WALLET", p1="0xB_ISOLATED_DEST", p2=140000000.0,
        )
        w("SEED_B=OK")
    d.close()

    import core.config
    from agents.graph_aml import analyze_graph

    # ========================= TEST B =========================
    st_b = {
        "wallet_from": "0xB_TEST_WALLET",
        "wallet_to": "0xB_ISOLATED_DEST",
        "amount_vnd": 140000000.0,
        "hashed_fullname": "x",
    }
    r_b = analyze_graph(st_b)
    w("")
    w("=== TEST B (graph có data, KHÔNG path tới ví sanction) — NEo4j/GDS THẬT ===")
    w("status=" + str(r_b.get("graph_analysis_status")))
    w("graph_data_available=" + str(r_b.get("graph_data_available")))
    w("sanction_path_found=" + str(r_b.get("sanction_path_found")))
    w("hop=" + str(r_b.get("hop_distance_to_blacklist")))
    w("community_id=" + str(r_b.get("community_id")))
    w("fan_out=" + str(r_b.get("fan_out")))
    w("graph_score=" + str(r_b.get("graph_score")))
    w("suspicious_path=" + str(r_b.get("suspicious_path")))

    # ========================= TEST C =========================
    st_c = {
        "wallet_from": "0xNEVER_SEEN_WALLET_00000000000000000000000000000",
        "wallet_to": "0x000000000000000000000000000000000000dEaD",
        "amount_vnd": 50000000.0,
        "hashed_fullname": "x",
    }
    r_c = analyze_graph(st_c)
    w("")
    w("=== TEST C (ví KHÔNG tồn tại trong Neo4j) — NEo4j/GDS THẬT ===")
    w("status=" + str(r_c.get("graph_analysis_status")))
    w("graph_data_available=" + str(r_c.get("graph_data_available")))
    w("sanction_path_found=" + str(r_c.get("sanction_path_found")))
    w("hop=" + str(r_c.get("hop_distance_to_blacklist")))
    w("community_id=" + str(r_c.get("community_id")))
    w("fan_out=" + str(r_c.get("fan_out")))
    w("graph_score=" + str(r_c.get("graph_score")))
    w("suspicious_path=" + str(r_c.get("suspicious_path")))
    w("TC_DONE")

except Exception as e:
    w("TC_FAILED: " + repr(e))
    w(traceback.format_exc())

out.close()
print("SEE logs/testd_neo4j_bc.txt")