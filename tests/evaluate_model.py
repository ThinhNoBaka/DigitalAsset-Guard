"""
tests/evaluate_model.py
Đánh giá mô hình XGBoost. Ưu tiên xuất các chỉ số Recall, F1 và AUC-PR.

[THAY ĐỔI 2026-08-08] Chuyển từ Elliptic sang Ethereum Fraud Detection:
- Model được train bởi agents/train_classifier.py trên
  data/processed/ethereum_fraud_training_clean.csv.
- Test set dùng chung là data/processed/ethereum_fraud_test.csv (test split
  đã được agents/train_classifier.py tách stratified 20% và ghi ra khi train).
- Cột feature = toàn bộ cột trừ nhãn (FLAG), dùng đúng feature_names model.

[V2-1 -- THAY_DOI_V2.md] Sau phần đánh giá model CHÍNH THỨC (model đã được
agents/train_classifier.py chọn là tốt nhất), in thêm bảng so sánh Recall/F1/
AUC-PR THẬT của cả 3 cấu hình xử lý mất cân bằng nhãn đã thử nghiệm (chỉ
scale_pos_weight / chỉ SMOTE / cả hai) -- đọc từ tests/model_comparison_v2.json
do agents/train_classifier.py ghi ra. Nếu chưa chạy train_classifier.py (file
chưa tồn tại), phần này tự bỏ qua, không lỗi.
"""
import json
import pandas as pd
import joblib
from sklearn.metrics import precision_recall_fscore_support, average_precision_score, accuracy_score
from pathlib import Path

MODEL_PATH = Path("models/xgboost_aml.pkl")
TEST_DATA = Path("data/processed/ethereum_fraud_test.csv")
COMPARISON_PATH = Path("tests/model_comparison_v2.json")

def evaluate():
    if not MODEL_PATH.exists() or not TEST_DATA.exists():
        print("LỖI: Thiếu model hoặc dữ liệu test. Hãy chạy agents/train_classifier.py trước.")
        return

    print("Đang load model và tập dữ liệu test...")
    clf = joblib.load(MODEL_PATH)
    df_test = pd.read_csv(TEST_DATA)

    # Feature names model lưu lúc train (khớp 100% thứ tự) -- tránh mismatch
    booster = clf.get_booster()
    feature_names = booster.feature_names
    if not feature_names:
        feature_names = [f"feat_{i}" for i in range(clf.n_features_in_)]

    X_test = df_test[feature_names]
    y_true = df_test['FLAG']

    # Dự đoán nhãn (0/1) và xác suất
    y_pred = clf.predict(X_test)
    y_proba = clf.predict_proba(X_test)[:, 1]

    # Tính toán Metrics
    acc = accuracy_score(y_true, y_pred)
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, average="binary")
    auc_pr = average_precision_score(y_true, y_proba)

    # In kết quả chuẩn format SPEC.md Mục 3.1
    print("\n=== KẾT QUẢ ĐÁNH GIÁ MÔ HÌNH CHÍNH THỨC (SPEC.md Mục 3.1) ===")
    print(f"Accuracy           : {acc:.4f} (Chỉ tham khảo, không dùng đánh giá chính)")
    print(f"Precision (illicit): {precision:.4f} (Tỷ lệ cảnh báo đúng)")
    print(f"Recall (illicit)   : {recall:.4f} (Chỉ số quan trọng nhất)")
    print(f"F1-score (illicit) : {f1:.4f} (Cân bằng Precision/Recall)")
    print(f"AUC-PR             : {auc_pr:.4f} (Phù hợp hơn AUC-ROC do mất cân bằng nhãn)")

    _print_v21_comparison()


def _print_v21_comparison() -> None:
    """[V2-1] In bảng so sánh 3 cấu hình xử lý mất cân bằng nhãn, số liệu THẬT
    lấy từ tests/model_comparison_v2.json (ghi bởi agents/train_classifier.py)."""
    if not COMPARISON_PATH.exists():
        print(f"\n[V2-1] Chưa có {COMPARISON_PATH} -- chạy agents/train_classifier.py trước "
              "để có bảng so sánh 3 cấu hình (scale_pos_weight / SMOTE / cả hai).")
        return

    with open(COMPARISON_PATH, "r", encoding="utf-8") as f:
        comparison = json.load(f)

    print("\n=== [V2-1] SO SÁNH 3 CẤU HÌNH XỬ LÝ MẤT CÂN BẰNG NHÃN (số liệu thật) ===")
    print(f"{'Cấu hình':<32} {'Recall':>8} {'F1':>8} {'AUC-PR':>8}")
    for name, metrics in comparison.get("configs", {}).items():
        marker = " <== được chọn" if name == comparison.get("selected_config") else ""
        if metrics is None:
            print(f"{name:<32} {'--':>8} {'--':>8} {'--':>8}  (không chạy được, xem notes)")
            continue
        print(f"{name:<32} {metrics['recall']:>8.4f} {metrics['f1']:>8.4f} {metrics['auc_pr']:>8.4f}{marker}")

    if comparison.get("notes"):
        print("Ghi chú:", comparison["notes"])

    baseline = comparison.get("configs", {}).get("scale_pos_weight_only")
    selected = comparison.get("configs", {}).get(comparison.get("selected_config"))
    if baseline and selected and comparison.get("selected_config") != "scale_pos_weight_only":
        delta = selected["auc_pr"] - baseline["auc_pr"]
        if delta <= 0:
            print(f"[LƯU Ý BÁO CÁO] Cấu hình được chọn ({comparison['selected_config']}) "
                  f"không thật sự vượt baseline về AUC-PR (delta={delta:+.4f}) -- "
                  "cần giải thích rõ trong báo cáo, không chỉ report số đẹp.")

if __name__ == "__main__":
    evaluate()