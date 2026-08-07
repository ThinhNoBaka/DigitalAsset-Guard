# agents/graph_aml.py
"""
Graph Assistant - Phân tích cấu trúc dòng tiền đa hop.
SPEC_v2: KHÔNG tự boost điểm khi wallet bị sanction.
Trả về graph_score (PPR), community_id, suspicious_path, metadata current_wallet_is_sanctioned.
"""

import os
import networkx as nx
from networkx.algorithms.community import louvain_communities
from core.config import DEMO_MODE
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState
import logging

logging.getLogger("neo4j").setLevel(logging.ERROR)

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        uri = os.getenv("NEO4J_URI")
        user = os.getenv("NEO4J_USER", "neo4j")
        pwd = os.getenv("NEO4J_PASSWORD")
        _driver = GraphDatabase.driver(uri, auth=(user, pwd))
    return _driver


def _analyze_via_networkx(
    wallet_from: str,
    wallet_to: str,
    mock_edges=None,
    mock_blacklisted_wallets=None,
):
    """
    Demo Mode: dùng NetworkX với dữ liệu mock hoặc đồ thị mặc định.
    KHÔNG nhận has_ofac_flag để boost điểm nữa.
    Trả về: (graph_score, community_id, target_community_nodes, hop_distance, fan_out, suspicious_path, current_wallet_is_sanctioned)
    """
    if not mock_edges:
        # KHÔNG có dữ liệu đồ thị thật cho giao dịch này (chưa truyền
        # mock_graph_edges, và ở demo mode không có nguồn Neo4j thật để
        # query). TRƯỚC ĐÂY code dùng 1 đồ thị mặc định cố định (bad_seed →
        # mixer_a → wallet_from) khiến MỌI wallet_from đều cách blacklist
        # đúng 2 hop bất kể giao dịch thật là gì — Decision Engine (Rule 4:
        # hop_distance_to_blacklist <= 2 → REPORT) sẽ báo động giả 100% các
        # giao dịch demo không kèm mock data, vì "2 hop" đó không phản ánh gì
        # về giao dịch thật cả, chỉ là placeholder graph.
        #
        # Để nhất quán với hành vi _analyze_via_neo4j (khi ví không tồn tại
        # trong Neo4j graph thật, trả None thay vì suy diễn) — nhánh NetworkX
        # cũng trả "không có dữ liệu" thay vì tự vẽ đồ thị giả. Quyết định
        # thiết kế mock/demo graph thay thế sẽ làm riêng sau, tách khỏi logic
        # tính hop_distance thật.
        return 0.0, None, [], None, 0, [], False

    G = nx.DiGraph()
    for u, v, amount in mock_edges:
        G.add_edge(u, v, weight=float(amount))
    blacklisted = list(mock_blacklisted_wallets or [])

    # Personalization: tập trung 0.7 vào TOÀN BỘ ví trong blacklisted có mặt trong G
    personalization = {node: 0.0 for node in G.nodes()}
    blacklisted_in_graph = [w for w in blacklisted if w in G]
    if blacklisted_in_graph:
        share = 0.7 / len(blacklisted_in_graph)
        for w in blacklisted_in_graph:
            personalization[w] = share

    # KHÔNG còn phần boost cho has_ofac_flag

    total_p = sum(personalization.values())
    personalization = (
        {k: v / total_p for k, v in personalization.items()} if total_p > 0 else None
    )

    try:
        ppr_scores = nx.pagerank(G, alpha=0.85, personalization=personalization, weight="weight")
    except Exception:
        ppr_scores = nx.pagerank(G, alpha=0.85, weight="weight")

    graph_score = float(ppr_scores.get(wallet_from, 0.0))

    # Louvain — resolution=0.5 phù hợp cho đồ thị mock (thường nhỏ, cần cụm
    # mịn hơn để phân biệt community). Nếu sau này có nguồn graph thật khác
    # (không qua mock_graph_edges) cho nhánh NetworkX, cần xem lại resolution
    # này theo đặc điểm dữ liệu đó.
    communities = louvain_communities(G.to_undirected(), seed=42, weight="weight", resolution=0.5)
    community_id, target_community_nodes = 0, list(G.nodes())
    for idx, comm in enumerate(communities):
        if wallet_from in comm:
            community_id, target_community_nodes = idx, list(comm)
            break

    # Khoảng cách hop đến blacklisted gần nhất
    hop_distance = None
    suspicious_path = []
    for source in blacklisted_in_graph:
        try:
            path = nx.shortest_path(G, source=source, target=wallet_from)
            hop = len(path) - 1
            if hop_distance is None or hop < hop_distance:
                hop_distance, suspicious_path = hop, path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    fan_out = G.out_degree(wallet_from) if wallet_from in G else 0

    # Metadata: wallet_from có nằm trong blacklisted không?
    current_wallet_is_sanctioned = wallet_from in blacklisted

    return graph_score, community_id, target_community_nodes, hop_distance, fan_out, suspicious_path, current_wallet_is_sanctioned


