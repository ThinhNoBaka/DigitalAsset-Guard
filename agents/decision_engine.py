# agents/decision_engine.py
"""
Decision Engine — REDESIGN sau khi pivot khỏi weighted-sum ensemble.

*** LỊCH SỬ THAY ĐỔI (để ai đọc sau không thắc mắc vì sao có vẻ "đổi ý") ***

Bản trước dùng:
    risk_assessment_score = w1 * classifier_score + w2 * graph_score
    REPORT nếu risk_assessment_score >= 0.7

Vấn đề: cả (w1, w2) học bằng logistic regression VÀ ngưỡng 0.7 đều cần dữ
liệu ghép cặp (classifier_score, graph_score, label) có ground-truth thật.
Project KHÔNG có dữ liệu này và không có cách hợp lệ để tạo ra nó (Elliptic
chỉ có classifier features, graph_score chạy trên đồ thị ví demo khác kiến
trúc — không map 1-1). Train trên < 30 dòng mock hoặc bịa graph_score cho
Elliptic sẽ tạo cảm giác "đã calibrate bằng dữ liệu" trong khi thực chất là
số giả — đúng loại lỗi project này chủ đích tránh.

=> Quyết định: bỏ hẳn weighted-sum. `agents/train_decision_weights.py` và
`models/decision_weights.json` đã bị XOÁ khỏi repo, không còn được dùng ở
đâu. Thay bằng RULE-BASED COMPOSITE: mỗi tín hiệu (sanctions, structuring,
classifier, graph) được đánh giá ĐỘC LẬP bằng ngưỡng/logic riêng, không gộp
thành 1 con số xác suất tổng.

Lý do mỗi rule dùng được ngay (không cần dữ liệu ghép cặp không tồn tại):

    - classifier_score >= θ  : θ calibrate bằng precision-recall curve trên
      Elliptic (CÓ ground-truth thật) — xem
      agents/calibrate_classifier_threshold.py, output models/classifier_threshold.json
    - hop_distance_to_blacklist <= N : đây là FACT hình học của đồ thị (số
      hop ngắn nhất tới ví đã biết là sanctioned), không cần nhãn illicit/
      licit để "học" — N=2 chọn bằng quyết định nghiệp vụ (cân bằng recall/
      false positive), giống cách luật chọn ngưỡng 500tr, không phải số
      thống kê giả tạo.
    - structuring_detected, sanctions match : đã là rule cứng từ trước,
      không đổi. structuring_detected xử lý 3 trạng thái True/False/None —
      None (không có dữ liệu off-chain Core Banking) KHÔNG được coi như False
      (False ngầm hiểu "đã kiểm tra, không phát hiện", sai thực tế).

*** NGƯỠNG "MEDIUM" CHO RULE REVIEW (2 tín hiệu vừa) — GIẢ ĐỊNH TẠM ***
_CLASSIFIER_MEDIUM_RATIO = 0.6 (tức là medium = θ * 0.6) là số CHỌN TẠM,
CHƯA kiểm chứng bằng dữ liệu — người yêu cầu tính năng này xác nhận sẽ chốt
số tốt hơn sau. Đánh dấu rõ ràng bằng hằng số riêng + comment, để không bị
lẫn với các ngưỡng đã có căn cứ (θ, hop_distance<=2). Nếu cần thay đổi, chỉ
sửa 2 hằng số _CLASSIFIER_MEDIUM_RATIO / _GRAPH_MEDIUM_HOP_RANGE bên dưới.
"""

import json
from pathlib import Path

from core.config import MODELS_DIR
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState

# =============================================================================
# NGƯỠNG CLASSIFIER (θ) — calibrate bằng dữ liệu thật (Elliptic PR curve)
# =============================================================================

_THRESHOLD_PATH = MODELS_DIR / "classifier_threshold.json"
_threshold_cache = None  # cache trong process, tránh đọc file mỗi giao dịch

# Fallback nếu CHƯA chạy calibrate_classifier_threshold.py — số tạm, không
# có căn cứ dữ liệu, chỉ để hệ thống không crash. In cảnh báo mỗi lần dùng.
_DEFAULT_CLASSIFIER_THRESHOLD = 0.7


