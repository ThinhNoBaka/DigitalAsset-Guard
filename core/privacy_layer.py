"""
core/privacy_layer.py -- Lớp bảo vệ dữ liệu cá nhân (SHA-256 Masking).

Ranh giới bảo mật bắt buộc: mọi PII gốc (fullname, id_number, account_number)
phải đi qua mask_pii() trước khi lọt vào AMLState. Không log giá trị gốc
ở bất kỳ đâu trong module này, kể cả khi debug.
"""
import os
import hashlib

from dotenv import load_dotenv

from core.name_screening import screen_name_against_sdn

# BẮT BUỘC: load biến môi trường từ .env (giống pattern đã dùng ở
# agents/regulation_rag.py) -- mỗi file muốn đọc .env đều phải tự gọi
# load_dotenv() riêng, không tự động chia sẻ giữa các module.
load_dotenv()

PII_FIELDS = {"fullname", "id_number", "account_number"}

# Salt cố định cho MVP (SPEC.md mục 8) -- xoay theo chu kỳ quý ở production
# (xem nhắc vận hành trong core/audit_logger.py). KHÔNG hardcode giá trị
# thật trong code, chỉ có fallback dev để module không crash khi thiếu .env.
PII_SALT = os.getenv("PII_SALT", "DEV_ONLY_CHANGE_ME_IN_ENV")


def mask_pii(raw_customer: dict, salt: str) -> dict:
    """
    Nhận vào dict khách hàng gốc và salt, trả về dict đã băm các trường PII.

    Input:  {"fullname": ..., "id_number": ..., "account_number": ..., ...}
    Output: {"hashed_fullname": ..., "hashed_id_number": ...,
             "hashed_account_number": ..., ...}  (các trường không phải PII giữ nguyên)
    """
    masked_customer = {}

    for key, value in raw_customer.items():
        if key in PII_FIELDS:
            masked_customer[f"hashed_{key}"] = _hash_value(value, salt)
        else:
            masked_customer[key] = value

    return masked_customer


def _hash_value(value, salt: str) -> str:
    """Băm 1 giá trị PII bằng SHA-256 + salt. Rỗng/None -> chuỗi rỗng."""
    if value is None:
        return ""

    cleaned_value = str(value).strip()
    if not cleaned_value:
        return ""

    # Chuẩn hóa viết hoa để tránh lệch hash do khác biệt hoa/thường
    normalized_value = cleaned_value.upper()
    salted_input = f"{normalized_value}{salt}"
    return hashlib.sha256(salted_input.encode("utf-8")).hexdigest()


def assert_no_raw_pii(state: dict) -> None:
    """
    Chốt kiểm tra tự động: quét state, nếu phát hiện key PII gốc
    (không có tiền tố hashed_) thì raise ValueError ngay lập tức.
    Được gọi ở đầu mỗi agent từ Phần 4 trở đi.
    """
    violations = PII_FIELDS & set(state.keys())
    if violations:
        raise ValueError(
            f"PHÁT HIỆN VI PHẠM PRIVACY LAYER: state chứa trường PII gốc: {violations}. "
            "Chỉ được dùng các trường hashed_*."
        )


def privacy_layer_node(raw_state: dict) -> dict:
    """
    Node đầu tiên của pipeline (SPEC_v2 §1 bước 2, core/graph_builder.py
    gọi hàm này làm entry_point).

    Input: raw_state chứa PII gốc (fullname, id_number, account_number) +
    các field non-PII đã có sẵn (tx_hash, wallet_from, wallet_to,
    amount_vnd, ...).

    Thứ tự BẮT BUỘC theo SPEC_v2 §3:
        1. Fuzzy name-matching (Levenshtein, core/name_screening.py::
           screen_name_against_sdn — so với danh sách tên SDN thật, parse từ
           data/raw/ofac/sdn.xml) chạy TRÊN fullname DẠNG GỐC, TRƯỚC khi băm
           -- băm rồi thì không so khớp mờ được nữa.
        2. Băm toàn bộ PII (SHA-256 + salt) qua mask_pii().
        3. assert_no_raw_pii() chốt lần cuối trước khi state rời khỏi node
           này -- không agent nào phía sau (transaction_classifier,
           graph_aml, kyc_verification, decision_engine, regulation_rag,
           alert_report) được thấy PII gốc.

    name_similarity_warning/score là field RIÊNG trong AMLState, KHÔNG nằm
    trong sanction_result (xem core/state.py, agents/decision_engine.py).
    name_similarity_score là % tương đồng (0-100, xem
    core/name_screening.py::levenshtein_similarity_pct), KHÔNG phải tỉ lệ
    0-1 -- lưu ý khi hiển thị/định dạng ở report và chat.
    """
    state = dict(raw_state)

    # Bước 1: fuzzy name-matching TRƯỚC khi băm.
    fullname = state.get("fullname")
    warning, score_pct, matched_name = screen_name_against_sdn(fullname)
    state["name_similarity_warning"] = warning
    state["name_similarity_score"] = score_pct
    if matched_name is not None:
        state["name_similarity_matched_name"] = matched_name

    # Bước 2: băm PII.
    state = mask_pii(state, salt=PII_SALT)

    # Bước 3: chốt an toàn trước khi rời Privacy Layer.
    assert_no_raw_pii(state)

    return state