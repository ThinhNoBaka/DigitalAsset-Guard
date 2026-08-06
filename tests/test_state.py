"""
tests/test_state.py -- Kiểm tra an toàn của AMLState
"""
import pytest
from core.state import create_initial_state, assert_no_raw_pii

def test_create_initial_state():
    s = create_initial_state("0x123", "0xA", "0xB", 600_000_000)
    assert s["tx_hash"] == "0x123"
    assert s["approval_status"] == "pending"
    assert s["hashed_fullname"] is None

def test_assert_no_raw_pii_pass_khi_sach():
    s = create_initial_state("0x123", "0xA", "0xB", 100)
    assert_no_raw_pii(s)

def test_assert_no_raw_pii_raise_loi_khi_co_pii():
    bad_state = {"tx_hash": "0x123", "fullname": "Nguyen Van A"}
    with pytest.raises(ValueError):
        assert_no_raw_pii(bad_state)