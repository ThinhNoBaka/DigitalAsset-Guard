# agents/calibrate_classifier_threshold.py
"""
Calibrate ngưỡng classifier_score dùng trong Decision Engine (Rule 4) bằng
precision-recall curve trên test set — dataset CÓ ground truth
thật (illicit/licit), khác với graph_score (không có dữ liệu ghép cặp để
calibrate — xem docstring đầu agents/decision_engine.py để biết lý do
project đổi sang kiến trúc rule-based thay vì weighted-sum ensemble).

[THAY ĐỔI 2026-08-08] Chuyển từ Elliptic sang Ethereum Fraud Detection:
test set dùng chung là data/processed/ethereum_fraud_test.csv (được
agents/train_classifier.py tách stratified 20% và ghi ra khi train), nhãn
là cột FLAG (1 = illicit/fraud, 0 = licit).

CÁCH DÙNG:
    python -m agents.calibrate_classifier_threshold \
        --test-csv data/processed/ethereum_fraud_test.csv \
        --target-recall 0.9

Script:
    1. Load model đã train (models/xgboost_aml.pkl).
    2. Load test CSV, tự tìm cột nhãn (label/class/illicit/is_illicit).
    3. Chấm điểm classifier_score cho toàn bộ test set.
    4. Dựng precision-recall curve, trong các ngưỡng giữ được recall >=
       target_recall (mặc định 0.9) chọn ngưỡng có PRECISION CAO NHẤT; hoà
       precision thì lấy ngưỡng THẤP hơn (recall cao hơn, ít FN hơn) -- xem
       select_threshold(). target_recall là sàn recall chấp nhận được: với
       0.9 hệ thống CHẤP NHẬN bỏ lọt tới ~10% ví illicit trên test set để
       giảm báo động giả. Muốn ít false negative hơn thì tăng
       --target-recall, KHÔNG phải sửa logic chọn ngưỡng.
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

LABEL_COLUMN_CANDIDATES = ["label", "class", "illicit", "is_illicit", "target", "FLAG"]


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


def select_threshold(y_true, y_scores, target_recall: float) -> dict:
    """
    Chọn ngưỡng classifier_score: precision cao nhất trong các ngưỡng có
    recall >= target_recall; hoà precision thì lấy ngưỡng thấp nhất.

    Bản cũ ghi "chọn ngưỡng THẤP NHẤT" nhưng code lấy argmax(threshold) --
    ngưỡng CAO NHẤT còn giữ được recall, tức đẩy recall sát sàn target và FN
    tăng mà precision không chắc tốt hơn (precision không đơn điệu theo
    ngưỡng). Lấy đúng ngưỡng thấp nhất theo nghĩa đen cũng không dùng được:
    đó là điểm thấp nhất của test set (recall=1, gắn cờ gần như mọi ví).
    """
    y_true = np.asarray(y_true).astype(int)
    y_scores = np.asarray(y_scores, dtype=float)

    precision, recall, thresholds = precision_recall_curve(y_true, y_scores)
    # precision_recall_curve trả precision/recall có độ dài = len(thresholds)+1
    # (điểm cuối cùng ứng với recall=0, không có threshold tương ứng).
    # Cắt bớt để 3 mảng cùng độ dài.
    precision = precision[:-1]
    recall = recall[:-1]

    eligible = np.where(recall >= target_recall)[0]

    if len(eligible) == 0:
        achievable_max_recall = float(recall.max()) if len(recall) else 0.0
        raise ValueError(
            f"Không có ngưỡng nào đạt target_recall={target_recall} trên tập "
            f"test này. Recall tối đa đạt được: {achievable_max_recall:.4f}. "
            "Hạ target_recall xuống hoặc cải thiện model trước khi calibrate."
        )

    best_precision = precision[eligible].max()
    best_candidates = eligible[np.isclose(precision[eligible], best_precision)]
    best_idx = best_candidates[np.argmin(thresholds[best_candidates])]

    return {
        "threshold": float(thresholds[best_idx]),
        "precision": float(precision[best_idx]),
        "recall": float(recall[best_idx]),
    }


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

    chosen = select_threshold(y_true, y_scores, target_recall)
    chosen_threshold = chosen["threshold"]
    chosen_precision = chosen["precision"]
    chosen_recall = chosen["recall"]

    result = {
        "threshold": round(chosen_threshold, 4),
        "target_recall": target_recall,
        "selection_rule": "max precision s.t. recall >= target_recall (tie -> lowest threshold)",
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
        description="Calibrate ngưỡng classifier_score bằng precision-recall curve trên test set Ethereum Fraud Detection."
    )
    parser.add_argument(
        "--test-csv",
        required=True,
        help="Đường dẫn CSV test set (vd: data/processed/ethereum_fraud_test.csv).",
    )
    parser.add_argument(
        "--target-recall",
        type=float,
        default=0.9,
        help="Recall tối thiểu cần đạt (mặc định 0.9 -- ưu tiên không bỏ lọt giao dịch rửa tiền).",
    )
    args = parser.parse_args()

    calibrate(args.test_csv, args.target_recall)