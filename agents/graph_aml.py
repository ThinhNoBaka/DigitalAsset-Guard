"""
agents/graph_aml.py
Agent phân tích cấu trúc dòng tiền đa hop (on-chain data).
DEMO_MODE=True  -> NetworkX (mock local)
DEMO_MODE=False -> Neo4j Community local (Docker) + GDS Plugin (PPR + Louvain thật)
"""
import os
import networkx as nx
from networkx.algorithms.community import louvain_communities
from core.config import DEMO_MODE
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState
import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)

_driver = None  # singleton, khởi tạo lười (lazy) chỉ khi DEMO_MODE=False


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        uri = os.getenv("NEO4J_URI")
        user = os.getenv("NEO4J_USER", "neo4j")
        pwd = os.getenv("NEO4J_PASSWORD")
        _driver = GraphDatabase.driver(uri, auth=(user, pwd))
    return _driver


def _analyze_via_networkx(wallet_from: str, wallet_to: str, has_ofac_flag: bool):
    """[Demo Mode] Giả lập đồ thị cục bộ bằng NetworkX khi DEMO_MODE=True"""
    G = nx.DiGraph()
    bad_seed = "0xblacklisted_seed_wallet"

    edges = [
        (bad_seed, "0xlayer1_mixer_a", {"amount": 100.0}),
        (bad_seed, "0xlayer1_mixer_b", {"amount": 50.0}),
        ("0xlayer1_mixer_a", wallet_from, {"amount": 80.0}),
        ("0xlayer1_mixer_b", "0xclean_node", {"amount": 45.0}),
        (wallet_from, wallet_to, {"amount": 75.0}),
        (wallet_to, "0xexchange_cashout", {"amount": 70.0}),
    ]
    for u, v, data in edges:
        G.add_edge(u, v, weight=data["amount"])

    personalization = {node: 0.0 for node in G.nodes()}
    if bad_seed in G:
        personalization[bad_seed] = 0.7
    if has_ofac_flag and wallet_from in G:
        personalization[wallet_from] = 0.3

    total_p = sum(personalization.values())
    personalization = (
        {k: v / total_p for k, v in personalization.items()} if total_p > 0 else None
    )

    try:
        ppr_scores = nx.pagerank(G, alpha=0.85, personalization=personalization, weight="weight")
    except Exception:
        ppr_scores = nx.pagerank(G, alpha=0.85, weight="weight")

    communities = louvain_communities(G, seed=42)
    community_id, target_community_nodes = 0, list(G.nodes())
    for idx, comm in enumerate(communities):
        if wallet_from in comm:
            community_id, target_community_nodes = idx, list(comm)
            break

    risk_score = float(ppr_scores.get(wallet_from, 0.0))

    # [Bổ sung -- dữ liệu phục vụ Explainable AI + Graph Visualization, Thay đổi 3]
    # Dùng lại đúng graph G và bad_seed đã dựng ở trên, không tính lại từ đầu.
    try:
        hop_distance = nx.shortest_path_length(G, source=bad_seed, target=wallet_from)
        suspicious_path = nx.shortest_path(G, source=bad_seed, target=wallet_from)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        hop_distance = None
        suspicious_path = []

    fan_out = G.out_degree(wallet_from) if wallet_from in G else 0

    return risk_score, community_id, target_community_nodes, hop_distance, fan_out, suspicious_path


