"""scripts/diagnose_threshold_0009.py — CHẨN ĐOÁN CHỈ ĐỌC, KHÔNG GHI/CHỈNH SỬA GÌ.

Chẩn đoán threshold = 0.0009 bất thường từ calibrate_classifier_threshold.py.
Script này CHỈ in thông tin — không sửa model, không sửa data, không sửa
agents/calibrate_classifier_threshold.py, không ghi bất kỳ file nào.
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from agents.calibrate_classifier_threshold import (
    _find_label_column,
    _score_transactions,
)

MODEL_PATH = Path("models/xgboost_aml.pkl")
TEST_CSV = Path("data/raw/elliptic/elliptic_test.csv")
TRAIN_CSV = Path("data/raw/elliptic/elliptic_clean.csv")

print("=" * 72)
print("BƯỚC 1: PHÂN PHỐI LABEL TRONG TEST SET")
print("=" * 72)
df_test = pd.read_csv(TEST_CSV)
label_col = _find_label_column(df_test)
print(f"Tổng số dòng: {len(df_test)}")
print(f"Cột nhãn được dùng (LABEL_COLUMN_CANDIDATES): '{label_col}'")
print("value_counts:")
print(df_test[label_col].value_counts())
print("value_counts(normalize=True):")
print(df_test[label_col].value_counts(normalize=True))
n_licit = int((df_test[label_col] == 0).sum())
n_illicit = int((df_test[label_col] == 1).sum())
print(f"label=0 (licit)  : {n_licit} dòng — {n_licit / len(df_test):.4f}")
print(f"label=1 (illicit): {n_illicit} dòng — {n_illicit / len(df_test):.4f}")

print()
print("=" * 72)
print("BƯỚC 3: FEATURE_NAMES TRONG MODEL vs CỘT TRONG TEST CSV")
print("=" * 72)
model = joblib.load(MODEL_PATH)
booster = model.get_booster()
feature_names = booster.feature_names
print(f"feature_names lúc train: {feature_names}")
print(f"Số cột feature_names: {len(feature_names) if feature_names else 'None'}")
print(f"model.n_features_in_: {model.n_features_in_}")
non_label_cols = [c for c in df_test.columns if c != label_col]
print(f"Cột trong test CSV (trừ label '{label_col}') — {len(non_label_cols)} cột:")
print(non_label_cols)
if feature_names:
    print(
        f"feature_names CÓ khớp tập cột test không (không xét thứ tự): "
        f"{set(feature_names).issubset(set(df_test.columns))}"
    )
    try:
        X_by_name = pd.DataFrame(df_test[feature_names])
        print(f"df[feature_names] shape: {X_by_name.shape} — index đúng theo model")
    except KeyError as e:
        print(f"df[feature_names] lỗi KeyError: {e}")
else:
    print("feature_names = None/[] -> code rơi vào fallback theo thứ tự cột (NGHI VẤN HÀNG ĐẦU).")

print()
print("=" * 72)
print("BƯỚC 2: PHÂN PHỐI Y_SCORES THEO TỪNG LỚP (dùng đúng _score_transactions)")
print("=" * 72)
y_true = df_test[label_col].astype(int).values
y_scores = _score_transactions(model, df_test, label_col)
scores_label_0 = y_scores[y_true == 0]
scores_label_1 = y_scores[y_true == 1]
print(f"y_scores shape: {y_scores.shape}")
print(
    f"Label=0 (licit)   — min/mean/median/max: {scores_label_0.min():.6f} / "
    f"{scores_label_0.mean():.6f} / {np.median(scores_label_0):.6f} / {scores_label_0.max():.6f}"
)
print(
    f"Label=1 (illicit) — min/mean/median/max: {scores_label_1.min():.6f} / "
    f"{scores_label_1.mean():.6f} / {np.median(scores_label_1):.6f} / {scores_label_1.max():.6f}"
)
print("Percentiles toàn bộ y_scores:", np.percentile(y_scores, [1, 5, 25, 50, 75, 95, 99]))
print(f"Số dòng label=0: {len(scores_label_0)}, label=1: {len(scores_label_1)}")

print()
print("=" * 72)
print("BƯỚC 4: ĐỐI CHIẾU QUY ƯỚC TRAIN vs CALIBRATE")
print("=" * 72)
print("Lúc train (agents/train_classifier.py):")
print("  - drop_cols = ['txId', 'time_step', 'class'] -> X_train KHÔNG chứa txId/time_step.")
print("  - Train: class 1=illicit->1, 2=licit->0, loại bỏ 'unknown' (binary=False).")
print("  - Test : class đã là 0/1 sẵn (binary=True, 1=illicit, 0=licit).")
print("Lúc calibrate (agents/calibrate_classifier_threshold.py):")
print(f"  - Tìm label qua LABEL_COLUMN_CANDIDATES -> tìm thấy '{label_col}'.")
print("  - feature_cols = [c for c in df.columns if c != label_col] -> CÓ THỂ chứa txId/time_step.")
has_txid = "txId" in non_label_cols
has_timestep = "time_step" in non_label_cols
print(
    f"  - feature_cols ({len(non_label_cols)} cột) {'CÓ' if has_txid else 'KHÔNG'} chứa 'txId', "
    f"{'CÓ' if has_timestep else 'KHÔNG'} chứa 'time_step'."
)
if feature_names and set(feature_names).issubset(set(df_test.columns)):
    print("  -> feature_names đầy đủ + khớp tên cột test => chọn df[feature_names] (an toàn, tự bỏ txId/time_step).")
else:
    print("  -> KHÔNG khớp => fallback dùng feature_cols có thể CHỨA CẢ txId/time_step -> NGHI VẤN HÀNG ĐẦU.")
print("  - Quy ước nhãn: test file 0=licit, 1=illicit. Nếu label_col là 'class' thì khớp train;")
print("    nếu tìm nhầm cột khác (vd 'target' có quy ước khác) sẽ lệch nhãn -> score random.")

print()
print("=" * 72)
print("BƯỚC 5: SANITY CHECK TRÊN CHÍNH TẬP TRAIN (elliptic_clean.csv)")
print("=" * 72)
if TRAIN_CSV.exists():
    df_train_raw = pd.read_csv(TRAIN_CSV)
    print(f"Tổng số dòng trong {TRAIN_CSV.name}: {len(df_train_raw)}")
    print("Phân phối 'class' thô (trước khi lọc):")
    print(df_train_raw["class"].value_counts(dropna=False))

    # Làm sạch GIỐNG HỆT _load_and_clean(binary=False) trong train_classifier.py
    # để có y_true đúng quy ước 1=illicit, 0=licit (chỉ để tách 2 lớp khi in thống kê).
    df_train = df_train_raw.copy()
    df_train["class"] = df_train["class"].astype(str).str.strip()
    before = len(df_train)
    df_train = df_train[df_train["class"] != "unknown"].copy()
    df_train["class"] = pd.to_numeric(df_train["class"], errors="coerce").map({1: 1, 2: 0})
    df_train = df_train.dropna(subset=["class"])
    df_train["class"] = df_train["class"].astype(int)
    n_dropped = before - len(df_train)
    print(
        f"Sau khi lọc 'unknown' + map (1->illicit, 2->licit): {len(df_train)} dòng "
        f"(illicit={(df_train['class'] == 1).sum()}, licit={(df_train['class'] == 0).sum()}, "
        f"đã loại {n_dropped} dòng unknown)."
    )

    y_train = df_train["class"].values
    y_scores_train = _score_transactions(model, df_train, "class")
    s0 = y_scores_train[y_train == 0]
    s1 = y_scores_train[y_train == 1]
    print(
        f"TRAIN — Label=0 (licit)   — min/mean/median/max: {s0.min():.6f} / "
        f"{s0.mean():.6f} / {np.median(s0):.6f} / {s0.max():.6f}"
    )
    print(
        f"TRAIN — Label=1 (illicit) — min/mean/median/max: {s1.min():.6f} / "
        f"{s1.mean():.6f} / {np.median(s1):.6f} / {s1.max():.6f}"
    )
    print("Percentiles toàn bộ y_scores TRAIN:", np.percentile(y_scores_train, [1, 5, 25, 50, 75, 95, 99]))
else:
    print(f"KHÔNG thấy {TRAIN_CSV} — bỏ qua sanity check train.")