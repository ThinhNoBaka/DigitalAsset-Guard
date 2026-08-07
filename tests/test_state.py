"""
tests/test_state.py -- Kiểm tra an toàn của AMLState

Sửa theo kiến trúc hiện tại (pivot SPEC_v2):
- core/state.py CHỈ khai báo class AMLState (TypedDict, total=False — mọi
  field đều optional, không set thì key không tồn tại). KHÔNG có hàm
  create_initial_state như bản cũ.
- State khởi tạo thật được dựng bằng AMLState(...) trực tiếp — pattern như
  tests/test_decision_engine.py — và theo đúng api/main.py::_build_initial_state:
  approval_status="pending", hashed_fullname/hashed_id_number/
  hashed_account_number khởi tạo = None. PipelineRun.__init__ cũng có quy ước
  setdefault approval_status="pending".
- assert_no_raw_pii() THẬT nằm ở core/privacy_layer.py, không phải core.state.
"""
import pytest
from core.state import AMLState
from core.privacy_layer import assert_no_raw_pii

def test_create_initial_state():
    s = AMLState(
        tx_hash="0x123",
        wallet_from="0xA",
        wallet_to="0xB",
        amount_vnd=600_000_000,
        approval_status="pending",
        hashed_fullname=None,
        hashed_id_number=None,
        hashed_account_number=None,
    )
    assert s["tx_hash"] == "0x123"
    assert s["approval_status"] == "pending"
    assert s["hashed_fullname"] is None

def test_assert_no_raw_pii_pass_khi_sach():
    s = AMLState(
        tx_hash="0x123",
        wallet_from="0xA",
        wallet_to="0xB",
        amount_vnd=100,
        approval_status="pending",
        hashed_fullname=None,
        hashed_id_number=None,
        hashed_account_number=None,
    )
    assert_no_raw_pii(s)

def test_assert_no_raw_pii_raise_loi_khi_co_pii():
    bad_state = {"tx_hash": "0x123", "fullname": "Nguyen Van A"}
    with pytest.raises(ValueError):
        assert_no_raw_pii(bad_state)
