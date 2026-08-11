"""
agents/train_classifier.py
Huấn luyện XGBoost trên dataset Ethereum Fraud Detection (đã clean).

[THAY ĐỔI 2026-08-08] Chuyển từ Elliptic sang Ethereum Fraud Detection
theo dataset MỚI (data/raw/ethereum_fraud/Complete.csv -> đã clean bởi
scripts/01_prepare_ethereum_fraud_dataset.py thành
data/processed/ethereum_fraud_training_clean.csv):

- Dataset clean chỉ có 1 file (không tách sẵn train/test theo time_step như
  Elliptic) -> script TỰ tách train/test bằng StratifiedSplit (test_size=0.2,
  random_state=42) để giữ nguyên phân bố nhãn, KHÔNG rò rỉ (tách SAU khi đã
  drop cột Address).
- Test split được lưu ra data/processed/ethereum_fraud_test.csv để
  tests/evaluate_model.py và agents/calibrate_classifier_threshold.py dùng
  chung đúng 1 tập test (tránh mỗi nơi tự split khác nhau).
- Cột danh tính Address bị loại khỏi feature; nhãn là FLAG (1 = illicit/fraud,
  0 = licit).

[V2-1 -- THAY_DOI_V2.md] Xử lý mất cân bằng nhãn: thử nghiệm CẢ 3 cấu hình
  (1) chỉ scale_pos_weight (baseline V1)
  (2) chỉ SMOTE (imblearn.over_sampling.SMOTE, CHỈ áp dụng trên tập train,
      sau khi đã tách train/test -- tuyệt đối không SMOTE trước khi tách
      vì sẽ rò rỉ dữ liệu tổng hợp từ tương lai vào quá khứ)
  (3) cả hai cùng lúc
và GHI SỐ Recall/F1/AUC-PR THẬT của từng cấu hình (không dùng số minh hoạ) ra
`tests/model_comparison_v2.json` + in ra console, để tests/evaluate_model.py
hoặc báo cáo kỹ thuật dùng lại. Cấu hình tốt nhất (theo AUC-PR, tie-break bằng
Recall) được chọn làm model chính thức lưu ở MODEL_OUT.

Lưu ý báo cáo (bắt buộc theo THAY_DOI_V2.md V2-1): nếu SMOTE không cải thiện
Recall/AUC-PR so với chỉ dùng scale_pos_weight, KHÔNG được che số liệu đó --
giữ nguyên trong model_comparison_v2.json và giải thích trong báo cáo, không
chỉ report cấu hình đẹp nhất.
"""
import json
from pathlib import Path

import pandas as pd
import xgboost as xgb
import joblib
from sklearn.model_selection import train_test_split
from sklearn.metrics import recall_score, f1_score, average_precision_score, confusion_matrix, precision_score, classification_report

try:
    from imblearn.over_sampling import SMOTE
    SMOTE_AVAILABLE = True
except ImportError:
    SMOTE_AVAILABLE = False

ETHEREUM_FRAUD_CLEAN = Path("data/processed/ethereum_fraud_training_clean.csv")  # dataset sạch Ethereum Fraud (1 file)
ETHEREUM_FRAUD_TEST = Path("data/processed/ethereum_fraud_test.csv")             # test split tự tách khi train
MODEL_OUT = Path("models/xgboost_aml.pkl")
COMPARISON_OUT = Path("tests/model_comparison_v2.json")


def _load_clean_dataset(filepath: Path) -> pd.DataFrame:
    """
    Load dataset Ethereum Fraud đã clean (scripts/01_prepare_ethereum_fraud_dataset.py).

    Schema: cột 'Address' (danh tính ví, KHÔNG phải feature), cột 'FLAG'
    (1 = illicit/fraud, 0 = licit), 37 cột feature numeric.
    """
    df = pd.read_csv(filepath)

    if "Address" not in df.columns:
        raise ValueError(f"Thiếu cột bắt buộc 'Address' trong {filepath}.")
    if "FLAG" not in df.columns:
        raise ValueError(f"Thiếu cột bắt buộc 'FLAG' trong {filepath}.")

    df["FLAG"] = df["FLAG"].astype(int)

    before = len(df)
    df = df[df["FLAG"].isin([0, 1])].copy()
    dropped = before - len(df)
    if dropped:
        print(f"[CẢNH BÁO] {filepath}: loại {dropped} dòng có FLAG khác 0/1")

    return df


