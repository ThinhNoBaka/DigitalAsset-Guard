"""
scripts/etherscan_fetcher.py — Fetch dữ liệu on-chain của 1 ví qua Etherscan API V2,
trả về dict ĐÚNG schema wallet_record mà feature_builder / transaction_classifier
(FIX 2026-08-08) bắt buộc ở production path:

    {
        "address": str,
        "chains": {"ethereum": [txlist]},          # output endpoint txlist
        "token_transfers": {"ethereum": [tokentx]} # output endpoint tokentx
    }

Được dùng bởi:
  - api/main.py (production path) — gọi động theo wallet_from từng request.
  - scripts/02_fetch_etherscan_sample.py — fetch ví demo rồi lưu file.

KHÔNG chứa PII — dữ liệu on-chain công khai, hợp lệ để tồn tại trong state sau
Privacy Layer.
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.etherscan.io/v2/api"

# Chỉ dùng Ethereum. BNB (56), Base (8453), Optimism (10), Avalanche (43114) hiện
# đã bị Etherscan giới hạn chỉ cho gói trả phí. Polygon (137), Arbitrum (42161) đã
# bỏ theo yêu cầu -> thêm lại vào dict này nếu sau cần mở rộng.
CHAINS = {
    "ethereum": 1,
}


def fetch_chain_action(chain_name, chain_id, address, api_key, action, max_records=1000, page_size=100):
    """
    Lấy dữ liệu của 1 địa chỉ trên 1 chain qua action bất kỳ của module 'account',
    tự phân trang. Dùng chung cho:
      - action='txlist'  -> giao dịch ETH thường
      - action='tokentx' -> giao dịch chuyển ERC20
    """
    all_records = []
    page = 1

    while len(all_records) < max_records:
        params = {
            "chainid": chain_id,
            "module": "account",
            "action": action,
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": page,
            "offset": page_size,
            "sort": "asc",
            "apikey": api_key,
        }
        response = requests.get(BASE_URL, params=params)
        data = response.json()

        if data.get("status") != "1":
            msg = data.get("message", "")
            result = data.get("result", "")
            if "No transactions found" in str(result) or "No transactions found" in msg:
                pass  # hết dữ liệu, không phải lỗi
            else:
                print(f"    [LỖI] {chain_name} ({action}): {msg} - {result}")
            break

        batch = data.get("result", [])
        if not batch:
            break

        all_records.extend(batch)
        print(f"    [i] {chain_name} ({action}) - trang {page}: +{len(batch)} bản ghi (tổng: {len(all_records)})")

        if len(batch) < page_size:
            break

        page += 1
        time.sleep(0.25)  # tránh vượt rate limit (free tier ~5 req/s, dùng chung mọi chain)

    return all_records[:max_records]


def fetch_wallet_record(address: str, max_records: int = 100, page_size: int = 100) -> dict:
    """
    Fetch toàn bộ dữ liệu on-chain của 1 ví, trả về dict ĐÚNG schema wallet_record
    mà feature_builder / transaction_classifier bắt buộc ở production path.

    - Ví không có giao dịch (vd. địa chỉ giả "0xbadwallet123") -> trả về
      chains/token_transfers = [] (KHÔNG raise). feature_builder xử lý list rỗng
      -> feature toàn 0 -> classifier chạy bình thường (không crash pipeline).
    - Thiếu ETHERSCAN_API_KEY -> raise RuntimeError để caller biết rõ lý do.
    """
    api_key = os.getenv("ETHERSCAN_API_KEY")
    if not api_key:
        raise RuntimeError("Chưa cấu hình ETHERSCAN_API_KEY trong file .env")

    chain_name = "ethereum"
    chain_id = CHAINS[chain_name]

    txs = fetch_chain_action(
        chain_name, chain_id, address, api_key,
        "txlist", max_records, page_size,
    )
    time.sleep(0.3)

    token_txs = fetch_chain_action(
        chain_name, chain_id, address, api_key,
        "tokentx", max_records, page_size,
    )

    return {
        "address": address,
        "chains": {chain_name: txs},
        "token_transfers": {chain_name: token_txs},
    }


if __name__ == "__main__":
    import sys

    address = sys.argv[1] if len(sys.argv) > 1 else "0x28C6c06298d514Db089934071355E5743bf21d60"
    record = fetch_wallet_record(address, max_records=50, page_size=50)
    total_txs = sum(len(v) for v in record["chains"].values())
    total_token_txs = sum(len(v) for v in record["token_transfers"].values())
    print(f"[✓] wallet_record cho {address}: {total_txs} tx thường + {total_token_txs} tx ERC20.")