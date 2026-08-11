# scripts/mock_core_banking.py
"""
MOCK Core Banking — nguồn dữ liệu OFF-CHAIN cho demo, ĐỘC LẬP HOÀN TOÀN với
wallet_record (ON-CHAIN, từ Etherscan — dùng cho Transaction Assistant/XGBoost
qua scripts/feature_builder.py). Hai nguồn KHÔNG được hợp nhất.

MỤC ĐÍCH: chỉ phục vụ video demo agents/aggregation_monitor.py (structuring/
smurfing detection). KHÔNG kết nối Core Banking thật.

KHÓA TRA CỨU: account_number — field đã có sẵn trong
api.main.RawTransactionRequest (frontend hiện tại đã nhập "Số tài khoản ngân
hàng"), KHÔNG cần thêm input/UI mới.

QUAN TRỌNG — KHÔNG có mapping wallet<->customer nào ở đây, và file này không
tạo ra mapping đó. Đây chỉ là bảng tra account_number -> lịch sử giao dịch
ngân hàng, phục vụ đúng 1 mục đích: cấp dữ liệu cho Aggregation Monitor khi
account_number của case trùng với 1 record demo. Nếu account_number không có
trong bảng, hàm trả về None — caller (api/main.py::_build_initial_state)
KHÔNG được set state["wallet_tx_history"], giữ nguyên hành vi mặc định
aggregation_status="not_assessed" (agents/aggregation_monitor.py).

SCHEMA ĐẦU RA (get_wallet_tx_history) — đúng theo state.py::AMLState và
docstring agents/aggregation_monitor.py:
    list[dict], mỗi phần tử:
        {"timestamp": <unix epoch giây, int>, "direction": "in" | "out",
         "amount": <float, VND>, "block": <int, optional — không dùng ở mock
         này, off-chain không có khái niệm block>}
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional

# -----------------------------------------------------------------------
# Dữ liệu thô, dễ đọc/sửa tay cho demo (timestamp dạng ISO 8601 cho dễ nhìn,
# convert sang unix epoch giây ở get_wallet_tx_history()).
# -----------------------------------------------------------------------
_MOCK_CORE_BANKING_RAW: dict[str, list[dict]] = {
    # Case demo: 4 giao dịch chuyển ra liên tiếp trong vài ngày, mỗi giao
    # dịch riêng lẻ đều DƯỚI ngưỡng báo cáo (thường 500tr — xem
    # core/config.py::REPORT_THRESHOLD_VND, không hard-code lại số đó ở đây)
    # nhưng cộng dồn 7 ngày vượt ngưỡng -> kỳ vọng
    # agents/aggregation_monitor.py trigger structuring_detected=True qua
    # near_threshold_count_30d (mỗi khoản đều nằm trong dải "gần ngưỡng").
    "0123456789": [
        {"timestamp": "2026-08-01T09:00:00", "amount_vnd": 480_000_000, "direction": "out"},
        {"timestamp": "2026-08-02T10:00:00", "amount_vnd": 490_000_000, "direction": "out"},
        {"timestamp": "2026-08-03T11:00:00", "amount_vnd": 470_000_000, "direction": "out"},
        {"timestamp": "2026-08-04T12:00:00", "amount_vnd": 495_000_000, "direction": "out"},
    ],
}


def _iso_to_epoch_seconds(iso_ts: str) -> int:
    """Convert chuỗi ISO 8601 (naive) sang unix epoch giây (int)."""
    return int(datetime.fromisoformat(iso_ts).timestamp())


def get_wallet_tx_history(account_number: str) -> Optional[list[dict]]:
    """
    Tra bảng mock theo account_number, trả về đúng schema wallet_tx_history
    mà agents/aggregation_monitor.py và agents/transaction_classifier.py yêu
    cầu (xem docstring đầu file).

    Trả về None nếu account_number không có trong bảng demo — KHÔNG trả về
    list rỗng ([]), để phân biệt rõ "không có case demo cho STK này" (None,
    caller không set field) với "có case nhưng lịch sử rỗng" (không xảy ra
    ở mock này, nhưng giữ quy ước None = không set, để nhất quán với cách
    agents/aggregation_monitor.py phân biệt None vs []).
    """
    if not account_number:
        return None

    raw_history = _MOCK_CORE_BANKING_RAW.get(account_number)
    if not raw_history:
        return None

    return [
        {
            "timestamp": _iso_to_epoch_seconds(tx["timestamp"]),
            "direction": tx["direction"],
            "amount": float(tx["amount_vnd"]),
        }
        for tx in raw_history
    ]


if __name__ == "__main__":
    for acc in ("0123456789", "9999999999"):
        result = get_wallet_tx_history(acc)
        print(f"account_number={acc!r} -> {result}")