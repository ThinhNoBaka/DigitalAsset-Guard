"""TEST D thật — GRAPH_SOURCE=neo4j, chạy trên Neo4j/GDS thật (tạm thời, phục vụ verify)."""
import os
import sys
import traceback
from pathlib import Path

# Chạy từ logs/ -> thêm root project vào sys.path để import core/agents được
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["GRAPH_SOURCE"] = "neo4j"

out = open("logs/testd_neo4j_real.txt", "w", encoding="utf-8")


def w(s):
    out.write(str(s) + "\n")


try:
    from neo4j import GraphDatabase

    d = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "123456789"))

    with d.session() as s:
        w("GDS_VERSION=" + str(s.run("RETURN gds.version() AS v").single()["v"]))

    # Seed demo case theo schema db/neo4j_setup.py
    edges = [
        ("0xSANCTIONED_DEMO_SOURCE", "0xINTERMEDIATE_DEMO_WALLET", 210000000.0),
        ("0xINTERMEDIATE_DEMO_WALLET", "0x28C6c06298d514Db089934071355E5743bf21d60", 205000000.0),
        ("0x28C6c06298d514Db089934071355E5743bf21d60", "0x000000000000000000000000000000000000dEaD", 200000000.0),
    ]
    with d.session() as s:
        for src, dst, amt in edges:
            s.run(
                "MERGE (a:Wallet {address: $p0}) MERGE (b:Wallet {address: $p1}) "
                "MERGE (a)-[r:TRANSFER]->(b) SET r.amount = $p2",
                p0=src, p1=dst, p2=amt,
            )
        s.run(
            "MATCH (w:Wallet {address: $p0}) SET w.is_sanctioned = true",
            p0="0xSANCTIONED_DEMO_SOURCE",
        )
        w("SEEDED=OK")

    # Run REAL Neo4j analysis (API path: KHÔNG có mock fields)
    import core.config
    import core.graph_provider
    import agents.graph_aml
    from agents.graph_aml import analyze_graph

    st = {
        "wallet_from": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "wallet_to": "0x000000000000000000000000000000000000dEaD",
        "amount_vnd": 200000000.0,
        "hashed_fullname": "x",
    }
    r = analyze_graph(st)

    w("GRAPH_SOURCE=" + core.config.resolve_graph_source())
    w("status=" + str(r.get("graph_analysis_status")))
    w("graph_data_available=" + str(r.get("graph_data_available")))
    w("sanction_path_found=" + str(r.get("sanction_path_found")))
    w("graph_score=" + str(r.get("graph_score")))
    w("hop=" + str(r.get("hop_distance_to_blacklist")))
    w("suspicious_path=" + str(r.get("suspicious_path")))
    w("fan_out=" + str(r.get("fan_out")))
    w("community_id=" + str(r.get("community_id")))
    w("current_wallet_is_sanctioned=" + str(r.get("current_wallet_is_sanctioned")))
    w("TC_DONE")

    d.close()
except Exception as e:
    w("TC_FAILED: " + repr(e))
    w(traceback.format_exc())

out.close()
print("SEE logs/testd_neo4j_real.txt")