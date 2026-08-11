#!/usr/bin/env python3
"""
scripts/01_prepare_ethereum_fraud_dataset.py

Làm sạch dataset gốc Ethereum Fraud Detection (Farrugia et al., "Complete.csv")
thành một dataset TRAINING SẠCH, chỉ chứa các feature mà production
`feature_builder.py` (build_wallet_features + build_erc20_features) thực sự có
thể tính ra từ dữ liệu Etherscan (txlist + tokentx) lấy qua API.

Script này KHÔNG train model, KHÔNG split train/test, KHÔNG scale, KHÔNG
undersample/oversample. Nó chỉ tạo dataset sạch để người dùng tự train XGBoost.

Cách map reproducibility (mục PRODUCTION_FEATURE_MAP bên dưới) được xây dựng
trực tiếp từ danh sách key mà `build_wallet_features()` và
`build_erc20_features()` trong feature_builder.py trả về, cộng với các giới hạn
đã ghi rõ trong docstring đầu file đó (heuristic "to contract", không có
internal tx, không cộng gộp giá trị ERC20 theo USD, không tính được
"ERC20 total ether sent contract", loại bỏ "most sent/received token type" vì
là categorical).

Outputs:
- data/processed/ethereum_fraud_training_clean.csv
- data/processed/feature_schema.json
- data/processed/feature_mapping.csv
- data/processed/cleaning_report.json
- data/processed/dataset_summary.txt

Usage:
    python scripts/01_prepare_ethereum_fraud_dataset.py \
        --input data/raw/ethereum_fraud/Complete.csv \
        --output data/processed/ethereum_fraud_training_clean.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# PRODUCTION FEATURE MAP
#
# dataset_column -> (production_feature_name, status, reason)
#
# status:
#   KEEP            production tạo được feature này (đúng key trong
#                   feature_builder.py), giữ lại, đổi sang tên chuẩn dataset.
#   KEEP_CAVEAT     production tạo được nhưng có giới hạn/heuristic đã ghi rõ
#                   trong feature_builder.py -- vẫn giữ vì đây là cách production
#                   thật sự tính ra con số này, nhưng cần biết giới hạn khi diễn
#                   giải model.
#   DROP            production KHÔNG tạo được feature này (không có key tương
#                   ứng trong build_wallet_features/build_erc20_features, hoặc
#                   bị loại rõ ràng theo docstring feature_builder.py).
#
# Cột không có trong dict này (nếu phát sinh do schema đổi) sẽ tự động được
# đánh dấu REVIEW_REQUIRED bởi build_feature_mapping(), KHÔNG tự ý đưa vào KEEP.
# -----------------------------------------------------------------------------
PRODUCTION_FEATURE_MAP: Dict[str, tuple[str, str, str]] = {
    # --- nhóm ETH thường (build_wallet_features) ---
    "Avg_min_between_sent_tnx": ("Avg min between sent tnx", "KEEP",
        "Tính trực tiếp từ timestamp các giao dịch sent trong txlist."),
    "Avg_min_between_received_tnx": ("Avg min between received tnx", "KEEP",
        "Tính trực tiếp từ timestamp các giao dịch received trong txlist."),
    "Time_Diff_between_first_and_last_(Mins)": ("Time Diff between first and last (Mins)", "KEEP",
        "Tính từ timestamp giao dịch đầu/cuối trong txlist."),
    "Sent_tnx": ("Sent tnx", "KEEP", "Đếm trực tiếp số giao dịch sent trong txlist."),
    "Received_Tnx": ("Received Tnx", "KEEP", "Đếm trực tiếp số giao dịch received trong txlist."),
    "Number_of_Created_Contracts": ("Number of Created Contracts", "KEEP",
        "Đếm giao dịch sent có contractAddress khác rỗng trong txlist."),
    "Unique_Received_From_Addresses": ("Unique Received From Addresses", "KEEP",
        "Đếm địa chỉ 'from' duy nhất trong các giao dịch received."),
    "Unique_Sent_To_Addresses": ("Unique Sent To Addresses", "KEEP",
        "Đếm địa chỉ 'to' duy nhất trong các giao dịch sent."),
    "min_value_received": ("min value received", "KEEP", "min() giá trị Ether nhận, quy đổi từ wei."),
    "max_value_received": ("max value received", "KEEP", "max() giá trị Ether nhận, quy đổi từ wei."),
    "avg_val_received": ("avg val received", "KEEP", "mean() giá trị Ether nhận, quy đổi từ wei."),
    "min_val_sent": ("min val sent", "KEEP", "min() giá trị Ether gửi, quy đổi từ wei."),
    "max_val_sent": ("max val sent", "KEEP", "max() giá trị Ether gửi, quy đổi từ wei."),
    "avg_val_sent": ("avg val sent", "KEEP", "mean() giá trị Ether gửi, quy đổi từ wei."),
    "min_value_sent_to_contract": ("min value sent to contract", "KEEP_CAVEAT",
        "Production suy luận 'to contract' bằng heuristic input != '0x' "
        "(xem giới hạn (1) trong feature_builder.py) -- không chính xác 100%."),
    "max_val_sent_to_contract": ("max val sent to contract", "KEEP_CAVEAT",
        "Cùng heuristic 'to contract' như trên -- xem giới hạn (1) feature_builder.py."),
    "avg_value_sent_to_contract": ("avg value sent to contract", "KEEP_CAVEAT",
        "Cùng heuristic 'to contract' như trên -- xem giới hạn (1) feature_builder.py."),
    "total_transactions_(including_tnx_to_create_contract)": (
        "total transactions (including tnx to create contract)", "KEEP",
        "len(txs) thành công (isError == '0') trong txlist."),
    "total_Ether_sent": ("total Ether sent", "KEEP", "sum() giá trị Ether gửi."),
    "total_ether_received": ("total ether received", "KEEP", "sum() giá trị Ether nhận."),
    "total_ether_sent_contracts": ("total ether sent contracts", "KEEP_CAVEAT",
        "Cùng heuristic 'to contract' như trên -- xem giới hạn (1) feature_builder.py."),
    "total_ether_balance": ("total ether balance (APPROX, no internal tx)", "KEEP_CAVEAT",
        "Production KHÔNG tính internal transactions (xem giới hạn (2) "
        "feature_builder.py) -- với ví luân chuyển tiền qua contract, số này "
        "có thể lệch đáng kể so với balance thật."),

    # --- nhóm ERC20 (build_erc20_features) ---
    "Total_ERC20_tnxs": ("Total ERC20 tnxs", "KEEP", "len(token_transfers) từ tokentx."),
    "ERC20_total_Ether_received": ("ERC20 total Ether received", "KEEP_CAVEAT",
        "Production cộng gộp số lượng token đã quy đổi decimal của MỌI loại "
        "token khác nhau vào chung 1 con số, không quy đổi USD "
        "(xem giới hạn (3) feature_builder.py) -- giữ để khớp semantics dataset gốc."),
    "ERC20_total_ether_sent": ("ERC20 total ether sent", "KEEP_CAVEAT",
        "Cùng giới hạn cộng gộp token như trên -- xem giới hạn (3) feature_builder.py."),
    "ERC20_total_Ether_sent_contract": (None, "DROP",
        "Production không tính được: endpoint tokentx không có field 'input' "
        "để suy luận 'to' có phải contract hay không (giới hạn (4) feature_builder.py)."),
    "ERC20_uniq_sent_addr": ("ERC20 uniq sent addr", "KEEP",
        "Đếm địa chỉ 'to' duy nhất trong các ERC20 transfer gửi đi."),
    "ERC20_uniq_rec_addr": ("ERC20 uniq rec addr", "KEEP",
        "Đếm địa chỉ 'from' duy nhất trong các ERC20 transfer nhận về."),
    "ERC20_uniq_sent_addr.1": (None, "DROP",
        "Cột trùng tên với 'ERC20_uniq_sent_addr' trong file gốc (bug dữ liệu "
        "nguồn, pandas tự đổi tên thành '.1' khi đọc). Giá trị chỉ gồm {0,1}, "
        "không khớp ý nghĩa 'unique address count' và không có key production "
        "tương ứng nào tái tạo được nó -- không đủ tin cậy để giữ."),
    "ERC20_uniq_rec_contract_addr": (None, "DROP",
        "Không có key tương ứng trong build_erc20_features(); trong dữ liệu "
        "thực cột này trùng giá trị hệt ERC20_uniq_rec_token_name -- nhiều khả "
        "năng là cột bị gán nhãn sai ở nguồn, không tái tạo được ở production."),
    "ERC20_avg_time_between_sent_tnx": ("ERC20 avg time between sent tnx", "KEEP",
        "Tính từ timestamp các ERC20 transfer gửi đi."),
    "ERC20_avg_time_between_rec_tnx": ("ERC20 avg time between rec tnx", "KEEP",
        "Tính từ timestamp các ERC20 transfer nhận về."),
    "ERC20_avg_time_between_rec_2_tnx": (None, "DROP",
        "Không có key tương ứng trong build_erc20_features(); production chỉ "
        "tính 1 phiên bản 'avg time between rec tnx', không phân biệt '2nd'."),
    "ERC20_avg_time_between_contract_tnx": (None, "DROP",
        "Không có key tương ứng -- phụ thuộc phân loại 'contract' cho ERC20 "
        "transfer, thứ mà production không tính được (giới hạn (4))."),
    "ERC20_min_val_rec": ("ERC20 min val rec", "KEEP_CAVEAT", "Xem giới hạn (3) feature_builder.py."),
    "ERC20_max_val_rec": ("ERC20 max val rec", "KEEP_CAVEAT", "Xem giới hạn (3) feature_builder.py."),
    "ERC20_avg_val_rec": ("ERC20 avg val rec", "KEEP_CAVEAT", "Xem giới hạn (3) feature_builder.py."),
    "ERC20_min_val_sent": ("ERC20 min val sent", "KEEP_CAVEAT", "Xem giới hạn (3) feature_builder.py."),
    "ERC20_max_val_sent": ("ERC20 max val sent", "KEEP_CAVEAT", "Xem giới hạn (3) feature_builder.py."),
    "ERC20_avg_val_sent": ("ERC20 avg val sent", "KEEP_CAVEAT", "Xem giới hạn (3) feature_builder.py."),
    "ERC20_min_val_sent_contract": (None, "DROP",
        "Phụ thuộc phân loại 'to contract' cho ERC20 transfer -- không tính "
        "được (giới hạn (4) feature_builder.py)."),
    "ERC20_max_val_sent_contract": (None, "DROP",
        "Phụ thuộc phân loại 'to contract' cho ERC20 transfer -- không tính "
        "được (giới hạn (4) feature_builder.py)."),
    "ERC20_avg_val_sent_contract": (None, "DROP",
        "Phụ thuộc phân loại 'to contract' cho ERC20 transfer -- không tính "
        "được (giới hạn (4) feature_builder.py)."),
    "ERC20_uniq_sent_token_name": ("ERC20 uniq sent token name", "KEEP",
        "Đây là SỐ LƯỢNG loại token khác nhau đã gửi (len(set(tokenName))), "
        "là số nguyên -- production tính được, khác với "
        "ERC20_most_sent_token_type (categorical)."),
    "ERC20_uniq_rec_token_name": ("ERC20 uniq rec token name", "KEEP",
        "Số lượng loại token khác nhau đã nhận (len(set(tokenName)))."),
    "ERC20_most_sent_token_type": (None, "DROP",
        "Dữ liệu categorical (tên token phổ biến nhất) -- production cố ý "
        "không đưa vào feature dict dạng số (giới hạn (5) feature_builder.py); "
        "cần bước encode riêng nếu muốn dùng, không thuộc phạm vi script này."),
    "ERC20_most_rec_token_type": (None, "DROP",
        "Cùng lý do như ERC20_most_sent_token_type -- categorical, cố ý loại "
        "khỏi feature dict production (giới hạn (5) feature_builder.py)."),

    # --- cột non-reproducible / metadata ---
    "Index": (None, "DROP", "Số thứ tự dòng trong file gốc, không phải feature ví, không reproducible."),
}

# Các tên cột nếu xuất hiện trong dataset là dấu hiệu leakage đã biết (phụ
# thuộc thời điểm crawl dữ liệu, hoặc tiết lộ trực tiếp nhãn). Kiểm tra theo
# pattern vì tên cột thật có thể khác chút so với đây.
KNOWN_LEAKAGE_PATTERNS = [
    "confirmation",   # phụ thuộc thời điểm query API, không cố định theo ví
    "block_number",   # có thể rò rỉ thời điểm crawl nếu dùng làm feature thô
    "blocknumber",
    "query_time",
    "crawl_time",
    "fetched_at",
    "label",          # bất kỳ cột nào chứa "label" ngoài FLAG đáng nghi ngờ
]


def normalize_address(addr: Any) -> str:
    if not isinstance(addr, str):
        return ""
    return addr.strip().lower()


class EthereumFraudDatasetCleaner:
    def __init__(self, input_path: Path, output_path: Path):
        self.input_path = input_path
        self.output_path = output_path
        self.df_raw: Optional[pd.DataFrame] = None
        self.df_clean: Optional[pd.DataFrame] = None
        self.report: Dict[str, Any] = {"warnings": []}
        self.feature_mapping: List[Dict[str, str]] = []
        self.feature_schema: Dict[str, Any] = {}
        self.dropped_columns: List[str] = []
        self.review_required_columns: List[str] = []

    # ------------------------------------------------------------------ #
    def inspect_and_load(self) -> None:
        logger.info(f"Reading dataset from {self.input_path}")
        self.df_raw = pd.read_csv(self.input_path, low_memory=False)

        self.report["original_rows"] = len(self.df_raw)
        self.report["original_columns"] = len(self.df_raw.columns)
        self.report["column_names"] = list(self.df_raw.columns)
        self.report["dtypes"] = self.df_raw.dtypes.astype(str).to_dict()
        self.report["missing_counts"] = self.df_raw.isnull().sum().to_dict()

        if "Address" not in self.df_raw.columns or "FLAG" not in self.df_raw.columns:
            raise ValueError("Dataset thiếu cột bắt buộc 'Address' hoặc 'FLAG'.")

        self.report["unique_addresses_raw"] = self.df_raw["Address"].nunique()
        self.report["flag_distribution_raw"] = self.df_raw["FLAG"].value_counts().to_dict()

        # pandas tự đổi tên cột trùng thành 'name.1', '.2', ... ngay khi đọc CSV
        # -- kiểm tra dấu hiệu này thay vì tìm tên trùng y hệt (sẽ không bao
        # giờ tìm thấy vì pandas đã đổi tên trước khi code chạy tới).
        mangled = [c for c in self.df_raw.columns if "." in c and c.rsplit(".", 1)[-1].isdigit()]
        if mangled:
            logger.warning(f"Phát hiện cột bị pandas tự đổi tên do trùng tên gốc: {mangled}")
            self.report["mangled_duplicate_columns"] = mangled
            self.report["warnings"].append(
                f"Cột trùng tên ở file gốc bị pandas đổi tên tự động: {mangled}. "
                "Các cột này được xử lý riêng trong PRODUCTION_FEATURE_MAP."
            )

        logger.info(
            f"Loaded {self.report['original_rows']} rows, "
            f"{self.report['original_columns']} columns."
        )
        logger.info(f"FLAG distribution: {self.report['flag_distribution_raw']}")

    # ------------------------------------------------------------------ #
    def normalize_addresses(self) -> None:
        self.df_raw["Address"] = self.df_raw["Address"].apply(normalize_address)
        logger.info("Addresses normalised (lowercase, stripped).")

    # ------------------------------------------------------------------ #
    def handle_duplicate_addresses(self) -> None:
        groups = self.df_raw.groupby("Address")
        conflicting: List[str] = []
        duplicate_same: List[str] = []

        for addr, group in groups:
            if len(group) == 1:
                continue
            flags = group["FLAG"].unique()
            if len(flags) == 1:
                duplicate_same.append(addr)
            else:
                conflicting.append(addr)

        if conflicting:
            logger.warning(
                f"Found {len(conflicting)} addresses with conflicting FLAGs. "
                "Dropping all records for these addresses."
            )
            self.df_raw = self.df_raw[~self.df_raw["Address"].isin(conflicting)]
            self.report["removed_conflicting_addresses"] = conflicting
            self.report["removed_conflicting_addresses_count"] = len(conflicting)

        rows_before = len(self.df_raw)
        self.df_raw = self.df_raw.drop_duplicates(subset=["Address"], keep="first")
        rows_after = len(self.df_raw)
        self.report["removed_duplicates_count"] = rows_before - rows_after
        self.report["duplicate_addresses_same_flag"] = duplicate_same

        logger.info(
            f"Kept one record per address. Removed {rows_before - rows_after} "
            "exact-duplicate-address rows (same FLAG)."
        )
        self.report["unique_addresses_after_dedup"] = self.df_raw["Address"].nunique()

    # ------------------------------------------------------------------ #
    def apply_production_feature_map(self) -> None:
        """
        Áp dụng PRODUCTION_FEATURE_MAP: chỉ giữ lại cột mà production
        feature_builder.py thực sự tái tạo được. Cột nào không có trong map
        (schema lạ, chưa từng thấy) -> REVIEW_REQUIRED, KHÔNG tự động giữ.
        """
        rename_map: Dict[str, str] = {}
        keep_cols: List[str] = []

        for col in self.df_raw.columns:
            if col in ("Address", "FLAG"):
                keep_cols.append(col)
                continue

            if col not in PRODUCTION_FEATURE_MAP:
                # Cột lạ so với schema đã biết -- không tự ý đưa vào dataset.
                self.review_required_columns.append(col)
                logger.warning(
                    f"Cột '{col}' không có trong PRODUCTION_FEATURE_MAP -> "
                    "đánh dấu REVIEW_REQUIRED, loại khỏi dataset cho tới khi "
                    "được xác nhận thủ công."
                )
                continue

            prod_name, status, reason = PRODUCTION_FEATURE_MAP[col]
            if status == "DROP":
                self.dropped_columns.append(col)
                continue

            # KEEP hoặc KEEP_CAVEAT -> giữ lại, đổi tên về đúng schema dataset
            # gốc (giữ tên cột hiện tại, không đổi -- rename_map chỉ dùng nếu
            # muốn chuẩn hoá thêm sau này).
            keep_cols.append(col)

        self.df_raw = self.df_raw[keep_cols].copy()
        self.report["removed_columns"] = self.dropped_columns
        self.report["review_required_columns"] = self.review_required_columns
        logger.info(
            f"Production reproducibility filter: giữ {len(keep_cols)} cột, "
            f"loại {len(self.dropped_columns)} cột (không reproducible), "
            f"{len(self.review_required_columns)} cột REVIEW_REQUIRED."
        )

    # ------------------------------------------------------------------ #
    def check_leakage(self) -> None:
        """Kiểm tra tên cột còn lại theo các pattern leakage đã biết."""
        suspects = []
        for col in self.df_raw.columns:
            if col in ("Address", "FLAG"):
                continue
            lower = col.lower()
            for pattern in KNOWN_LEAKAGE_PATTERNS:
                if pattern in lower:
                    suspects.append(col)
                    break

        self.report["leakage_check_suspect_columns"] = suspects
        if suspects:
            logger.warning(f"Cột nghi ngờ leakage theo tên: {suspects}")
            self.report["warnings"].append(
                f"Cột nghi ngờ leakage (theo tên, cần review thủ công): {suspects}"
            )
        else:
            logger.info("Không phát hiện cột nào khớp pattern leakage đã biết.")

    # ------------------------------------------------------------------ #
    def clean_numeric_features(self) -> None:
        feature_cols = [c for c in self.df_raw.columns if c not in ("Address", "FLAG")]
        non_numeric_after_coerce: List[str] = []

        for col in feature_cols:
            self.df_raw[col] = self.df_raw[col].replace("", np.nan)
            converted = pd.to_numeric(self.df_raw[col], errors="coerce")
            if converted.isna().all() and not self.df_raw[col].isna().all():
                # Cột không convert được sang numeric (còn giá trị non-null nhưng
                # coerce ra toàn NaN) -- đây là categorical còn sót lại, không
                # thuộc phạm vi KEEP của production_feature_map (lẽ ra đã bị
                # DROP ở bước trước). Ghi nhận để validate raise ở bước sau,
                # KHÔNG âm thầm bỏ qua.
                non_numeric_after_coerce.append(col)
                continue
            self.df_raw[col] = converted.replace([np.inf, -np.inf], np.nan)

        self.report["non_numeric_after_coercion"] = non_numeric_after_coerce
        logger.info("Numeric features cleaned (inf -> NaN, strings converted where possible).")

    # ------------------------------------------------------------------ #
    def impute_erc20_nan(self) -> None:
        """
        Impute NaN = 0.0 CHỈ cho các cột ERC20 numeric mà PRODUCTION_FEATURE_MAP
        đánh dấu KEEP/KEEP_CAVEAT (tức production đã xác nhận convention
        'không có ERC20 tx' = 0.0, khớp _empty_erc20_feature_set() trong
        feature_builder.py). Không zero-fill máy móc toàn bộ dataset.
        """
        imputed: List[str] = []
        for col, (prod_name, status, _reason) in PRODUCTION_FEATURE_MAP.items():
            if col not in self.df_raw.columns:
                continue
            if status not in ("KEEP", "KEEP_CAVEAT"):
                continue
            if not col.startswith("ERC20") and not col.startswith("Total_ERC20"):
                continue
            if pd.api.types.is_numeric_dtype(self.df_raw[col]):
                n_missing = int(self.df_raw[col].isna().sum())
                if n_missing:
                    self.df_raw[col] = self.df_raw[col].fillna(0.0)
                    imputed.append(col)

        self.report["imputed_columns"] = imputed
        logger.info(f"ERC20 NaN imputed (0.0, khớp _empty_erc20_feature_set()): {imputed}")

    # ------------------------------------------------------------------ #
    def validate_and_clean_final(self) -> None:
        """Validate nghiêm ngặt -- raise ngay nếu có vấn đề, không silently pass."""
        self.df_raw["FLAG"] = self.df_raw["FLAG"].astype(int)

        errors: List[str] = []

        if self.df_raw["Address"].duplicated().any():
            errors.append("Vẫn còn địa chỉ trùng lặp sau khi dedup.")

        if not set(self.df_raw["FLAG"].unique()).issubset({0, 1}):
            errors.append("Cột FLAG chứa giá trị khác 0/1.")

        feature_cols = [c for c in self.df_raw.columns if c not in ("Address", "FLAG")]

        non_numeric = [c for c in feature_cols if not pd.api.types.is_numeric_dtype(self.df_raw[c])]
        if non_numeric:
            errors.append(f"Các cột feature sau vẫn không phải numeric: {non_numeric}")

        remaining_nan: List[str] = []
        remaining_inf: List[str] = []
        for col in feature_cols:
            if not pd.api.types.is_numeric_dtype(self.df_raw[col]):
                continue
            if self.df_raw[col].isna().any():
                remaining_nan.append(col)
            if np.isinf(self.df_raw[col]).any():
                remaining_inf.append(col)

        if remaining_nan:
            errors.append(f"Các cột feature sau vẫn còn NaN sau impute: {remaining_nan}")
        if remaining_inf:
            errors.append(f"Các cột feature sau vẫn còn inf: {remaining_inf}")

        dup_names = self.df_raw.columns[self.df_raw.columns.duplicated()].tolist()
        if dup_names:
            errors.append(f"Vẫn còn tên cột trùng lặp: {dup_names}")

        self.report["remaining_nan"] = remaining_nan
        self.report["remaining_inf"] = remaining_inf

        constant_features = [
            c for c in feature_cols if self.df_raw[c].nunique(dropna=False) == 1
        ]
        self.report["constant_features"] = constant_features
        if constant_features:
            self.report["warnings"].append(
                f"Constant feature (giữ lại nhưng không mang thông tin phân biệt "
                f"FLAG): {constant_features}"
            )
            logger.warning(f"Constant features (giữ lại, chỉ cảnh báo): {constant_features}")

        if errors:
            for e in errors:
                logger.error(e)
            raise ValueError("Validation thất bại:\n- " + "\n- ".join(errors))

        self.df_clean = self.df_raw.copy()
        logger.info("Final validation passed.")

    # ------------------------------------------------------------------ #
    def generate_reports(self) -> None:
        self.report["final_rows"] = len(self.df_clean)
        self.report["final_columns"] = len(self.df_clean.columns)
        self.report["final_features"] = [
            c for c in self.df_clean.columns if c not in ("Address", "FLAG")
        ]
        self.report["final_feature_count"] = len(self.report["final_features"])
        self.report["class_distribution"] = self.df_clean["FLAG"].value_counts().to_dict()
        self.report["final_unique_addresses"] = self.df_clean["Address"].nunique()

        for col in self.df_raw.columns.tolist() if False else self.report["column_names"]:
            pass  # placeholder removed below (kept structure simple)

        # feature_mapping.csv: tất cả cột GỐC (kể cả những cột đã bị drop),
        # theo đúng format dataset_column, production_feature, status, reason
        for col in self.report["column_names"]:
            if col in ("Address", "FLAG"):
                continue
            if col in PRODUCTION_FEATURE_MAP:
                prod_name, status, reason = PRODUCTION_FEATURE_MAP[col]
            elif col in self.review_required_columns:
                prod_name, status, reason = None, "REVIEW_REQUIRED", (
                    "Cột không khớp schema production đã biết -- cần xác nhận "
                    "thủ công trước khi đưa vào training."
                )
            else:
                prod_name, status, reason = None, "REVIEW_REQUIRED", "Chưa được phân loại."
            self.feature_mapping.append({
                "dataset_column": col,
                "production_feature": prod_name or "",
                "status": status,
                "reason": reason,
            })

        for i, col in enumerate(self.report["final_features"]):
            self.feature_schema[col] = {
                "order": i,
                "data_type": self.df_clean[col].dtype.name,
                "source_column": col,
                "production_feature_name": PRODUCTION_FEATURE_MAP.get(col, (col,))[0] or col,
            }

        logger.info("=== Cleaning Report ===")
        logger.info(f"Original rows: {self.report['original_rows']}")
        logger.info(f"Final rows: {self.report['final_rows']}")
        logger.info(f"Original columns: {self.report['original_columns']}")
        logger.info(f"Final features: {self.report['final_feature_count']}")
        logger.info(f"Class distribution: {self.report['class_distribution']}")
        logger.info("=======================")

    # ------------------------------------------------------------------ #
    def save_outputs(self) -> None:
        output_dir = self.output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        self.df_clean.to_csv(self.output_path, index=False, encoding="utf-8")
        logger.info(f"Saved cleaned dataset to {self.output_path}")

        report_path = output_dir / "cleaning_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(self.report, f, indent=2, default=str, ensure_ascii=False)
        logger.info(f"Saved cleaning report to {report_path}")

        mapping_path = output_dir / "feature_mapping.csv"
        pd.DataFrame(self.feature_mapping).to_csv(mapping_path, index=False, encoding="utf-8")
        logger.info(f"Saved feature mapping to {mapping_path}")

        schema_path = output_dir / "feature_schema.json"
        with open(schema_path, "w", encoding="utf-8") as f:
            json.dump(self.feature_schema, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved feature schema to {schema_path}")

        summary_path = output_dir / "dataset_summary.txt"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("Ethereum Fraud Detection Dataset Summary\n")
            f.write("========================================\n")
            f.write(f"Input file: {self.input_path}\n")
            f.write(f"Output file: {self.output_path}\n")
            f.write(f"Original rows: {self.report['original_rows']}\n")
            f.write(f"Final rows: {self.report['final_rows']}\n")
            f.write(f"Original columns: {self.report['original_columns']}\n")
            f.write(f"Final features: {self.report['final_feature_count']}\n")
            f.write(f"Class distribution: {self.report['class_distribution']}\n")
            f.write(f"Unique addresses: {self.report['final_unique_addresses']}\n")
            f.write(f"Removed rows (duplicates): {self.report.get('removed_duplicates_count', 0)}\n")
            f.write(
                "Removed rows (conflicting FLAG addresses): "
                f"{self.report.get('removed_conflicting_addresses_count', 0)}\n"
            )
            f.write(f"Removed columns (non-reproducible): {len(self.dropped_columns)}\n")
            f.write(f"Review-required columns: {len(self.review_required_columns)}\n")
            f.write("========================================\n")
        logger.info(f"Saved dataset summary to {summary_path}")

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.inspect_and_load()
        self.normalize_addresses()
        self.handle_duplicate_addresses()
        self.apply_production_feature_map()
        self.check_leakage()
        self.clean_numeric_features()
        self.impute_erc20_nan()
        self.validate_and_clean_final()
        self.generate_reports()
        self.save_outputs()
        logger.info("Cleaning complete.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Clean Ethereum fraud detection dataset for production compatibility."
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to raw Complete.csv file")
    parser.add_argument("--output", required=True, type=Path, help="Path to save cleaned dataset")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    cleaner = EthereumFraudDatasetCleaner(args.input, args.output)
    cleaner.run()


if __name__ == "__main__":
    main()