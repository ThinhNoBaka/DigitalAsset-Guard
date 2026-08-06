"""
scripts/05_gen_mock_data.py -- Tạo dữ liệu khách hàng giả lập thô (chứa PII) để test hệ thống.
"""
import json
import os

def generate_mock_customers():
    # Dữ liệu khách hàng thô (PII) giả lập
    mock_data = [
        {
            "fullname": "Nguyen Van A",
            "id_number": "012345678901",
            "account_number": "1000000001",
            "linked_wallet": "hashed_wallet_good_user"
        },
        {
            "fullname": "Tran Thi B",
            "id_number": "098765432109",
            "account_number": "2000000002",
            "linked_wallet": "hashed_wallet_source_aaa"
        }
    ]
    
    # Đảm bảo thư mục tồn tại
    os.makedirs("data/mock", exist_ok=True)
    
    # Ghi ra file JSON
    output_path = "data/mock/customers.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(mock_data, f, ensure_ascii=False, indent=4)
    
    print(f"[✓] Đã tạo dữ liệu khách hàng giả lập thành công tại: {output_path}")

if __name__ == "__main__":
    generate_mock_customers()