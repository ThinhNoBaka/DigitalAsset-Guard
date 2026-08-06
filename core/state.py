"""
Không bao giờ thêm trường chứa PII gốc (fullname, id_number, account_number) vào state này. Chỉ dùng bản đã băm.

Mọi trường mới thêm vào AMLState phải quay lại đối chiếu với SPEC.md trước.
Các trường đánh dấu "[STATE MỚI]" dưới đây được thêm theo
THAY_DOI_SO_VOI_BAN_GOC.md (Thay đổi 2, 3, 5) -- Explainable AI + Graph
Visualization. Nhớ cập nhật lại mục 4/SPEC.md cho khớp.
"""
from typing import TypedDict


class AMLState(TypedDict):
    tx_hash: str
    wallet_from: str
    wallet_to: str
    amount_vnd: float
    hashed_fullname: str | None
    hashed_id_number: str | None
    hashed_account_number: str | None
    risk_score_classifier: float | None
    kyc_flags: list | None
    graph_risk_score: float | None
    legal_citations: list | None
    final_risk_score: float | None
    report_path: str | None
    approval_status: str | None

    # --- [STATE MỚI] Thay đổi 2 (agents/transaction_classifier.py) ---
    # Top feature importance TOÀN CỤC của XGBoost (không phải giải thích riêng
    # cho từng giao dịch -- xem lưu ý trong agents/transaction_classifier.py).
    # Dạng: list[tuple[str, float]], ví dụ [("in_degree", 0.31), ...]
    top_features: list | None

    # --- [STATE MỚI] Thay đổi 3 (agents/graph_aml.py) ---
    # Dữ liệu phục vụ Explainable AI + Graph Visualization (UI Phần 10.a, 10.c)
    hop_distance_to_blacklist: int | None
    fan_out: int | None
    suspicious_path: list | None
    community_id: int | str | None

    # --- [STATE MỚI] Thay đổi 5 (agents/alert_report.py) ---
    # % đóng góp thật của từng thành phần vào final_risk_score, suy trực tiếp
    # từ công thức 0.2*Classifier + 0.3*KYC + 0.5*Graph đã công bố -- khác với
    # ý tưởng gán % tùy ý cho từng yếu tố đồ thị con (xem alert_report.py).
    # Dạng: {"classifier_contribution_pct": .., "kyc_contribution_pct": ..,
    #         "graph_contribution_pct": ..}
    risk_breakdown: dict | None

    # --- Formalize lại các trường đã được agent dùng thật trong code cũ
    # nhưng trước đây CHƯA khai báo trong AMLState (không đổi hành vi gì,
    # chỉ khai báo type cho khớp thực tế) ---
    legal_sources_retrieved: list | None  # set bởi agents/regulation_rag.py
    thought: str | None  # set bởi agents/graph_aml.py
    kyc_exact_match: bool | None  # đọc bởi agents/alert_report.py (safety-net override)