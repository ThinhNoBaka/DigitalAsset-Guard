# tests/test_aggregation_monitor.py
"""
Test Aggregation Monitor (structuring / smurfing detection) sau FIX phân biệt
2 trạng thái đánh giá:

1. wallet_tx_history=None (chưa có nguồn dữ liệu OFF-CHAIN Core Banking):
   - aggregation_status == "not_assessed"
   - structuring_detected is None  (KHÔNG phải False — False ngầm hiểu
     "đã kiểm tra, không phát hiện", sai thực tế)
   - aggregation_reason ghi rõ lý do.

2. wallet_tx_history CÓ dữ liệu structuring thật (nhiều giao dịch nhỏ né
   ngưỡng báo cáo 500tr):
   - aggregation_status == "assessed"
   - structuring_detected is True (rule hiện có: cộng dồn 7 ngày vượt ngưỡng
     trong khi giao dịch hiện tại tự nó chưa vượt).

Bổ sung test decision_engine: None (chưa đánh giá) KHÔNG được coi như False,
evidence phải ghi rõ "STRUCTURING CHƯA ĐƯỢC ĐÁNH GIÁ"; True phải REPORT.

LƯU Ý KIẾN TRÚC: wallet_tx_history là dữ liệu OFF-CHAIN Core Banking, ĐỘC LẬP
hoàn toàn với wallet_record (ON-CHAIN, từ Etherscan, dùng cho Transaction
Classifier/XGBoost). Hai nguồn dữ liệu này KHÔNG được hợp nhất — test này
KHÔNG đụng tới wallet_record.
"""

import pytest

from core.state import AMLState
from core.config import REPORT_THRESHOLD_VND
from agents.aggregation_monitor import (
    analyze_aggregation,
    NOT_ASSESSED_REASON,
)
from agents.decision_engine import (
    make_decision,
    reset_threshold_cache,
)


@pytest.fixture(autouse=True)
def _reset_threshold():
    """Mỗi test đọc lại threshold từ file để luôn khớp production."""
    reset_threshold_cache()
    yield
    reset_threshold_cache()


def _base_state() -> AMLState:
    """State ĐÃ QUA Privacy Layer (chỉ chứa hashed PII) — khớp quy ước thật."""
    return AMLState(
        tx_hash="TX_AGG_MONITOR",
        wallet_from="0xAggMonitorWallet",
        wallet_to="0xDestination",
        amount_vnd=200_000_000.0,
        hashed_fullname="e3b0c44298fc1c14",
        hashed_id_number="a591a6d40bf42040",
        hashed_account_number="2c26b46b68ffc68f",
    )


# =============================================================================
# TEST 1 — wallet_tx_history = None → NOT_ASSESSED
# =============================================================================

def test_no_wallet_tx_history_results_not_assessed():
    """
    Chưa có dữ liệu off-chain (Core Banking) → KHÔNG được trả về
    structuring_detected=False. Phải:
    - aggregation_status == "not_assessed"
    - structuring_detected is None (KHÔNG phải False)
    - aggregated_amount_7d / near_threshold_count_30d = None (không bịa 0)
    - aggregation_reason ghi rõ lý do
    """
    state = _base_state()
    # Không set wallet_tx_history → state.get("wallet_tx_history") is None

    result = analyze_aggregation(state)

    assert result["aggregation_status"] == "not_assessed"
    assert result["structuring_detected"] is None
    assert result["aggregated_amount_7d"] is None
    assert result["near_threshold_count_30d"] is None
    assert result["aggregation_reason"] == NOT_ASSESSED_REASON
    assert "Không có dữ liệu off-chain" in result["aggregation_reason"]


def test_empty_wallet_tx_history_results_not_assessed():
    """
    wallet_tx_history = [] (rỗng) cũng coi là chưa có nguồn dữ liệu →
    not_assessed, structuring_detected=None (same semantics).
    """
    state = _base_state()
    state["wallet_tx_history"] = []

    result = analyze_aggregation(state)

    assert result["aggregation_status"] == "not_assessed"
    assert result["structuring_detected"] is None


# =============================================================================
# TEST 2 — wallet_tx_history có structuring thật → assessed + True
# =============================================================================

