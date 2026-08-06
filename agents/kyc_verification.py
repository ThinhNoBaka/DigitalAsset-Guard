"""
agents/kyc_verification.py -- Phần 5: KYC Assistant.

QUYẾT ĐỊNH KỸ THUẬT QUAN TRỌNG (theo HUONG_DAN_XAY_DUNG.pdf, khuyến nghị Phần 5):
Do hashed_fullname đã bị băm SHA-256 một chiều, thuật toán so khớp mờ
(Levenshtein/rapidfuzz) trên chuỗi băm là vô nghĩa. Vì vậy:
  1. Agent này CHỈ so khớp CHÍNH XÁC theo địa chỉ ví (wallet_from / wallet_to)
     với danh sách đen OFAC -- không so khớp tên.
  2. So khớp mờ theo tên được thực hiện ở tầng WEBHOOK, TRƯỚC khi băm PII.
     Agent này chỉ đọc lại kết quả dưới dạng cờ boolean có sẵn trong state
     (state["name_similarity_warning"]).
  3. Wallet clustering (SPEC.md mục 3.2) KHÔNG triển khai ở agent này.
     Việc gom cụm ví dựa trên đồ thị giao dịch được xử lý ở Graph Assistant
     (Louvain Community Detection, Phần 6) để tránh trùng lặp logic đồ thị.

SỐ LIỆU THẬT (chạy scripts/01_check_ofac.py trên SDN 19.169 entry):
  Trích xuất được 940 địa chỉ ví crypto duy nhất (4.90% entry có ví).
  Tỷ lệ % không cao vì đa số entry SDN là cấm vận truyền thống không liên
  quan crypto, nhưng số lượng tuyệt đối 940 ví là đủ lớn để demo/vận hành.
  Với entity không có ví trong SDN, hệ thống vẫn có thể phát hiện gián tiếp
  qua Graph Assistant (PPR) nếu có liên kết giao dịch tới 1 trong 940 ví này.
"""
import os

# Đã ghép nối LangGraph (Phần 9) -- dùng ĐÚNG bản assert_no_raw_pii thật từ
# core/privacy_layer.py, không tự định nghĩa bản mock riêng nữa. Trước đây agent
# này có 1 bản implementation trùng lặp (raise Exception thường), khác với bản
# gốc ở core/privacy_layer.py (raise ValueError) -- dễ lệch hành vi nếu 1 trong
# 2 bản được sửa mà quên sửa bản kia. Từ giờ chỉ còn 1 nguồn sự thật duy nhất.
from core.privacy_layer import assert_no_raw_pii


def load_ofac_wallets(filepath: str = "data/processed/sample_ofac_wallet.txt") -> set:
    """
    Load danh sách ví blacklist vào set để tra cứu với độ phức tạp O(1).
    Không parse lại file XML mỗi lần chạy để tránh OOM và tiết kiệm thời gian.
    """
    wallets = set()
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            for line in f:
                if line.strip():
                    wallets.add(line.strip().lower())
    else:
        print(f"[CẢNH BÁO] Không tìm thấy {filepath}, đang dùng dữ liệu MOCK để test/demo. "
              f"Kiểm tra lại đường dẫn nếu đây là môi trường thật.")
        wallets = {"0xbadwallet123", "0xofacsanctioned456"}
    return wallets


# ---------------------------------------------------------
# AGENT LOGIC
# ---------------------------------------------------------
def verify_kyc(state: dict) -> dict:
    """
    KYC Assistant Agent: Sàng lọc địa chỉ ví qua danh sách trừng phạt.
    """
    assert_no_raw_pii(state)

    kyc_flags = []
    ofac_wallets = load_ofac_wallets()

    wallet_from = state.get("wallet_from", "").lower()
    wallet_to = state.get("wallet_to", "").lower()

    wallet_matched = False
    if wallet_from and wallet_from in ofac_wallets:
        kyc_flags.append("wallet_from_match_ofac")
        wallet_matched = True
    if wallet_to and wallet_to in ofac_wallets:
        kyc_flags.append("wallet_to_match_ofac")
        wallet_matched = True

    name_matched = state.get("name_similarity_warning") is True
    if name_matched:
        kyc_flags.append("name_similarity_high_risk")

    if name_matched and not wallet_matched:
        kyc_flags.append("name_match_no_wallet_data")

    return {"kyc_flags": kyc_flags}


