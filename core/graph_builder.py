"""
core/graph_builder.py -- Khởi tạo LangGraph Orchestrator + PipelineRun facade.

Điều phối tuần tự/tất định 5 Assistant qua AMLState (SPEC.md §1, §2, §3.5).
Đây là nguồn orchestration DUY NHẤT -- demo_run.py, ui/app.py (và gián tiếp
api/main.py) đều chỉ gọi vào class PipelineRun ở file này, không viết lại
logic điều phối ở nơi khác.

=== BẢN SỬA NÀY THAY ĐỔI GÌ SO VỚI BẢN CŨ (đọc trước khi sửa tiếp) ===

1) SỬA TÊN HÀM IMPORT SAI (nguyên nhân ImportError khi chạy streamlit):
   Bản cũ                                Tên hàm THẬT trong agent
   ----------------------------------    ------------------------------------
   run_transaction_classifier            agents.transaction_classifier.analyze_transaction
   run_kyc_verification                  agents.kyc_verification.verify_kyc
   run_graph_aml                         agents.graph_aml.analyze_graph
   (regulation_rag, alert_report tên đã đúng sẵn: run_regulation_rag,
    generate_alert_report -- không đổi.)

2) verify_kyc() CHỈ TRẢ VỀ {"kyc_flags": [...]}, KHÔNG TRẢ FULL STATE:
   Khi verify_kyc được đăng ký làm 1 node LangGraph, điều này hoàn toàn hợp lệ
   -- 1 node LangGraph được PHÉP trả về partial update dict, LangGraph tự merge
   key đó vào state chung rồi mới đưa full state cho node kế tiếp. Không cần
   viết wrapper gộp state thủ công. Ở nhánh Fallback (không có LangGraph), ta
   tự merge bằng state.update(...) vì lúc đó không có cơ chế merge tự động.

3) PRIVACY LAYER CHẠY NGOÀI GRAPH (không phải 1 node trong StateGraph):
   Đã tái hiện được lỗi thật khi test: schema TypedDict mặc định của LangGraph
   dùng cơ chế merge "key nào node không trả lại thì channel giữ nguyên giá trị
   cũ" -- không phải "xoá". Nếu Privacy Layer là 1 node bên trong graph (băm PII
   rồi .pop() field gốc, trả state thiếu key đó), PII gốc vẫn SỐNG NGUYÊN trong
   state channel ở các node sau, vì các node đó không trả lại key "fullname" nên
   LangGraph coi là "giữ nguyên giá trị cũ" thay vì hiểu là "đã bị xoá". Điều
   này phá vỡ thẳng yêu cầu cốt lõi ở SPEC.md §4.
   => Sửa đúng: privacy_layer_node() chạy 1 lần bằng Python thuần, TRƯỚC khi
   app.stream()/app.invoke() -- đúng như sơ đồ gốc SPEC.md §1 (Privacy Layer là
   khối riêng, đứng trước "AI Copilot Core", không nằm trong đó).

=== GHI CHÚ VỀ ĐIỂM DỪNG CHỜ DUYỆT (HITL) -- giữ nguyên từ bản trước ===
SPEC.md gắn interrupt vào ĐÚNG nhánh Final Score >= 0.7, không phải mọi giao dịch:
  - §3.5 Report Assistant: "Nếu Final Score >= 0.7 -> biên soạn STR ... Kích hoạt
    LangGraph interrupt chờ phê duyệt."
  - §1: Human Review Checkpoint "Bắt buộc theo Thông tư 27" -- tức bắt buộc trước
    khi NỘP STR, không phải bắt buộc duyệt tay mọi giao dịch ngân hàng xử lý hàng
    ngày.
Vì vậy: dùng conditional_edges để chỉ đưa giao dịch có approval_status == "pending"
vào node human_checkpoint -- CHỈ node đó mới nằm trong interrupt_after. Giao dịch
an toàn (approval_status == "approved" do alert_report.py tự set khi score < 0.7)
đi thẳng tới END, không ai phải bấm duyệt gì cả.
"""

import os
import uuid
from typing import Any, Dict, Iterator, Tuple

try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

from core.state import AMLState
from core.config import REPORT_THRESHOLD_VND
from core.privacy_layer import mask_pii, assert_no_raw_pii

# Import ĐÚNG tên hàm thật từ các Agent (Phần 4 -> Phần 8) -- xem bảng đối chiếu
# ở docstring đầu file để biết vì sao khác bản cũ.
from agents.transaction_classifier import analyze_transaction
from agents.kyc_verification import verify_kyc
from agents.graph_aml import analyze_graph
from agents.regulation_rag import run_regulation_rag
from agents.alert_report import generate_alert_report


