"""
api/main.py -- Phần 10 (SPEC.md §1, §5, §6, §7, §10) + mở rộng cho FE HTML/JS thuần.

FastAPI Gateway. Có 3 nhóm:

1. `/screen-wallet` (giữ nguyên như bản gốc) -- tra cứu ví đơn lẻ,
   API-as-a-Service, KHÔNG chạy full LangGraph, KHÔNG cần đăng nhập.

2. `/api/pipeline/*` (thay thế vai trò của ui/app.py Streamlit) -- expose đúng
   luồng mà core/graph_builder.PipelineRun đang chạy. YÊU CẦU đăng nhập (xem
   mục 3) trừ `/api/auth/login` chính nó.

   Vì PipelineRun cần "sống" qua nhiều request (chạy xong -> chờ Approve/Reject
   ở 1 request khác), ta giữ instance trong bộ nhớ (dict RUNS), key = tx_hash.
   Cách này ĐÚNG với quy mô demo (1 process, 1 buổi báo cáo) -- KHÔNG dùng cho
   production nhiều instance/nhiều người dùng đồng thời (ghi rõ hạn chế này
   trong báo cáo, giống như PII_SALT cố định ở Privacy Layer).

3. `/api/auth/login` -- đăng nhập username/password (đọc từ biến môi trường
   AML_AUTH_USERNAME / AML_AUTH_PASSWORD, mặc định compliance/changeme123 --
   NÊN đổi qua biến môi trường trước khi demo cho người khác xem). Token cấp
   ra cũng chỉ sống trong RAM (dict TOKENS) -- cùng hạn chế như RUNS ở trên.

Không còn React/Vite/npm: `/` trả thẳng file `frontend_html/index.html`
(HTML + JS thuần, gọi fetch() vào các endpoint trên), `/static/*` phục vụ
app.js/styles.css đi kèm. Mở trình duyệt vào chính địa chỉ backend là xong,
không cần chạy thêm process nào khác.

Chạy thử:
    uvicorn api.main:app --reload --port 8000
    (chạy từ thư mục gốc digitalasset_guard/, để import agents.* / core.* đúng)
    rồi mở http://localhost:8000
"""
import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.privacy_layer import assert_no_raw_pii
from core.graph_builder import PipelineRun
from agents.kyc_verification import verify_kyc
from agents.transaction_classifier import analyze_transaction

app = FastAPI(
    title="DigitalAsset Guard -- API Gateway",
    description="API cho DigitalAsset Guard AI Copilot (screen-wallet + full pipeline).",
    version="0.3.0-MVP",
)

# =============================================================================
# --- Đăng nhập (username/password) -- bảo vệ nhóm /api/pipeline/* ---
# =============================================================================
# Demo-scale: 1 cặp user/pass đọc từ biến môi trường (đổi được, không hardcode
# lộ trong git), token cấp ra giữ trong RAM (dict TOKENS). Hạn chế giống RUNS:
# mất khi restart server, không hợp production nhiều instance. Đủ dùng cho 1
# buổi demo/nội bộ vài chuyên viên.
AUTH_USERS: Dict[str, str] = {
    os.environ.get("AML_AUTH_USERNAME", "nhanvien1"): os.environ.get("AML_AUTH_PASSWORD", "123456789"),
}
TOKENS: Dict[str, str] = {}  # token -> username


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    expected = AUTH_USERS.get(payload.username)
    if expected is None or not secrets.compare_digest(expected, payload.password):
        raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu.")
    token = secrets.token_urlsafe(32)
    TOKENS[token] = payload.username
    return LoginResponse(token=token, username=payload.username)