def _analyze_via_neo4j(wallet_from: str, wallet_to: str, has_ofac_flag: bool):
    """
    [Production Mode] PPR + Louvain thật qua Neo4j GDS Plugin.
    Cùng thuật toán / cùng thang điểm với bản NetworkX ở trên.
    """
    graph_name = "wallet_graph_tmp"
    driver = _get_driver()

    try:
        with driver.session() as session:
            # 1. Kiểm tra ví có tồn tại trong DB không (tránh lỗi khi projection rỗng)
            exists = session.run(
                "MATCH (w:Wallet {address: $addr}) RETURN count(w) AS c",
                addr=wallet_from,
            ).single()["c"]
            if exists == 0:
                # Ví chưa có dữ liệu giao dịch nào -> điểm mặc định an toàn
                return 0.0, 0, [wallet_from], None, 0, []

            # 2. Xoá projection cũ nếu còn sót (từ lần gọi trước bị lỗi giữa chừng)
            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
            )

            # 3. Project subgraph vào GDS (native projection)
            session.run("""
                CALL gds.graph.project(
                    $name, 'Wallet', 'TRANSFER',
                    {relationshipProperties: 'amount'}
                )
            """, name=graph_name)

            # 4. Lấy internal nodeId của các ví is_sanctioned=true làm nguồn PPR
            source_nodes = [
                r["id"] for r in session.run("""
                    MATCH (w:Wallet {is_sanctioned: true})
                    RETURN id(w) AS id
                """)
            ]
            # Nếu KYC Assistant đã gắn cờ OFAC cho chính wallet_from, cộng thêm vào nguồn
            # (tương đương has_ofac_flag trong bản NetworkX)
            if has_ofac_flag:
                wf_id = session.run(
                    "MATCH (w:Wallet {address: $addr}) RETURN id(w) AS id",
                    addr=wallet_from,
                ).single()
                if wf_id and wf_id["id"] not in source_nodes:
                    source_nodes.append(wf_id["id"])

            ppr_config = {"dampingFactor": 0.85, "relationshipWeightProperty": "amount"}
            if source_nodes:
                ppr_config["sourceNodes"] = source_nodes

            # 5. PageRank cá nhân hóa (PPR)
            ppr_result = session.run(
                "CALL gds.pageRank.stream($name, $config) "
                "YIELD nodeId, score "
                "RETURN gds.util.asNode(nodeId).address AS address, score",
                name=graph_name, config=ppr_config,
            )
            ppr_scores = {r["address"]: r["score"] for r in ppr_result}

            # 6. Louvain community detection
            louvain_result = session.run(
                "CALL gds.louvain.stream($name) "
                "YIELD nodeId, communityId "
                "RETURN gds.util.asNode(nodeId).address AS address, communityId",
                name=graph_name,
            )
            community_map = {}
            for r in louvain_result:
                community_map.setdefault(r["communityId"], []).append(r["address"])

            # 6b. [Bổ sung -- Thay đổi 3] Đường đi ngắn nhất tới ví bị trừng phạt gần
            # nhất, bằng Cypher thuần (shortestPath), KHÔNG cần GDS -- chạy độc lập
            # với projection ở trên, không ảnh hưởng logic PPR/Louvain.
            hop_distance = None
            suspicious_path = []
            try:
                path_result = session.run(
                    """
                    MATCH (target:Wallet {address: $addr})
                    MATCH (black:Wallet {is_sanctioned: true})
                    MATCH p = shortestPath((black)-[:TRANSFER*..10]->(target))
                    RETURN [n IN nodes(p) | n.address] AS path, length(p) AS hop
                    ORDER BY hop ASC
                    LIMIT 1
                    """,
                    addr=wallet_from,
                ).single()
                if path_result:
                    suspicious_path = path_result["path"]
                    hop_distance = path_result["hop"]
            except Exception as e:
                print(f"⚠️ Không tính được hop_distance_to_blacklist: {e}")

            # 6c. [Bổ sung -- Thay đổi 3] Fan-out (out-degree) của ví đang xét.
            fan_out = 0
            try:
                fan_out_result = session.run(
                    "MATCH (w:Wallet {address: $addr})-[:TRANSFER]->(other) "
                    "RETURN count(DISTINCT other) AS fan_out",
                    addr=wallet_from,
                ).single()
                fan_out = fan_out_result["fan_out"] if fan_out_result else 0
            except Exception as e:
                print(f"⚠️ Không tính được fan_out: {e}")

            # 7. Xoá projection để giải phóng bộ nhớ (bắt buộc trên tài nguyên giới hạn)
            session.run("CALL gds.graph.drop($name) YIELD graphName", name=graph_name)

        risk_score = float(ppr_scores.get(wallet_from, 0.0))

        wf_community_id, wf_community_nodes = 0, [wallet_from]
        for cid, nodes in community_map.items():
            if wallet_from in nodes:
                wf_community_id, wf_community_nodes = cid, nodes
                break

        return risk_score, wf_community_id, wf_community_nodes, hop_distance, fan_out, suspicious_path

    except Exception as e:
        # Lỗi hệ thống (kết nối, GDS chưa sẵn sàng, cú pháp...) -> KHÔNG lặng lẽ
        # coi là "ví sạch". Ghi rõ lỗi để Report Assistant / UI biết agent này fail.
        print(f"⚠️ Lỗi Graph Assistant (Neo4j/GDS): {e}")
        raise


