# agents/aggregation_monitor.py
"""
Aggregation Monitor — module mới, bổ sung sau SPEC_v2 để bắt hành vi
"structuring" / "smurfing" (chia nhỏ giao dịch để né ngưỡng báo cáo 500tr).

VẤN ĐỀ GỐC: Decision Engine trước đây chỉ nhìn classifier_score/graph_score/
sanction_result của TỪNG giao dịch đơn lẻ. Nếu kẻ rửa tiền chia 1 khoản 600tr
thành 3 lần chuyển 200tr, không có tín hiệu nào bắt được vì mỗi giao dịch
riêng lẻ đều "sạch" theo cả classifier lẫn graph.

Aggregation Monitor KHÔNG thay thế Graph Assistant (fan-in/fan-out multi-ví
vẫn do Graph Assistant xử lý). Module này xử lý tầng đơn giản hơn: lịch sử
giao dịch CỦA CHÍNH 1 VÍ theo thời gian (time-series 1 node) — điều mà
PPR/Louvain không được thiết kế để bắt.

*** NGUỒN DỮ LIỆU ***
Đọc state["wallet_tx_history"] — dữ liệu OFF-CHAIN Core Banking (amount_vnd),
ĐỘC LẬP hoàn toàn với wallet_record (ON-CHAIN, từ Etherscan, dùng cho
Transaction Classifier/XGBoost). Hai nguồn dữ liệu này KHÔNG được hợp nhất.
Nếu key này không có trong state (chưa có nguồn dữ liệu off-chain), module
trả về aggregation_status="not_assessed" và structuring_detected=None —
KHÔNG trả về False (False ngầm hiểu là "đã kiểm tra, không phát hiện", sai
thực tế). KHÔNG bịa dữ liệu để "chạy cho có".

Format wallet_tx_history: list[dict], mỗi phần tử:
    {"timestamp": <unix epoch giây>, "direction": "in" | "out",
     "amount": <float, VND>, "block": <int, optional>}

Field tx_timestamp (cũng non-standard) — mốc thời gian của giao dịch ĐANG
xét. Nếu không cung cấp, module lấy timestamp lớn nhất trong wallet_tx_history
làm mốc "hiện tại" (best-effort, không phải giả định chính xác tuyệt đối).
"""

from core.config import REPORT_THRESHOLD_VND
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState

# Ngưỡng "gần ngưỡng báo cáo" — coi là "né ngưỡng" nếu amount nằm trong
# [NEAR_THRESHOLD_RATIO * REPORT_THRESHOLD_VND, REPORT_THRESHOLD_VND).
NEAR_THRESHOLD_RATIO = 0.9

# Số lần giao dịch "gần ngưỡng" trong 30 ngày để coi là dấu hiệu structuring.
NEAR_THRESHOLD_COUNT_TRIGGER = 3

# Cửa sổ thời gian (giây) để cộng dồn giá trị giao dịch.
WINDOW_7D_SECONDS = 7 * 24 * 3600
WINDOW_30D_SECONDS = 30 * 24 * 3600

# Nội dung reason dùng chung khi không có dữ liệu off-chain Core Banking.
NOT_ASSESSED_REASON = (
    "Không có dữ liệu off-chain (Core Banking) để đánh giá structuring — "
    "không đồng nghĩa với việc đã kiểm tra và không phát hiện."
)


def _compute_aggregated_amount(
    current_amount: float,
    current_ts,
    wallet_history: list,
    window_seconds: int,
) -> float:
    """
    Cộng dồn amount (VND) của các giao dịch trong wallet_history nằm trong
    [current_ts - window_seconds, current_ts], CỘNG thêm giao dịch hiện tại.

    Nếu current_ts là None, chỉ trả về giao dịch hiện tại (không đủ dữ liệu
    thời gian để xác định cửa sổ — không suy đoán).
    """
    total = float(current_amount or 0)

    if current_ts is None:
        return total

    for tx in wallet_history:
        ts = tx.get("timestamp")
        amount = tx.get("amount")
        if ts is None or amount is None:
            continue
        delta = current_ts - float(ts)
        if 0 <= delta <= window_seconds:
            total += float(amount)

    return total


def _compute_near_threshold_count(
    current_amount: float,
    current_ts,
    wallet_history: list,
    window_seconds: int,
    threshold: float,
    ratio: float,
) -> int:
    """
    Đếm số giao dịch (kể cả giao dịch hiện tại) trong window_seconds gần đây
    có amount nằm trong [ratio * threshold, threshold).
    """
    lower_bound = ratio * threshold

    def _is_near(amount: float) -> bool:
        return lower_bound <= amount < threshold

    count = 1 if _is_near(float(current_amount or 0)) else 0

    if current_ts is None:
        return count

    for tx in wallet_history:
        ts = tx.get("timestamp")
        amount = tx.get("amount")
        if ts is None or amount is None:
            continue
        delta = current_ts - float(ts)
        if 0 <= delta <= window_seconds and _is_near(float(amount)):
            count += 1

    return count


