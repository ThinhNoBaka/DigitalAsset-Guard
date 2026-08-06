"""
demo_run.py -- Phần 10 (SPEC.md §10, mục 3).

Script chạy toàn bộ luồng nghiệp vụ 8 bước (SPEC.md §2) từ webhook giao dịch
giả lập -> Privacy Layer -> 5 Assistants -> điểm dừng chờ duyệt (HITL) -> STR.

Toàn bộ logic điều phối thật sự nằm ở core/graph_builder.py (Phần 9,
class PipelineRun) -- file này chỉ là 1 client CLI mỏng gọi vào đó, dùng
CHUNG một nguồn orchestration duy nhất với ui/app.py và (gián tiếp) api/main.py.
Không viết lại pipeline logic ở đây để tránh 2 nơi lệch nhau theo thời gian.
"""
import uuid
from typing import Any, Dict

from core.graph_builder import PipelineRun


if __name__ == "__main__":
    print("=== DEMO: DigitalAsset Guard AI Copilot -- chạy full workflow ===\n")

    # Webhook giả lập (SPEC.md §2 bước 1) -- 1 giao dịch vượt ngưỡng báo cáo,
    # ví nguồn nằm trong danh sách đen mẫu để demo thuyết phục hội đồng.
    mock_raw_transaction: Dict[str, Any] = {
        "tx_hash": f"0xDEMO_{uuid.uuid4().hex[:10]}",
        "wallet_from": "0xbadwallet123",
        "wallet_to": "0xdestination_wallet_demo",
        "amount_vnd": 620_000_000,
        "fullname": "Nguyễn Văn A",
        "id_number": "001096001234",
        "account_number": "1903456789012",
    }

    run = PipelineRun()

    for step_key, label, snapshot in run.steps(mock_raw_transaction):
        print(f"--- {label} ---")
        if step_key == "webhook" and snapshot.get("skipped"):
            print(snapshot["reason"])
            break
        for k, v in snapshot.items():
            print(f"    {k}: {v}")
        print()

    final_state = run.state
    if final_state.get("approval_status") == "pending":
        print(
            f"[HITL] Giao dịch cần chuyên viên duyệt. "
            f"final_risk_score={final_state.get('final_risk_score')} "
            f"report_path={final_state.get('report_path')}"
        )
        # Mô phỏng chuyên viên bấm Approve trên UI:
        final_state = run.resume("approved")
        print(f"[HITL] Đã xử lý quyết định -> approval_status={final_state['approval_status']}")
    elif final_state:
        print(f"[*] Giao dịch tự động: approval_status={final_state.get('approval_status')}")