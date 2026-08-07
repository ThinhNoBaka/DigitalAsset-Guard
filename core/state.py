# core/state.py
"""
AMLState - Schema cho toàn bộ pipeline.
Tuân thủ SPEC_v2: tách Risk Assessment và Compliance Screening.
Không chứa PII gốc, chỉ chứa các trường đã băm.

FIX so với bản trước: bổ sung mock_graph_edges / mock_blacklisted_wallets —
2 field agents/graph_aml.py đang đọc (state.get(...)) nhưng chưa được khai
báo trong AMLState. Theo nguyên tắc #10.2 (PROJECT_SUMMARY.md): mọi field
state phải được khai báo ở đây, không được "ngầm" tồn tại chỉ vì .get()
không crash lúc runtime.
"""

from typing import TypedDict, Optional, List, Dict, Any, Tuple


class AMLState(TypedDict, total=False):
    # === Transaction metadata ===
    tx_hash: str
    wallet_from: str
    wallet_to: str
    amount_vnd: float

    # === Privacy Layer (hashed PII, không raw) ===
    hashed_fullname: Optional[str]
    hashed_id_number: Optional[str]
    hashed_account_number: Optional[str]

    # === Risk Assessment (điểm số) ===
    classifier_score: Optional[float]       # từ Transaction Assistant
    graph_score: Optional[float]            # từ Graph Assistant (KHÔNG còn dùng để tính risk_assessment_score)
    risk_assessment_score: Optional[float]
    # ĐÃ BỎ weighted-sum (0.3*classifier + 0.7*graph) — xem docstring đầu
    # agents/decision_engine.py để biết lý do (thiếu dữ liệu ghép cặp có
    # nhãn thật để calibrate weights/threshold cho công thức tổng hợp).
    # Decision Engine hiện dùng RULE-BASED COMPOSITE: mỗi tín hiệu
    # (classifier_score vs θ calibrate, hop_distance_to_blacklist, sanctions,
    # structuring) được xét độc lập. Field này luôn = None từ nay, giữ lại
    # trong schema chỉ để code cũ (UI/report) đọc không bị crash — KHÔNG
    # dùng làm căn cứ quyết định.

    # === Aggregation Monitor (structuring / smurfing detection) ===
    # Bổ sung sau SPEC_v2 — xem agents/aggregation_monitor.py để biết cách
    # tính. Đọc từ wallet_tx_history (field non-standard, không khai báo ở
    # đây, cùng quy ước với transaction_classifier.py).
    is_large_tx: Optional[bool]
    # amount_vnd >= config.REPORT_THRESHOLD_VND — nghĩa vụ báo cáo giao dịch
    # lớn theo TT27, ĐỘC LẬP với risk score. Chỉ mang tính thông tin, KHÔNG
    # tự động kích hoạt REPORT (structuring_flag mới là tín hiệu quyết định).
    aggregated_amount_7d: Optional[float]
    # Tổng amount_vnd (gồm cả giao dịch hiện tại) của wallet_from trong 7
    # ngày gần nhất. None nếu không có wallet_tx_history để tính.
    near_threshold_count_30d: Optional[int]
    # Số giao dịch trong 30 ngày qua có amount nằm trong khoảng
    # [0.9 * REPORT_THRESHOLD_VND, REPORT_THRESHOLD_VND) — dấu hiệu né ngưỡng
    # lặp lại. None nếu không có wallet_tx_history để tính.
    structuring_flag: Optional[bool]
    # True nếu aggregated_amount_7d vượt ngưỡng trong khi giao dịch hiện tại
    # tự nó chưa vượt, HOẶC near_threshold_count_30d vượt ngưỡng lặp lại.
    # False mặc định khi không có dữ liệu lịch sử (không phải "không có structuring"
    # mà là "không đủ dữ liệu để kết luận" — xem decision_evidence khi dùng).

    # === Compliance Screening (fact, không điểm) ===
    sanction_result: Optional[Dict[str, Any]]
    # {
    #   "is_match": bool,
    #   "matched_wallet": Optional[str],
    #   "source": str,                 # "OFAC SDN"
    #   "match_type": Optional[str],   # "Exact" | None  (KHÔNG có "Fuzzy" ở đây)
    #   "program": Optional[str]
    # }
    current_wallet_is_sanctioned: Optional[bool]
    # Graph-derived metadata only.
    # NOT authoritative for sanctions decision.
    # Authoritative source: sanction_result.is_match

    # === Graph details ===
    community_id: Optional[int]
    suspicious_path: Optional[List[str]]
    hop_distance_to_blacklist: Optional[int]
    fan_out: Optional[int]

    # === Mock data cho kịch bản demo (scripts/generate_complex_mock.py) ===
    mock_graph_edges: Optional[List[Tuple[str, str, float]]]
    # [(wallet_u, wallet_v, amount_vnd), ...] — dùng để dựng đồ thị NetworkX/Neo4j
    # động thay cho đồ thị demo cố định 6 cạnh mặc định trong graph_aml.py
    mock_blacklisted_wallets: Optional[List[str]]
    # Danh sách ví coi là is_sanctioned=true trong kịch bản mock, dùng làm
    # personalization source cho PPR và điểm xuất phát tính hop_distance

    # === Name similarity (fuzzy, từ Privacy Layer, TRƯỚC khi băm) ===
    # Nguồn: core/name_screening.py::screen_name_against_sdn(), so khớp
    # fullname gốc với danh sách tên SDN thật (data/raw/ofac/sdn.xml).
    name_similarity_warning: Optional[bool]
    name_similarity_score: Optional[float]
    # % tương đồng Levenshtein (thang 0-100, KHÔNG PHẢI 0-1) -- vd 87.34
    # nghĩa là 87.34%, không phải 0.8734. None nếu không có dữ liệu SDN để
    # so khớp hoặc fullname rỗng.
    name_similarity_matched_name: Optional[str]
    # Tên trong danh sách SDN khớp gần nhất khi warning=True. KHÔNG phải PII
    # của khách hàng (đây là tên từ danh sách công khai OFAC SDN), nên không
    # vi phạm ranh giới Privacy Layer. None khi warning=False.

    # === Decision Engine output (module duy nhất được ghi các field này) ===
    decision: Optional[str]                  # "PASS" | "REVIEW" | "REPORT"
    # REVIEW = mới thêm khi pivot sang rule-based composite: 2 tín hiệu
    # "medium" (classifier + graph) trùng nhau cùng lúc — xem
    # agents/decision_engine.py Rule 5. case_status vẫn là "pending_review"
    # cho cả REVIEW lẫn REPORT (HITL xử lý chung 1 hàng đợi, phân biệt qua
    # field `decision`, không qua case_status).
    decision_reason: Optional[str]
    decision_evidence: Optional[List[str]]
    case_status: Optional[str]                # "auto_cleared" | "pending_review"

    # === RAG output ===
    legal_citations: Optional[List[str]]
    legal_sources_retrieved: Optional[List[str]]

    # === Report ===
    report_path: Optional[str]

    # === Human-in-the-loop ===
    approval_status: Optional[str]            # "pending" | "approved" | "rejected"

    # === Misc / Explainability ===
    top_features: Optional[List[Tuple[str, float]]]
    avg_time_between_tx: Optional[float]
    balance_clustering_flag: Optional[bool]
    thought: Optional[str]