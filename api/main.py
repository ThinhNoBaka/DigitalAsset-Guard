"""
api/main.py
FastAPI Gateway cho DigitalAsset Guard.

SPEC_v2 pipeline:

Privacy Layer
    ↓
Transaction Assistant
    ↓
Graph Assistant
    ↓
Sanctions Assistant
    ↓
Decision Engine
    ↓
    ├── PASS → END
    │
    └── REPORT → RAG → Report → Human Checkpoint

FastAPI KHÔNG tự điều phối agent.
Toàn bộ orchestration phải đi qua core.graph_builder.PipelineRun.
"""

import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.graph_builder import PipelineRun
from core.privacy_layer import assert_no_raw_pii
from agents.kyc_verification import verify_kyc
from agents.transaction_classifier import analyze_transaction
from agents.decision_engine import (
    _load_classifier_threshold,
    _CLASSIFIER_MEDIUM_RATIO,
)


# =============================================================================
# APP
# =============================================================================

app = FastAPI(
    title="DigitalAsset Guard — API Gateway",
    description="API Gateway cho DigitalAsset Guard AI Copilot.",
    version="0.4.0-SPEC-v2",
)


# =============================================================================
# AUTH
# =============================================================================

AUTH_USERS: Dict[str, str] = {
    os.environ.get("AML_AUTH_USERNAME", "nhanvien1"):
        os.environ.get("AML_AUTH_PASSWORD", "123456789"),
}

TOKENS: Dict[str, str] = {}


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    expected = AUTH_USERS.get(payload.username)

    if expected is None or not secrets.compare_digest(
        expected,
        payload.password,
    ):
        raise HTTPException(
            status_code=401,
            detail="Sai tài khoản hoặc mật khẩu.",
        )

    token = secrets.token_urlsafe(32)
    TOKENS[token] = payload.username

    return LoginResponse(
        token=token,
        username=payload.username,
    )


def require_auth(
    authorization: Optional[str] = Header(None),
) -> str:

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Thiếu token đăng nhập.",
        )

    token = authorization.removeprefix("Bearer ").strip()

    username = TOKENS.get(token)

    if username is None:
        raise HTTPException(
            status_code=401,
            detail="Token không hợp lệ hoặc server đã restart.",
        )

    return username


# =============================================================================
# SCREEN WALLET
# =============================================================================

class WalletScreenRequest(BaseModel):
    wallet_address: str = Field(
        ...,
        description="Địa chỉ ví on-chain cần sàng lọc.",
    )
    amount_vnd: float = Field(
        0,
        ge=0,
    )


class WalletScreenResponse(BaseModel):
    wallet_address: str
    is_sanctioned: bool
    sanction_result: Dict[str, Any]
    classifier_score: Optional[float] = None
    risk_level: str
    note: Optional[str] = None


def _build_minimal_state(
    wallet_address: str,
    amount_vnd: float,
) -> dict:

    state = {
        "wallet_from": wallet_address,
        "wallet_to": "",
        "amount_vnd": amount_vnd,
    }

    assert_no_raw_pii(state)

    return state


