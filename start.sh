#!/usr/bin/env bash
# Chạy: bash start.sh
# (từ thư mục gốc repo digitalasset_guard, sau khi đã giải nén nội dung file
# zip này VÀO NGAY thư mục gốc repo -- tức main.py, frontend_html/ nằm cạnh
# api/, core/, agents/ của bạn)
#
# KHÔNG CẦN Node.js/npm nữa -- chỉ cần Python. Script này:
#   1. Copy main.py (bản có đăng nhập) đè lên api/main.py
#   2. Cài Python deps cần thiết (fastapi, uvicorn) nếu thiếu
#   3. Chạy server, mở sẵn trình duyệt tới http://localhost:8000
set -e
cd "$(dirname "$0")"

if [ ! -d "api" ] || [ ! -d "core" ]; then
  echo "LỖI: không thấy thư mục api/ hoặc core/ ở đây."
  echo "-> Giải nén file zip này TRỰC TIẾP vào thư mục gốc repo digitalasset_guard"
  echo "   (chỗ có sẵn api/, core/, agents/), rồi chạy lại: bash start.sh"
  exit 1
fi

echo "[1/3] Copy main.py -> api/main.py"
cp main.py api/main.py

echo "[2/3] Kiểm tra/cài Python deps (fastapi, uvicorn)"
PIP_CMD="pip"
command -v pip >/dev/null 2>&1 || PIP_CMD="pip3"
python3 -c "import fastapi, uvicorn" 2>/dev/null || "$PIP_CMD" install fastapi uvicorn

echo "[3/3] Khởi động server tại http://localhost:8000"
echo ""
echo "=================================================="
echo " Tài khoản đăng nhập mặc định: compliance / changeme123"
echo " Đổi bằng cách export AML_AUTH_USERNAME / AML_AUTH_PASSWORD trước khi chạy."
echo " Mở trình duyệt: http://localhost:8000"
echo " Bấm Ctrl+C để tắt."
echo "=================================================="
echo ""

uvicorn api.main:app --reload --port 8000
