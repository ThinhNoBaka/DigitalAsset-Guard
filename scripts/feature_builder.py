"""
Feature Builder — trích feature wallet-level từ dữ liệu giao dịch Etherscan thô.

BỐI CẢNH:
Dataset gốc (Farrugia et al., "Ethereum Fraud Detection") có 51 feature: ~21 feature
"cơ bản" tính từ danh sách normal transactions (endpoint `txlist`), và nhóm ERC20
tính từ endpoint `tokentx` — cả hai đều đã có trong output của
scripts/02_fetch_etherscan_sample.py (key "chains" và "token_transfers").

Module này build cả 2 nhóm, dùng chung được cho cả training (từ dataset gán nhãn)
lẫn inference (chấm điểm ví thật ở production), miễn là input cùng 1 schema.

GIỚI HẠN ĐÃ BIẾT (cần đọc trước khi dùng):
1. "to contract" (nhóm ETH thường) suy luận bằng heuristic: input != "0x" nghĩa là
   giao dịch có gọi hàm contract → khả năng cao `to` là smart contract. Suy luận rẻ,
   không tốn thêm API call, nhưng KHÔNG chính xác 100%. Muốn chính xác tuyệt đối cần
   gọi eth_getCode(to) riêng.
2. "total ether balance" tính từ (tổng nhận - tổng gửi) trong CHÍNH danh sách normal
   tx. KHÔNG bao gồm internal transactions (ETH di chuyển qua lệnh gọi contract) —
   với ví dùng nhiều contract để luân chuyển tiền, con số này có thể sai lệch đáng
   kể. Muốn chính xác cần thêm endpoint `txlistinternal` hoặc gọi thẳng balance API.
3. Nhóm ERC20: các feature "total/min/max/avg val sent/received" CỘNG GỘP số lượng
   token đã quy đổi theo decimal của TẤT CẢ loại token khác nhau lại với nhau (vd:
   1000 USDT + 50000000 SHIB → cộng thẳng thành 1 con số). Đây là cách làm y hệt
   dataset gốc Farrugia (không quy đổi ra USD), nên GIỮ ĐỂ khớp semantics lúc train,
   nhưng về bản chất tài chính đây là cộng các loại tiền khác nhau — không phản ánh
   đúng "giá trị" thực. Muốn chính xác hơn cần thêm bước quy đổi giá USD tại thời
   điểm giao dịch (cần nguồn giá riêng, không có trong Etherscan free tier).
4. "ERC20 total ether sent contract" của dataset gốc — KHÔNG tính được, vì endpoint
   `tokentx` không có field kiểu 'input' để suy luận `to` có phải contract hay
   không (khác với txlist thường). Bỏ qua feature này, không "chế" giá trị giả.
5. "ERC20 most sent/received token type" (tên loại token phổ biến nhất) là dữ liệu
   dạng chữ (categorical), không đưa vào dict feature dạng số ở đây — cần bước mã
   hoá riêng (one-hot/embedding) nếu muốn dùng, để tránh lẫn kiểu dữ liệu.
6. Chỉ tính trên transaction có isError == "0" (giao dịch thành công) đối với
   txlist — tokentx không có field này (Etherscan chỉ trả token transfer đã xảy ra
   thật trên chain), nên không cần lọc thêm.
"""

from __future__ import annotations
from datetime import datetime
from statistics import mean
from typing import Any


WEI_PER_ETHER = 10**18


def _to_ether(value_wei: str) -> float:
    """Chuyển value (wei, dạng string) sang Ether (float)."""
    try:
        return int(value_wei) / WEI_PER_ETHER
    except (TypeError, ValueError):
        return 0.0


def _to_token_amount(value_raw: str, decimals: str | int) -> float:
    """Chuyển value ERC20 (đơn vị raw, dạng string) sang số lượng token thật theo decimal."""
    try:
        return int(value_raw) / (10 ** int(decimals))
    except (TypeError, ValueError):
        return 0.0
def _is_to_contract(tx: dict) -> bool:
    """
    Heuristic: input khác '0x' -> giao dịch có gọi hàm/contract.
    Xem giới hạn (1) ở docstring đầu file.
    """
    input_data = tx.get("input", "0x")
    return bool(input_data) and input_data != "0x"


def _minutes_between(timestamps_sorted: list[int]) -> float:
    """Trung bình số phút giữa các giao dịch liên tiếp. 0.0 nếu <2 giao dịch."""
    if len(timestamps_sorted) < 2:
        return 0.0
    diffs = [
        (timestamps_sorted[i + 1] - timestamps_sorted[i]) / 60.0
        for i in range(len(timestamps_sorted) - 1)
    ]
    return mean(diffs)


