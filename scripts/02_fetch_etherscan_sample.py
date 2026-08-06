"""
scripts/02_fetch_etherscan_sample.py -- Lấy dữ liệu ví mẫu từ Etherscan.
"""
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

def fetch_etherscan_data():
    api_key = os.getenv("ETHERSCAN_API_KEY")
    if not api_key:
        print("[LỖI] Chưa cấu hình ETHERSCAN_API_KEY trong file .env")
        return
        
    # Lấy thử giao dịch của một ví công khai (ví dụ ví sàn Binance)
    address = "0x28C6c06298d514Db089934071355E5743bf21d60" 
    url = f"https://api.etherscan.io/api?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=10&sort=asc&apikey={api_key}"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        output_file = "data/raw/etherscan/sample_txs.json"
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, "w") as f:
            json.dump(data, f, indent=4)
            
        print(f"[✓] Đã lưu dữ liệu Etherscan tại: {output_file} ({len(data.get('result', []))} bản ghi)")
    except Exception as e:
        print(f"[LỖI] Không thể kết nối Etherscan: {e}")

if __name__ == "__main__":
    fetch_etherscan_data()