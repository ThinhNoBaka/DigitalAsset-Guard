"""
tests/test_classifier.py -- Kiểm thử Agent phân loại giao dịch sau khi sửa lỗi tham số
"""
from core.state import create_initial_state
from agents.transaction_classifier import classify_transaction

def test_classify_normal_transaction():
    # Thử nghiệm với một giao dịch nhỏ bình thường (50 triệu VND)
    # Bổ sung wallet_from và wallet_to để khớp với cấu trúc hệ thống
    state = create_initial_state(
        tx_hash="TX123", 
        amount_vnd=50_000_000.0,
        wallet_from="hashed_wallet_source_aaa",
        wallet_to="hashed_wallet_dest_bbb"
    )
    
    updated_state = classify_transaction(state)
    
    assert updated_state["is_suspicious"] is False
    assert updated_state["risk_score"] == 0.0
    assert len(updated_state["laundering_reasons"]) == 0

def test_classify_high_value_transaction():
    # Thử nghiệm với giao dịch khủng (600 triệu VND -> vượt ngưỡng 500 triệu)
    state = create_initial_state(
        tx_hash="TX456", 
        amount_vnd=600_000_000.0,
        wallet_from="hashed_wallet_source_aaa",
        wallet_to="hashed_wallet_dest_bbb"
    )
    
    updated_state = classify_transaction(state)
    
    assert updated_state["is_suspicious"] is True
    assert updated_state["risk_score"] >= 0.7
    assert "vượt nguong quy dinh" in updated_state["laundering_reasons"][0]