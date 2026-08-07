# tests/test_decision_engine.py
"""
Test Decision Engine theo SPEC_v2 (rule-based composite — KHÔNG còn weighted-sum).

Kiến trúc mới (xem agents/decision_engine.py):
- 5 rule tuần tự: sanctions match → structuring_flag → classifier_score >= θ
  (đọc từ models/classifier_threshold.json, calibrate trên Elliptic) →
  hop_distance_to_blacklist <= 2 → 2 tín hiệu "medium" cùng lúc → REVIEW.
- risk_assessment_score luôn = None (không còn tính điểm tổng hợp).

LƯU Ý VỀ NGƯỠNG θ: tests dùng threshold THẬT từ models/classifier_threshold.json
(đã chạy calibrate trên Elliptic — hiện θ=0.0009, rất thấp vì model chưa phân
biệt mạnh illicit/licit). Vì vậy mọi classifier_score > 0 đều có thể trigger
REPORT — test dùng classifier_score=0.0 cho các case PASS/REVIEW để kiểm tra
đúng rule khác (sanctions, structuring, graph hop, 2 medium signals) mà không
bị Rule 3 chặn trước.
"""

import pytest
from core.state import AMLState
from agents.decision_engine import make_decision, reset_threshold_cache


@pytest.fixture(autouse=True)
def _reset_threshold():
    """Mỗi test đọc lại threshold từ file để luôn khớp production."""
    reset_threshold_cache()
    yield
    reset_threshold_cache()


def test_decision_pass_low_risk_no_sanctions():
    # classifier_score=0.0 < θ (mọi θ hợp lệ), hop=None, không sanctions/structuring.
    state = AMLState(
        classifier_score=0.0,
        graph_score=0.0,
        sanction_result={"is_match": False},
        name_similarity_warning=False,
    )
    result = make_decision(state)
    assert result["decision"] == "PASS"
    assert result["case_status"] == "auto_cleared"
    assert "classifier_score=0.000" in result["decision_evidence"][0]
    assert result["risk_assessment_score"] is None  # không còn tính điểm tổng hợp


def test_decision_report_classifier_above_threshold():
    # classifier_score >= θ (θ calibrate trên Elliptic) -> REPORT độc lập với sanctions.
    state = AMLState(
        classifier_score=0.01,
        graph_score=0.0,
        sanction_result={"is_match": False},
        name_similarity_warning=False,
    )
    result = make_decision(state)
    assert result["decision"] == "REPORT"
    assert result["case_status"] == "pending_review"
    assert "vượt ngưỡng calibrate" in result["decision_evidence"][0]
    # risk_assessment_score không còn được tính (weighted-sum đã bỏ).
    assert result["risk_assessment_score"] is None


def test_decision_report_sanctions_match_low_risk():
    state = AMLState(
        classifier_score=0.0,
        graph_score=0.0,
        sanction_result={
            "is_match": True,
            "matched_wallet": "0xabc",
            "program": "CYBER2",
        },
        name_similarity_warning=False,
    )
    result = make_decision(state)
    assert result["decision"] == "REPORT"
    assert result["case_status"] == "pending_review"
    assert "Exact OFAC SDN match" in result["decision_evidence"][0]


def test_decision_report_graph_hop_close_to_blacklist():
    # Rule 4: hop_distance_to_blacklist <= 2 -> REPORT (fact hình học, không cần label).
    state = AMLState(
        classifier_score=0.0,
        graph_score=0.0,
        hop_distance_to_blacklist=1,
        sanction_result={"is_match": False},
        name_similarity_warning=False,
    )
    result = make_decision(state)
    assert result["decision"] == "REPORT"
    assert result["case_status"] == "pending_review"
    assert "Graph exposure" in result["decision_evidence"][0]


def test_decision_review_two_medium_signals():
    # Rule 5: classifier medium (θ*0.6 <= score < θ) + graph medium (2 < hop <= 4)
    # cùng lúc -> REVIEW. Ngưỡng medium là giả định tạm (xem docstring decision_engine).
    state = AMLState(
        classifier_score=0.0007,  # medium: θ*0.6=0.00054 <= 0.0007 < θ=0.0009
        graph_score=0.0,
        hop_distance_to_blacklist=3,  # medium: 2 < 3 <= 4
        sanction_result={"is_match": False},
        name_similarity_warning=False,
    )
    result = make_decision(state)
    assert result["decision"] == "REVIEW"
    assert result["case_status"] == "pending_review"
    assert any("Tín hiệu vừa kết hợp" in ev for ev in result["decision_evidence"])


def test_decision_evidence_includes_fuzzy_warning():
    state = AMLState(
        classifier_score=0.0,
        graph_score=0.0,
        sanction_result={"is_match": False},
        name_similarity_warning=True,
        name_similarity_score=0.85,
    )
    result = make_decision(state)
    assert result["decision"] == "PASS"  # vẫn PASS
    # Nhưng evidence có fuzzy warning
    assert any("Fuzzy name similarity warning" in ev for ev in result["decision_evidence"])
