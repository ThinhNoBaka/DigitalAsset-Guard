"""
tests/test_privacy.py -- Kiểm tra tính đúng đắn và an toàn của Privacy Layer

Sửa theo kiến trúc hiện tại:
- core/privacy_layer.py KHÔNG có hàm hash_pii như bản cũ. Hàm hash đơn lẻ
  THẬT là _hash_value(value, salt) — SHA-256 + salt, tự normalize (strip +
  UPPERCASE), trả "" cho rỗng/None. Public API mask_pii() gọi _hash_value
  cho từng trường PII.
- Giữ nguyên ý định kiểm thử ban đầu: deterministic, case/space-insensitive,
  xử lý an toàn dữ liệu rỗng.
"""
from core.privacy_layer import _hash_value


def test_hash_pii_consistency():
    # Cùng một đầu vào phải ra cùng một chuỗi hash cố định
    hash1 = _hash_value("Nguyen Van A", "test-salt")
    hash2 = _hash_value("Nguyen Van A", "test-salt")
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 luôn có độ dài 64 ký tự hex


def test_hash_pii_case_and_space():
    # Tự động xử lý khoảng trắng thừa và chữ hoa/thường
    # _hash_value strip chuỗi + viết hoa trước khi băm (giống mask_pii)
    hash_lowercase = _hash_value("  nguyen van a  ", "test-salt")
    hash_uppercase = _hash_value("NGUYEN VAN A", "test-salt")
    assert hash_lowercase == hash_uppercase


def test_hash_pii_empty_handling():
    # Xử lý an toàn các trường hợp dữ liệu trống
    assert _hash_value("", "test-salt") == ""
    assert _hash_value(None, "test-salt") == ""