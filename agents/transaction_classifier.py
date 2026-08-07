"""
agents/transaction_classifier.py
Agent độc lập chấm điểm rủi ro sơ bộ từ đặc trưng giao dịch.

[V2-2 -- THAY_DOI_V2.md] Explainability nâng lên mức PER-TRANSACTION bằng SHAP
(shap.TreeExplainer), thay cho feature_importances_ toàn cục của V1. Trường
`top_features` trong AMLState GIỮ NGUYÊN tên, chỉ đổi nguồn dữ liệu bên trong
(không tính là thêm trường mới -- xem core/state.py).

[V2-1 -- THAY_DOI_V2.md] Thêm 2 đặc trưng hành vi:
  - avg_time_between_tx: thời gian trung bình (giây) giữa các giao dịch liên
    tiếp của cùng 1 ví -- đặc trưng cho hành vi smurfing (chu kỳ đều đặn bất
    thường).
  - balance_clustering_flag: cờ nhị phân, bật khi ví nhận 1 khoản tiền rồi
    chuyển đi >= 90% số dư đó trong cùng 1 block hoặc trong khung thời gian
    rất ngắn (< 10 phút) -- đặc trưng điển hình của mixing service.

*** GHI CHÚ QUAN TRỌNG VỀ NGUỒN DỮ LIỆU (đọc trước khi dùng ở production) ***
THAY_DOI_V2.md nói 2 đặc trưng trên tính "dựa trên dữ liệu Etherscan đã crawl
ở Phần 3, không cần dữ liệu mới" -- nhưng file/module crawl Etherscan thật của
Phần 3 KHÔNG có trong bộ file được cung cấp cho lần sửa này. Vì vậy 2 hàm
`_compute_avg_time_between_tx` / `_compute_balance_clustering_flag` bên dưới
nhận trực tiếp lịch sử giao dịch của ví qua `state["wallet_tx_history"]`
(list[dict], format ở docstring của từng hàm). Nếu key này không có trong
state, agent KHÔNG tự bịa dữ liệu -- trả về None cho 2 trường mới, tuyệt đối
không mock ngầm để tránh đánh lừa hội đồng bằng số liệu giả. Cần nối dây với
module crawl Etherscan thật của Phần 3 (gửi file đó để nối tiếp).
"""
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

try:
    import shap
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False

# Import chuẩn từ nền tảng đã dọn dẹp ở Phần 1 & 2
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState

MODEL_PATH = Path("models/xgboost_aml.pkl")
_model = None
_explainer = None  # cache SHAP TreeExplainer cùng vòng đời với _model


def load_model():
    """Load model từ đĩa, cache lại trên memory."""
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Không tìm thấy model tại {MODEL_PATH}")
        _model = joblib.load(MODEL_PATH)
    return _model


def _get_explainer(model):
    """Cache shap.TreeExplainer theo model đang dùng (tránh dựng lại mỗi giao dịch)."""
    global _explainer
    if _explainer is None:
        _explainer = shap.TreeExplainer(model)
    return _explainer


# =============================================================================
# --- [V2-1] Feature engineering hành vi ---
# =============================================================================

def _compute_avg_time_between_tx(tx_timestamps: list) -> Optional[float]:
    """
    Thời gian trung bình (giây) giữa các giao dịch liên tiếp của cùng 1 ví.

    Input: tx_timestamps -- list[float|int] các mốc thời gian (unix epoch,
    giây) của các giao dịch mà ví đó tham gia (không cần sắp xếp sẵn).
    Trả về None nếu không đủ dữ liệu (< 2 giao dịch) để tính khoảng cách.
    """
    if not tx_timestamps or len(tx_timestamps) < 2:
        return None
    sorted_ts = sorted(float(t) for t in tx_timestamps)
    diffs = [t2 - t1 for t1, t2 in zip(sorted_ts, sorted_ts[1:])]
    return sum(diffs) / len(diffs)