def require_auth(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Thiếu token đăng nhập.")
    token = authorization.removeprefix("Bearer ").strip()
    username = TOKENS.get(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc đã hết hạn (server có thể vừa restart) -- đăng nhập lại.")
    return username

# =============================================================================
# --- Endpoint gốc (Phần 10, mục 1): /screen-wallet -- KHÔNG thay đổi logic ---
# =============================================================================


class WalletScreenRequest(BaseModel):
    wallet_address: str = Field(..., description="Địa chỉ ví on-chain cần sàng lọc.")
    amount_vnd: float = Field(0, ge=0)


class WalletScreenResponse(BaseModel):
    wallet_address: str
    is_sanctioned: bool
    kyc_flags: List[str]
    risk_score_classifier: Optional[float] = None
    risk_level: str
    note: Optional[str] = None


def _build_minimal_state(wallet_address: str, amount_vnd: float) -> dict:
    state = {"wallet_from": wallet_address, "wallet_to": "", "amount_vnd": amount_vnd}
    assert_no_raw_pii(state)
    return state


@app.post("/screen-wallet", response_model=WalletScreenResponse)
def screen_wallet(payload: WalletScreenRequest) -> WalletScreenResponse:
    wallet_address = payload.wallet_address.strip()
    if not wallet_address:
        raise HTTPException(status_code=422, detail="wallet_address không được để trống.")

    state = _build_minimal_state(wallet_address, payload.amount_vnd)

    kyc_result = verify_kyc(state)
    kyc_flags = kyc_result.get("kyc_flags", [])
    is_sanctioned = any("match_ofac" in flag for flag in kyc_flags)

    risk_score: Optional[float] = None
    note: Optional[str] = None
    try:
        state["kyc_flags"] = kyc_flags
        scored_state = analyze_transaction(state)
        risk_score = scored_state.get("risk_score_classifier")
    except FileNotFoundError as e:
        note = f"Model XGBoost chưa sẵn sàng ({e}); chỉ trả kết quả sàng lọc danh sách trừng phạt."
    except Exception as e:
        note = f"Lỗi khi chấm điểm rủi ro sơ bộ: {e}"

    if risk_score is None:
        risk_level = "unknown"
    elif is_sanctioned or risk_score >= 0.7:
        risk_level = "high"
    elif risk_score >= 0.3:
        risk_level = "medium"
    else:
        risk_level = "low"

    return WalletScreenResponse(
        wallet_address=wallet_address,
        is_sanctioned=is_sanctioned,
        kyc_flags=kyc_flags,
        risk_score_classifier=risk_score,
        risk_level=risk_level,
        note=note,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# =============================================================================
# --- MỚI: /api/pipeline/* -- thay thế vai trò của ui/app.py (Streamlit) ---
# =============================================================================

# Lưu các PipelineRun đang "chờ duyệt" trong bộ nhớ process, key = tx_hash.
# Hạn chế đã biết: mất khi restart server, không an toàn multi-worker -- chấp
# nhận được cho quy mô demo (xem docstring đầu file).
RUNS: Dict[str, PipelineRun] = {}


class RawTransactionRequest(BaseModel):
    """Khớp đúng các trường form nhập giao dịch của ui/app.py cũ."""

    tx_hash: Optional[str] = Field(None, description="Để trống để hệ thống tự sinh.")
    wallet_from: str
    wallet_to: str
    amount_vnd: float = Field(..., ge=0)
    fullname: str = Field(..., description="PII gốc -- sẽ bị băm ngay tại Privacy Layer, không lưu lại.")
    id_number: str
    account_number: str


class PipelineStepView(BaseModel):
    step_key: str
    label: str
    snapshot: Dict[str, Any]


class PipelineRunResponse(BaseModel):
    tx_hash: Optional[str] = Field(
        None,
        description=(
            "None khi giao dịch bị bỏ qua (skipped=True) và người dùng không tự nhập "
            "tx_hash -- pipeline dưới ngưỡng báo cáo không sinh ra tx_hash thật."
        ),
    )
    skipped: bool = Field(
        False,
        description=(
            "True nếu giao dịch dưới ngưỡng báo cáo (REPORT_THRESHOLD_VND) -- đây là kết "
            "quả nghiệp vụ HỢP LỆ (không cần soi AML), KHÔNG phải lỗi hệ thống. Khi True, "
            "không có phiên nào được lưu để Approve/Reject/tải báo cáo/chat."
        ),
    )
    steps: List[PipelineStepView]
    state: Dict[str, Any]


def _serialize_run(
    tx_hash: Optional[str], run: PipelineRun, steps: List[PipelineStepView], *, skipped: bool = False
) -> PipelineRunResponse:
    return PipelineRunResponse(tx_hash=tx_hash, skipped=skipped, steps=steps, state=run.state)


@app.post("/api/pipeline/run", response_model=PipelineRunResponse)
def run_pipeline(payload: RawTransactionRequest, _user: str = Depends(require_auth)) -> PipelineRunResponse:
    """
    Chạy toàn bộ pipeline (Webhook -> Privacy Layer -> 5 Assistants -> điểm dừng
    chờ duyệt) TRONG 1 REQUEST đồng bộ -- giống hệt cách demo_run.py/ui/app.py
    cũ đang làm, chỉ khác là trả kết quả về JSON cho FE thay vì in ra
    console/Streamlit. Không cần polling/WebSocket vì pipeline chạy đủ nhanh
    cho mục đích demo.
    """
    raw_transaction: Dict[str, Any] = payload.model_dump()

    run = PipelineRun()
    steps: List[PipelineStepView] = []
    try:
        for step_key, label, snapshot in run.steps(raw_transaction):
            steps.append(PipelineStepView(step_key=step_key, label=label, snapshot=snapshot))
            if step_key == "webhook" and snapshot.get("skipped"):
                # Giao dịch dưới ngưỡng báo cáo -- KẾT QUẢ HỢP LỆ, không phải lỗi.
                # run.state ở đây chỉ có {"skipped": True, "amount_vnd": ...}, KHÔNG có
                # tx_hash (xem core/graph_builder.py::PipelineRun.steps). Không được ép
                # coi thiếu tx_hash là lỗi 500 -- chỉ dùng tx_hash người dùng tự nhập (nếu
                # có) để hiển thị, và KHÔNG lưu vào RUNS vì không có gì để Approve/Reject/
                # tải báo cáo/chat (final_risk_score, report_path đều không tồn tại).
                return _serialize_run(payload.tx_hash, run, steps, skipped=True)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Lỗi cấu hình: {e}")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"Thiếu dữ liệu/model cần thiết cho pipeline: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi không mong muốn khi chạy pipeline: {e}")

    tx_hash = run.state.get("tx_hash") or payload.tx_hash
    if not tx_hash:
        raise HTTPException(status_code=500, detail="Pipeline không sinh ra tx_hash -- không thể lưu phiên chờ duyệt.")

    RUNS[tx_hash] = run
    return _serialize_run(tx_hash, run, steps)


def _get_run_or_404(tx_hash: str) -> PipelineRun:
    run = RUNS.get(tx_hash)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy phiên chạy cho tx_hash={tx_hash} (có thể server đã restart).",
        )
    return run


@app.get("/api/pipeline/{tx_hash}/state")
def get_pipeline_state(tx_hash: str, _user: str = Depends(require_auth)) -> Dict[str, Any]:
    run = _get_run_or_404(tx_hash)
    return run.state


class DecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")


@app.post("/api/pipeline/{tx_hash}/decision")
def submit_decision(tx_hash: str, payload: DecisionRequest, _user: str = Depends(require_auth)) -> Dict[str, Any]:
    """Approve/Reject -- tương đương 2 nút bấm trong ui/app.py cũ."""
    run = _get_run_or_404(tx_hash)
    updated_state = run.resume(payload.decision)
    return updated_state


@app.get("/api/pipeline/{tx_hash}/report")
def download_report(tx_hash: str, token: Optional[str] = None) -> FileResponse:
    # Thẻ <a href download> không gửi được header Authorization, nên endpoint
    # này nhận token qua query string (?token=...) thay vì Header. Vẫn kiểm
    # tra đúng như require_auth, chỉ khác nguồn lấy token.
    username = TOKENS.get(token or "")
    if username is None:
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai token -- đăng nhập lại rồi tải lại trang.")
    run = _get_run_or_404(tx_hash)
    report_path = run.state.get("report_path")
    if not report_path or not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Chưa có file báo cáo STR cho giao dịch này.")
    return FileResponse(
        report_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(report_path),
    )


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    answer: str


@app.post("/api/pipeline/{tx_hash}/chat", response_model=ChatResponse)
def chat_about_transaction(tx_hash: str, payload: ChatRequest, _user: str = Depends(require_auth)) -> ChatResponse:
    """
    Phần 12.1 -- chatbot TỔNG HỢP/GIẢI THÍCH/KHUYẾN NGHỊ dựa trên context đã
    tính sẵn, y hệt logic trong ui/app.py::_render_chat_panel, chỉ chuyển thành
    endpoint để FE React gọi. 1 câu hỏi = 1 lần gọi API, không giữ lịch sử hội
    thoại phía server (khớp yêu cầu trong THAY_DOI_SO_VOI_BAN_GOC.md §12.1).
    """
    import json as _json

    from agents.regulation_rag import call_llm_api

    run = _get_run_or_404(tx_hash)
    state = run.state

    if state.get("final_risk_score") is None:
        raise HTTPException(status_code=400, detail="Pipeline chưa chạy xong tới bước tính final_risk_score.")

    context = {
        "final_risk_score": state.get("final_risk_score"),
        "risk_breakdown": state.get("risk_breakdown"),
        "top_features": state.get("top_features"),
        "hop_distance_to_blacklist": state.get("hop_distance_to_blacklist"),
        "fan_out": state.get("fan_out"),
        "community_id": state.get("community_id"),
        "legal_citations": state.get("legal_citations"),
        "kyc_flags": state.get("kyc_flags"),
    }

    prompt = f"""Bạn là trợ lý giải thích cho chuyên viên AML. Dưới đây là dữ liệu đã được
hệ thống tính toán sẵn cho 1 giao dịch (định dạng JSON):

{_json.dumps(context, ensure_ascii=False, indent=2, default=str)}

Câu hỏi của chuyên viên: "{payload.question}"

Hãy tổng hợp, giải thích bằng ngôn ngữ nghiệp vụ dễ hiểu, và đưa khuyến nghị hành động cụ thể
nếu câu hỏi yêu cầu (VD: có nên lập STR không). CHỈ được dùng dữ liệu trong JSON trên, không suy
diễn hay bịa thêm số liệu/căn cứ pháp lý nào khác. Nếu câu hỏi hỏi về 1 thực thể/địa chỉ/dịch vụ
KHÔNG có trong dữ liệu trên (VD: không có trong kyc_flags), phải trả lời rõ "chưa được kiểm tra
trong phạm vi dữ liệu hiện có" -- TUYỆT ĐỐI không khẳng định "không liên quan" nếu chưa thực sự
được kiểm tra, vì đây là hồ sơ AML và 1 câu trả lời sai kiểu này là 1 false negative thật."""

    try:
        answer = call_llm_api(prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi gọi LLM: {e}")

    return ChatResponse(answer=answer)


# =============================================================================
# --- Phục vụ giao diện HTML/JS thuần (thay thế Vite dev server) ---
# =============================================================================
# Đặt SAU tất cả route /api/* ở trên -- FastAPI khớp route theo thứ tự khai
# báo, nên mount "/" ở cuối cùng không che mất các route API phía trên.
_HTML_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend_html")


@app.get("/")
def serve_ui() -> FileResponse:
    return FileResponse(os.path.join(_HTML_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=_HTML_DIR), name="static")

ALLOWED_ORIGINS = ["*"]  # cùng origin (FastAPI tự phục vụ HTML) -- CORS gần như không cần nữa
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
