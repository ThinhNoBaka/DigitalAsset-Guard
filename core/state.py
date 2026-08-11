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

    # === On-chain data (KHÔNG PII — dữ liệu public) ===
    wallet_record: Optional[Dict[str, Any]]
    # Dữ liệu on-chain của ví (địa chỉ wallet_from), ĐÚNG schema output của
    # scripts/02_fetch_etherscan_sample.py:
    #     {"address": str, "chains": {"ethereum": [txlist]},
    #      "token_transfers": {"ethereum": [tokentx]}}
    # BẮT BUỘC cho Transaction Assistant — xem agents/transaction_classifier.py
    # FIX 2026-08-08: production path build feature vector THẬT qua feature_builder
    # (build_full_wallet_features), KHÔNG được dùng mock. api/main.py fetch động
    # qua scripts/etherscan_fetcher.fetch_wallet_record() rồi gán vào state ngay
    # tại _build_initial_state (trước khi graph chạy).
    # Ví không có giao dịch (hoặc fetch thất bại) → chains/token_transfers = []
    # → feature toàn 0 → classifier chạy bình thường (không crash pipeline).

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
    # tính. NGUỒN DỮ LIỆU OFF-CHAIN Core Banking — ĐỘC LẬP hoàn toàn với
    # wallet_record (ON-CHAIN, từ Etherscan, dùng cho Transaction Classifier/
    # XGBoost). Hai nguồn dữ liệu này KHÔNG được hợp nhất.
    wallet_tx_history: Optional[List[Dict[str, Any]]]
    # Lịch sử giao dịch OFF-CHAIN (Core Banking) — format:
    #     {"timestamp": <unix epoch giây>, "direction": "in"|"out",
    #      "amount": <float VND>, "block": <int, optional>}
    # None/[] = chưa có nguồn dữ liệu off-chain. Khi đó Aggregation Monitor
    # trả về aggregation_status="not_assessed", structuring_detected=None
    # (KHÔNG phải False — "không đánh giá được" khác "đã kiểm tra sạch").
    aggregation_status: Optional[str]
    # Trạng thái đánh giá structuring của Aggregation Monitor:
    #   - "assessed": đã có wallet_tx_history và đã chạy rule structuring thật
    #   - "not_assessed": KHÔNG có dữ liệu off-chain Core Banking để đánh giá —
    #     structuring_detected = None, KHÔNG đồng nghĩa "đã kiểm tra sạch".
    aggregation_reason: Optional[str]
    # Lý do chi tiết khi aggregation_status = "not_assessed" (None khi đã assessed).
    is_large_tx: Optional[bool]
    # amount_vnd >= config.REPORT_THRESHOLD_VND — nghĩa vụ báo cáo giao dịch
    # lớn theo TT27, ĐỘC LẬP với risk score. Chỉ mang tính thông tin, KHÔNG
    # tự động kích hoạt REPORT (structuring_detected mới là tín hiệu quyết định).
    aggregated_amount_7d: Optional[float]
    # Tổng amount_vnd (gồm cả giao dịch hiện tại) của wallet_from trong 7
    # ngày gần nhất. None nếu không có wallet_tx_history để tính.
    near_threshold_count_30d: Optional[int]
    # Số giao dịch trong 30 ngày qua có amount nằm trong khoảng
    # [0.9 * REPORT_THRESHOLD_VND, REPORT_THRESHOLD_VND) — dấu hiệu né ngưỡng
    # lặp lại. None nếu không có wallet_tx_history để tính.
    structuring_detected: Optional[bool]
    # True nếu aggregated_amount_7d vượt ngưỡng trong khi giao dịch hiện tại
    # tự nó chưa vượt, HOẶC near_threshold_count_30d vượt ngưỡng lặp lại.
    # None khi aggregation_status = "not_assessed" (không có wallet_tx_history)
    # — KHÔNG phải False. False CHỈ khi đã chạy rule structuring với dữ liệu
    # off-chain thật và không phát hiện dấu hiệu (xem aggregation_status để
    # phân biệt "đã kiểm tra sạch" vs "chưa đánh giá được").

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

    # === Graph analysis semantics (Phase 2 — phân biệt 3 trạng thái) ===
    # Vì sao thêm: frontend trước đây suy luận từ graph_score=0 / hop=None /
    # fan_out=0 / community_id=0 để đoán "không có graph" — nhưng PPR=0 và
    # fan_out=0 CŨNG là kết quả thuật toán HỢP LỆ khi graph có dữ liệu, nên
    # không thể phân biệt 2 nghĩa bằng con số. Thêm 3 field tường minh, do
    # Graph Assistant (agents/graph_aml.py) ghi — frontend/report dùng field
    # này làm TÍN HIỆU CHÍNH, không suy luận từ giá trị số.
    graph_analysis_status: Optional[str]
    #   - "NO_GRAPH_DATA": không có edges cho wallet_from (mock: không khớp
    #     scenario; neo4j: wallet không tồn tại). graph_score giữ 0.0 chỉ để
    #     không làm vỡ code đọc cũ (alert_report) — KHÔNG phải kết quả thuật toán.
    #   - "GRAPH_AVAILABLE_NO_SANCTION_PATH": có edges, thuật toán chạy, không
    #     tìm được path tới ví sanction. PPR/fan_out/community là SỐ THẬT;
    #     hop=None vì LÝ DO PHÂN TÍCH (không có path), không phải thiếu data.
    #   - "GRAPH_AVAILABLE_SANCTION_PATH_FOUND": có edges + path tồn tại.
    #     Mọi số (PPR/hop/fan_out/community) đều là số thật.
    graph_data_available: Optional[bool]
    # True khi có edges (status != NO_GRAPH_DATA). Frontend gate hiển thị
    # graph_score/hop/fan_out/community theo field này — KHÔNG hiển thị
    # "0"/"N/A" như số liệu thật khi field=false.
    sanction_path_found: Optional[bool]
    # True khi status == GRAPH_AVAILABLE_SANCTION_PATH_FOUND.
    # False khi graph có dữ liệu nhưng không có path tới ví blacklisted/sanctioned.
    # None khi NO_GRAPH_DATA.

    # === Mock data cho kịch bản demo (scripts/generate_complex_mock.py) ===
    mock_graph_edges: Optional[List[Tuple[str, str, float]]]
    # [(wallet_u, wallet_v, amount_vnd), ...] — dùng để dựng đồ thị NetworkX/Neo4j
    # động thay cho đồ thị demo cố định 6 cạnh mặc định trong graph_aml.py
    mock_blacklisted_wallets: Optional[List[str]]
    # Danh sách ví coi là is_sanctioned=true trong kịch bản mock, dùng làm
    # personalization source cho PPR và điểm xuất phát tính hop_distance
    graph_scenario_id: Optional[str]
    # Tên scenario mock đã khớp (vd "graph_sanction") — ghi chú audit/explainability,
    # do api/main.py ghi khi MockGraphProvider khớp scenario. None khi
    # NO_GRAPH_DATA hoặc production (Neo4j). KHÔNG phải PII — chỉ là tên scenario.

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

    # === Dữ liệu đủ/thiếu cho đánh giá rủi ro ===
    insufficient_data: bool
    # Mặc định False. Được Transaction Assistant (agents/transaction_classifier.py)
    # set THẬT sau khi đếm DỮ LIỆU THÔ của wallet_record (không dựa trên feature
    # đã tính): True khi ví chưa từng gửi/nhận ETH lẫn ERC20 trên Etherscan
    # (Sent tnx == 0 VÀ Received Tnx == 0 VÀ Total ERC20 tnxs == 0 — ví mới/chưa
    # có lịch sử, KHÔNG phải "có hoạt động nhưng giá trị nhỏ").
    # Khi True, Decision Engine BẮT BUỘC route REVIEW (không REPORT/PASS tự động)
    # và KHÔNG dùng classifier_score làm căn cứ quyết định — điểm vẫn được giữ
    # trong state để tham khảo/audit. Không phải PII.

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
    # Trạng thái truy xuất căn cứ pháp lý của RAG Agent:
    #   - "OK": LLM hoạt động bình thường, legal_citations là trích dẫn pháp lý THẬT
    #           (được truy xuất từ ChromaDB + LLM đối chiếu).
    #   - "UNAVAILABLE": KHÔNG truy xuất được căn cứ pháp lý — LLM gọi API lỗi
    #           (timeout/rate limit/sai key/model lỗi) HOẶC chưa cấu hình LLM_API_KEY.
    #           Khi đó legal_citations LUÔN = [] (KHÔNG bao giờ chứa mock/placeholder
    #           trông giống dữ liệu thật) và alert_report.py in cảnh báo tường minh
    #           vào STR. KHÔNG ảnh hưởng Decision Engine (REPORT/REVIEW/PASS quyết
    #           định độc lập, RAG chỉ trích căn cứ SAU khi decision đã output).
    legal_rag_status: Optional[str]
    # Chi tiết trạng thái lỗi khi legal_rag_status == "UNAVAILABLE" (VD thiếu key /
    # exception message). None khi "OK".
    legal_rag_error: Optional[str]

    # === Report ===
    report_path: Optional[str]

    # === Human-in-the-loop ===
    approval_status: Optional[str]            # "pending" | "approved" | "rejected"

    # === Misc / Explainability ===
    top_features: Optional[List[Tuple[str, float]]]
    avg_time_between_tx: Optional[float]
    balance_clustering_flag: Optional[bool]
    thought: Optional[str]