def build_erc20_features(wallet_record: dict[str, Any], chain: str = "ethereum") -> dict[str, float]:
    """
    Tính nhóm feature ERC20 từ wallet_record["token_transfers"][chain] — đúng output
    endpoint `tokentx` trong scripts/02_fetch_etherscan_sample.py.

    Xem giới hạn (3), (4), (5) ở docstring đầu file trước khi dùng.
    """
    address = wallet_record["address"].lower()
    token_txs = wallet_record.get("token_transfers", {}).get(chain, [])

    if not token_txs:
        return _empty_erc20_feature_set()

    sent = [tx for tx in token_txs if tx.get("from", "").lower() == address]
    received = [tx for tx in token_txs if tx.get("to", "").lower() == address]

    sent_amounts = [_to_token_amount(tx["value"], tx.get("tokenDecimal", 18)) for tx in sent]
    received_amounts = [_to_token_amount(tx["value"], tx.get("tokenDecimal", 18)) for tx in received]

    sent_timestamps = sorted(int(tx["timeStamp"]) for tx in sent)
    received_timestamps = sorted(int(tx["timeStamp"]) for tx in received)

    return {
        "Total ERC20 tnxs": len(token_txs),
        "ERC20 total Ether received": sum(received_amounts),
        "ERC20 total ether sent": sum(sent_amounts),
        "ERC20 uniq sent addr": len({tx["to"].lower() for tx in sent if tx.get("to")}),
        "ERC20 uniq rec addr": len({tx["from"].lower() for tx in received if tx.get("from")}),
        "ERC20 avg time between sent tnx": _minutes_between(sent_timestamps),
        "ERC20 avg time between rec tnx": _minutes_between(received_timestamps),
        "ERC20 min val rec": min(received_amounts, default=0.0),
        "ERC20 max val rec": max(received_amounts, default=0.0),
        "ERC20 avg val rec": mean(received_amounts) if received_amounts else 0.0,
        "ERC20 min val sent": min(sent_amounts, default=0.0),
        "ERC20 max val sent": max(sent_amounts, default=0.0),
        "ERC20 avg val sent": mean(sent_amounts) if sent_amounts else 0.0,
        "ERC20 uniq sent token name": len({tx.get("tokenName", "") for tx in sent}),
        "ERC20 uniq rec token name": len({tx.get("tokenName", "") for tx in received}),
    }


def _empty_erc20_feature_set() -> dict[str, float]:
    """Trả về feature ERC20 toàn 0 cho ví không có token transfer nào."""
    keys = [
        "Total ERC20 tnxs", "ERC20 total Ether received", "ERC20 total ether sent",
        "ERC20 uniq sent addr", "ERC20 uniq rec addr",
        "ERC20 avg time between sent tnx", "ERC20 avg time between rec tnx",
        "ERC20 min val rec", "ERC20 max val rec", "ERC20 avg val rec",
        "ERC20 min val sent", "ERC20 max val sent", "ERC20 avg val sent",
        "ERC20 uniq sent token name", "ERC20 uniq rec token name",
    ]
    return {k: 0.0 for k in keys}


def build_full_wallet_features(wallet_record: dict[str, Any], chain: str = "ethereum") -> dict[str, float]:
    """Gộp feature ETH thường (build_wallet_features) + feature ERC20 (build_erc20_features)."""
    features = build_wallet_features(wallet_record, chain=chain)
    features.update(build_erc20_features(wallet_record, chain=chain))
    return features


