"""
tests/test_classifier.py -- Kiểm thử Agent phân loại giao dịch theo kiến trúc SPEC_v2.

Sửa theo kiến trúc hiện tại (pivot sang Decision Engine rule-based composite):

- Hàm cũ `classify_transaction` KHÔNG còn tồn tại. Hàm thật là
  `analyze_transaction` (agents/transaction_classifier.py) — chỉ ghi
  `classifier_score` (0-1) + `top_features` (SHAP per-transaction) + 2 đặc
  trưng hành vi. Các trường `is_suspicious`/`risk_score`/`laundering_reasons`
  đã bị bỏ hẳn ở kiến trúc mới.
- Ý định test cũ "giao dịch 50tr không đáng ngờ / 600tr vượt ngưỡng" nay thuộc
  về Aggregation Monitor: `analyze_aggregation` ghi `is_large_tx =
  amount_vnd >= REPORT_THRESHOLD_VND` (agents/aggregation_monitor.py). Test
  chuyển ý định đó sang đúng hàm này.
- LƯU Ý HẠN CHẾ ĐÃ BIẾT (PROJECT_SUMMARY mục 6 #1): `analyze_transaction`
  đang dùng feature vector MOCK (zero-vector 166 chiều, chỉ gán amount_vnd
  vào slot 0) — model không phân biệt được 50tr vs 600tr (score ≈ 0.000284
  cho cả hai). Vì vậy test analyze_transaction KHÔNG assert tương quan
  amount → score; chỉ assert score hợp lệ + top_features tồn tại.
"""
import pytest

from core.state import AMLState
from agents.transaction_classifier import analyze_transaction
from agents.aggregation_monitor import analyze_aggregation


def _base_state(tx_hash: str, amount_vnd: float) -> AMLState:
    """State ĐÃ QUA Privacy Layer (chỉ chứa hashed PII) — khớp quy ước thật."""
    return AMLState(
        tx_hash=tx_hash,
        amount_vnd=amount_vnd,
        wallet_from="hashed_wallet_source_aaa",
        wallet_to="hashed_wallet_dest_bbb",
        hashed_fullname="e3b0c44298fc1c14",
        hashed_id_number="a591a6d40bf42040",
        hashed_account_number="2c26b46b68ffc68f",
    )


def test_classify_normal_transaction():
    # Giao dịch nhỏ 50 triệu VND — dưới ngưỡng báo cáo 500tr (TT27).
    # Ý định test cũ: không bị đánh dấu đáng ngờ.
    state = _base_state("TX123", 50_000_000.0)

    # Aggregation Monitor: không phải giao dịch lớn (dưới ngưỡng báo cáo).
    agg = analyze_aggregation(dict(state))
    assert agg["is_large_tx"] is False

    # Transaction Assistant: score hợp lệ trong [0, 1] (không phải None).
    scored = analyze_transaction(dict(state))
    assert scored["classifier_score"] is not None
    assert 0.0 <= scored["classifier_score"] <= 1.0
    # SHAP per-transaction: nếu shap đã cài thì top_features có dữ liệu.
    assert isinstance(scored["top_features"], list)
    assert all(
        isinstance(name, str) and isinstance(val, float)
        for name, val in scored["top_features"]
    )


def test_classify_high_value_transaction():
    # Giao dịch khủng 600 triệu VND — VƯỢT ngưỡng báo cáo 500tr (TT27).
    # Ý định test cũ: vượt ngưỡng quy định → phải được đánh dấu.
    state = _base_state("TX456", 600_000_000.0)

    # Aggregation Monitor: phải được gắn cờ giao dịch lớn (is_large_tx=True).
    agg = analyze_aggregation(dict(state))
    assert agg["is_large_tx"] is True

    # Transaction Assistant: vẫn phải cho score hợp lệ.
    scored = analyze_transaction(dict(state))
    assert scored["classifier_score"] is not None
    assert 0.0 <= scored["classifier_score"] <= 1.0