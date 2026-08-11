"""
scripts/02_fetch_etherscan_sample.py -- Lấy dữ liệu ví mẫu từ nhiều chain qua
Etherscan API V2, lưu ra data/raw/etherscan/sample_txs.json.

Refactor 2026-08-08: toàn bộ logic fetch dời sang scripts/etherscan_fetcher.py
(module dùng chung với api/main.py production path — bắt buộc có wallet_record
đúng schema, xem agents/transaction_classifier.py FIX 2026-08-08).
"""
import json
import os

from dotenv import load_dotenv

from scripts.etherscan_fetcher import fetch_wallet_record

load_dotenv()


def fetch_etherscan_data(address, max_records=1000, page_size=100):
    """Giữ tên hàm tương thích với phiên bản cũ (có thể có caller khác import)."""
    wallet_record = fetch_wallet_record(address, max_records=max_records, page_size=page_size)

    os.makedirs(os.path.dirname("data/raw/etherscan/sample_txs.json"), exist_ok=True)
    output_file = "data/raw/etherscan/sample_txs.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(wallet_record, f, indent=4)

    total_txs = sum(len(v) for v in wallet_record["chains"].values())
    total_token_txs = sum(len(v) for v in wallet_record["token_transfers"].values())
    print(
        f"[✓] Đã lưu toàn bộ dữ liệu ({total_txs} tx thường + {total_token_txs} tx ERC20, "
        f"{len(wallet_record['chains'])} chain) tại: {output_file}"
    )


if __name__ == "__main__":
    address = "0x28C6c06298d514Db089934071355E5743bf21d60"
    fetch_etherscan_data(address, max_records=1000, page_size=100)