def analyze_graph(state: AMLState) -> AMLState:
    """Hàm điều phối chính của Graph Assistant."""
    assert_no_raw_pii(state)

    wallet_from = state.get("wallet_from", "").strip()
    wallet_to = state.get("wallet_to", "").strip()

    kyc_flags = state.get("kyc_flags", [])
    has_ofac_flag = any("match_ofac" in flag for flag in kyc_flags) if kyc_flags else False

    if DEMO_MODE:
        risk_score, comm_id, comm_nodes, hop_distance, fan_out, suspicious_path = _analyze_via_networkx(
            wallet_from, wallet_to, has_ofac_flag
        )
        engine = "NetworkX"
    else:
        risk_score, comm_id, comm_nodes, hop_distance, fan_out, suspicious_path = _analyze_via_neo4j(
            wallet_from, wallet_to, has_ofac_flag
        )
        engine = "Neo4j Community local (GDS Plugin)"

    state["graph_risk_score"] = risk_score
    state["community_id"] = comm_id
    # [Bổ sung -- Thay đổi 3] dữ liệu phục vụ Explainable AI + Graph Visualization
    state["hop_distance_to_blacklist"] = hop_distance
    state["fan_out"] = fan_out
    state["suspicious_path"] = suspicious_path
    state["thought"] = (
        f"GraphAgent chạy trên {engine}. "
        f"Phát hiện ví thuộc cụm #{comm_id} với {len(comm_nodes)} nút liên đới."
        + (f" Cách ví trong danh sách trừng phạt {hop_distance} hop." if hop_distance is not None else "")
    )
    return state


if __name__ == "__main__":
    print("--- Đang chạy kiểm thử Graph Assistant ---")
    print(f"Cấu hình hiện tại: DEMO_MODE = {DEMO_MODE}")

    test_state = AMLState(
        tx_hash="0x77778888",
        wallet_from="0xuser_target_wallet",
        wallet_to="0xdestination_wallet",
        amount_vnd=620_000_000,
        hashed_fullname="5a8a72b527bbd51d",
        hashed_id_number="e8c56e2978cf4d30",
        hashed_account_number="2c36b46b68ffc67f",
        risk_score_classifier=0.45,
        kyc_flags=["wallet_from_match_ofac: 0xblacklisted_seed_wallet"],
        graph_risk_score=None,
        legal_citations=None,
        final_risk_score=None,
        report_path=None,
        approval_status=None,
    )

    updated_state = analyze_graph(test_state)
    print("\n=== KẾT QUẢ KIỂM TRA HỆ THỐNG ===")
    print(f"Điểm rủi ro đồ thị (graph_risk_score): {updated_state['graph_risk_score']}")
    print(f"Hop tới ví trừng phạt gần nhất (hop_distance_to_blacklist): {updated_state.get('hop_distance_to_blacklist')}")
    print(f"Fan-out: {updated_state.get('fan_out')}")
    print(f"Đường đi đáng ngờ (suspicious_path): {updated_state.get('suspicious_path')}")
    print(f"Nhật ký hệ thống (thought): {updated_state['thought']}")