# Nhãn hiển thị cho từng bước (dùng chung cho demo_run.py CLI và ui/app.py Streamlit)
_NODE_LABELS = {
    "webhook": "Bước 1: Webhook giao dịch (kiểm tra ngưỡng báo cáo)",
    "privacy_layer": "Bước 2: Privacy Layer (băm SHA-256 PII on-premise)",
    "transaction_classifier": "Bước 3: Transaction Assistant (XGBoost risk score sơ bộ)",
    "kyc_verification": "Bước 4: KYC Assistant (sàng lọc OFAC/UN/NHNN)",
    "graph_aml": "Bước 5: Graph Assistant (PPR + Louvain)",
    "regulation_rag": "Bước 6: RAG Assistant (căn cứ pháp lý Thông tư 27/32)",
    "alert_report": "Bước 7: Report Assistant (Weighted Risk Score + soạn STR)",
    "human_checkpoint": "Bước 8: Điểm dừng chờ chuyên viên phê duyệt (HITL)",
}


def _get_pii_salt() -> str:
    """
    Đọc salt cho SHA-256 từ biến môi trường PII_SALT.

    Cố ý KHÔNG fallback êm về một giá trị mặc định hardcode -- với dữ liệu PII
    ngân hàng, "chạy được nhưng âm thầm dùng salt yếu/đoán được" nguy hiểm hơn
    nhiều so với báo lỗi rõ ràng ngay khi khởi động. SPEC §8 chấp nhận salt cố
    định trong .env cho MVP, nhưng không có nghĩa là được phép thiếu.
    """
    salt = os.getenv("PII_SALT")
    if not salt:
        raise RuntimeError(
            "Thiếu biến môi trường PII_SALT. Đặt PII_SALT trong file .env trước khi "
            "chạy (xem SPEC.md §4, §8) -- không dùng giá trị mặc định cho dữ liệu PII thật."
        )
    return salt


def privacy_layer_node(raw_transaction: Dict[str, Any]) -> Dict[str, Any]:
    """
    Nhận giao dịch thô (có thể chứa PII gốc: fullname, id_number, account_number)
    và trả về AMLState ĐÃ BĂM, sẵn sàng đưa vào LangGraph.

    QUAN TRỌNG: hàm này chạy NGOÀI StateGraph (xem giải thích ở đầu file) -- gọi
    trực tiếp bằng Python thuần trước khi app.stream()/app.invoke().
    """
    salt = _get_pii_salt()
    pii_input = {
        "fullname": raw_transaction.get("fullname", ""),
        "id_number": raw_transaction.get("id_number", ""),
        "account_number": raw_transaction.get("account_number", ""),
    }
    masked = mask_pii(pii_input, salt)

    state: Dict[str, Any] = {
        "tx_hash": raw_transaction.get("tx_hash") or f"0xAUTO_{uuid.uuid4().hex[:10]}",
        "wallet_from": raw_transaction.get("wallet_from", "") or "",
        "wallet_to": raw_transaction.get("wallet_to", "") or "",
        "amount_vnd": raw_transaction.get("amount_vnd", 0) or 0,
        "hashed_fullname": masked.get("hashed_fullname"),
        "hashed_id_number": masked.get("hashed_id_number"),
        "hashed_account_number": masked.get("hashed_account_number"),
        "risk_score_classifier": None,
        "kyc_flags": None,
        "graph_risk_score": None,
        "legal_citations": None,
        "final_risk_score": None,
        "report_path": None,
        "approval_status": None,
    }

    # Chốt kiểm tra an toàn bắt buộc trước khi nhường quyền cho AI Core
    assert_no_raw_pii(state)
    return state


