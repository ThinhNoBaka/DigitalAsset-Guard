"""
core/privacy_layer.py -- Lớp bảo vệ dữ liệu cá nhân (SHA-256 Masking).

Ranh giới bảo mật bắt buộc: mọi PII gốc (fullname, id_number, account_number)
phải đi qua mask_pii() trước khi lọt vào AMLState. Không log giá trị gốc
ở bất kỳ đâu trong module này, kể cả khi debug.
"""
import hashlib

PII_FIELDS = {"fullname", "id_number", "account_number"}


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