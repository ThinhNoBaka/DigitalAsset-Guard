# core/graph_builder.py

"""
LangGraph Orchestrator - Điều phối pipeline theo SPEC_v2 (đã pivot sang
Decision Engine rule-based composite — xem agents/decision_engine.py).

Pipeline:

    privacy_layer
        ↓
    aggregation_monitor
        ↓
    transaction_classifier
        ↓
    graph_aml
        ↓
    sanctions
        ↓
    decision_engine
        ├── PASS   → END
        │
        ├── REVIEW → END (chờ chuyên viên xem qua decision_evidence,
        │            KHÔNG tự soạn STR draft — xem _route_after_decision)
        │
        └── REPORT → regulation_rag
                          ↓
                     alert_report
                          ↓
                     HUMAN CHECKPOINT

Nguyên tắc:
- PipelineRun là facade orchestration duy nhất.
- Routing SAU decision_engine dựa trên field `decision` (PASS/REVIEW/REPORT),
  KHÔNG dựa trên case_status — vì REVIEW và REPORT đều gán
  case_status="pending_review" (cả 2 đều cần người xem), case_status không
  đủ để phân biệt nhánh nào cần soạn STR draft.
- PASS và REVIEW đều kết thúc ngay sau Decision Engine.
- Chỉ REPORT chạy RAG → Report → Human Checkpoint.
- Human Checkpoint (is_paused()) dựa trên:
      case_status == "pending_review"   (đúng cho cả REVIEW lẫn REPORT)
      approval_status == "pending"
- APPROVE/REJECT chỉ cập nhật approval_status rồi kết thúc pipeline.
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from core.state import AMLState
from core.privacy_layer import privacy_layer_node

from agents.aggregation_monitor import analyze_aggregation
from agents.transaction_classifier import analyze_transaction
from agents.graph_aml import analyze_graph
from agents.kyc_verification import verify_kyc
from agents.decision_engine import make_decision
from agents.regulation_rag import run_regulation_rag
from agents.alert_report import generate_alert_report


# =============================================================================
# AUDIT LOGGER
# =============================================================================

try:
    from core.audit_logger import timed_step
except ImportError:
    # Fallback chỉ để module không crash nếu audit_logger chưa tồn tại.
    # Production nên luôn có audit_logger.
    def timed_step(name, tx_hash, func, state):
        return func(state)


# =============================================================================
# ROUTING
# =============================================================================

def _route_after_decision(state: AMLState):
    """
    Rẽ nhánh SAU Decision Engine.

    QUAN TRỌNG — đổi từ nhìn case_status sang nhìn thẳng `decision`:

    Sau khi Decision Engine chuyển sang rule-based composite (xem
    agents/decision_engine.py), có 3 giá trị `decision`: PASS / REVIEW /
    REPORT. Cả REVIEW lẫn REPORT đều gán case_status="pending_review" (để
    API/HITL biết cần người xem), nên KHÔNG thể dùng case_status để phân
    biệt 2 luồng nữa như trước — phải route theo `decision`:

        PASS
            → END (không có gì cần xem)

        REVIEW (2 tín hiệu "medium" cùng lúc, chưa đủ mạnh để REPORT)
            → END (dừng ở decision_engine, CHỜ chuyên viên xem trực tiếp
              qua decision_evidence — KHÔNG tự soạn sẵn dự thảo STR, vì case
              còn mơ hồ, có thể chuyên viên sẽ đóng hồ sơ chứ không phải
              REPORT thật. Nếu chuyên viên nâng cấp lên REPORT, làm thủ công
              hoặc qua 1 luồng riêng, không tự động ở routing này.)

        REPORT (sanctions/structuring/classifier/graph vượt ngưỡng)
            → regulation_rag → alert_report → soạn dự thảo STR

    Ghi chú vận hành: vì REVIEW không đi qua node nào sau decision_engine,
    graph sẽ chạy thẳng tới END trong lần invoke() đầu tiên — KHÔNG có
    LangGraph interrupt xảy ra (interrupt_after=["alert_report"] chỉ kích
    hoạt khi node đó thực sự chạy). is_paused() vẫn trả True cho REVIEW vì
    dựa trên case_status/approval_status (field-based), không phụ thuộc
    interrupt — nhưng resume() gọi sau đó (graph.invoke(None)) sẽ không
    còn node nào để chạy tiếp, chỉ xác nhận lại state hiện có. Hành vi này
    được chấp nhận vì REVIEW vốn không có bước xử lý nào sau decision_engine
    để "resume" cả.
    """

    if state.get("decision") == "REPORT":
        return "regulation_rag"

    return END


# =============================================================================
# BUILD LANGGRAPH
# =============================================================================

def build_pipeline(checkpointer=None):
    """
    Build và compile LangGraph pipeline.

    Flow:

        privacy_layer
            ↓
        aggregation_monitor
            ↓
        transaction_classifier
            ↓
        graph_aml
            ↓
        sanctions
            ↓
        decision_engine
            ├── PASS → END
            │
            └── REPORT → regulation_rag
                              ↓
                         alert_report
                              ↓
                             END

    interrupt_after=["alert_report"]:

    Với case REPORT, graph sẽ dừng sau khi alert_report hoàn thành,
    trước khi pipeline kết thúc hoàn toàn.

    PASS không đi qua alert_report nên không bị interrupt.
    """

    builder = StateGraph(AMLState)

    # -------------------------------------------------------------------------
    # Nodes
    # -------------------------------------------------------------------------

    builder.add_node(
        "privacy_layer",
        privacy_layer_node,
    )

    builder.add_node(
        "aggregation_monitor",
        lambda state: timed_step(
            "aggregation_monitor",
            state.get("tx_hash"),
            analyze_aggregation,
            state,
        ),
    )

    builder.add_node(
        "transaction_classifier",
        lambda state: timed_step(
            "transaction_classifier",
            state.get("tx_hash"),
            analyze_transaction,
            state,
        ),
    )

    builder.add_node(
        "graph_aml",
        lambda state: timed_step(
            "graph_aml",
            state.get("tx_hash"),
            analyze_graph,
            state,
        ),
    )

    builder.add_node(
        "sanctions",
        lambda state: timed_step(
            "sanctions",
            state.get("tx_hash"),
            verify_kyc,
            state,
        ),
    )

    builder.add_node(
        "decision_engine",
        lambda state: timed_step(
            "decision_engine",
            state.get("tx_hash"),
            make_decision,
            state,
        ),
    )

    builder.add_node(
        "regulation_rag",
        lambda state: timed_step(
            "regulation_rag",
            state.get("tx_hash"),
            run_regulation_rag,
            state,
        ),
    )

    builder.add_node(
        "alert_report",
        lambda state: timed_step(
            "alert_report",
            state.get("tx_hash"),
            generate_alert_report,
            state,
        ),
    )

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    builder.set_entry_point("privacy_layer")

    # -------------------------------------------------------------------------
    # Main pipeline
    # -------------------------------------------------------------------------

    builder.add_edge(
        "privacy_layer",
        "aggregation_monitor",
    )

    builder.add_edge(
        "aggregation_monitor",
        "transaction_classifier",
    )

    builder.add_edge(
        "transaction_classifier",
        "graph_aml",
    )

    builder.add_edge(
        "graph_aml",
        "sanctions",
    )

    builder.add_edge(
        "sanctions",
        "decision_engine",
    )

    # -------------------------------------------------------------------------
    # Decision Engine routing
    # -------------------------------------------------------------------------

    builder.add_conditional_edges(
        "decision_engine",
        _route_after_decision,
        {
            "regulation_rag": "regulation_rag",
            END: END,
        },
    )

    # -------------------------------------------------------------------------
    # REPORT branch
    # -------------------------------------------------------------------------

    builder.add_edge(
        "regulation_rag",
        "alert_report",
    )

    builder.add_edge(
        "alert_report",
        END,
    )

    # -------------------------------------------------------------------------
    # Compile
    # -------------------------------------------------------------------------

    checkpointer = checkpointer or MemorySaver()

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=["alert_report"],
    )


# =============================================================================
# PIPELINE RUN
# =============================================================================

class PipelineRun:
    """
    Facade DUY NHẤT để chạy AML pipeline.

    Các thành phần khác:

        demo_run.py
        api/main.py

    không tự viết lại orchestration.

    Tất cả đều gọi PipelineRun.
    """

    def __init__(
        self,
        state: AMLState,
        thread_id: str = None,
    ):
        """
        Khởi tạo một pipeline run.

        Không chạy graph ở đây.

        Graph chỉ thực sự chạy khi gọi:
            run()

        hoặc:
            stream()
        """

        # Copy state để tránh sửa trực tiếp object bên ngoài.
        self.state = dict(state)

        # REPORT cần approval_status = pending để chờ HITL.
        #
        # PASS không sử dụng field này về mặt nghiệp vụ,
        # nhưng việc default pending giúp state luôn có schema nhất quán.
        self.state.setdefault(
            "approval_status",
            "pending",
        )

        # Mỗi transaction nên có thread riêng.
        self.thread_id = (
            thread_id
            or self.state.get("tx_hash")
            or "default-thread"
        )

        # Checkpointer sống cùng PipelineRun.
        #
        # Quan trọng:
        # run() → checkpoint → resume() phải sử dụng CÙNG checkpointer.
        self.checkpointer = MemorySaver()

        self.graph = build_pipeline(
            checkpointer=self.checkpointer,
        )

        self.config = {
            "configurable": {
                "thread_id": self.thread_id,
            }
        }

    # =========================================================================
    # RUN
    # =========================================================================

    def run(self) -> AMLState:
        """
        Chạy pipeline từ đầu.

        Có 2 khả năng:

        1. PASS

            privacy
              ↓
            classifier
              ↓
            graph
              ↓
            sanctions
              ↓
            decision
              ↓
            END

        2. REPORT

            privacy
              ↓
            classifier
              ↓
            graph
              ↓
            sanctions
              ↓
            decision
              ↓
            RAG
              ↓
            report
              ↓
            HUMAN CHECKPOINT

        Trả về state mới nhất.
        """

        result_state = self.graph.invoke(
            self.state,
            config=self.config,
        )

        self.state = dict(result_state)

        return self.state

    # =========================================================================
    # STREAM
    # =========================================================================

    def stream(self):
        """
        Chạy pipeline và yield output sau từng node.

        Dùng cho UI realtime / debug.
        """

        for step_output in self.graph.stream(
            self.state,
            config=self.config,
        ):
            # LangGraph thường trả:
            #
            # {
            #     "node_name": {
            #         "field": value
            #     }
            # }
            #
            # Cập nhật state nội bộ nếu output chứa state update.

            if isinstance(step_output, dict):

                for node_state in step_output.values():

                    if isinstance(node_state, dict):
                        self.state.update(node_state)

            yield step_output

    # =========================================================================
    # PAUSE CHECK
    # =========================================================================

    def is_paused(self) -> bool:
        """
        Kiểm tra pipeline có đang chờ Human Checkpoint hay không.

        SPEC_v2:

            REPORT
                ↓
            case_status = pending_review
                ↓
            approval_status = pending
                ↓
            HUMAN CHECKPOINT

        PASS:

            case_status = auto_cleared
                ↓
            không pause

        APPROVED / REJECTED:

            approval_status != pending
                ↓
            không còn chờ HITL
        """

        return (
            self.state.get("case_status")
            == "pending_review"
            and self.state.get("approval_status")
            == "pending"
        )

    # =========================================================================
    # RESUME
    # =========================================================================

    def resume(
        self,
        approval_status: str,
    ) -> AMLState:
        """
        Resume pipeline sau khi chuyên viên APPROVE / REJECT.

        approval_status:

            "approved"
            "rejected"

        Luồng:

            alert_report
                ↓
            HUMAN CHECKPOINT
                ↓
            update approval_status
                ↓
            invoke(None)
                ↓
            END

        Không chạy lại:
            - Privacy
            - Classifier
            - Graph
            - Sanctions
            - Decision
            - RAG
            - Report
        """

        if approval_status not in (
            "approved",
            "rejected",
        ):
            raise ValueError(
                f"approval_status không hợp lệ: "
                f"{approval_status}"
            )

        # Chỉ được resume khi thực sự đang chờ chuyên viên.
        if self.state.get("case_status") != "pending_review":
            raise RuntimeError(
                "Pipeline không ở trạng thái "
                "pending_review, không thể APPROVE/REJECT."
            )

        if self.state.get("approval_status") != "pending":
            raise RuntimeError(
                "Pipeline đã được xử lý trước đó: "
                f"approval_status="
                f"{self.state.get('approval_status')}"
            )

        # ---------------------------------------------------------------------
        # Update checkpoint
        # ---------------------------------------------------------------------

        self.graph.update_state(
            self.config,
            {
                "approval_status": approval_status,
            },
        )

        # Đồng bộ state local.
        self.state["approval_status"] = approval_status

        # ---------------------------------------------------------------------
        # Resume từ checkpoint
        # ---------------------------------------------------------------------

        result_state = self.graph.invoke(
            None,
            config=self.config,
        )

        self.state = dict(result_state)

        return self.state