def _split_train_test(df: pd.DataFrame, *, test_size: float = 0.2,
                      random_state: int = 42) -> tuple:
    """
    Tách train/test STRATIFIED trên dataset clean (1 file).

    - Bỏ cột danh tính 'Address' trước khi tách (không bao giờ đưa ví vào feature).
    - stratify=y giữ nguyên phân bố nhãn ở cả 2 tập.
    - KHÔNG dùng SMOTE trước bước này (chống rò rỉ dữ liệu tổng hợp).
    """
    X = df.drop(columns=["Address", "FLAG"])
    y = df["FLAG"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=test_size,
        random_state=random_state,
        stratify=y,
    )
    return X_train, y_train, X_test, y_test


def _make_classifier(scale_weight: float) -> xgb.XGBClassifier:
    """Cấu hình XGBoost dùng chung cho cả 3 thử nghiệm (chỉ đổi input train)."""
    return xgb.XGBClassifier(
        scale_pos_weight=scale_weight,
        n_estimators=100,
        max_depth=6,
        random_state=42,
        eval_metric="aucpr",
    )


def _evaluate(clf, X_test: pd.DataFrame, y_test: pd.Series) -> dict:
    """Đánh giá model trên tập test và in Confusion Matrix."""
    y_pred = clf.predict(X_test)
    y_proba = clf.predict_proba(X_test)[:, 1]

    cm = confusion_matrix(y_test, y_pred)
    tn, fp, fn, tp = cm.ravel()

    print("\n========== CONFUSION MATRIX ==========")
    print("                Predicted")
    print("              Licit   Illicit")
    print(f"Actual Licit    {tn:5d}     {fp:5d}")
    print(f"Actual Illicit  {fn:5d}     {tp:5d}")
    print("======================================")

    print("\n===== Classification Report =====")
    print(classification_report(y_test, y_pred, target_names=["Licit", "Illicit"], digits=4))

    print("\n===== Probability Summary =====")
    print(pd.Series(y_proba).describe())
    print("Top 20 probabilities:")
    print(y_proba[:20])

    return {
        "precision": round(float(precision_score(y_test, y_pred)), 4),
        "recall": round(float(recall_score(y_test, y_pred)), 4),
        "f1": round(float(f1_score(y_test, y_pred)), 4),
        "auc_pr": round(float(average_precision_score(y_test, y_proba)), 4),
    }


def _run_config(name: str, X_train, y_train, X_test, y_test, *, use_smote: bool,
                 force_scale_weight: float | None = None) -> dict:
    """
    Chạy 1 cấu hình huấn luyện, trả về dict {"clf", "metrics", "note"}.

    use_smote=True: áp dụng SMOTE lên (X_train, y_train) TRƯỚC KHI fit.
    force_scale_weight: nếu truyền vào (dùng cho cấu hình "smote_only" để nó
    thật sự tách biệt với "smote_plus_scale_pos_weight"), bỏ qua việc tự tính
    scale_pos_weight từ dữ liệu đã oversample.
    """
    X_fit, y_fit = X_train, y_train

    if use_smote:
        if not SMOTE_AVAILABLE:
            note = "SMOTE không khả dụng (chưa cài imblearn) -- bỏ qua cấu hình này."
            print(f"[{name}] {note}")
            return {"clf": None, "metrics": None, "note": note}
        smote = SMOTE(random_state=42)
        X_fit, y_fit = smote.fit_resample(X_train, y_train)

    if force_scale_weight is not None:
        scale_weight = force_scale_weight
    else:
        num_neg = (y_fit == 0).sum()
        num_pos = (y_fit == 1).sum()
        scale_weight = num_neg / num_pos if num_pos > 0 else 1.0

    clf = _make_classifier(scale_weight)
    clf.fit(X_fit, y_fit)
    metrics = _evaluate(clf, X_test, y_test)

    print(f"[{name}] train={len(X_fit)} (illicit={(y_fit == 1).sum()}, licit={(y_fit == 0).sum()}) "
          f"scale_pos_weight={scale_weight:.2f} -> {metrics}")

    return {"clf": clf, "metrics": metrics, "note": None}


