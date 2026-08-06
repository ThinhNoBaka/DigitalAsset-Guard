"""
scripts/03_load_elliptic.py -- Đọc và gộp 3 file CSV của Elliptic Dataset.
"""
import pandas as pd
import os

def load_and_merge_elliptic():
    data_dir = "data/raw/elliptic"

    # Đường dẫn 3 file csv gốc + file output đã gộp
    features_path = os.path.join(data_dir, "elliptic_txs_features.csv")
    classes_path = os.path.join(data_dir, "elliptic_txs_classes.csv")
    edgelist_path = os.path.join(data_dir, "elliptic_txs_edgelist.csv")
    output_path = os.path.join(data_dir, "elliptic_clean.csv")

    if not all(os.path.exists(p) for p in [features_path, classes_path, edgelist_path]):
        print("[CẢNH BÁO] Thiếu file CSV gốc của Elliptic Dataset trong data/raw/elliptic/")
        return

    print("Đang đọc và gộp dữ liệu Elliptic...")
    try:
        df_features = pd.read_csv(features_path, header=None)
        df_classes = pd.read_csv(classes_path)
        df_edges = pd.read_csv(edgelist_path)

        # File features gốc không có header: cột 0 = txId, cột 1 = time_step,
        # các cột còn lại là đặc trưng (feature) của giao dịch.
        num_features = df_features.shape[1] - 2
        feature_cols = [f"feat_{i}" for i in range(num_features)]
        df_features.columns = ["txId", "time_step"] + feature_cols

        # Merge features và classes dựa trên ID giao dịch (txId)
        df_merged = df_features.merge(df_classes, on="txId", how="left")

        # Chuẩn hóa cột class: ép về string, coi mọi giá trị thiếu (NaN do merge
        # không khớp được, hoặc None/rỗng) là "unknown" luôn — để đồng nhất với
        # quy ước lọc ở agents/train_classifier.py (chỉ loại bỏ đúng chuỗi "unknown").
        # Nếu không chuẩn hóa bước này, NaN sẽ bị ép thành chuỗi "nan" và lọt qua
        # bộ lọc unknown, khiến train_classifier.py crash khi ép kiểu int sau đó.
        df_merged["class"] = df_merged["class"].astype(str).str.strip()
        df_merged.loc[df_merged["class"].isin(["nan", "None", ""]), "class"] = "unknown"

        # Cảnh báo nếu có txId không khớp được nhãn, để biết dữ liệu có vấn đề gì không
        n_unmatched = (df_merged["class"] == "unknown").sum()
        if n_unmatched > 0:
            print(f"[LƯU Ý] {n_unmatched} dòng có class='unknown' (bao gồm cả unmatched khi merge).")

        # Lưu ra file sạch để agents/train_classifier.py sử dụng
        df_merged.to_csv(output_path, index=False)

        print(f"[✓] Đã đọc thành công: {len(df_features)} dòng features, "
              f"{len(df_classes)} dòng classes, {len(df_edges)} cạnh (edges).")
        print(f"[✓] Đã gộp và lưu ra: {output_path} ({len(df_merged)} dòng)")

    except Exception as e:
        print(f"[LỖI] Khi xử lý file Elliptic: {e}")

if __name__ == "__main__":
    load_and_merge_elliptic()