def human_checkpoint_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node passthrough -- KHÔNG xử lý gì thêm. Lý do nó tồn tại: interrupt_after chỉ
    nhận tên node tĩnh (không nhận điều kiện theo state), nên cần một node riêng
    làm "điểm dừng" -- chỉ những giao dịch cần duyệt (routed bởi route_after_report)
    mới đi vào đây, và chỉ node này mới nằm trong interrupt_after khi compile.
    """
    return {}


def route_after_report(state: Dict[str, Any]) -> str:
    """
    Quyết định giao dịch có cần dừng chờ chuyên viên duyệt hay không, dựa trên
    approval_status mà alert_report.py đã set:
      - "pending"  (score >= 0.7, đã tạo STR) -> cần duyệt -> vào human_checkpoint
      - "approved" (score < 0.7, tự động an toàn) -> đi thẳng tới END
    """
    return "needs_review" if state.get("approval_status") == "pending" else "auto_done"


def build_langgraph():
    """
    Khởi tạo và cấu hình LangGraph kết nối tuần tự 5 Agent thật.
    Privacy Layer KHÔNG nằm trong graph này (xem privacy_layer_node ở trên) --
    graph chỉ nhận state đã băm PII làm input.
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("LangGraph chưa được cài đặt. Vui lòng chạy: pip install langgraph")

    workflow = StateGraph(AMLState)

    # Đăng ký đúng hàm thật của từng Agent (Phần 4 -> Phần 8)
    workflow.add_node("transaction_classifier", analyze_transaction)
    workflow.add_node("kyc_verification", verify_kyc)
    workflow.add_node("graph_aml", analyze_graph)
    workflow.add_node("regulation_rag", run_regulation_rag)
    workflow.add_node("alert_report", generate_alert_report)
    workflow.add_node("human_checkpoint", human_checkpoint_node)

    # Luồng tuần tự chính (SPEC.md §2, bước 3-6)
    workflow.set_entry_point("transaction_classifier")
    workflow.add_edge("transaction_classifier", "kyc_verification")
    workflow.add_edge("kyc_verification", "graph_aml")
    workflow.add_edge("graph_aml", "regulation_rag")
    workflow.add_edge("regulation_rag", "alert_report")

    # Rẽ nhánh SAU alert_report (bước 7): chỉ giao dịch pending mới vào human_checkpoint
    workflow.add_conditional_edges(
        "alert_report",
        route_after_report,
        {
            "needs_review": "human_checkpoint",
            "auto_done": END,
        },
    )
    workflow.add_edge("human_checkpoint", END)

    memory = MemorySaver()

    # Chỉ dừng (interrupt) tại human_checkpoint -- tức chỉ khi thật sự cần duyệt
    app = workflow.compile(
        checkpointer=memory,
        interrupt_after=["human_checkpoint"],
    )
    return app


