"""
core/config.py -- Cấu hình nền tảng.
Chỉ chứa các thông số môi trường cơ bản theo Phần 1 (SPEC.md mục 1).
Không thêm biến của các phần sau (API key, trọng số risk score, Neo4j...)
cho tới khi build tới đúng phần đó.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Thư mục gốc dự án ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Đường dẫn thư mục (dùng Path để join an toàn ở các phần sau) ---
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
REPORTS_OUTPUT_DIR = BASE_DIR / "reports" / "output"

# --- Ngưỡng báo cáo (Thông tư 27/2025/TT-NHNN) ---
REPORT_THRESHOLD_VND = 500_000_000
REPORT_THRESHOLD_USD = 1_000

# --- Cờ chạy mô phỏng (NetworkX + mock data thay vì Neo4j/API thật) ---
DEMO_MODE = os.getenv("DEMO_MODE", "True").lower() in ("true", "1", "yes")


def ensure_dirs() -> None:
    """Tạo các thư mục output nếu chưa tồn tại."""
    for d in (MODELS_DIR, REPORTS_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    ensure_dirs()
    print("BASE_DIR:", BASE_DIR)
    print("DEMO_MODE:", DEMO_MODE)