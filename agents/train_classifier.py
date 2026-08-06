"""
agents/train_classifier.py
Huấn luyện XGBoost trên tập Elliptic.
Chia tập train/test theo time_step (<=34 là train, >34 là test).
Xử lý mất cân bằng nhãn bằng scale_pos_weight.
"""
import pandas as pd
import xgboost as xgb
import joblib
from pathlib import Path

ELLIPTIC_CLEAN = Path("data/raw/elliptic/elliptic_clean.csv")
MODEL_OUT = Path("models/xgboost_aml.pkl")
TEST_DATA_OUT = Path("data/raw/elliptic/elliptic_test.csv")

def train_model():
    if not ELLIPTIC_CLEAN.exists():
        print(f"LỖI: Không tìm thấy {ELLIPTIC_CLEAN}. Vui lòng chạy scripts/03_load_elliptic.py trước.")
        return

    print("Đang load dữ liệu Elliptic...")
    df = pd.read_csv(ELLIPTIC_CLEAN)

    # Loại bỏ nhãn "unknown" (bắt buộc theo SPEC.md Phần 4)
    # Ép kiểu string tường minh trước khi lọc, vì cột 'class' gốc có lẫn
    # giá trị "unknown" nên pandas sẽ đọc cả cột dưới dạng string (kể cả "1"/"2"),
    # nếu map thẳng bằng key số nguyên sẽ ra toàn NaN mà không báo lỗi.
    df['class'] = df['class'].astype(str).str.strip()
    df = df[df['class'] != 'unknown'].copy()

    # Chuyển đổi nhãn từ nguyên bản sang Binary Classification
    # Gốc: 1 (illicit), 2 (licit)
    # Đích: 1 (Positive - illicit), 0 (Negative - licit)
    df['class'] = df['class'].astype(int).map({1: 1, 2: 0})

    # Temporal split theo time_step (Cấm dùng random split)
    train_df = df[df['time_step'] <= 34]
    test_df = df[df['time_step'] > 34]

    # Tách X, y (loại bỏ các cột định danh và nhãn)
    drop_cols = ['txId', 'time_step', 'class']
    X_train = train_df.drop(columns=drop_cols)
    y_train = train_df['class']
    X_test = test_df.drop(columns=drop_cols)
    y_test = test_df['class']

    # Tính scale_pos_weight = số lượng class 0 / số lượng class 1
    num_neg = (y_train == 0).sum()
    num_pos = (y_train == 1).sum()
    scale_weight = num_neg / num_pos if num_pos > 0 else 1.0

    print(f"Kích thước tập Train: {len(X_train)} (Illicit: {num_pos}, Licit: {num_neg})")
    print(f"Kích thước tập Test: {len(X_test)}")
    print(f"Chỉ số scale_pos_weight áp dụng: {scale_weight:.2f}")

    print("Đang huấn luyện mô hình XGBoost...")
    clf = xgb.XGBClassifier(
        scale_pos_weight=scale_weight,
        n_estimators=100,
        max_depth=6,
        random_state=42,
        eval_metric="aucpr"
    )
    clf.fit(X_train, y_train)

    # Lưu model
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, MODEL_OUT)
    print(f"Hoàn tất! Đã lưu model tại {MODEL_OUT}")

    # Lưu tập test ra đĩa để file evaluate_model.py sử dụng chung
    test_df.to_csv(TEST_DATA_OUT, index=False)

if __name__ == "__main__":
    train_model()