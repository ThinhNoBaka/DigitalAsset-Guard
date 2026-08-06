"""
scripts/00_healthcheck.py -- Kiểm tra toàn diện sự tồn tại của file và API
"""
import os
from dotenv import load_dotenv

load_dotenv()

def check_data_health():
    print("=== KIỂM TRA SỨC KHỎE DỮ LIỆU (PHẦN 3) ===")
    all_passed = True
    
    # 1. Kiểm tra biến môi trường
    if not os.getenv("ETHERSCAN_API_KEY"):
        print("[FAIL] Chưa có ETHERSCAN_API_KEY trong .env")
        all_passed = False
    else:
        print("[PASS] Đã tìm thấy ETHERSCAN_API_KEY")

    # 2. Kiểm tra các thư mục/file bắt buộc
    required_paths = [
        "data/raw/elliptic/",
        "data/raw/ofac/",
        "data/raw/etherscan/",
        "data/mock/customers.json",
        "data/legal_docs/thong_tu_27_2025.txt",
        "data/legal_docs/thong_tu_32_2026.txt"
    ]
    
    for path in required_paths:
        if os.path.exists(path):
            print(f"[PASS] {path} - Tồn tại.")
        else:
            print(f"[FAIL] {path} - Không tìm thấy!")
            all_passed = False
            
    if all_passed:
        print("\n[SUCCESS] Cấu trúc chuẩn bị dữ liệu Phần 3 đã đầy đủ!")
    else:
        print("\n[WARNING] Vẫn thiếu thành phần dữ liệu, vui lòng chạy các script 01, 02, 03, 05 để bổ sung.")

if __name__ == "__main__":
    check_data_health()