def test_structuring_detected_with_real_offchain_history():
    """
    Nhiều giao dịch nhỏ né ngưỡng: 3× 200tr trong 7 ngày, giao dịch hiện tại
    200tr (< ngưỡng 500tr) nhưng cộng dồn 7 ngày = 600tr >= ngưỡng.
    → aggregation_status == "assessed", structuring_detected is True
      (rule hiện có không đổi).
    """
    state = _base_state()
    state["tx_timestamp"] = 1_700_000_000
    state["wallet_tx_history"] = [
        {"timestamp": 1_699_850_000, "direction": "out", "amount": 200_000_000},
        {"timestamp": 1_699_900_000, "direction": "out", "amount": 200_000_000},
        {"timestamp": 1_699_950_000, "direction": "out", "amount": 200_000_000},
    ]

    result = analyze_aggregation(state)

    assert result["aggregation_status"] == "assessed"
    assert result["structuring_detected"] is True
    assert result["aggregation_reason"] is None
    # Cộng dồn 7 ngày = 200tr (hiện tại) + 200tr + 200tr + 200tr = 800tr
    assert result["aggregated_amount_7d"] >= REPORT_THRESHOLD_VND
    # amount_vnd hiện tại dưới ngưỡng → điều kiện (a) của rule structuring.
    assert state["amount_vnd"] < REPORT_THRESHOLD_VND


def test_no_structuring_with_offchain_history_is_false():
    """
    Có wallet_tx_history nhưng không có dấu hiệu cộng dồn/ né ngưỡng →
    assessed + structuring_detected=False (ĐÂY mới là "đã kiểm tra sạch").
    """
    state = _base_state()
    state["tx_timestamp"] = 1_700_000_000
    state["wallet_tx_history"] = [
        {"timestamp": 1_699_000_000, "direction": "out", "amount": 10_000_000},
        {"timestamp": 1_699_500_000, "direction": "out", "amount": 5_000_000},
    ]

    result = analyze_aggregation(state)

    assert result["aggregation_status"] == "assessed"
    assert result["structuring_detected"] is False


# =============================================================================
# DECISION ENGINE — xử lý đúng 3 trạng thái True / False / None
# =============================================================================

def test_decision_engine_none_structuring_not_treated_as_false():
    """
    structuring_detected=None (chưa đánh giá — không có dữ liệu off-chain)
    KHÔNG được coi như False trong decision_engine:
    - Rule 2 (structuring → REPORT) KHÔNG được trigger.
    - decision_evidence phải ghi rõ "STRUCTURING CHƯA ĐƯỢC ĐÁNH GIÁ" để
      chuyên viên biết structuring chưa hề được đánh giá cho case này.
    """
    state = _base_state()
    state["classifier_score"] = 0.0
    state["sanction_result"] = {"is_match": False}
    state["aggregation_status"] = "not_assessed"
    state["structuring_detected"] = None

    result = make_decision(state)

    assert result["decision"] == "PASS"  # không bị trigger REPORT do None
    assert any(
        "STRUCTURING CHƯA ĐƯỢC ĐÁNH GIÁ" in ev
        for ev in result["decision_evidence"]
    )
    assert any(
        "không đồng nghĩa đã kiểm tra và không phát hiện" in ev
        for ev in result["decision_evidence"]
    )


def test_decision_engine_structuring_true_triggers_report():
    """
    structuring_detected=True (đã đánh giá với dữ liệu off-chain thật)
    → Rule 2 trigger REPORT (giữ nguyên logic rule hiện có).
    """
    state = _base_state()
    state["classifier_score"] = 0.0
    state["sanction_result"] = {"is_match": False}
    state["aggregation_status"] = "assessed"
    state["structuring_detected"] = True
    state["aggregated_amount_7d"] = 600_000_000.0
    state["near_threshold_count_30d"] = 3

    result = make_decision(state)

    assert result["decision"] == "REPORT"
    assert result["case_status"] == "pending_review"
    assert any(
        "Phát hiện dấu hiệu chia nhỏ giao dịch" in ev
        for ev in result["decision_evidence"]
    )


def test_decision_engine_structuring_false_no_report():
    """
    structuring_detected=False (đã đánh giá, sạch) → không trigger REPORT
    do structuring (decision có thể PASS nếu không còn tín hiệu khác).
    """
    state = _base_state()
    state["classifier_score"] = 0.0
    state["sanction_result"] = {"is_match": False}
    state["aggregation_status"] = "assessed"
    state["structuring_detected"] = False

    result = make_decision(state)

    assert result["decision"] == "PASS"
    # Không có evidence "STRUCTURING CHƯA ĐƯỢC ĐÁNH GIÁ" — đã đánh giá rồi.
    assert not any(
        "STRUCTURING CHƯA ĐƯỢC ĐÁNH GIÁ" in ev
        for ev in result["decision_evidence"]
    )