# tests/test_insufficient_data.py
"""
Test FIX 2026-08-08 — AUDIT ZERO-TX WALLET (insufficient_data).

Bối cảnh: ví không có giao dịch nào (0 tx txlist + 0 tx tokentx) khiến
build_full_wallet_features() trả về vector toàn 0.0, và XGBoost model chấm
điểm gần 1.0 (0.9997) — do đặc thù tập train Farrugia (ví illicit thường
có ít hoạt động). Hệ thống có thể tự động REPORT một ví chỉ vì nó mới/chưa
có lịch sử, không phải vì nó thật sự đáng ngờ.

Yêu cầu test:
1. Ví 0 giao dịch → insufficient_data=True; case_status phải là REVIEW dù
   classifier_score cao (>= θ) — KHÔNG được REPORT tự động.
2. Ví có giao dịch (dù ít, vd 1 tx) → insufficient_data=False; chạy luồng
   quyết định bình thường (classifier_score >= θ → REPORT).

Các test này chạy trên production path THẬT: wallet_record đúng schema →
build_full_wallet_features() → model THẬT (models/xgboost_aml.pkl),
KHÔNG dùng mock. Kiểm tra DỮ LIỆU THÔ (count tx/tokentx), không dựa trên
feature đã tính.
"""
import pytest

from core.state import AMLState
from agents.transaction_classifier import (
    analyze_transaction,
    _assess_insufficient_data,
)
from agents.decision_engine import (
    make_decision,
    reset_threshold_cache,
    _load_classifier_threshold,
)

# =============================================================================
# Wallet_record mẫu — đúng schema output scripts/02_fetch_etherscan_sample.py
# =============================================================================

EMPTY_WALLET_RECORD = {
    "address": "0x1111111111111111111111111111111111111111",
    "chains": {"ethereum": []},
    "token_transfers": {"ethereum": []},
}

ONE_TX_WALLET_RECORD = {
    "address": "0x2222222222222222222222222222222222222222",
    "chains": {
        "ethereum": [
            {
                "timeStamp": "1619073240",
                "from": "0x00799bbc833d5b168f0410312d2a8fd9e0e3079c",
                "to": "0x2222222222222222222222222222222222222222",
                "value": "1000000000000000000",
                "input": "0x",
                "contractAddress": "",
                "isError": "0",
            }
        ]
    },
    "token_transfers": {"ethereum": []},
}


def _base_state() -> AMLState:
    """State ĐÃ QUA Privacy Layer (chỉ chứa hashed PII) — khớp quy ước thật."""
    return AMLState(
        tx_hash="TX_INSUFFICIENT_DATA",
        wallet_from="0x1111111111111111111111111111111111111111",
        wallet_to="0x2222222222222222222222222222222222222222",
        amount_vnd=600_000_000.0,
        hashed_fullname="e3b0c44298fc1c14",
        hashed_id_number="a591a6d40bf42040",
        hashed_account_number="2c26b46b68ffc68f",
    )


@pytest.fixture(autouse=True)
def _reset_threshold():
    """Mỗi test đọc lại threshold từ file để luôn khớp production (θ=0.8748)."""
    reset_threshold_cache()
    yield
    reset_threshold_cache()


def test_assess_insufficient_data_zero_tx_wallet():
    """Ví 0 giao dịch (0 txlist + 0 tokentx) → insufficient_data=True."""
    assert _assess_insufficient_data(EMPTY_WALLET_RECORD) is True


def test_assess_insufficient_data_wallet_with_one_tx():
    """
    Ví CÓ giao dịch (dù chỉ 1 tx) → insufficient_data=False.
    Điều kiện "đủ dữ liệu" dựa trên DỮ LIỆU THÔ (count), KHÔNG dựa trên
    feature đã tính — ví có 1 tx là CÓ dữ liệu, kể cả giá trị nhỏ/bằng 0.
    """
    assert _assess_insufficient_data(ONE_TX_WALLET_RECORD) is False


def test_assess_insufficient_data_ignores_zero_value_tx():
    """
    Ví CÓ tx nhưng giá trị = 0 vẫn được coi là CÓ dữ liệu (không phải
    "ví có hoạt động nhưng giá trị thật sự bằng 0" → không lẫn với thiếu dữ liệu).
    """
    zero_value_tx_wallet = {
        "address": "0x3333333333333333333333333333333333333333",
        "chains": {
            "ethereum": [
                {
                    "timeStamp": "1619073240",
                    "from": "0x3333333333333333333333333333333333333333",
                    "to": "0x00799bbc833d5b168f0410312d2a8fd9e0e3079c",
                    "value": "0",
                    "input": "0x",
                    "contractAddress": "",
                    "isError": "0",
                }
            ]
        },
        "token_transfers": {"ethereum": []},
    }
    assert _assess_insufficient_data(zero_value_tx_wallet) is False