def build_wallet_features(wallet_record: dict[str, Any], chain: str = "ethereum") -> dict[str, float]:
    """
    Input: 1 record theo đúng schema output của scripts/02_fetch_etherscan_sample.py:
        {
            "address": "0x...",
            "chains": {"ethereum": [ {..tx theo format Etherscan txlist..}, ... ]}
        }
    Chỉ dùng Ethereum (param `chain` mặc định "ethereum", giữ lại để dễ mở rộng
    sau này nếu cần, nhưng hiện tại project chỉ fetch đúng 1 chain).

    Output: dict feature (KHÔNG bao gồm nhóm ERC20 — xem giới hạn (1) đầu file).
    """
    address = wallet_record["address"].lower()
    raw_txs = wallet_record.get("chains", {}).get(chain, [])

    # Chỉ giữ giao dịch thành công.
    txs = [tx for tx in raw_txs if tx.get("isError", "0") == "0"]

    if not txs:
        return _empty_feature_set()

    sent = [tx for tx in txs if tx.get("from", "").lower() == address]
    received = [tx for tx in txs if tx.get("to", "").lower() == address]
    created_contracts = [
        tx for tx in sent if tx.get("contractAddress", "") not in ("", None)
    ]
    sent_to_contract = [tx for tx in sent if _is_to_contract(tx)]

    sent_timestamps = sorted(int(tx["timeStamp"]) for tx in sent)
    received_timestamps = sorted(int(tx["timeStamp"]) for tx in received)
    all_timestamps = sorted(int(tx["timeStamp"]) for tx in txs)

    sent_values = [_to_ether(tx["value"]) for tx in sent]
    received_values = [_to_ether(tx["value"]) for tx in received]
    sent_to_contract_values = [_to_ether(tx["value"]) for tx in sent_to_contract]

    total_ether_sent = sum(sent_values)
    total_ether_received = sum(received_values)

    features = {
        "Avg min between sent tnx": _minutes_between(sent_timestamps),
        "Avg min between received tnx": _minutes_between(received_timestamps),
        "Time Diff between first and last (Mins)": (
            (all_timestamps[-1] - all_timestamps[0]) / 60.0 if len(all_timestamps) >= 2 else 0.0
        ),
        "Sent tnx": len(sent),
        "Received Tnx": len(received),
        "Number of Created Contracts": len(created_contracts),
        "Unique Received From Addresses": len({tx["from"].lower() for tx in received}),
        "Unique Sent To Addresses": len({tx["to"].lower() for tx in sent if tx.get("to")}),
        "min value received": min(received_values, default=0.0),
        "max value received": max(received_values, default=0.0),
        "avg val received": mean(received_values) if received_values else 0.0,
        "min val sent": min(sent_values, default=0.0),
        "max val sent": max(sent_values, default=0.0),
        "avg val sent": mean(sent_values) if sent_values else 0.0,
        "min value sent to contract": min(sent_to_contract_values, default=0.0),
        "max val sent to contract": max(sent_to_contract_values, default=0.0),
        "avg value sent to contract": mean(sent_to_contract_values) if sent_to_contract_values else 0.0,
        "total transactions (including tnx to create contract)": len(txs),
        "total Ether sent": total_ether_sent,
        "total ether received": total_ether_received,
        "total ether sent contracts": sum(sent_to_contract_values),
        # Giới hạn (3): xấp xỉ, KHÔNG tính internal tx. Xem docstring đầu file.
        "total ether balance (APPROX, no internal tx)": total_ether_received - total_ether_sent,
    }
    return features


def _empty_feature_set() -> dict[str, float]:
    """Trả về feature toàn 0 cho ví không có giao dịch thành công nào."""
    keys = [
        "Avg min between sent tnx", "Avg min between received tnx",
        "Time Diff between first and last (Mins)", "Sent tnx", "Received Tnx",
        "Number of Created Contracts", "Unique Received From Addresses",
        "Unique Sent To Addresses", "min value received", "max value received",
        "avg val received", "min val sent", "max val sent", "avg val sent",
        "min value sent to contract", "max val sent to contract",
        "avg value sent to contract",
        "total transactions (including tnx to create contract)",
        "total Ether sent", "total ether received", "total ether sent contracts",
        "total ether balance (APPROX, no internal tx)",
    ]
    return {k: 0.0 for k in keys}


if __name__ == "__main__":
    # Ví dụ chạy thử — giả lập output thật của scripts/02_fetch_etherscan_sample.py
    # (gồm cả "chains" (txlist) và "token_transfers" (tokentx)).
    sample_wallet = {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "chains": {
            "ethereum": [
                {
                    "timeStamp": "1619073240", "from": "0x00799bbc833d5b168f0410312d2a8fd9e0e3079c",
                    "to": "0x28c6c06298d514db089934071355e5743bf21d60",
                    "value": "1000000000000000000", "input": "0x",
                    "contractAddress": "", "isError": "0",
                },
                {
                    "timeStamp": "1619091803", "from": "0x28c6c06298d514db089934071355e5743bf21d60",
                    "to": "0x0cf0ee63788a0849fe5297f3407f701e122cc023",
                    "value": "0",
                    "input": "0xa9059cbb000000000000000000000000b7b544f4fcb62941f8d6fbcc61e0265c6ae4462600000000000000000000000000000000000000000000000089e917994f71c0000",
                    "contractAddress": "", "isError": "0",
                },
            ]
        },
        "token_transfers": {
            "ethereum": [
                {
                    "timeStamp": "1619091803", "from": "0x28c6c06298d514db089934071355e5743bf21d60",
                    "to": "0xb7b544f4fcb62941f8d6fbcc61e0265c6ae4462",
                    "value": "10000000000000000000", "tokenName": "DATAcoin",
                    "tokenSymbol": "DATA", "tokenDecimal": "18",
                },
            ]
        },
    }
    import json
    print(json.dumps(build_full_wallet_features(sample_wallet), indent=2, ensure_ascii=False))