@app.post(
    "/screen-wallet",
    response_model=WalletScreenResponse,
)
def screen_wallet(
    payload: WalletScreenRequest,
) -> WalletScreenResponse:

    wallet_address = payload.wallet_address.strip()

    if not wallet_address:
        raise HTTPException(
            status_code=422,
            detail="wallet_address không được để trống.",
        )

    state = _build_minimal_state(
        wallet_address,
        payload.amount_vnd,
    )

    # Sanctions Assistant.
    sanctions_state = verify_kyc(state)

    sanction_result = sanctions_state.get(
        "sanction_result",
        {
            "is_match": False,
            "matched_wallet": None,
            "source": "OFAC SDN",
            "match_type": None,
            "program": None,
        },
    )

    is_sanctioned = sanction_result.get(
        "is_match",
        False,
    )

    # Transaction Assistant.
    classifier_score: Optional[float] = None
    note: Optional[str] = None

    try:
        scored_state = analyze_transaction(state)
        classifier_score = scored_state.get("classifier_score")

    except FileNotFoundError as exc:
        note = (
            f"Model XGBoost chưa sẵn sàng ({exc}); "
            "chỉ trả kết quả sanctions screening."
        )

    except Exception as exc:
        note = (
            f"Lỗi khi chấm điểm classifier: {exc}"
        )

    # Đây chỉ là mức hiển thị của endpoint /screen-wallet.
    # Không phải Decision Engine của SPEC_v2.
    #
    # QUAN TRỌNG — TRƯỚC ĐÂY hardcode 0.7/0.3 ở đây, là 1 nguồn quyết định
    # riêng đá nhau với Decision Engine mới (agents/decision_engine.py dùng θ
    # calibrate bằng precision-recall trên Elliptic). Từ nay dùng ĐÚNG θ:
    #     - high   : classifier_score >= θ            (khớp Rule 3 của DE)
    #     - medium : θ*0.6 <= classifier_score < θ    (khớp Rule 5 "medium"
    #               tín hiệu classifier — cùng hằng số _CLASSIFIER_MEDIUM_RATIO)
    #     - low    : còn lại
    classifier_threshold = _load_classifier_threshold()
    classifier_medium_threshold = classifier_threshold * _CLASSIFIER_MEDIUM_RATIO

    if is_sanctioned:
        risk_level = "high"
    elif classifier_score is None:
        risk_level = "unknown"
    elif classifier_score >= classifier_threshold:
        risk_level = "high"
    elif classifier_score >= classifier_medium_threshold:
        risk_level = "medium"
    else:
        risk_level = "low"

    return WalletScreenResponse(
        wallet_address=wallet_address,
        is_sanctioned=is_sanctioned,
        sanction_result=sanction_result,
        classifier_score=classifier_score,
        risk_level=risk_level,
        note=note,
    )


# =============================================================================
# HEALTH
# =============================================================================

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
    }


# =============================================================================
# AUDIT LOG
# =============================================================================

_AUDIT_LOG_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "logs",
        "audit_trail.log",
    )
)


class AuditLogResponse(BaseModel):
    lines: List[str]


@app.get(
    "/logs",
    response_model=AuditLogResponse,
)
def get_audit_logs(
    n: int = 100,
    _user: str = Depends(require_auth),
) -> AuditLogResponse:

    if n <= 0 or n > 1000:
        raise HTTPException(
            status_code=422,
            detail="Tham số n phải trong khoảng 1-1000.",
        )

    if not os.path.exists(_AUDIT_LOG_PATH):
        return AuditLogResponse(lines=[])

    with open(
        _AUDIT_LOG_PATH,
        "r",
        encoding="utf-8",
    ) as file:

        lines = file.readlines()

    return AuditLogResponse(
        lines=[
            line.rstrip("\n")
            for line in lines[-n:]
        ]
    )


# =============================================================================
# PIPELINE
# =============================================================================

RUNS: Dict[str, PipelineRun] = {}


class RawTransactionRequest(BaseModel):
    """
    Input giao dịch trước Privacy Layer.

    PII chỉ tồn tại ở request boundary và được chuyển vào
    Privacy Layer để hashing.
    """

    tx_hash: Optional[str] = None

    wallet_from: str
    wallet_to: str

    amount_vnd: float = Field(
        ...,
        ge=0,
    )

    fullname: str
    id_number: str
    account_number: str


class PipelineRunResponse(BaseModel):
    tx_hash: Optional[str]
    state: Dict[str, Any]


# =============================================================================
# BUILD INITIAL STATE
# =============================================================================

