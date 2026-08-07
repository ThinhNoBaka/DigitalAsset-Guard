# agents/calibrate_classifier_threshold.py
"""
Calibrate ngưỡng classifier_score dùng trong Decision Engine (Rule 4) bằng
precision-recall curve trên Elliptic test set — dataset CÓ ground truth
thật (illicit/licit), khác với graph_score (không có dữ liệu ghép cặp để
calibrate — xem docstring đầu agents/decision_engine.py để biết lý do
project đổi sang kiến trúc rule-based thay vì weighted-sum ensemble).

CÁCH DÙNG:
    python -m agents.calibrate_classifier_threshold \
        --test-csv data/raw/elliptic/elliptic_test.csv \
        --target-recall 0.9

Script:
    1. Load model đã train (models/xgboost_aml.pkl).
    2. Load test CSV, tự tìm cột nhãn (label/class/illicit/is_illicit).
    3. Chấm điểm classifier_score cho toàn bộ test set.
    4. Dựng precision-recall curve, chọn ngưỡng THẤP NHẤT đạt được
       target_recall (mặc định 0.9) -- tối ưu recall vì bỏ lọt 1 giao dịch
       rửa tiền thật (false negative) nghiêm trọng hơn nhiều so với 1 báo
       động giả (false positive) phải chuyên viên xem xét thêm.
    5. Ghi models/classifier_threshold.json để decision_engine.py tự đọc.

KHÔNG tự bịa ngưỡng nếu thiếu model/dữ liệu -- raise lỗi rõ ràng.
"""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve

from core.config import MODELS_DIR

MODEL_PATH = MODELS_DIR / "xgboost_aml.pkl"
OUTPUT_PATH = MODELS_DIR / "classifier_threshold.json"

LABEL_COLUMN_CANDIDATES = ["label", "class", "illicit", "is_illicit", "target"]


def _find_label_column(df: pd.DataFrame) -> str:
    for candidate in LABEL_COLUMN_CANDIDATES:
        if candidate in df.columns:
            return candidate
    raise ValueError(
        f"Không tìm thấy cột nhãn trong test CSV. Đã thử: "
        f"{LABEL_COLUMN_CANDIDATES}. Các cột hiện có: {list(df.columns)}. "
        "Nếu tên cột nhãn khác, thêm vào LABEL_COLUMN_CANDIDATES hoặc đổi "
        "tên cột trong file CSV."
    )


def _score_transactions(model, df: pd.DataFrame, label_col: str) -> np.ndarray:
    """Chấm điểm classifier_score cho toàn bộ test set, dùng đúng
    feature_names model đã lưu lúc train (tránh lỗi feature mismatch,
    cùng cách xử lý với agents/transaction_classifier.py)."""
    booster = model.get_booster()
    feature_names = booster.feature_names

    feature_cols = [c for c in df.columns if c != label_col]

    if feature_names and set(feature_names).issubset(set(df.columns)):
        X = df[feature_names]
    else:
        # Fallback: dùng toàn bộ cột không phải nhãn theo đúng thứ tự có sẵn.
        # Chỉ an toàn nếu số lượng cột khớp num_features của model.
        if len(feature_cols) != model.n_features_in_:
            raise ValueError(
                f"Số cột feature trong CSV ({len(feature_cols)}) không khớp "
                f"số feature model kỳ vọng ({model.n_features_in_}), và "
                f"feature_names lưu trong model cũng không khớp tên cột CSV. "
                "Kiểm tra lại format test CSV có đúng với lúc train không."
            )
        X = df[feature_cols]
        X.columns = feature_names if feature_names else X.columns

    return model.predict_proba(X)[:, 1]


def calibrate(test_csv_path: str, target_recall: float = 0.9) -> dict:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Không tìm thấy model tại {MODEL_PATH}. Chạy "
            "`python -m agents.train_classifier` trước."
        )

    path = Path(test_csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy test CSV tại {test_csv_path}.")

    if not (0.0 < target_recall <= 1.0):
        raise ValueError(f"target_recall phải trong (0, 1], nhận: {target_recall}")

    df = pd.read_csv(path)
    label_col = _find_label_column(df)
    y_true = df[label_col].astype(int).values

    if len(set(y_true)) < 2:
        raise ValueError(
            "Test set chỉ có 1 lớp -- không thể dựng precision-recall curve."
        )

    model = joblib.load(MODEL_PATH)
    y_scores = _score_transactions(model, df, label_col)

    precision, recall, thresholds = precision_recall_curve(y_true, y_scores)
    # precision_recall_curve trả precision/recall có độ dài = len(thresholds)+1
    # (điểm cuối cùng ứng với threshold=1.0, không có threshold tương ứng).
    # Cắt bớt để 3 mảng cùng độ dài, dễ chọn theo target_recall.
    precision = precision[:-1]
    recall = recall[:-1]

    # Chọn ngưỡng THẤP NHẤT đạt được target_recall (ưu tiên không bỏ lọt).
    # recall giảm dần khi threshold tăng -> lọc các điểm đạt target rồi lấy
    # threshold lớn nhất trong số đó (tối đa hoá precision trong khi vẫn giữ
    # đủ recall yêu cầu).
    eligible = np.where(recall >= target_recall)[0]

    if len(eligible) == 0:
        achievable_max_recall = float(recall.max()) if len(recall) else 0.0
        raise ValueError(
            f"Không có ngưỡng nào đạt target_recall={target_recall} trên tập "
            f"test này. Recall tối đa đạt được: {achievable_max_recall:.4f}. "
            "Hạ target_recall xuống hoặc cải thiện model trước khi calibrate."
        )

    best_idx = eligible[np.argmax(thresholds[eligible])]
    chosen_threshold = float(thresholds[best_idx])
    chosen_precision = float(precision[best_idx])
    chosen_recall = float(recall[best_idx])

    result = {
        "threshold": round(chosen_threshold, 4),
        "target_recall": target_recall,
        "achieved_recall": round(chosen_recall, 4),
        "achieved_precision": round(chosen_precision, 4),
        "calibrated_on_file": str(path),
        "calibrated_on_n_samples": int(len(df)),
        "calibrated_on_n_positive": int(y_true.sum()),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ Đã lưu ngưỡng vào {OUTPUT_PATH}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calibrate ngưỡng classifier_score bằng precision-recall curve trên Elliptic test set."
    )
    parser.add_argument(
        "--test-csv",
        required=True,
        help="Đường dẫn CSV test set (vd: data/raw/elliptic/elliptic_test.csv).",
    )
    parser.add_argument(
        "--target-recall",
        type=float,
        default=0.9,
        help="Recall tối thiểu cần đạt (mặc định 0.9 -- ưu tiên không bỏ lọt giao dịch rửa tiền).",
    )
    args = parser.parse_args()

    calibrate(args.test_csv, args.target_recall)