def _compute_balance_clustering_flag(
    wallet_history: list,
    *,
    threshold_pct: float = 0.9,
    window_seconds: int = 600,
) -> Optional[bool]:
    """
    Cờ mixing-service: bật khi ví nhận 1 khoản tiền rồi chuyển đi >= threshold_pct
    số dư đó trong cùng 1 block HOẶC trong window_seconds giây sau đó.

    Input: wallet_history -- list[dict], mỗi phần tử:
        {"timestamp": <unix epoch giây>, "direction": "in" | "out",
         "amount": <float>, "block": <int, optional>}
    Trả về None nếu không có dữ liệu; False nếu có dữ liệu nhưng không khớp mẫu.
    """
    if not wallet_history:
        return None

    incoming = [tx for tx in wallet_history if tx.get("direction") == "in"]
    outgoing = [tx for tx in wallet_history if tx.get("direction") == "out"]
    if not incoming or not outgoing:
        return False

    for in_tx in incoming:
        in_amount = float(in_tx.get("amount", 0) or 0)
        if in_amount <= 0:
            continue
        in_ts = in_tx.get("timestamp")
        in_block = in_tx.get("block")

        # Gom các khoản ra trong cùng block HOẶC trong window_seconds sau khi nhận
        matched_out_total = 0.0
        for out_tx in outgoing:
            same_block = in_block is not None and out_tx.get("block") == in_block
            within_window = (
                in_ts is not None
                and out_tx.get("timestamp") is not None
                and 0 <= (out_tx["timestamp"] - in_ts) <= window_seconds
            )
            if same_block or within_window:
                matched_out_total += float(out_tx.get("amount", 0) or 0)

        if in_amount > 0 and (matched_out_total / in_amount) >= threshold_pct:
            return True

    return False


def analyze_transaction(state: AMLState) -> AMLState:
    """
    Nhận AMLState, trích xuất đặc trưng giao dịch thô và trả về classifier_score
    + top_features (SHAP per-transaction, V2-2) + 2 đặc trưng hành vi (V2-1).
    """
    # 1. CHỐT KIỂM TRA BẮT BUỘC: Raise Error ngay nếu lọt PII gốc
    assert_no_raw_pii(state)

    # 2. Load model
    model = load_model()

    # 3. Trích xuất đặc trưng (Feature Extraction)
    # TODO: Trong production, bước này cần map dữ liệu giao dịch thật (amount_vnd, frequency...)
    # ra vector 166 chiều tương đương đặc trưng của tập Elliptic.
    # Ở giai đoạn MVP, chúng ta tạo một vector zero độ dài bằng số feature của model.
    num_features = model.n_features_in_
    mock_features = np.zeros((1, num_features))

    # Mô phỏng: Gán amount_vnd đã quy đổi vào một số trường đặc trưng
    mock_features[0, 0] = state.get("amount_vnd", 0) / 1_000_000

    # --- [V2-1] Tính 2 đặc trưng hành vi từ lịch sử ví (nếu có) ---
    # `wallet_tx_history` không phải trường chuẩn của AMLState (không đi qua
    # Privacy Layer, không chứa PII) -- xem GHI CHÚ QUAN TRỌNG ở đầu file.
    wallet_history = state.get("wallet_tx_history")
    avg_time_between_tx = _compute_avg_time_between_tx(
        [tx.get("timestamp") for tx in wallet_history if tx.get("timestamp") is not None]
    ) if wallet_history else None
    balance_clustering_flag = _compute_balance_clustering_flag(wallet_history) if wallet_history else None

    # Mô phỏng MVP: gán 2 đặc trưng hành vi vào 2 slot tiếp theo của vector mock
    # (giống cách amount_vnd được gán ở trên) -- KHÔNG gán nếu None, để model
    # không nhận nhầm "0" (một giá trị hợp lệ) thay cho "không có dữ liệu".
    if num_features > 1 and avg_time_between_tx is not None:
        mock_features[0, 1] = avg_time_between_tx
    if num_features > 2 and balance_clustering_flag is not None:
        mock_features[0, 2] = float(balance_clustering_flag)

    # SỬA LỖI "feature_names mismatch": model train bằng XGBClassifier lưu lại
    # tên cột dạng "feat_0", "feat_1", ... (không phải số nguyên trần). Nếu tạo
    # DataFrame từ mảng numpy mà không đặt tên cột, pandas tự gán tên cột là
    # số nguyên ('0', '1', ...) -- khác hẳn tên cột lúc train -> XGBoost raise
    # ValueError ngay ở bước predict_proba. Lấy đúng feature_names model đã
    # lưu (thứ tự khớp 100% với lúc train) thay vì tự đoán quy ước đặt tên.
    booster = model.get_booster()
    feature_names = booster.feature_names
    if not feature_names:
        # Phòng hờ trường hợp model cũ không lưu feature_names -- fallback về
        # đúng quy ước "feat_i" mà train_classifier.py (Phần 4) đang dùng.
        feature_names = [f"feat_{i}" for i in range(num_features)]

    df_features = pd.DataFrame(mock_features, columns=feature_names)

    # 4. Chấm điểm rủi ro (lấy xác suất thuộc class 1 - illicit)
    risk_score = float(model.predict_proba(df_features)[0, 1])

    # 5. [V2-2 -- Explainable AI PER-TRANSACTION bằng SHAP, thay feature_importances_]
    # KHÁC V1: đây không còn là mức độ quan trọng TOÀN CỤC của model, mà là
    # đóng góp thật của từng đặc trưng cho ĐÚNG giao dịch đang xét (df_features,
    # 1 dòng). shap_value dương = đẩy risk_score lên, âm = kéo xuống -- được
    # phép và NÊN hiển thị cả giá trị âm, không lọc để "kể câu chuyện đẹp".
    top_features = []
    if not SHAP_AVAILABLE:
        print("⚠️ Chưa cài `shap` (pip install shap) -- bỏ qua top_features cho giao dịch này.")
    else:
        try:
            explainer = _get_explainer(model)
            shap_values = explainer.shap_values(df_features)
            # TreeExplainer trên XGBClassifier nhị phân: tuỳ phiên bản shap trả
            # về mảng (1, n_features) cho class dương, hoặc list [class0, class1].
            if isinstance(shap_values, list):
                sv_row = np.asarray(shap_values[1])[0]
            else:
                sv_row = np.asarray(shap_values)[0]

            ranked = sorted(
                zip(feature_names, sv_row), key=lambda x: abs(float(x[1])), reverse=True
            )
            top_features = [(name, round(float(val), 4)) for name, val in ranked[:3]]
        except Exception as e:
            print(f"⚠️ Lỗi khi tính SHAP values (bỏ qua top_features): {e}")
            top_features = []

    # 6. Cập nhật state và trả về
    # SPEC_v2 §2: đổi tên risk_score_classifier -> classifier_score (breaking change).
    state["classifier_score"] = risk_score
    state["top_features"] = top_features
    state["avg_time_between_tx"] = avg_time_between_tx
    state["balance_clustering_flag"] = balance_clustering_flag

    return state