def _build_initial_state(
    payload: RawTransactionRequest,
) -> Dict[str, Any]:
    """
    Tạo AMLState đầu vào cho PipelineRun.

    Không thực hiện classifier / graph / sanctions / decision ở đây.
    Những việc đó thuộc LangGraph.
    """

    tx_hash = payload.tx_hash or secrets.token_hex(16)

    state: Dict[str, Any] = {
        "tx_hash": tx_hash,

        "wallet_from": payload.wallet_from,
        "wallet_to": payload.wallet_to,
        "amount_vnd": payload.amount_vnd,

        # Raw PII chỉ đi vào Privacy Layer.
        "fullname": payload.fullname,
        "id_number": payload.id_number,
        "account_number": payload.account_number,

        # -----------------------------------------------------------------
        # SPEC_v2 fields
        # -----------------------------------------------------------------

        "classifier_score": None,
        "graph_score": None,

        "sanction_result": {
            "is_match": False,
            "matched_wallet": None,
            "source": "OFAC SDN",
            "match_type": None,
            "program": None,
        },

        "current_wallet_is_sanctioned": False,

        "risk_assessment_score": None,

        "decision": None,
        "decision_reason": "",
        "decision_evidence": [],

        "case_status": None,

        "approval_status": "pending",

        # Existing fields.
        "hashed_fullname": None,
        "hashed_id_number": None,
        "hashed_account_number": None,

        "top_features": [],
        "avg_time_between_tx": None,
        "balance_clustering_flag": False,

        "name_similarity_warning": False,
        "name_similarity_score": None,

        "hop_distance_to_blacklist": None,
        "fan_out": None,
        "suspicious_path": None,
        "community_id": None,

        "legal_citations": [],
        "legal_sources_retrieved": [],

        "thought": None,
        "report_path": None,
    }

    return state


# =============================================================================
# RUN PIPELINE
# =============================================================================

@app.post(
    "/api/pipeline/run",
    response_model=PipelineRunResponse,
)
def run_pipeline(
    payload: RawTransactionRequest,
    _user: str = Depends(require_auth),
) -> PipelineRunResponse:

    state = _build_initial_state(payload)

    # Privacy boundary check.
    assert_no_raw_pii(state)

    tx_hash = state["tx_hash"]

    # PipelineRun là orchestration duy nhất.
    run = PipelineRun(
        state=state,
        thread_id=tx_hash,
    )

    try:
        result_state = run.run()

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Thiếu model/dữ liệu cần thiết: {exc}",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi chạy AML pipeline: {exc}",
        )

    # Lưu PipelineRun để request sau có thể resume.
    RUNS[tx_hash] = run

    return PipelineRunResponse(
        tx_hash=tx_hash,
        state=result_state,
    )


# =============================================================================
# GET STATE
# =============================================================================

def _get_run_or_404(
    tx_hash: str,
) -> PipelineRun:

    run = RUNS.get(tx_hash)

    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Không tìm thấy phiên "
                f"tx_hash={tx_hash}. "
                "Có thể server đã restart."
            ),
        )

    return run


@app.get(
    "/api/pipeline/{tx_hash}/state",
)
def get_pipeline_state(
    tx_hash: str,
    _user: str = Depends(require_auth),
) -> Dict[str, Any]:

    run = _get_run_or_404(tx_hash)

    return run.state


# =============================================================================
# APPROVE / REJECT
# =============================================================================

class DecisionRequest(BaseModel):
    approval_status: str = Field(
        ...,
        pattern="^(approved|rejected)$",
    )


@app.post(
    "/api/pipeline/{tx_hash}/decision",
)
def submit_decision(
    tx_hash: str,
    payload: DecisionRequest,
    _user: str = Depends(require_auth),
) -> Dict[str, Any]:

    run = _get_run_or_404(tx_hash)

    state = run.state

    # REVIEW và REPORT đều gán case_status="pending_review" (cả 2 cần chuyên
    # viên xem) — nên endpoint này phục vụ chung cả 2 loại. REVIEW không có
    # interrupt thật (graph chạy thẳng tới END), resume() chỉ xác nhận lại
    # approval_status (đã kiểm tra không crash — xem core/graph_builder.py
    # docstring _route_after_decision).
    if state.get("case_status") != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=(
                "Giao dịch này không ở trạng thái "
                "pending_review."
            ),
        )

    updated_state = run.resume(
        payload.approval_status
    )

    return updated_state


# =============================================================================
# REPORT
# =============================================================================

