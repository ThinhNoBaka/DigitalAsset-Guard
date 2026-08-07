"""
core/audit_logger.py -- Audit Logging (V2-6, THAY_DOI_V2.md Phần 9B).

Mục tiêu: có bằng chứng vận hành thực tế cho kiểm toán nội bộ -- "hệ thống
lưu vết mọi hành động". Ghi ra logs/audit_trail.log, mỗi dòng 1 JSON:
    {"timestamp", "agent", "tx_hash", "duration_ms", "state_keys_present"}

RÀNG BUỘC BẮT BUỘC, KHÔNG THƯƠNG LƯỢNG (V2-6, mục 3):
  - Chỉ ghi các trường có tiền tố "hashed_", hoặc không chứa PII (tx_hash,
    wallet_from/to vì là địa chỉ công khai on-chain, risk scores, agent name,
    timestamp, duration).
  - Trước khi ghi log, gọi assert_no_raw_pii() trên state_snapshot -- nếu
    raise lỗi thì log ghi "ERROR: raw PII detected, log entry suppressed"
    thay vì ghi state, KHÔNG BAO GIỜ ghi PII gốc ra file dù là để debug.

Gọi log_step() ở đầu và cuối mỗi node trong core/graph_builder.py -- không
sửa logic bên trong từng agent, chỉ wrap ở tầng điều phối graph.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.privacy_layer import assert_no_raw_pii

AUDIT_LOG_PATH = Path("logs/audit_trail.log")

# Nhắc vận hành: mô phỏng "xoay salt PII_SALT hàng quý" -- CHỈ ghi 1 dòng cảnh
# báo mô tả vận hành, KHÔNG code cơ chế xoay salt thật trong MVP (salt cố định
# trong .env cho MVP đã chốt ở Phần 2/SPEC.md mục 8).
_SALT_ROTATION_NOTICE_LOGGED = False


def _append_line(payload: Dict[str, Any]) -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def log_step(
    agent_name: str,
    tx_hash: Optional[str],
    duration_ms: float,
    state_snapshot: Dict[str, Any],
) -> None:
    """
    Ghi 1 dòng audit log cho 1 bước (agent) trong pipeline.

    state_snapshot: dict state (đầy đủ hoặc partial update của node) TẠI THỜI
    ĐIỂM ghi log. Hàm này KHÔNG ghi giá trị của state_snapshot, chỉ ghi TÊN các
    key đang có mặt (state_keys_present) -- tuyệt đối không serialize giá trị
    thô ra log, kể cả các trường không phải PII, để tránh rò rỉ ngoài ý muốn
    khi có agent nào đó vô tình nhét thêm dữ liệu nhạy cảm vào state sau này.

    Nếu state_snapshot chứa PII gốc (fullname/id_number/account_number chưa
    băm), KHÔNG ghi state -- ghi dòng lỗi thay thế và KHÔNG raise, vì audit
    logger không được phép làm sập pipeline chính.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        assert_no_raw_pii(state_snapshot)
    except ValueError:
        _append_line({
            "timestamp": timestamp,
            "agent": agent_name,
            "tx_hash": tx_hash,
            "duration_ms": round(duration_ms, 2),
            "state_keys_present": "ERROR: raw PII detected, log entry suppressed",
        })
        return

    _append_line({
        "timestamp": timestamp,
        "agent": agent_name,
        "tx_hash": tx_hash,
        "duration_ms": round(duration_ms, 2),
        "state_keys_present": sorted(state_snapshot.keys()),
    })

    _maybe_log_salt_rotation_notice()


def _maybe_log_salt_rotation_notice() -> None:
    """Ghi 1 lần duy nhất/tiến trình dòng nhắc xoay salt định kỳ (mô tả vận hành)."""
    global _SALT_ROTATION_NOTICE_LOGGED
    if _SALT_ROTATION_NOTICE_LOGGED:
        return
    _SALT_ROTATION_NOTICE_LOGGED = True
    _append_line({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "audit_logger",
        "tx_hash": None,
        "duration_ms": 0.0,
        "state_keys_present": "SALT_ROTATION_DUE: khuyến nghị xoay PII_SALT theo chu kỳ quý trong môi trường production",
    })


def timed_step(agent_name: str, tx_hash: Optional[str], fn, state: Dict[str, Any]):
    """
    Helper tiện dùng: gọi fn(state), đo duration_ms, ghi log_step ở ĐẦU và CUỐI
    bước (2 dòng log/bước, đúng yêu cầu "gọi log_step ở đầu và cuối mỗi node").
    Trả về kết quả của fn(state) (partial update dict hoặc full state, tuỳ agent).

    Dùng trong core/graph_builder.py để wrap các node LangGraph / bước fallback
    mà KHÔNG cần sửa logic bên trong từng agent.
    """
    log_step(agent_name, tx_hash, 0.0, state)  # log "đầu" node -- state trước khi chạy
    start = time.perf_counter()
    result = fn(state)
    duration_ms = (time.perf_counter() - start) * 1000

    # Snapshot "sau" để log: luôn merge lên state gốc, dù fn trả full state
    # (LangGraph node) hay chỉ partial update dict (giống verify_kyc) -- merge
    # không hại gì trong cả 2 trường hợp, chỉ ghi đè đúng các key mới có.
    merged_snapshot = {**state, **result} if isinstance(result, dict) else state

    log_step(agent_name, tx_hash, duration_ms, merged_snapshot)
    return result