"""
scripts/diagnose_feature_vector.py -- CHẨN ĐOÁN CHỈ ĐỌC (không sửa gì).

Mục đích (Lỗi 2 của nhiệm vụ fix):
Xác định hàm build feature vector cho luồng runtime thật, in ra feature vector
của 3 kịch bản mock (smurfing, layering, name_similarity) và so sánh.

Feature build hiện nằm INLINE trong agents/transaction_classifier.py::analyze_transaction
(dòng ~147-171) -- KHÔNG có hàm riêng. Script này tái hiện ĐÚNG logic đó
(đọc nguyên mẫu từ file, không thay đổi code) để in vector cho 3 kịch bản.

Chạy: python -m scripts.diagnose_feature_vector
"""
import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

MOCK_DIR = Path("data/mock")
MODEL_PATH = Path("models/xgboost_aml.pkl")
SCENARIO_FILES = [
    "scenario_smurfing.json",
    "scenario_layering.json",
    "scenario_name_similarity.json",
]


def _build_feature_vector(state: dict, model) -> tuple:
    """
    Tái hiện ĐÚNG logic build feature của analyze_transaction:
      - vector zero, n_features = model.n_features_in_
      - mock_features[0, 0] = amount_vnd / 1_000_000
      - nếu có wallet_tx_history: feat_1 = avg_time_between_tx, feat_2 = balance_clustering_flag
    Trả về (vector numpy 1D, feature_names list).
    """
    num_features = model.n_features_in_
    mock_features = np.zeros((1, num_features))

    mock_features[0, 0] = state.get("amount_vnd", 0) / 1_000_000

    wallet_history = state.get("wallet_tx_history")
    if wallet_history:
        # _compute_avg_time_between_tx (chéo từ source, chỉ đọc)
        timestamps = [tx.get("timestamp") for tx in wallet_history if tx.get("timestamp") is not None]
        if len(timestamps) >= 2:
            sorted_ts = sorted(float(t) for t in timestamps)
            diffs = [t2 - t1 for t1, t2 in zip(sorted_ts, sorted_ts[1:])]
            avg = sum(diffs) / len(diffs)
            if num_features > 1:
                mock_features[0, 1] = avg
        # _compute_balance_clustering_flag -- mock path không có history, bỏ qua

    booster = model.get_booster()
    feature_names = booster.feature_names
    if not feature_names:
        feature_names = [f"feat_{i}" for i in range(num_features)]

    return mock_features[0].copy(), feature_names


def main():
    if not MODEL_PATH.exists():
        print(f"LỖI: Không tìm thấy model tại {MODEL_PATH}")
        return

    model = joblib.load(MODEL_PATH)
    print(f"Model: {MODEL_PATH}")
    print(f"n_features_in_ = {model.n_features_in_}")
    print("=" * 80)

    vectors = {}
    for filename in SCENARIO_FILES:
        with open(MOCK_DIR / filename, "r", encoding="utf-8") as f:
            scenario = json.load(f)

        evaluated_tx = dict(scenario["evaluated_transaction"])
        evaluated_tx["tx_hash"] = evaluated_tx.get("tx_hash") or f"DEMO_{scenario['scenario']}"
        vec, feature_names = _build_feature_vector(evaluated_tx, model)

        # Chấm điểm bằng model — tái hiện predict_proba của analyze_transaction
        df = pd.DataFrame(vec.reshape(1, -1), columns=feature_names)
        score = float(model.predict_proba(df)[0, 1])

        vectors[scenario["scenario"]] = {
            "amount_vnd": evaluated_tx["amount_vnd"],
            "vector": vec,
            "score": score,
            "non_zero_count": int(np.count_nonzero(vec)),
            "non_zero_indices": [i for i, v in enumerate(vec) if v != 0],
            "wallet_from": evaluated_tx.get("wallet_from"),
            "wallet_to": evaluated_tx.get("wallet_to"),
            "fullname": evaluated_tx.get("fullname"),
        }

    # Bước B: in feature vector đầy đủ
    for name, info in vectors.items():
        print(f"\n--- KỊCH BẢN: {name.upper()} ---")
        print(f"  amount_vnd       : {info['amount_vnd']}")
        print(f"  wallet_from      : {info['wallet_from']}")
        print(f"  wallet_to        : {info['wallet_to']}")
        print(f"  fullname         : {info['fullname']}")
        print(f"  classifier_score : {info['score']}")
        print(f"  non_zero_count   : {info['non_zero_count']} / {len(info['vector'])}")
        print(f"  non_zero_indices : {info['non_zero_indices']}")
        print(f"  feat_0 value     : {info['vector'][0]}")
        print("  Vector (165 giá trị):")
        print("  " + str([round(float(v), 6) for v in info["vector"]]))

    # So sánh
    print("\n" + "=" * 80)
    print("SO SÁNH 3 VECTOR:")
    names = list(vectors.keys())
    v0, v1, v2 = (vectors[n]["vector"] for n in names)
    identical_feat = all(
        abs(a - b) < 1e-12
        for a, b in zip(v0, v1)
    ) and all(
        abs(a - b) < 1e-12
        for a, b in zip(v0, v2)
    )
    print(f"  3 vector GIỐNG HỆT NHAU: {identical_feat}")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            diff_count = sum(
                1 for a, b in zip(vectors[names[i]]["vector"], vectors[names[j]]["vector"])
                if abs(a - b) > 1e-12
            )
            print(f"  {names[i]} vs {names[j]}: {diff_count} giá trị khác nhau")
    print(f"  classifier_score smurfing       : {vectors['smurfing']['score']}")
    print(f"  classifier_score layering       : {vectors['layering']['score']}")
    print(f"  classifier_score name_similarity : {vectors['name_similarity']['score']}")


if __name__ == "__main__":
    main()