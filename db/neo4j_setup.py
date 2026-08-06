"""
db/neo4j_setup.py
Thiết lập schema, chỉ mục (index), ràng buộc (constraint) và dữ liệu test
cho Neo4j Community Edition (local, chạy qua Docker, GDS Plugin).
"""
import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


def _get_driver():
    if not NEO4J_URI or not NEO4J_PASSWORD:
        raise RuntimeError("Thiếu NEO4J_URI hoặc NEO4J_PASSWORD trong .env")
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def init_neo4j_schema():
    print(f"Đang kết nối tới Neo4j (local Docker) tại: {NEO4J_URI}...")
    driver = _get_driver()
    try:
        with driver.session() as session:
            session.run("""
                CREATE CONSTRAINT wallet_address_unique IF NOT EXISTS
                FOR (w:Wallet) REQUIRE w.address IS UNIQUE
            """)
            print("✅ Constraint Wallet(address) UNIQUE — OK")

            session.run("""
                CREATE INDEX wallet_sanctioned_idx IF NOT EXISTS
                FOR (w:Wallet) ON (w.is_sanctioned)
            """)
            print("✅ Index Wallet(is_sanctioned) — OK")

            # Xác nhận GDS plugin hoạt động
            result = session.run("RETURN gds.version() AS v")
            version = result.single()["v"]
            print(f"✅ GDS Plugin sẵn sàng — version {version}")
    finally:
        driver.close()


def seed_test_data():
    """
    Chèn dữ liệu test tối thiểu để kiểm thử Graph Assistant độc lập,
    trước khi có dữ liệu Etherscan thật từ Phần 3.
    KHÔNG chứa PII — chỉ địa chỉ ví công khai giả lập.
    """
    driver = _get_driver()
    edges = [
        ("0xblacklisted_seed_wallet", "0xlayer1_mixer_a", 100.0),
        ("0xblacklisted_seed_wallet", "0xlayer1_mixer_b", 50.0),
        ("0xlayer1_mixer_a", "0xuser_target_wallet", 80.0),
        ("0xlayer1_mixer_b", "0xclean_node", 45.0),
        ("0xuser_target_wallet", "0xdestination_wallet", 75.0),
        ("0xdestination_wallet", "0xexchange_cashout", 70.0),
        # 1 ví hoàn toàn tách biệt để test "ví xa / không liên quan"
        ("0xisolated_wallet_a", "0xisolated_wallet_b", 10.0),
    ]
    try:
        with driver.session() as session:
            for src, dst, amount in edges:
                session.run("""
                    MERGE (a:Wallet {address: $src})
                    MERGE (b:Wallet {address: $dst})
                    MERGE (a)-[r:TRANSFER]->(b)
                    SET r.amount = $amount
                """, src=src, dst=dst, amount=amount)

            session.run("""
                MATCH (w:Wallet {address: '0xblacklisted_seed_wallet'})
                SET w.is_sanctioned = true
            """)
        print(f"✅ Đã seed {len(edges)} cạnh TRANSFER + 1 ví is_sanctioned=true")
    finally:
        driver.close()


if __name__ == "__main__":
    init_neo4j_schema()
    seed_test_data()