def _analyze_via_neo4j(
    wallet_from: str,
    wallet_to: str,
    mock_edges=None,
    mock_blacklisted_wallets=None,
):
    """
    Production Mode: Neo4j GDS.
    KHÔNG dùng has_ofac_flag boost.
    """
    graph_name = "wallet_graph_tmp"
    driver = _get_driver()

    try:
        with driver.session() as session:
            # Nếu có mock_edges, tạo dữ liệu tạm trong Neo4j
            using_mock = bool(mock_edges)
            if using_mock:
                # Xóa dữ liệu cũ (các node có prefix mock)
                session.run("MATCH (w:Wallet) WHERE w.address STARTS WITH '0xsmurf' OR w.address STARTS WITH '0xlayering' DETACH DELETE w")
                # Tạo node và relationship
                for u, v, amount in mock_edges:
                    session.run(
                        "MERGE (a:Wallet {address: $u}) "
                        "MERGE (b:Wallet {address: $v}) "
                        "CREATE (a)-[:TRANSFER {amount: $amount}]->(b)",
                        u=u, v=v, amount=float(amount)
                    )
                # Đánh dấu ví đen
                for addr in mock_blacklisted_wallets or []:
                    session.run(
                        "MATCH (w:Wallet {address: $addr}) SET w.is_sanctioned = true",
                        addr=addr
                    )

            # Kiểm tra tồn tại của wallet_from
            exists = session.run(
                "MATCH (w:Wallet {address: $addr}) RETURN count(w) AS c",
                addr=wallet_from,
            ).single()["c"]
            if exists == 0:
                # Không có dữ liệu -> trả về mặc định
                return 0.0, 0, [wallet_from], None, 0, [], False

            # Xóa projection cũ
            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
            )

            # Project graph
            session.run("""
                CALL gds.graph.project(
                    $name, 'Wallet', 'TRANSFER',
                    {relationshipProperties: 'amount'}
                )
            """, name=graph_name)

            # Lấy internal nodeId của các ví is_sanctioned=true
            source_nodes = [
                r["id"] for r in session.run("""
                    MATCH (w:Wallet {is_sanctioned: true})
                    RETURN id(w) AS id
                """)
            ]

            ppr_config = {"dampingFactor": 0.85, "relationshipWeightProperty": "amount"}
            if source_nodes:
                ppr_config["sourceNodes"] = source_nodes

            # PPR
            ppr_result = session.run(
                "CALL gds.pageRank.stream($name, $config) "
                "YIELD nodeId, score "
                "RETURN gds.util.asNode(nodeId).address AS address, score",
                name=graph_name, config=ppr_config,
            )
            ppr_scores = {r["address"]: r["score"] for r in ppr_result}

            # Louvain
            louvain_result = session.run(
                "CALL gds.louvain.stream($name) "
                "YIELD nodeId, communityId "
                "RETURN gds.util.asNode(nodeId).address AS address, communityId",
                name=graph_name,
            )
            community_map = {}
            for r in louvain_result:
                community_map.setdefault(r["communityId"], []).append(r["address"])

            # Shortest path to blacklisted
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
                print(f"⚠️ Không tính được hop_distance: {e}")

            # Fan-out
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

            # Xóa projection
            session.run("CALL gds.graph.drop($name) YIELD graphName", name=graph_name)

            graph_score = float(ppr_scores.get(wallet_from, 0.0))
            wf_community_id, wf_community_nodes = 0, [wallet_from]
            for cid, nodes in community_map.items():
                if wallet_from in nodes:
                    wf_community_id, wf_community_nodes = cid, nodes
                    break

            # Metadata: wallet_from có is_sanctioned?
            current_wallet_is_sanctioned = False
            if using_mock:
                # mock: kiểm tra trong mock_blacklisted_wallets
                current_wallet_is_sanctioned = wallet_from in (mock_blacklisted_wallets or [])
            else:
                # Neo4j thật: query property
                res = session.run(
                    "MATCH (w:Wallet {address: $addr}) RETURN w.is_sanctioned AS s",
                    addr=wallet_from
                ).single()
                if res:
                    current_wallet_is_sanctioned = res["s"] or False

            return graph_score, wf_community_id, wf_community_nodes, hop_distance, fan_out, suspicious_path, current_wallet_is_sanctioned

    except Exception as e:
        print(f"⚠️ Lỗi Graph Assistant (Neo4j/GDS): {e}")
        raise


def analyze_graph(state: AMLState) -> AMLState:
    """Điều phối chính của Graph Assistant."""
    assert_no_raw_pii(state)

    wallet_from = state.get("wallet_from", "").strip()
    wallet_to = state.get("wallet_to", "").strip()

    # Lấy mock data từ state (nếu có)
    mock_edges = state.get("mock_graph_edges")
    mock_blacklisted = state.get("mock_blacklisted_wallets")

    if DEMO_MODE:
        graph_score, comm_id, comm_nodes, hop_distance, fan_out, suspicious_path, current_sanctioned = _analyze_via_networkx(
            wallet_from, wallet_to,
            mock_edges=mock_edges,
            mock_blacklisted_wallets=mock_blacklisted,
        )
        engine = "NetworkX"
    else:
        graph_score, comm_id, comm_nodes, hop_distance, fan_out, suspicious_path, current_sanctioned = _analyze_via_neo4j(
            wallet_from, wallet_to,
            mock_edges=mock_edges,
            mock_blacklisted_wallets=mock_blacklisted,
        )
        engine = "Neo4j GDS"

    state["graph_score"] = graph_score  # đổi tên từ graph_risk_score
    state["community_id"] = comm_id
    state["hop_distance_to_blacklist"] = hop_distance
    state["fan_out"] = fan_out
    state["suspicious_path"] = suspicious_path
    state["current_wallet_is_sanctioned"] = current_sanctioned

    state["thought"] = (
        f"GraphAgent chạy trên {engine}. "
        f"Phát hiện ví thuộc cụm #{comm_id} với {len(comm_nodes)} nút liên đới."
        + (f" Cách ví trong danh sách trừng phạt {hop_distance} hop." if hop_distance is not None else "")
        + (f" (Ví hiện tại {'CÓ' if current_sanctioned else 'KHÔNG'} bị sanction.)")
    )
    return state