if __name__ == "__main__":
    # Test độc lập: Tạo state giả lập ĐÃ QUA Privacy Layer
    test_state = AMLState(
        tx_hash="0xabcd1234",
        wallet_from="0x11112222",
        wallet_to="0x33334444",
        amount_vnd=550_000_000,
        hashed_fullname="e3b0c44298fc1c14",
        hashed_id_number="a591a6d40bf42040",
        hashed_account_number="2c26b46b68ffc68f",
        classifier_score=None,
        graph_score=None,
        sanction_result=None,
        legal_citations=None,
        risk_assessment_score=None,
        report_path=None,
        approval_status=None,
    )
    # Lịch sử ví mẫu để test 2 đặc trưng hành vi (V2-1) -- không phải trường
    # chuẩn của AMLState, chỉ dùng cho test độc lập ở đây.
    test_state["wallet_tx_history"] = [
        {"timestamp": 1_700_000_000, "direction": "in", "amount": 500_000_000, "block": 100},
        {"timestamp": 1_700_000_300, "direction": "out", "amount": 480_000_000, "block": 100},
        {"timestamp": 1_700_003_600, "direction": "in", "amount": 10_000_000, "block": 101},
    ]

    print("Đang chạy thử Transaction Agent...")
    updated_state = analyze_transaction(test_state)
    print(f"Hoàn thành! Điểm rủi ro sơ bộ (classifier_score): {updated_state['classifier_score']:.4f}")
    print(f"Top features (SHAP per-transaction, V2-2): {updated_state.get('top_features')}")
    print(f"avg_time_between_tx (V2-1): {updated_state.get('avg_time_between_tx')}")
    print(f"balance_clustering_flag (V2-1): {updated_state.get('balance_clustering_flag')}")