def analyze_aggregation(state: AMLState) -> AMLState:
    """
    Đọc amount_vnd hiện tại + wallet_tx_history, ghi is_large_tx,
    aggregated_amount_7d, near_threshold_count_30d, structuring_detected,
    aggregation_status, aggregation_reason vào state.

    PHÂN BIỆT 2 TRẠNG THÁI:
    - Có wallet_tx_history → aggregation_status="assessed",
      structuring_detected=True/False (đã chạy rule structuring thật).
    - KHÔNG có wallet_tx_history → aggregation_status="not_assessed",
      structuring_detected=None (KHÔNG phải False — "chưa đánh giá được"
      khác "đã kiểm tra sạch").
    """
    assert_no_raw_pii(state)

    amount_vnd = state.get("amount_vnd", 0) or 0

    # --- is_large_tx: nghĩa vụ báo cáo giao dịch lớn theo TT27 ---
    # ĐỘC LẬP với risk score / structuring — chỉ là metadata pháp lý, dùng
    # để Report Assistant biết cần đính kèm nghĩa vụ báo cáo giao dịch lớn
    # (khác với STR) khi sinh báo cáo, không tự động kích hoạt REPORT.
    state["is_large_tx"] = amount_vnd >= REPORT_THRESHOLD_VND

    wallet_history = state.get("wallet_tx_history")

    if not wallet_history:
        # KHÔNG có dữ liệu off-chain (Core Banking) — không đủ dữ liệu để
        # đánh giá cộng dồn.
        # KHÔNG gán 0 (0 là giá trị hợp lệ khác với "không biết").
        # KHÔNG gán structuring_detected=False — False ngầm hiểu "đã kiểm tra,
        # không phát hiện", sai thực tế.
        state["aggregated_amount_7d"] = None
        state["near_threshold_count_30d"] = None
        state["structuring_detected"] = None
        state["aggregation_status"] = "not_assessed"
        state["aggregation_reason"] = NOT_ASSESSED_REASON
        state["thought"] = (
            "AggregationMonitor: không có wallet_tx_history (dữ liệu off-chain "
            "Core Banking) — aggregation_status='not_assessed', structuring_detected=None. "
            "KHÔNG đồng nghĩa đã kiểm tra và không phát hiện."
        )
        return state

    current_ts = state.get("tx_timestamp")
    if current_ts is None:
        timestamps = [
            tx.get("timestamp")
            for tx in wallet_history
            if tx.get("timestamp") is not None
        ]
        current_ts = max(timestamps) if timestamps else None

    aggregated_amount_7d = _compute_aggregated_amount(
        amount_vnd, current_ts, wallet_history, WINDOW_7D_SECONDS
    )
    near_threshold_count_30d = _compute_near_threshold_count(
        amount_vnd, current_ts, wallet_history, WINDOW_30D_SECONDS,
        REPORT_THRESHOLD_VND, NEAR_THRESHOLD_RATIO,
    )

    # structuring_detected = True nếu:
    #  (a) cộng dồn 7 ngày vượt ngưỡng trong khi giao dịch hiện tại tự nó
    #      CHƯA vượt (nếu tự nó đã vượt thì đã là is_large_tx, không cần suy
    #      đoán "chia nhỏ" nữa); HOẶC
    #  (b) lặp lại giao dịch "gần ngưỡng" nhiều lần bất thường trong 30 ngày.
    structuring_detected = (
        (aggregated_amount_7d >= REPORT_THRESHOLD_VND and amount_vnd < REPORT_THRESHOLD_VND)
        or near_threshold_count_30d >= NEAR_THRESHOLD_COUNT_TRIGGER
    )

    state["aggregated_amount_7d"] = aggregated_amount_7d
    state["near_threshold_count_30d"] = near_threshold_count_30d
    state["structuring_detected"] = structuring_detected
    state["aggregation_status"] = "assessed"
    state["aggregation_reason"] = None

    state["thought"] = (
        f"AggregationMonitor: cộng dồn 7 ngày = {aggregated_amount_7d:,.0f} VND, "
        f"số giao dịch gần ngưỡng trong 30 ngày = {near_threshold_count_30d}. "
        f"structuring_detected = {structuring_detected}."
    )

    return state


if __name__ == "__main__":
    # Test độc lập: 3 giao dịch 200tr liên tiếp trong vài ngày, mỗi giao dịch
    # riêng lẻ đều dưới ngưỡng 500tr, nhưng cộng dồn 7 ngày vượt ngưỡng.
    # Kỳ vọng: structuring_detected = True (qua điều kiện (a), không phải (b)
    # vì 200tr chưa phải "gần ngưỡng" theo NEAR_THRESHOLD_RATIO=0.9).
    test_state: AMLState = {
        "tx_hash": "0xstructuring_test",
        "wallet_from": "0xsmurf_wallet",
        "wallet_to": "0xdestination",
        "amount_vnd": 200_000_000,
        "hashed_fullname": "abc123",
        "hashed_id_number": "def456",
        "hashed_account_number": "ghi789",
    }
    test_state["tx_timestamp"] = 1_700_000_000
    test_state["wallet_tx_history"] = [
        {"timestamp": 1_699_950_000, "direction": "out", "amount": 200_000_000},
        {"timestamp": 1_699_990_000, "direction": "out", "amount": 200_000_000},
    ]

    print("Đang chạy thử Aggregation Monitor...")
    updated = analyze_aggregation(test_state)
    print(f"is_large_tx: {updated['is_large_tx']}")
    print(f"aggregated_amount_7d: {updated['aggregated_amount_7d']:,.0f} VND")
    print(f"near_threshold_count_30d: {updated['near_threshold_count_30d']}")
    print(f"structuring_detected: {updated['structuring_detected']}")
    print(f"aggregation_status: {updated['aggregation_status']}")