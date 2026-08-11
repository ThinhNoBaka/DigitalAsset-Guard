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
from core.graph_provider import get_graph_provider
from core.privacy_layer import assert_no_raw_pii
from agents.kyc_verification import verify_kyc
from agents.transaction_classifier import analyze_transaction
from agents.decision_engine import (
    _load_classifier_threshold,
    _CLASSIFIER_MEDIUM_RATIO,
)

# FIX 2026-08-08 — Transaction Assistant bắt buộc state["wallet_record"] đúng
# schema ở production path (xem agents/transaction_classifier.py docstring).
# Dùng chung module fetch với scripts/02_fetch_etherscan_sample.py để build
# feature vector THẬT qua feature_builder, KHÔNG dùng mock.
from scripts.etherscan_fetcher import fetch_wallet_record

# [DEMO] Mock Core Banking — nguồn OFF-CHAIN, ĐỘC LẬP hoàn toàn với
# wallet_record (ON-CHAIN, Etherscan, dòng import ngay trên). Chỉ phục vụ
# agents/aggregation_monitor.py (structuring/smurfing). Xem
# scripts/mock_core_banking.py để biết lý do KHÔNG hợp nhất 2 nguồn.
from scripts.mock_core_banking import get_wallet_tx_history


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

    # FIX 2026-08-08 — Transaction Assistant bắt buộc wallet_record ở production
    # path (xem agents/transaction_classifier.py). Giống /api/pipeline/run,
    # fetch on-chain data của ví để chấm điểm THẬT qua feature_builder.
    try:
        wallet_record = fetch_wallet_record(
            wallet_address,
            max_records=100,
            page_size=100,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Không thể fetch dữ liệu on-chain của wallet_address "
                f"({wallet_address}): {exc}."
            ),
        )

    state = {
        "wallet_from": wallet_address,
        "wallet_to": "",
        "amount_vnd": amount_vnd,

        # On-chain data — KHÔNG PII, cần cho Transaction Assistant.
        "wallet_record": wallet_record,
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

    # [DEMO ONLY — Phase 2 Graph Provider] Chọn scenario mock tường minh.
    # Production KHÔNG gửi field này (Neo4jGraphProvider bỏ qua hoàn toàn).
    # Khi None: MockGraphProvider tự khớp deterministic theo wallet_from.
    scenario: Optional[str] = None


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

    # -----------------------------------------------------------------
    # FIX 2026-08-08 — On-chain data của wallet_from (KHÔNG PII — dữ liệu
    # public, hợp lệ để tồn tại trong state sau Privacy Layer).
    # Transaction Assistant (production path) bắt buộc có wallet_record
    # đúng schema {"address", "chains": {"ethereum": [txlist]},
    # "token_transfers": {"ethereum": [tokentx]}} để build feature vector
    # THẬT qua feature_builder, KHÔNG được dùng mock. Fetch động mỗi
    # request để production path chạy đúng (demo/test mới được phép
    # allow_mock=True tường minh — xem scripts/demo_runner.py).
    # -----------------------------------------------------------------
    try:
        wallet_record = fetch_wallet_record(
            payload.wallet_from,
            max_records=100,
            page_size=100,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Không thể fetch dữ liệu on-chain của wallet_from "
                f"({payload.wallet_from}): {exc}. "
                "Kích hoạt webhook / Chạy pipeline yêu cầu dữ liệu Etherscan."
            ),
        )

    # -----------------------------------------------------------------
    # [DEMO] Mock Core Banking — nguồn OFF-CHAIN, ĐỘC LẬP với wallet_record
    # (ON-CHAIN) ở trên. Tra theo account_number RAW (dòng này chạy TRƯỚC
    # Privacy Layer, account_number chưa bị băm). Đây KHÔNG phải mapping
    # wallet<->customer — chỉ là tra cứu lịch sử ngân hàng của account_number
    # thuộc case đang xét, phục vụ agents/aggregation_monitor.py. Xem
    # scripts/mock_core_banking.py.
    #
    # Nếu account_number không khớp record demo nào -> get_wallet_tx_history
    # trả None -> KHÔNG set "wallet_tx_history" -> Aggregation Monitor giữ
    # nguyên hành vi mặc định hiện tại (aggregation_status="not_assessed",
    # xem agents/aggregation_monitor.py) -- không đổi gì so với trước.
    # -----------------------------------------------------------------
    wallet_tx_history = get_wallet_tx_history(payload.account_number)

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

        # On-chain data của wallet_from — bắt buộc cho Transaction Assistant
        # (xem agents/transaction_classifier.py FIX 2026-08-08). KHÔNG PII.
        "wallet_record": wallet_record,

        # Transaction Assistant sẽ set lại sau khi đếm DỮ LIỆU THÔ trên
        # wallet_record (xem agents/transaction_classifier.py FIX 2026-08-08).
        # Mặc định False = xem như đủ dữ liệu cho tới khi agent xác nhận ngược lại.
        "insufficient_data": False,

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

    # Chỉ set khi có case demo khớp account_number. KHÔNG set key này khi
    # wallet_tx_history is None -- agents/aggregation_monitor.py phân biệt
    # "key không tồn tại/None" (not_assessed) với "key tồn tại nhưng rỗng".
    # Giữ đúng convention .get("wallet_tx_history") hiện có trong
    # aggregation_monitor.py và transaction_classifier.py.
    if wallet_tx_history:
        state["wallet_tx_history"] = wallet_tx_history

    # -----------------------------------------------------------------
    # [Phase 2 — Graph Data Provider] Nạp graph data vào state theo DATA SOURCE.
    #
    # BUG ĐÃ SỬA: trước đây _build_initial_state KHÔNG nạp mock_graph_edges /
    # mock_blacklisted_wallets → Graph Agent qua UI/API luôn nhận None → rơi
    # vào fallback "no graph data" (PPR=0.0, hop=None, suspicious_path=[]) dù
    # demo đang chạy. Từ nay graph data được lấy từ provider theo config:
    #
    #     GRAPH_SOURCE=mock  → MockGraphProvider: đọc scenario JSON theo
    #                          wallet_from (deterministic) hoặc payload.scenario
    #     GRAPH_SOURCE=neo4j → Neo4jGraphProvider: trả None, KHÔNG thêm gì —
    #                          graph_aml query Neo4j/GDS gốc
    #
    # KHÔNG PII (chỉ địa chỉ ví công khai + số tiền), hợp lệ để tồn tại trong
    # state TRƯỚC Privacy Layer (core/privacy_layer.py giữ nguyên key non-PII).
    # -----------------------------------------------------------------
    graph_provider = get_graph_provider()
    graph_data = graph_provider.get_graph_data(
        wallet_from=payload.wallet_from,
        wallet_to=payload.wallet_to,
        scenario=getattr(payload, "scenario", None),
    )
    if graph_data is not None and not graph_data.is_empty():
        # Mock: nạp edges + blacklisted theo đúng contract agents/graph_aml.py
        # đọc (khớp state.py::mock_graph_edges / mock_blacklisted_wallets).
        state["mock_graph_edges"] = graph_data.edges
        state["mock_blacklisted_wallets"] = graph_data.blacklisted_wallets
        state["graph_scenario_id"] = graph_data.scenario_id

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

    # Privacy boundary check — kiểm tra SAU khi graph đã chạy xong node
    # privacy_layer (entry point, xem core/graph_builder.py). Không thể gọi
    # assert_no_raw_pii trên state THÔ vừa build từ request: tại điểm đó PII
    # gốc (fullname/id_number/account_number) chưa được băm — đó là đầu vào
    # hợp lệ của Privacy Layer (core/privacy_layer.py::privacy_layer_node).
    # Sau khi graph invoke, LangGraph đã loại các key PII gốc khỏi state và
    # chỉ còn các field hashed_* (đã verify bằng script chẩn đoán).
    assert_no_raw_pii(result_state)

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

Ý nghĩa các field trong JSON trên:
- classifier_score: điểm phân loại ML (transaction classifier).
- graph_score: còn được gọi là PPR (Personalized PageRank) — mức độ liên quan của ví này tới các ví seed/rủi ro theo thuật toán Personalized PageRank. Đây KHÔNG phải điểm risk tổng hợp duy nhất.
- hop_distance_to_blacklist: số hop giao dịch từ ví này tới ví nằm trong danh sách trừng phạt (OFAC/UN/NHNN).
- suspicious_path: đường đi dòng tiền đáng ngờ từ ví bị trừng phạt tới ví này.
- community_id: mã cộng đồng Louvain mà ví thuộc về.
- fan_out: số ví khác nhận tiền trực tiếp từ ví này.
- decision: quyết định REPORT/REVIEW là kết quả rule-based composite (từng tín hiệu xét độc lập: sanctions/structuring/classifier(θ)/graph hop), KHÔNG phải 1 điểm risk tổng hợp duy nhất.
- decision_evidence: danh sách các rule cụ thể đã kích hoạt — dùng làm căn cứ để giải thích quyết định.

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