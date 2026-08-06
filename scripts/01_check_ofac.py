"""
scripts/01_check_ofac.py -- Đọc file SDN XML từ OFAC và trích xuất địa chỉ ví.
"""
import xml.etree.ElementTree as ET
import os

def parse_ofac_xml():
    input_file = "data/raw/ofac/sdn.xml"
    output_dir = "data/processed"
    output_file = os.path.join(output_dir, "sample_ofac_wallet.txt")

    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(input_file):
        print(f"[CẢNH BÁO] Chưa có file {input_file}. Đang tạo danh sách ví đen mẫu...")
        with open(output_file, "w") as f:
            f.write("1a1zp1ep5qgefi2dmptftl5slmv7divfna\n")
        return

    wallets = set()
    n_entries = 0

    # SDN XML dùng namespace mặc định
    # (xmlns="https://sanctionslistservice.ofac.treas.gov/..."), nên tag thật
    # sẽ có dạng "{namespace}idType" -- phải bỏ phần "{...}" mới so sánh được.
    context = ET.iterparse(input_file, events=("end",))
    current_id_type = None

    for event, elem in context:
        tag = elem.tag.split("}")[-1]

        if tag == "sdnEntry":
            n_entries += 1

        if tag == "idType":
            current_id_type = (elem.text or "").strip()

        elif tag == "idNumber":
            if current_id_type and "Digital Currency Address" in current_id_type:
                wallet = (elem.text or "").strip()
                if wallet:
                    wallets.add(wallet.lower())
            current_id_type = None

        elem.clear()

    with open(output_file, "w") as f:
        for w in sorted(wallets):
            f.write(f"{w}\n")

    coverage_pct = (len(wallets) / n_entries * 100) if n_entries else 0
    print(f"[✓] Đã quét {n_entries} entry trong SDN.")
    print(f"[✓] Tìm thấy {len(wallets)} địa chỉ ví crypto duy nhất "
          f"({coverage_pct:.2f}% entry có ít nhất 1 ví).")
    print(f"[✓] Đã trích xuất danh sách ví đen ra: {output_file}")

if __name__ == "__main__":
    parse_ofac_xml()