class PipelineRun:
    """
    Facade DUY NHẤT cho 1 lượt chạy pipeline, dùng chung bởi demo_run.py,
    ui/app.py (và api/main.py nếu cần đầy đủ luồng). Giữ trong session_state
    (Streamlit) hoặc biến cục bộ (CLI) vì nó giữ thread_config để resume() sau
    khi chuyên viên bấm Approve/Reject.

    Cách dùng:
        run = PipelineRun()
        for step_key, label, snapshot in run.steps(raw_transaction):
            ...hiển thị snapshot...
        if run.state.get("approval_status") == "pending":
            run.resume("approved")  # hoặc "rejected"
    """

    def __init__(self):
        self.thread_id = str(uuid.uuid4())
        self.thread_config = {"configurable": {"thread_id": self.thread_id}}
        self._state: Dict[str, Any] = {}
        self._app = build_langgraph() if LANGGRAPH_AVAILABLE else None

    @property
    def state(self) -> Dict[str, Any]:
        return self._state

    def steps(self, raw_transaction: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
        """
        Generator chạy toàn bộ luồng nghiệp vụ 8 bước, yield (step_key, label, snapshot)
        sau mỗi bước để UI/CLI hiển thị realtime.
        """
        # Bước 1: Webhook -- chỉ kích hoạt khi giao dịch vượt ngưỡng báo cáo (SPEC §2 bước 1)
        amount_vnd = raw_transaction.get("amount_vnd", 0) or 0
        if amount_vnd < REPORT_THRESHOLD_VND:
            self._state = {"skipped": True, "amount_vnd": amount_vnd}
            yield (
                "webhook",
                _NODE_LABELS["webhook"],
                {
                    "skipped": True,
                    "reason": (
                        f"Giao dịch {amount_vnd:,.0f} VND CHƯA vượt ngưỡng báo cáo "
                        f"({REPORT_THRESHOLD_VND:,.0f} VND) theo Thông tư 27 -- "
                        "không kích hoạt pipeline AML."
                    ),
                },
            )
            return

        yield (
            "webhook",
            _NODE_LABELS["webhook"],
            {
                "amount_vnd": amount_vnd,
                "status": f"Vượt ngưỡng {REPORT_THRESHOLD_VND:,.0f} VND -- kích hoạt pipeline.",
            },
        )

        # Bước 2: Privacy Layer -- CHẠY NGOÀI GRAPH (xem privacy_layer_node)
        state = privacy_layer_node(raw_transaction)
        self._state = state
        yield (
            "privacy_layer",
            _NODE_LABELS["privacy_layer"],
            {
                "tx_hash": state.get("tx_hash"),
                "hashed_fullname": state.get("hashed_fullname"),
                "hashed_id_number": state.get("hashed_id_number"),
                "hashed_account_number": state.get("hashed_account_number"),
            },
        )

        if self._app is not None:
            yield from self._run_langgraph(state)
        else:
            yield from self._run_fallback(state)

    def _run_langgraph(self, state: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
        """Bước 3-8 chạy qua LangGraph thật (interrupt tại human_checkpoint)."""
        for event in self._app.stream(state, self.thread_config, stream_mode="updates"):
            for node_name, update in event.items():
                if node_name == "__interrupt__":
                    # Event nội bộ của LangGraph, trùng lặp state với human_checkpoint,
                    # không phải 1 bước nghiệp vụ thật -- lọc bỏ cho UI sạch hơn.
                    continue
                label = _NODE_LABELS.get(node_name, node_name)
                yield (node_name, label, update or {})
        self._state = self._app.get_state(self.thread_config).values

    def _run_fallback(self, state: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
        """
        Fallback khi chưa cài LangGraph: chạy tuần tự thuần Python.
        KHÔNG có interrupt thật (không async chờ input) -- nếu approval_status
        ra "pending", generator dừng lại, trả state cho UI/CLI xử lý tiếp bằng
        resume(); không tự ý coi giao dịch là đã duyệt.
        """
        print("[!] LangGraph chưa được cài đặt -- đang chạy Fallback Pipeline tuần tự.")

        state = analyze_transaction(state)
        yield ("transaction_classifier", _NODE_LABELS["transaction_classifier"],
               {"risk_score_classifier": state.get("risk_score_classifier")})

        kyc_update = verify_kyc(state)  # chỉ trả {"kyc_flags": [...]}, tự merge thủ công
        state.update(kyc_update)
        yield ("kyc_verification", _NODE_LABELS["kyc_verification"], kyc_update)

        state = analyze_graph(state)
        yield ("graph_aml", _NODE_LABELS["graph_aml"], {
            "graph_risk_score": state.get("graph_risk_score"),
            "community_id": state.get("community_id"),
        })

        state = run_regulation_rag(state)
        yield ("regulation_rag", _NODE_LABELS["regulation_rag"], {
            "legal_citations": state.get("legal_citations"),
        })

        state = generate_alert_report(state)
        yield ("alert_report", _NODE_LABELS["alert_report"], {
            "final_risk_score": state.get("final_risk_score"),
            "report_path": state.get("report_path"),
            "approval_status": state.get("approval_status"),
        })

        self._state = state

        if state.get("approval_status") == "pending":
            print("[!] Giao dịch cần chuyên viên duyệt (final_risk_score >= 0.7). "
                  "Dừng tại đây -- gọi resume() sau khi có quyết định.")
            yield ("human_checkpoint", _NODE_LABELS["human_checkpoint"], {
                "approval_status": "pending",
                "note": "Không có LangGraph interrupt thật -- gọi PipelineRun.resume() để tiếp tục.",
            })

    def resume(self, decision: str) -> Dict[str, Any]:
        """
        Xử lý thao tác Approve/Reject từ UI, gọi sau khi pipeline đã dừng ở
        human_checkpoint (approval_status == "pending").
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"decision không hợp lệ: {decision!r} (chỉ nhận 'approved'/'rejected')")

        if self._app is not None:
            current_state = self._app.get_state(self.thread_config).values
            current_state["approval_status"] = decision
            self._app.update_state(self.thread_config, current_state)

            for _event in self._app.stream(None, self.thread_config):
                pass

            self._state = self._app.get_state(self.thread_config).values
        else:
            self._state["approval_status"] = decision

        return self._state


if __name__ == "__main__":
    # Test nhanh độc lập: python -m core.graph_builder (cần .env có PII_SALT,
    # models/xgboost_aml.pkl, data/processed/sample_ofac_wallet.txt).
    print(f"LangGraph khả dụng: {LANGGRAPH_AVAILABLE}")

    demo_tx = {
        "tx_hash": f"0xTEST_{uuid.uuid4().hex[:8]}",
        "wallet_from": "0xbadwallet123",
        "wallet_to": "0xdestination_wallet_demo",
        "amount_vnd": 620_000_000,
        "fullname": "Nguyễn Văn A",
        "id_number": "001096001234",
        "account_number": "1903456789012",
    }

    run = PipelineRun()
    for step_key, label, snapshot in run.steps(demo_tx):
        print(f"--- {label} ---")
        print(snapshot)

    print("\n=== TRẠNG THÁI CUỐI ===")
    print(run.state)

    if run.state.get("approval_status") == "pending":
        print("\n[Mô phỏng chuyên viên bấm Approve...]")
        final_state = run.resume("approved")
        print("approval_status sau resume:", final_state["approval_status"])