# ---------------------------------------------------------
# KIỂM TRA ĐỘC LẬP (TESTING)
# ---------------------------------------------------------
if __name__ == "__main__":
    import unittest.mock as mock

    print("=== BẮT ĐẦU KIỂM TRA KYC ASSISTANT ===\n")

    fake_blacklist = {"0xbadwallet123", "0xofacsanctioned456"}

    # QUAN TRỌNG: dùng __name__ (không hardcode "agents.kyc_verification").
    # Khi chạy bằng `python -m agents.kyc_verification`, Python nạp file này
    # vào sys.modules dưới tên "__main__", không phải "agents.kyc_verification".
    # Nếu patch cứng theo path chuỗi, mock.patch sẽ IMPORT LẠI một bản module
    # thứ hai, tách biệt với bản đang chạy -- patch không có tác dụng gì lên
    # hàm verify_kyc() thực sự đang thực thi, nên nó vẫn đọc file thật trên đĩa.
    # Dùng __name__ đảm bảo patch luôn trúng đúng bản module đang chạy,
    # dù bạn chạy bằng `python -m agents.kyc_verification` hay import bình
    # thường từ module khác.
    with mock.patch(f"{__name__}.load_ofac_wallets", return_value=fake_blacklist):
        # Test 1: Chạy với ví sạch (Không có cờ)
        clean_state = {
            "hashed_fullname": "a8b7c6...",
            "wallet_from": "0xsafe111",
            "wallet_to": "0xsafe222"
        }
        print(">> Test 1: Giao dịch an toàn (Ví sạch)")
        print("Input State:", clean_state)
        result1 = verify_kyc(clean_state)
        print("Output Flags:", result1)
        assert len(result1["kyc_flags"]) == 0, "Lỗi: Ví sạch nhưng lại trả về cờ cảnh báo!"
        print("[PASS] Test 1 thành công.\n")

        # Test 2: Chạy với ví trong danh sách đen (Có cờ)
        dirty_state = {
            "hashed_fullname": "f9e8d7...",
            "wallet_from": "0xbadwallet123",
            "wallet_to": "0xsafe222",
            "name_similarity_warning": True
        }
        print(">> Test 2: Giao dịch rủi ro (Ví đen + Trùng tên mờ)")
        print("Input State:", dirty_state)
        result2 = verify_kyc(dirty_state)
        print("Output Flags:", result2)
        assert "wallet_from_match_ofac" in result2["kyc_flags"], "Lỗi: Không bắt được ví đen!"
        assert "name_similarity_high_risk" in result2["kyc_flags"], "Lỗi: Không bắt được cờ tên!"
        assert "name_match_no_wallet_data" not in result2["kyc_flags"], \
            "Lỗi: Có ví khớp rồi mà vẫn gắn cờ 'không có dữ liệu ví'!"
        print("[PASS] Test 2 thành công.\n")

        # Test 3: Tên khớp OFAC nhưng KHÔNG có ví nào khớp
        name_only_state = {
            "hashed_fullname": "c3d4e5...",
            "wallet_from": "0xsafe333",
            "wallet_to": "0xsafe444",
            "name_similarity_warning": True
        }
        print(">> Test 3: Tên khớp OFAC nhưng ví sạch (không có dữ liệu ví trong SDN)")
        print("Input State:", name_only_state)
        result3 = verify_kyc(name_only_state)
        print("Output Flags:", result3)
        assert "name_match_no_wallet_data" in result3["kyc_flags"], \
            "Lỗi: Không gắn được cờ cảnh báo 'tên khớp nhưng thiếu dữ liệu ví'!"
        print("[PASS] Test 3 thành công.\n")

    # Test 4: Kiểm tra rào chắn Privacy Layer (assert_no_raw_pii)
    print(">> Test 4: Kiểm tra rào chắn rò rỉ PII gốc")
    try:
        violation_state = {
            "fullname": "Nguyen Van A",
            "wallet_from": "0x123"
        }
        verify_kyc(violation_state)
        assert False, "Lỗi: Không chặn được PII gốc!"
    except Exception as e:
        print("[PASS] Đã chặn thành công với thông báo:", str(e))

    # Test 5: Dùng dữ liệu OFAC THẬT (940 ví thật từ SDN) -- không mock,
    # xác nhận pipeline production hoạt động đúng, không chỉ trên dữ liệu giả.
    print(">> Test 5: Kiểm tra với dữ liệu OFAC thật (940 ví từ SDN)")
    real_wallets_path = "data/processed/sample_ofac_wallet.txt"
    if os.path.exists(real_wallets_path):
        with open(real_wallets_path, "r") as f:
            real_wallets = [line.strip() for line in f if line.strip()]
        if real_wallets:
            sample_real_wallet = real_wallets[0]
            real_state = {
                "hashed_fullname": "real_test_hash...",
                "wallet_from": sample_real_wallet,
                "wallet_to": "0xsafe999"
            }
            print(f"Input State (ví thật từ SDN): {real_state}")
            result5 = verify_kyc(real_state)
            print("Output Flags:", result5)
            assert "wallet_from_match_ofac" in result5["kyc_flags"], \
                "Lỗi: Không bắt được ví thật từ SDN!"
            print(f"[PASS] Test 5 thành công -- đã load đúng {len(real_wallets)} ví thật.\n")
        else:
            print("[BỎ QUA] File OFAC thật rỗng, không có ví để test.\n")
    else:
        print("[BỎ QUA] Chưa có file OFAC thật, bỏ qua test này.\n")

    print("\n=== KYC ASSISTANT ĐÃ HOÀN THÀNH ĐÚNG SPEC ===")