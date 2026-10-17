# agents/kyc_verification.py
"""
Sanctions Assistant (trước đây là KYC Assistant).
Chỉ làm exact matching địa chỉ ví với OFAC SDN.
Trả về sanction_result (fact, không điểm).
"""

import os
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState

# Đường dẫn đến file danh sách ví đen đã được extract từ OFAC SDN
OFAC_WALLET_FILE = os.path.join("data", "processed", "sample_ofac_wallet.txt")


def _normalize_wallet(address: str) -> str:
    """
    Chuẩn hoá địa chỉ ví trước khi so khớp. Địa chỉ EVM (0x...) không phân
    biệt hoa/thường — chữ hoa chỉ là checksum EIP-55 — nên đưa về lower(),
    nếu không ví dạng checksum (0x28C6c0...) không bao giờ khớp danh sách
    OFAC (lưu toàn chữ thường). Địa chỉ chain khác (vd BTC base58) phân biệt
    hoa/thường nên giữ nguyên.
    """
    address = (address or "").strip()
    if address[:2].lower() == "0x":
        return address.lower()
    return address


def _load_ofac_wallets() -> set:
    """Load danh sách địa chỉ ví từ file (mỗi dòng một địa chỉ)."""
    wallets = set()
    if not os.path.exists(OFAC_WALLET_FILE):
        print(f"⚠️ Không tìm thấy file OFAC: {OFAC_WALLET_FILE}. Trả về set rỗng.")
        return wallets
    with open(OFAC_WALLET_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                # Giả định mỗi dòng là một địa chỉ ví (hoặc tên, nhưng ta chỉ quan tâm địa chỉ ví)
                # Ở đây ta lưu nguyên dòng, nhưng sẽ so khớp chính xác
                wallets.add(_normalize_wallet(line))
    return wallets


# Cache để không đọc file mỗi lần
_OFAC_WALLETS_CACHE = None


def get_ofac_wallets() -> set:
    global _OFAC_WALLETS_CACHE
    if _OFAC_WALLETS_CACHE is None:
        _OFAC_WALLETS_CACHE = _load_ofac_wallets()
    return _OFAC_WALLETS_CACHE


def verify_kyc(state: AMLState) -> AMLState:
    """
    Sanctions Assistant.
    Đọc wallet_from, wallet_to, so khớp với danh sách OFAC.
    Trả về sanction_result trong state.
    """
    assert_no_raw_pii(state)

    wallet_from = (state.get("wallet_from") or "").strip()
    wallet_to = (state.get("wallet_to") or "").strip()

    ofac_wallets = get_ofac_wallets()

    # Kiểm tra wallet_from và wallet_to (so khớp sau chuẩn hoá, ghi lại địa
    # chỉ đúng như input để chuyên viên đối chiếu).
    matched_wallet = None
    if _normalize_wallet(wallet_from) in ofac_wallets:
        matched_wallet = wallet_from
    elif _normalize_wallet(wallet_to) in ofac_wallets:
        matched_wallet = wallet_to

    if matched_wallet:
        sanction_result = {
            "is_match": True,
            "matched_wallet": matched_wallet,
            "source": "OFAC SDN",
            "match_type": "Exact",
            "program": None,  # Có thể parse từ file nếu có thông tin, để None cho đơn giản
        }
    else:
        sanction_result = {
            "is_match": False,
            "matched_wallet": None,
            "source": "OFAC SDN",
            "match_type": None,
            "program": None,
        }

    state["sanction_result"] = sanction_result
    state["thought"] = f"SanctionsAssistant: {'MATCH' if matched_wallet else 'NO MATCH'} with OFAC SDN."

    return state