"""
scripts/cleanup_demo_graph_data.py
DỌN DỮ LIỆU DEMO ra khỏi Neo4j TRƯỚC KHI GO-LIVE (production).

MỤC ĐÍCH: Demontrative — data test tạm thời chèn vào Neo4j để verify
TEST D (0xSANCTIONED_DEMO_SOURCE, 0xINTERMEDIATE_DEMO_WALLET, 0xB_TEST_WALLET,
0xB_ISOLATED_SRC, 0xB_ISOLATED_DEST) KHÔNG được phép nằm lại trong graph thật
khi dùng instance này cho production. Chạy script này trước khi deploy.

QUAN TRỌNG:
- Script NÀY là file test/ops RIÊNG, NẰM NGOÀI mọi đường chạy production.
- KHÔNG được import bởi core/graph_builder.py, api/main.py, agents/* — ko
  tự động chạy khi server khởi động với GRAPH_SOURCE=neo4j.
- Chỉ xoá CÁC NODE có prefix demo/test, KHÔNG đụng wallet thật
  (0x28C6... chưa từng bị xoá vì nó là ví demo-onchain).

Chạy: python -m scripts.cleanup_demo_graph_data
"""
import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


def cleanup_demo_graph_data() -> int:
    """
    Xoá toàn bộ node demo/test khỏi Neo4j (có prefix 0xSANCTIONED_DEMO_SOURCE,
    0xINTERMEDIATE_DEMO_WALLET, 0xB_ISOLATED_*, 0xB_TEST_WALLET,
    0xsmurf_*, 0xlayering_* — các node mock từ db/neo4j_setup seed/old test).
    Trả về số node bị xoá.
    """
    if not NEO4J_URI or not NEO4J_PASSWORD:
        raise RuntimeError("Thiếu NEO4J_URI hoặc NEO4J_PASSWORD trong .env")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            # Xoá demo-sanctioned + intermediate + test-B wallets, cùng mọi
            # relationship liên quan (DETACH DELETE)
            result = session.run(
                """
                MATCH (w:Wallet)
                WHERE w.address STARTS WITH '0xSANCTIONED_DEMO_SOURCE'
                   OR w.address STARTS WITH '0xINTERMEDIATE_DEMO_WALLET'
                   OR w.address STARTS WITH '0xB_ISOLATED_'
                   OR w.address = '0xB_TEST_WALLET'
                   OR w.address STARTS WITH '0xsmurf_'
                   OR w.address STARTS WITH '0xlayering_'
                DETACH DELETE w
                RETURN count(w) AS deleted
                """
            )
            deleted = result.single()["deleted"]

            # Xoá luôn node đích demo-theo-Neo4j-setup (nếu còn)
            result2 = session.run(
                """
                MATCH (w:Wallet)
                WHERE w.address IN [
                    '0xblacklisted_seed_wallet', '0xlayer1_mixer_a',
                    '0xlayer1_mixer_b', '0xuser_target_wallet', '0xclean_node',
                    '0xdestination_wallet', '0xexchange_cashout',
                    '0xisolated_wallet_a', '0xisolated_wallet_b'
                ]
                DETACH DELETE w
                RETURN count(w) AS deleted2
                """
            )
            deleted2 = result2.single()["deleted2"]
            total = deleted + deleted2
            return total

    finally:
        driver.close()


if __name__ == "__main__":
    total = cleanup_demo_graph_data()
    print(f"✅ Đã xoá {total} node demo/test khỏi Neo4j. Graph thật sẵn sàng go-live.")