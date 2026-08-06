"""
agents/transaction_classifier.py
Agent độc lập chấm điểm rủi ro sơ bộ từ đặc trưng giao dịch.
"""
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

# Import chuẩn từ nền tảng đã dọn dẹp ở Phần 1 & 2
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState

MODEL_PATH = Path("models/xgboost_aml.pkl")
_model = None

def load_model():
    """Load model từ đĩa, cache lại trên memory."""
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Không tìm thấy model tại {MODEL_PATH}")
        _model = joblib.load(MODEL_PATH)
    return _model

def analyze_transaction(state: AMLState) -> AMLState:
    """
    Nhận AMLState, trích xuất đặc trưng giao dịch thô và trả về risk_score_classifier.
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

    # 5. [Bổ sung -- Explainable AI nhẹ, không cần SHAP, Thay đổi 2]
    # `feature_importances_` là mức độ quan trọng TOÀN CỤC của model (đặc trưng
    # nào nhìn chung hay được dùng để tách illicit/licit trên toàn bộ tập train)
    # -- KHÔNG PHẢI lý do tại sao GIAO DỊCH CỤ THỂ NÀY bị chấm điểm này (cái đó
    # mới đúng là SHAP/LIME per-instance, để dành cho Hướng phát triển). Thứ tự
    # importances khớp trực tiếp với feature_names vì đây chính là danh sách cột
    # model đã predict trên đó (không phải suy đoán quy ước riêng).
    try:
        importances = model.feature_importances_
        ranked = sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True)
        top_features = [(name, round(float(score), 4)) for name, score in ranked[:4]]
    except Exception as e:
        print(f"⚠️ Không lấy được feature_importances_ (bỏ qua top_features): {e}")
        top_features = []

    # 6. Cập nhật state và trả về
    state["risk_score_classifier"] = risk_score
    state["top_features"] = top_features

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
        risk_score_classifier=None,
        kyc_flags=None,
        graph_risk_score=None,
        legal_citations=None,
        final_risk_score=None,
        report_path=None,
        approval_status=None
    )
    
    print("Đang chạy thử Transaction Agent...")
    updated_state = analyze_transaction(test_state)
    print(f"Hoàn thành! Điểm rủi ro sơ bộ (risk_score_classifier): {updated_state['risk_score_classifier']:.4f}")
    print(f"Top features (Explainable AI nhẹ): {updated_state.get('top_features')}")