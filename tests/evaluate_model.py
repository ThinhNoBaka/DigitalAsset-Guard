"""
tests/evaluate_model.py
Đánh giá mô hình XGBoost. Ưu tiên xuất các chỉ số Recall, F1 và AUC-PR.
"""
import pandas as pd
import joblib
from sklearn.metrics import precision_recall_fscore_support, average_precision_score, accuracy_score
from pathlib import Path

MODEL_PATH = Path("models/xgboost_aml.pkl")
TEST_DATA = Path("data/raw/elliptic/elliptic_test.csv")

def evaluate():
    if not MODEL_PATH.exists() or not TEST_DATA.exists():
        print("LỖI: Thiếu model hoặc dữ liệu test. Hãy chạy agents/train_classifier.py trước.")
        return

    print("Đang load model và tập dữ liệu test...")
    clf = joblib.load(MODEL_PATH)
    df_test = pd.read_csv(TEST_DATA)

    drop_cols = ['txId', 'time_step', 'class']
    X_test = df_test.drop(columns=drop_cols)
    y_true = df_test['class']

    # Dự đoán nhãn (0/1) và xác suất
    y_pred = clf.predict(X_test)
    y_proba = clf.predict_proba(X_test)[:, 1]

    # Tính toán Metrics
    acc = accuracy_score(y_true, y_pred)
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, average="binary")
    auc_pr = average_precision_score(y_true, y_proba)

    # In kết quả chuẩn format SPEC.md Mục 3.1
    print("\n=== KẾT QUẢ ĐÁNH GIÁ MÔ HÌNH (SPEC.md Mục 3.1) ===")
    print(f"Accuracy           : {acc:.4f} (Chỉ tham khảo, không dùng đánh giá chính)")
    print(f"Precision (illicit): {precision:.4f} (Tỷ lệ cảnh báo đúng)")
    print(f"Recall (illicit)   : {recall:.4f} (Chỉ số quan trọng nhất)")
    print(f"F1-score (illicit) : {f1:.4f} (Cân bằng Precision/Recall)")
    print(f"AUC-PR             : {auc_pr:.4f} (Phù hợp hơn AUC-ROC do mất cân bằng nhãn)")

if __name__ == "__main__":
    evaluate()