def _load_classifier_threshold() -> float:
    """
    Trả về θ cho classifier_score (Rule 3 bên dưới).

    Ưu tiên đọc từ models/classifier_threshold.json (calibrate bằng
    precision-recall curve trên Elliptic — xem
    agents/calibrate_classifier_threshold.py). Nếu chưa có, fallback về
    hằng số mặc định CHƯA KIỂM CHỨNG và in cảnh báo rõ ràng — không âm thầm
    dùng số chưa calibrate mà không ai biết.
    """
    global _threshold_cache
    if _threshold_cache is not None:
        return _threshold_cache

    if _THRESHOLD_PATH.exists():
        try:
            with open(_THRESHOLD_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _threshold_cache = float(data["threshold"])
            return _threshold_cache
        except Exception as e:
            print(
                f"⚠️ Lỗi đọc {_THRESHOLD_PATH} ({e}) — dùng ngưỡng mặc định "
                f"CHƯA CALIBRATE ({_DEFAULT_CLASSIFIER_THRESHOLD})."
            )
    else:
        print(
            f"⚠️ Chưa có {_THRESHOLD_PATH} — chạy "
            "`python -m agents.calibrate_classifier_threshold --test-csv <path>` "
            f"để calibrate θ từ Elliptic. Hiện đang dùng ngưỡng mặc định "
            f"CHƯA KIỂM CHỨNG ({_DEFAULT_CLASSIFIER_THRESHOLD})."
        )

    _threshold_cache = _DEFAULT_CLASSIFIER_THRESHOLD
    return _threshold_cache


def reset_threshold_cache() -> None:
    """Xoá cache ngưỡng — dùng khi test hoặc vừa calibrate xong threshold mới."""
    global _threshold_cache
    _threshold_cache = None


# =============================================================================
# NGƯỠNG GRAPH EXPOSURE — fact hình học, không cần calibrate bằng label
# =============================================================================

# REPORT nếu hop_distance_to_blacklist <= giá trị này (đã chốt: 2 hop).
GRAPH_HOP_REPORT_THRESHOLD = 2

# === Ngưỡng "medium" cho rule REVIEW (2 tín hiệu vừa cùng lúc) ===
# GIẢ ĐỊNH TẠM — xem docstring đầu file. Chưa kiểm chứng bằng dữ liệu.
_CLASSIFIER_MEDIUM_RATIO = 0.6  # medium = θ * 0.6
# Graph "medium" = hop_distance nằm ngoài phạm vi REPORT (>2) nhưng vẫn khá
# gần (<=4). Ngoài phạm vi này coi là không có tín hiệu graph đáng kể.
_GRAPH_MEDIUM_HOP_MAX = 4


def make_decision(state: AMLState) -> AMLState:
    """
    Đọc state, áp dụng rule-based composite, KHÔNG tính risk_assessment_score
    tổng hợp nữa (xem docstring đầu file để biết lý do bỏ weighted-sum).
    Ghi decision, decision_reason, decision_evidence, case_status vào state.
    """
    assert_no_raw_pii(state)

    classifier_score = state.get("classifier_score", 0.0) or 0.0
    hop_distance = state.get("hop_distance_to_blacklist")
    sanction_result = state.get("sanction_result", {}) or {}
    is_sanction_match = sanction_result.get("is_match", False)

    classifier_threshold = _load_classifier_threshold()
    classifier_medium_threshold = classifier_threshold * _CLASSIFIER_MEDIUM_RATIO

    # --- Aggregation Monitor / structuring ---
    # structuring_detected có 3 trạng thái:
    #   True  : đã chạy rule structuring với dữ liệu off-chain → phát hiện.
    #   False : đã chạy rule structuring với dữ liệu off-chain → sạch.
    #   None  : CHƯA đánh giá (không có wallet_tx_history — thiếu dữ liệu
    #           off-chain Core Banking). KHÔNG coi None như False.
    # aggregation_status phân biệt rõ "đã kiểm tra sạch" vs "chưa đánh giá được".
    structuring_detected = state.get("structuring_detected")
    aggregation_status = state.get("aggregation_status")

    # `risk_assessment_score` không còn được tính (đã bỏ weighted-sum). Giữ
    # key = None trong state để code cũ (UI, report) đọc field này không
    # crash, nhưng KHÔNG dùng làm căn cứ quyết định.
    state["risk_assessment_score"] = None

    # -------------------------------------------------------------------------
    # [FIX 2026-08-08 — AUDIT ZERO-TX WALLET] Điều kiện kiểm tra ĐẦU TIÊN
    # -------------------------------------------------------------------------
    # Ví chưa từng có giao dịch on-chain (insufficient_data=True, do Transaction
    # Assistant ghi từ DỮ LIỆU THÔ — 0 tx txlist + 0 tx tokentx) thì KHÔNG được
    # dùng classifier_score để tự động REPORT/PASS: model chấm ~1.0 cho feature
    # vector toàn 0 là đặc thù tập train Farrugia (ví illicit thường có ít hoạt
    # động), KHÔNG phản ánh rủi ro thật của ví. BẮT BUỘC route REVIEW cho chuyên
    # viên xác minh thủ công. Đặt TRƯỚC mọi rule dựa trên classifier_score /
    # graph_score bên dưới — không REPORT, không PASS tự động.
    if state.get("insufficient_data", False):
        state["decision"] = "REVIEW"
        state["case_status"] = "pending_review"
        state["decision_reason"] = (
            "Không đủ dữ liệu giao dịch on-chain để đánh giá rủi ro (ví chưa có "
            "lịch sử Etherscan) — cần chuyên viên xác minh thủ công, KHÔNG dựa "
            "vào classifier_score."
        )
        state["decision_evidence"] = [
            "THIẾU DỮ LIỆU: ví chưa từng gửi/nhận ETH lẫn ERC20 trên Etherscan "
            "(0 tx txlist + 0 tx tokentx) — classifier_score KHÔNG được dùng làm "
            "căn cứ quyết định (model chấm ~1.0 cho vector toàn 0 là đặc thù tập "
            "train, không phản ánh rủi ro thật). Cần chuyên viên xác minh thủ công."
        ]
        return state

    evidence = []
    decision = "PASS"
    case_status = "auto_cleared"
    reason = ""

    graph_signal_report = hop_distance is not None and hop_distance <= GRAPH_HOP_REPORT_THRESHOLD
    classifier_signal_report = classifier_score >= classifier_threshold

    classifier_signal_medium = classifier_medium_threshold <= classifier_score < classifier_threshold
    graph_signal_medium = (
        hop_distance is not None
        and GRAPH_HOP_REPORT_THRESHOLD < hop_distance <= _GRAPH_MEDIUM_HOP_MAX
    )

    # --- Rule 1: Sanctions exact match ---
    if is_sanction_match:
        decision = "REPORT"
        case_status = "pending_review"
        evidence.append(
            f"Exact OFAC SDN match — wallet {sanction_result.get('matched_wallet', 'unknown')} "
            f"(program: {sanction_result.get('program', 'N/A')})"
        )
        reason = "Sanctions exact match triggers mandatory reporting."

    # --- Rule 2: Structuring / smurfing (Aggregation Monitor) ---
    # Chỉ trigger khi structuring_detected là True (đã được Aggregation Monitor
    # đánh giá với dữ liệu off-chain thật). None (chưa đánh giá — thiếu dữ
    # liệu) KHÔNG được coi là False: rơi qua các rule khác bình thường, và
    # evidence bổ sung ở cuối sẽ ghi rõ structuring chưa được đánh giá cho
    # case này.
    elif structuring_detected is True:
        decision = "REPORT"
        case_status = "pending_review"
        evidence.append(
            "Phát hiện dấu hiệu chia nhỏ giao dịch (structuring): "
            f"cộng dồn 7 ngày = {state.get('aggregated_amount_7d') or 0:,.0f} VND, "
            f"số giao dịch gần ngưỡng trong 30 ngày = {state.get('near_threshold_count_30d') or 0}"
        )
        reason = (
            "Aggregation Monitor detected a structuring pattern: multiple "
            "sub-threshold transactions aggregating above the reporting "
            "threshold within a short window."
        )

    # --- Rule 3: Classifier score vượt ngưỡng calibrate ---
    elif classifier_signal_report:
        decision = "REPORT"
        case_status = "pending_review"
        evidence.append(
            f"Classifier score {classifier_score:.3f} vượt ngưỡng calibrate "
            f"θ={classifier_threshold:.3f} (precision-recall curve trên Elliptic)"
        )
        reason = f"Classifier score ({classifier_score:.3f}) exceeds calibrated threshold ({classifier_threshold:.3f})."

    # --- Rule 4: Graph exposure — hop_distance tới ví sanctioned ---
    elif graph_signal_report:
        decision = "REPORT"
        case_status = "pending_review"
        evidence.append(
            f"Graph exposure: hop_distance_to_blacklist={hop_distance} "
            f"<= ngưỡng {GRAPH_HOP_REPORT_THRESHOLD} (ví nằm gần ví đã bị sanction trên đồ thị)"
        )
        reason = f"Wallet is {hop_distance} hop(s) from a sanctioned wallet on the transaction graph."

    # --- Rule 5: 2 tín hiệu "medium" cùng lúc (classifier + graph) ---
    elif classifier_signal_medium and graph_signal_medium:
        decision = "REVIEW"
        case_status = "pending_review"
        evidence.append(
            f"Tín hiệu vừa kết hợp: classifier_score={classifier_score:.3f} "
            f"(medium, ngưỡng tạm {classifier_medium_threshold:.3f}–{classifier_threshold:.3f}) "
            f"+ hop_distance_to_blacklist={hop_distance} (medium, {GRAPH_HOP_REPORT_THRESHOLD + 1}–{_GRAPH_MEDIUM_HOP_MAX} hop). "
            "Ngưỡng medium là giả định tạm, chưa kiểm chứng bằng dữ liệu — cần review thủ công."
        )
        reason = (
            "Two independent medium-strength risk signals (classifier + graph "
            "proximity) triggered simultaneously — routed to manual review."
        )

    else:
        # PASS
        evidence.append(
            f"classifier_score={classifier_score:.3f} (< θ={classifier_threshold:.3f}), "
            f"hop_distance_to_blacklist={hop_distance if hop_distance is not None else 'N/A'} "
            f"(> {GRAPH_HOP_REPORT_THRESHOLD}), không có sanctions/structuring match."
        )
        reason = "No sufficient risk signals detected."

    # --- Evidence bổ sung mang tính thông tin, không ảnh hưởng quyết định ---

    if state.get("name_similarity_warning", False):
        evidence.append(
            f"Fuzzy name similarity warning (score: {state.get('name_similarity_score', 0.0):.2f}%) - for information only"
        )

    # Structuring CHƯA được đánh giá (không có dữ liệu off-chain Core Banking).
    # KHÔNG đồng nghĩa "đã kiểm tra sạch" — phải ghi rõ trong evidence để
    # chuyên viên biết rule structuring chưa hề chạy cho case này.
    # (KHÔNG dùng cụm "THIẾU DỮ LIỆU" — cụm này dành riêng cho insufficient_data
    # on-chain trong AUDIT ZERO-TX WALLET, tránh hiểu nhầm 2 nguồn dữ liệu.)
    if aggregation_status == "not_assessed":
        evidence.append(
            "STRUCTURING CHƯA ĐƯỢC ĐÁNH GIÁ: không có dữ liệu off-chain "
            "(Core Banking) để chạy rule structuring cho case này — "
            "không đồng nghĩa đã kiểm tra và không phát hiện."
        )

    # Nghĩa vụ báo cáo giao dịch lớn theo TT27 — ĐỘC LẬP với quyết định
    # REPORT/REVIEW/PASS ở trên (nghĩa vụ luật định, không phải đánh giá rủi ro).
    if state.get("is_large_tx", False):
        evidence.append(
            f"Giao dịch lớn ({state.get('amount_vnd', 0):,.0f} VND >= ngưỡng báo cáo TT27) "
            "— nghĩa vụ báo cáo giao dịch lớn, độc lập với đánh giá rủi ro ở trên."
        )

    state["decision"] = decision
    state["decision_reason"] = reason
    state["decision_evidence"] = evidence
    state["case_status"] = case_status

    return state