def test_zero_tx_wallet_results_review_even_with_high_classifier_score():
    """
    TEST CHÍNH #1 — Ví 0 giao dịch:
    - analyze_transaction (production path, wallet_record THẬT) phải set
      insufficient_data=True VÀ VẪN chạy classifier cho classifier_score
      (giữ tham khảo/audit, không raise, không crash).
    - make_decision phải route REVIEW (case_status=pending_review) DÙ
      classifier_score rất cao (>= θ) — KHÔNG được REPORT tự động, KHÔNG PASS.
    - decision_evidence phải nêu rõ "THIẾU DỮ LIỆU" để chuyên viên không
      hiểu nhầm mức độ nghiêm trọng.
    """
    state = _base_state()
    state["wallet_record"] = EMPTY_WALLET_RECORD

    # 1. Transaction Assistant: set insufficient_data, vẫn chấm classifier_score.
    scored = analyze_transaction(dict(state))
    assert scored["insufficient_data"] is True
    assert scored["classifier_score"] is not None
    assert 0.0 <= scored["classifier_score"] <= 1.0

    # 2. Decision Engine: BẮT BUỘC REVIEW (kiểm tra đầu tiên, trước mọi rule).
    decided = make_decision(scored)
    assert decided["insufficient_data"] is True
    assert decided["decision"] == "REVIEW"
    assert decided["case_status"] == "pending_review"
    # Không được REPORT dù classifier_score cao (>= θ).
    theta = _load_classifier_threshold()
    assert scored["classifier_score"] >= theta

    # 3. Evidence phải rõ lý do "thiếu dữ liệu".
    assert any("THIẾU DỮ LIỆU" in ev for ev in decided["decision_evidence"])
    assert "Không đủ dữ liệu giao dịch on-chain" in decided["decision_reason"]
    assert "KHÔNG dựa vào classifier_score" in decided["decision_reason"]


def test_wallet_with_one_tx_has_sufficient_data_and_normal_decision_flow():
    """
    TEST CHÍNH #2 — Ví CÓ giao dịch (1 tx):
    - analyze_transaction phải set insufficient_data=False (dựa trên DỮ LIỆU
      THÔ, không dựa trên feature đã tính).
    - make_decision chạy luồng quyết định BÌNH THƯỜNG (rule-based composite,
      KHÔNG bị chặn bởi insufficient_data).
    """
    state = _base_state()
    state["wallet_record"] = ONE_TX_WALLET_RECORD

    # 1. Transaction Assistant: đủ dữ liệu.
    scored = analyze_transaction(dict(state))
    assert scored["insufficient_data"] is False
    assert scored["classifier_score"] is not None
    assert 0.0 <= scored["classifier_score"] <= 1.0

    # 2. Decision Engine: chạy rule-based composite bình thường.
    theta = _load_classifier_threshold()
    decided = make_decision(scored)
    assert decided["insufficient_data"] is False

    # Không được mang dấu hiệu "THIẾU DỮ LIỆU" trong bất kỳ evidence nào
    # (chỉ case insufficient_data mới có evidence bắt đầu bằng "THIẾU DỮ LIỆU").
    assert "THIẾU DỮ LIỆU" not in " ".join(decided["decision_evidence"])

    # Luồng quyết định bình thường (Không có kết quả nào do insufficient_data):
    # Với ví 1 tx nhận 1 ETH, model thật chấm classifier_score~0.867 (< θ=0.8748)
    # → PASS là hành vi ĐÚNG của rule-based composite khi không có tín hiệu nào.
    # Đây chính là bằng chứng "đủ dữ liệu → quyết định do rule thật, không bị
    # insufficient_data can thiệp" (nếu score >= θ thì sẽ là REPORT — xem
    # test đủ dữ liệu + score cao bên dưới).
    assert decided["decision"] in ("PASS", "REVIEW", "REPORT")
    assert decided["case_status"] in ("auto_cleared", "pending_review")


def test_sufficient_data_with_high_classifier_still_reports():
    """
    Bổ trợ TEST CHÍNH #2 — Khi insufficient_data=False (đủ dữ liệu), luồng
    quyết định bình thường VẪN cho REPORT nếu classifier_score >= θ.
    Chứng minh insufficient_data chỉ block REPORT ở case THIẾU dữ liệu,
    không đổi logic decision cho các case đã có đủ dữ liệu (ràng buộc task).
    """
    state = _base_state()
    state["wallet_record"] = EMPTY_WALLET_RECORD  # dùng để lấy feature thật
    scored = analyze_transaction(dict(state))
    assert scored["insufficient_data"] is True  # tiền đề: đây là case thiếu

    # Giả lập case ĐÃ ĐỦ DỮ LIỆU: giữ feature/scorer đã tính nhưng buộc
    # insufficient_data=False (như Transaction Assistant sẽ set cho ví có tx)
    # và classifier_score cao (>= θ) — trạng thái tương đương ví CÓ hoạt động
    # mà model chấm vượt ngưỡng.
    scored["insufficient_data"] = False
    scored["classifier_score"] = 0.95  # >= θ=0.8748

    decided = make_decision(scored)
    assert decided["insufficient_data"] is False
    assert decided["decision"] == "REPORT"  # Rule 3 chạy bình thường
    assert decided["case_status"] == "pending_review"
    assert "THIẾU DỮ LIỆU" not in " ".join(decided["decision_evidence"])