def train_model():
    if not ETHEREUM_FRAUD_CLEAN.exists():
        print(
            f"LỖI: Không tìm thấy {ETHEREUM_FRAUD_CLEAN}. "
            "Vui lòng chạy scripts/01_prepare_ethereum_fraud_dataset.py trước."
        )
        return

    print(f"Đang load dataset Ethereum Fraud đã clean: {ETHEREUM_FRAUD_CLEAN} ...")
    df = _load_clean_dataset(ETHEREUM_FRAUD_CLEAN)

    print("\n===== CLASS DISTRIBUTION (toàn bộ dataset clean) =====")
    print(df["FLAG"].value_counts())

    # --- Tách train/test STRATIFIED (thay cho 2 file Elliptic tách sẵn) ---
    X_train, y_train, X_test, y_test = _split_train_test(df)

    print(f"\n===== TRAIN LABELS =====")
    print(y_train.value_counts())
    print(f"\n===== TEST LABELS =====")
    print(y_test.value_counts())

    # Cảnh báo sớm nếu 1 trong 2 tập rỗng -- tránh crash average_precision_score.
    if len(X_train) == 0:
        print("LỖI: Tập train rỗng sau khi tách. Dừng lại.")
        return
    if len(X_test) == 0:
        print("LỖI: Tập test rỗng sau khi tách. Dừng lại.")
        return

    # Lưu test split ra file dùng chung cho tests/evaluate_model.py +
    # agents/calibrate_classifier_threshold.py (KHÔNG ghi đè file raw).
    test_df = X_test.copy()
    test_df["FLAG"] = y_test
    ETHEREUM_FRAUD_TEST.parent.mkdir(parents=True, exist_ok=True)
    test_df.to_csv(ETHEREUM_FRAUD_TEST, index=False, encoding="utf-8")
    print(f"\nĐã lưu test split ({len(test_df)} dòng) tại {ETHEREUM_FRAUD_TEST}")

    # --- [V2-1] Thử nghiệm cả 3 cấu hình, ghi lại số đo THẬT của từng cái ---
    print("\n=== Đang huấn luyện & so sánh 3 cấu hình xử lý mất cân bằng nhãn (V2-1) ===")
    results = {
        # (1) Baseline V1: chỉ scale_pos_weight, không SMOTE.
        "scale_pos_weight_only": _run_config(
            "scale_pos_weight_only", X_train, y_train, X_test, y_test, use_smote=False
        ),
        # (2) Chỉ SMOTE: sau khi SMOTE cân bằng 2 lớp, ép scale_weight=1.0 để
        #     cấu hình này KHÔNG lẫn thêm hiệu ứng của scale_pos_weight.
        "smote_only": _run_config(
            "smote_only", X_train, y_train, X_test, y_test,
            use_smote=True, force_scale_weight=1.0,
        ),
        # (3) Cả hai: SMOTE + scale_pos_weight tính lại trên dữ liệu đã oversample.
        "smote_plus_scale_pos_weight": _run_config(
            "smote_plus_scale_pos_weight", X_train, y_train, X_test, y_test, use_smote=True
        ),
    }

    valid_results = {k: v for k, v in results.items() if v["metrics"] is not None}
    if not valid_results:
        print("LỖI: Không có cấu hình nào chạy được (thiếu imblearn?). Dừng lại, không lưu model.")
        return

    # Chọn cấu hình tốt nhất theo AUC-PR (chỉ số chính SPEC.md đã dùng để tối
    # ưu XGBoost, phù hợp bài toán mất cân bằng nhãn), tie-break bằng Recall.
    best_name = max(
        valid_results,
        key=lambda k: (valid_results[k]["metrics"]["auc_pr"], valid_results[k]["metrics"]["recall"]),
    )
    best_clf = valid_results[best_name]["clf"]

    print(f"\n=== Cấu hình tốt nhất theo AUC-PR: {best_name} -> {valid_results[best_name]['metrics']} ===")
    baseline_metrics = results["scale_pos_weight_only"]["metrics"]
    if best_name != "scale_pos_weight_only" and baseline_metrics is not None:
        delta_aucpr = valid_results[best_name]["metrics"]["auc_pr"] - baseline_metrics["auc_pr"]
        if delta_aucpr <= 0:
            print(
                "LƯU Ý: cấu hình được chọn không thật sự vượt AUC-PR baseline "
                f"(delta={delta_aucpr:+.4f}) -- xem lại tests/model_comparison_v2.json "
                "khi viết báo cáo, không chỉ chọn số đẹp."
            )

    # Lưu model tốt nhất
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(best_clf, MODEL_OUT)
    print(f"Hoàn tất! Đã lưu model ({best_name}) tại {MODEL_OUT}")

    # Ghi số liệu so sánh THẬT ra file JSON cho tests/evaluate_model.py / báo cáo
    COMPARISON_OUT.parent.mkdir(parents=True, exist_ok=True)
    comparison_payload = {
        "selected_config": best_name,
        "configs": {k: v["metrics"] for k, v in results.items()},
        "notes": {k: v["note"] for k, v in results.items() if v["note"]},
    }
    with open(COMPARISON_OUT, "w", encoding="utf-8") as f:
        json.dump(comparison_payload, f, ensure_ascii=False, indent=2)
    print(f"Đã ghi số liệu so sánh 3 cấu hình tại {COMPARISON_OUT}")


if __name__ == "__main__":
    train_model()