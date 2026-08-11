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

# --- Graph data source — selector DUY NHẤT (xem core/graph_provider.py) ---
# GRAPH_SOURCE quyết định provider cho Graph Agent:
#   "mock"  = demo — MockGraphProvider đọc data/mock/scenario_*.json (cung cấp
#             DATA thật, thuật toán chạy thật — KHÔNG fake PPR/hop/path)
#   "neo4j" = production — Neo4jGraphProvider, graph_aml query Neo4j/GDS gốc
# Nếu GRAPH_SOURCE trống/không hợp lệ, fallback theo DEMO_MODE (true -> mock,
# false -> neo4j) để tương thích cấu hình cũ.
def resolve_graph_source() -> str:
    """Trả về "mock" hoặc "neo4j" — selector duy nhất cho graph data source."""
    source = os.getenv("GRAPH_SOURCE", "").strip().lower()
    if source in ("mock", "neo4j"):
        return source
    return "mock" if DEMO_MODE else "neo4j"


# --- Thông tin đối tượng báo cáo STR (Phần I Mẫu 04) ---
# Đây là config TĨNH của hệ thống (tên tổ chức báo cáo, người chịu trách
# nhiệm...) — KHÔNG phải PII khách hàng, KHÔNG đi qua Privacy Layer.
# Được dùng bởi agents/alert_report.py khi sinh STR .docx.
# Nếu biến env thiếu, trả chuỗi rỗng -> alert_report in placeholder
# "[CHƯA CẤU HÌNH]" như trước (không crash).
def build_report_entity_config() -> dict:
    """
    Build dict thông tin đối tượng báo cáo từ env, đúng key alert_report đọc.

    KHÔNG hard-code giá trị — nếu muốn thay đổi, sửa .env.
    """
    return {
        "reporting_entity_name": os.getenv("REPORTING_ENTITY_NAME", ""),
        "reporting_entity_code": os.getenv("REPORTING_ENTITY_CODE", ""),
        "reporting_entity_address": os.getenv("REPORTING_ENTITY_ADDRESS", ""),
        "reporting_entity_phone": os.getenv("REPORTING_ENTITY_PHONE", ""),
        "reporting_entity_email": os.getenv("REPORTING_ENTITY_EMAIL", ""),
        "aml_responsible_person": os.getenv("AML_RESPONSIBLE_PERSON", ""),
        "aml_responsible_position": os.getenv("AML_RESPONSIBLE_POSITION", ""),
        "reporter_name": os.getenv("REPORTER_NAME", ""),
    }


def ensure_dirs() -> None:
    """Tạo các thư mục output nếu chưa tồn tại."""
    for d in (MODELS_DIR, REPORTS_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    ensure_dirs()
    print("BASE_DIR:", BASE_DIR)
    print("DEMO_MODE:", DEMO_MODE)