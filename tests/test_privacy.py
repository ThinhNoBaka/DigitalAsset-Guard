"""
tests/test_privacy.py -- Kiểm tra tính đúng đắn và an toàn của Privacy Layer
"""
from core.privacy_layer import hash_pii

def test_hash_pii_consistency():
    # Cùng một đầu vào phải ra cùng một chuỗi hash cố định
    hash1 = hash_pii("Nguyen Van A")
    hash2 = hash_pii("Nguyen Van A")
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 luôn có độ dài 64 ký tự hex

def test_hash_pii_case_and_space():
    # Tự động xử lý khoảng trắng thừa và chữ hoa/thường
    hash_lowercase = hash_pii("  nguyen van a  ")
    hash_uppercase = hash_pii("NGUYEN VAN A")
    assert hash_lowercase == hash_uppercase

def test_hash_pii_empty_handling():
    # Xử lý an toàn các trường hợp dữ liệu trống
    assert hash_pii("") == ""
    assert hash_pii(None) == ""