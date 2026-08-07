"""
agents/train_classifier.py
Huấn luyện XGBoost trên tập Elliptic.

[SỬA] Train/test được load từ 2 FILE RIÊNG đã tách sẵn theo time_step
(elliptic_clean.csv = time_step 1-34, elliptic_test.csv = time_step 35-49),
không tự chia trong code nữa -- tránh trường hợp file input chỉ chứa 1 phần
dải time_step (vd chỉ có 1-34) khiến việc filter `time_step > 34` ra tập
test rỗng (0 dòng) và làm crash average_precision_score() ở bước evaluate.

[V2-1 -- THAY_DOI_V2.md] Xử lý mất cân bằng nhãn: thử nghiệm CẢ 3 cấu hình
  (1) chỉ scale_pos_weight (baseline V1)
  (2) chỉ SMOTE (imblearn.over_sampling.SMOTE, CHỈ áp dụng trên tập train,
      sau khi đã tách temporal split -- tuyệt đối không SMOTE trước khi tách
      train/test vì sẽ rò rỉ dữ liệu tổng hợp từ tương lai vào quá khứ)
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
from sklearn.metrics import recall_score, f1_score, average_precision_score, confusion_matrix, precision_score, classification_report

try:
    from imblearn.over_sampling import SMOTE
    SMOTE_AVAILABLE = True
except ImportError:
    SMOTE_AVAILABLE = False

ELLIPTIC_TRAIN = Path("data/raw/elliptic/elliptic_clean.csv")   # time_step 1-34
ELLIPTIC_TEST = Path("data/raw/elliptic/elliptic_test.csv")     # time_step 35-49
MODEL_OUT = Path("models/xgboost_aml.pkl")
COMPARISON_OUT = Path("tests/model_comparison_v2.json")


def _load_and_clean(filepath: Path, binary: bool = False) -> pd.DataFrame:
    """
    Train:
        unknown / 1 / 2
        1 = illicit
        2 = licit

    Test:
        0 / 1
        1 = illicit
        0 = licit
    """

    df = pd.read_csv(filepath)

    df["class"] = df["class"].astype(str).str.strip()

    if binary:
        # File test đã là 0/1
        df["class"] = pd.to_numeric(df["class"], errors="coerce")

        before = len(df)

        df = df[df["class"].isin([0, 1])].copy()

        dropped = before - len(df)

        if dropped:
            print(
                f"[CẢNH BÁO] {filepath}: loại {dropped} dòng không thuộc nhãn 0/1"
            )

        df["class"] = df["class"].astype(int)

    else:
        # File train gốc Elliptic: unknown / 1 / 2
        before = len(df)

        df = df[df["class"] != "unknown"].copy()

        df["class"] = (
            pd.to_numeric(df["class"], errors="coerce")
            .map({
                1: 1,  # illicit
                2: 0   # licit
            })
        )

        df = df.dropna(subset=["class"])

        dropped = before - len(df)

        if dropped:
            print(
                f"[INFO] {filepath}: loại {dropped} dòng unknown hoặc nhãn không hợp lệ"
            )

        df["class"] = df["class"].astype(int)

    return df


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
    print(classification_report(y_test,y_pred,target_names=["Licit","Illicit"],digits=4))

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
    if not ELLIPTIC_TRAIN.exists():
        print(f"LỖI: Không tìm thấy {ELLIPTIC_TRAIN}. Vui lòng chạy scripts/03_load_elliptic.py trước.")
        return
    if not ELLIPTIC_TEST.exists():
        print(f"LỖI: Không tìm thấy {ELLIPTIC_TEST}. Cần file test đã tách sẵn (time_step 35-49).")
        return

    print("Đang load dữ liệu Elliptic (train: 1-34, test: 35-49, 2 file riêng)...")
    train_df = _load_and_clean(
        ELLIPTIC_TRAIN,
        binary=False
    )

    test_df = _load_and_clean(
        ELLIPTIC_TEST,
        binary=True
    )
    print("\n===== TRAIN LABELS =====")
    print(train_df["class"].value_counts())

    print("\n===== TEST LABELS =====")
    print(test_df["class"].value_counts())
    
    # Cảnh báo sớm nếu 1 trong 2 file rỗng sau khi lọc 'unknown' -- tránh lặp
    # lại lỗi cũ (test rỗng khiến average_precision_score crash ở bước sau).
    if len(train_df) == 0:
        print(f"LỖI: {ELLIPTIC_TRAIN} rỗng sau khi lọc nhãn 'unknown'. Dừng lại.")
        return
    if len(test_df) == 0:
        print(f"LỖI: {ELLIPTIC_TEST} rỗng sau khi lọc nhãn 'unknown'. Dừng lại.")
        return

    # SMOTE (V2-1) chỉ được áp dụng SAU bước load 2 file riêng này, chỉ trên
    # train_df, không bao giờ trên test_df -- 2 file đã tách theo time_step
    # từ trước (temporal split), không random split.

    # Tách X, y (loại bỏ các cột định danh và nhãn)
    drop_cols = ['txId', 'time_step', 'class']
    X_train = train_df.drop(columns=drop_cols)
    y_train = train_df['class']
    X_test = test_df.drop(columns=drop_cols)
    y_test = test_df['class']

    print(f"Kích thước tập Train: {len(X_train)} (Illicit: {(y_train == 1).sum()}, "
          f"Licit: {(y_train == 0).sum()})")
    print(f"Kích thước tập Test: {len(X_test)}")

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

    # KHÔNG ghi lại test_df ra đĩa nữa -- ELLIPTIC_TEST (elliptic_test.csv)
    # đã là file test có sẵn do bạn tự tách trước (time_step 35-49), ghi đè
    # lên nó ở đây là thừa và rủi ro (nếu code sau này đổi logic lọc, có thể
    # vô tình làm sai lệch chính file gốc bạn đang dùng làm chuẩn).
    # tests/evaluate_model.py nên đọc trực tiếp ELLIPTIC_TEST thay vì file
    # trung gian riêng.

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