@app.get(
    "/api/pipeline/{tx_hash}/report",
)
def download_report(
    tx_hash: str,
    token: Optional[str] = None,
) -> FileResponse:

    username = TOKENS.get(token or "")

    if username is None:
        raise HTTPException(
            status_code=401,
            detail="Thiếu hoặc sai token.",
        )

    run = _get_run_or_404(tx_hash)

    state = run.state

    report_path = state.get("report_path")

    if not report_path:
        raise HTTPException(
            status_code=404,
            detail="Giao dịch chưa có báo cáo STR.",
        )

    if not os.path.exists(report_path):
        raise HTTPException(
            status_code=404,
            detail="File báo cáo không tồn tại.",
        )

    return FileResponse(
        report_path,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename=os.path.basename(report_path),
    )


# =============================================================================
# CHAT
# =============================================================================

class ChatRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
    )


class ChatResponse(BaseModel):
    answer: str


@app.post(
    "/api/pipeline/{tx_hash}/chat",
    response_model=ChatResponse,
)
def chat_about_transaction(
    tx_hash: str,
    payload: ChatRequest,
    _user: str = Depends(require_auth),
) -> ChatResponse:

    import json

    from agents.regulation_rag import call_llm_api

    run = _get_run_or_404(tx_hash)

    state = run.state

    if state.get("decision") is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Pipeline chưa chạy tới Decision Engine."
            ),
        )

    # Context hoàn toàn theo SPEC_v2.
    context = {
        "decision": state.get("decision"),
        "decision_reason": state.get("decision_reason"),
        "decision_evidence": state.get("decision_evidence"),
        "case_status": state.get("case_status"),

        "classifier_score": state.get(
            "classifier_score"
        ),
        "graph_score": state.get(
            "graph_score"
        ),

        "sanction_result": state.get(
            "sanction_result"
        ),

        "current_wallet_is_sanctioned": state.get(
            "current_wallet_is_sanctioned"
        ),

        "top_features": state.get(
            "top_features"
        ),
        "community_id": state.get(
            "community_id"
        ),
        "hop_distance_to_blacklist": state.get(
            "hop_distance_to_blacklist"
        ),
        "fan_out": state.get(
            "fan_out"
        ),
        "suspicious_path": state.get(
            "suspicious_path"
        ),

        "name_similarity_warning": state.get(
            "name_similarity_warning"
        ),
        "name_similarity_score": state.get(
            "name_similarity_score"
        ),

        "legal_citations": state.get(
            "legal_citations"
        ),
    }

    prompt = f"""
Bạn là trợ lý giải thích cho chuyên viên AML.

Dữ liệu hệ thống đã tính toán cho giao dịch:

{json.dumps(
    context,
    ensure_ascii=False,
    indent=2,
    default=str,
)}

Câu hỏi của chuyên viên:
"{payload.question}"

Chỉ sử dụng dữ liệu trong JSON trên.

Không được:
- tự tạo số liệu;
- tự tạo căn cứ pháp lý;
- tự thay đổi decision;
- tự biến fuzzy name match thành sanctions match;
- khẳng định một địa chỉ/thực thể đã được kiểm tra nếu JSON không chứa bằng chứng tương ứng.

Nếu dữ liệu không đủ để trả lời, phải nói rõ:
"Chưa được kiểm tra trong phạm vi dữ liệu hiện có."

Giải thích bằng ngôn ngữ nghiệp vụ AML, ngắn gọn và rõ ràng.
"""

    try:
        answer = call_llm_api(prompt)

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi gọi LLM: {exc}",
        )

    return ChatResponse(
        answer=answer,
    )


# =============================================================================
# FRONTEND
# =============================================================================

_HTML_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "frontend_html",
    )
)


@app.get("/")
def serve_ui() -> FileResponse:
    return FileResponse(
        os.path.join(
            _HTML_DIR,
            "index.html",
        )
    )


app.mount(
    "/static",
    StaticFiles(directory=_HTML_DIR),
    name="static",
)


# =============================================================================
# CORS
# =============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)