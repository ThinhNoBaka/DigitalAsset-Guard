"""
ui/app.py -- Phần 10 (SPEC.md §10, mục 2).

Giao diện Streamlit: form nhập giao dịch -> gọi core.graph_builder.PipelineRun
(cùng nguồn orchestration với demo_run.py) -> hiển thị kết quả từng agent theo
thời gian thực -> điểm dừng chờ duyệt (Approve/Reject) -> link tải file .docx
khi Approve.

Giao diện KHÔNG cần đẹp (SPEC.md §10 lưu ý) -- chỉ cần thể hiện đúng luồng
nghiệp vụ 8 bước ở SPEC.md §2.

Chạy: streamlit run ui/app.py  (chạy từ thư mục gốc digitalasset_guard/)
"""
import os
import sys

# Đảm bảo import được core.*/agents.* dù Streamlit chạy file này bằng đường dẫn
# tương đối khác nhau tuỳ hệ điều hành/IDE.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st

from core.graph_builder import PipelineRun

st.set_page_config(page_title="DigitalAsset Guard AI Copilot", layout="wide")


# =============================================================================
# [Bổ sung -- Phần 10.a, 10.b, 10.c, 10.d + Phần 12.1] Các hàm hiển thị nâng cao.
# Đặt tách riêng khỏi luồng chính (form -> PipelineRun -> approval) bên dưới --
# chỉ ĐỌC dữ liệu từ `state`, không sửa PipelineRun/graph_builder.
# =============================================================================

def _render_explainable_panel(state: dict) -> None:
    """[Phần 10.a] Explainable Risk panel -- đọc risk_breakdown/top_features/
    dữ liệu đồ thị và hiển thị dạng % + mô tả định tính, thay vì chỉ 1 con số
    final_risk_score. Đây là khối ưu tiên cao nhất trong các bổ sung UI."""
    st.markdown("#### 🔍 Explainable Risk Assessment")

    breakdown = state.get("risk_breakdown") or {}
    if breakdown:
        c_pct = breakdown.get("classifier_contribution_pct", 0.0)
        k_pct = breakdown.get("kyc_contribution_pct", 0.0)
        g_pct = breakdown.get("graph_contribution_pct", 0.0)
        col1, col2, col3 = st.columns(3)
        col1.metric("Classifier", f"{c_pct}%")
        col2.metric("KYC", f"{k_pct}%")
        col3.metric("Graph", f"{g_pct}%")
        st.caption(
            "Dựa trên công thức Final = 0.2×Classifier + 0.3×KYC + 0.5×Graph "
            "(xem docstring agents/alert_report.py để biết lý do chọn trọng số)."
        )
    else:
        st.caption("Chưa có risk_breakdown (agents/alert_report.py chưa chạy tới bước này).")

    top_features = state.get("top_features") or []
    if top_features:
        st.write("**Đặc trưng ảnh hưởng lớn nhất tới điểm phân loại (top feature importance toàn cục):**")
        for name, score in top_features:
            st.write(f"- `{name}`: {round(score, 3)}")
        st.caption(
            "⚠️ Đây là feature importance TOÀN CỤC của model, không phải lý do riêng cho "
            "giao dịch này (per-instance explanation cần SHAP, để dành Hướng phát triển)."
        )

    hop = state.get("hop_distance_to_blacklist")
    fan_out = state.get("fan_out")
    community_id = state.get("community_id")
    graph_notes = []
    if hop is not None:
        graph_notes.append(f"Cách ví trong danh sách trừng phạt **{hop} hop** giao dịch")
    if fan_out is not None:
        graph_notes.append(f"Fan-out = **{fan_out}**")
    if community_id is not None:
        graph_notes.append(f"Thuộc cộng đồng Louvain **#{community_id}**")
    if graph_notes:
        st.write("**Yếu tố đồ thị đáng chú ý:** " + " · ".join(graph_notes))


def _render_legal_citations_panel(state: dict) -> None:
    """[Phần 10.b] RAG citation panel -- hiển thị legal_citations có cấu trúc
    (list[dict], xem agents/regulation_rag.py) thành từng khối Điều khoản ->
    Tóm tắt -> Lý do áp dụng, thay vì 1 đoạn văn bản dài."""
    st.markdown("#### ⚖️ Căn cứ pháp lý (RAG)")
    citations = state.get("legal_citations")

    if isinstance(citations, str):
        # Tương thích ngược nếu state cũ (trước bản structured output) vẫn là chuỗi.
        st.write(citations)
        return

    if not citations:
        st.caption("Chưa có trích dẫn pháp lý.")
        return

    for i, citation in enumerate(citations):
        if "raw_text" in citation and len(citation) == 1:
            st.write(citation["raw_text"])
            continue
        source = citation.get("source", "(không rõ nguồn)")
        dieu_khoan = citation.get("dieu_khoan", "")
        title = source + (f" — {dieu_khoan}" if dieu_khoan else "")
        with st.expander(title, expanded=(i == 0)):
            if citation.get("noi_dung_tom_tat"):
                st.write(f"**Nội dung:** {citation['noi_dung_tom_tat']}")
            if citation.get("ly_do_ap_dung"):
                st.write(f"**Vì sao áp dụng cho giao dịch này:** {citation['ly_do_ap_dung']}")


def _render_graph_panel(state: dict) -> None:
    """[Phần 10.c] Graph Visualization + đường đi đáng ngờ. Dùng streamlit-agraph,
    vẽ tối giản (không cần đẹp) dựa trên suspicious_path (agents/graph_aml.py)."""
    st.markdown("#### 🕸️ Sơ đồ dòng tiền (đường đi đáng ngờ)")

    try:
        from streamlit_agraph import agraph, Node, Edge, Config
    except ImportError:
        st.caption("Chưa cài `streamlit-agraph` (`pip install streamlit-agraph`) -- bỏ qua sơ đồ.")
        return

    suspicious_path = state.get("suspicious_path") or []
    wallet_from = state.get("wallet_from", "")
    wallet_to = state.get("wallet_to", "")

    if not suspicious_path:
        st.caption(
            "Không tìm thấy đường đi tới ví trong danh sách trừng phạt trong phạm vi đồ thị hiện "
            "có (DEMO_MODE dùng đồ thị mẫu cố định, hoặc ví không kết nối tới ví đen)."
        )
        path_nodes = [wallet_from, wallet_to]
    else:
        path_nodes = suspicious_path if wallet_to in suspicious_path else suspicious_path + [wallet_to]

    flagged_node = suspicious_path[0] if suspicious_path else None
    nodes, edges, seen = [], [], set()
    for addr in path_nodes:
        if addr and addr not in seen:
            seen.add(addr)
            nodes.append(Node(
                id=addr,
                label=(addr[:10] + "…") if len(addr) > 10 else addr,
                size=20,
                color="#e74c3c" if addr == flagged_node else "#3498db",
            ))
    for a, b in zip(path_nodes, path_nodes[1:]):
        if a and b:
            edges.append(Edge(source=a, target=b))

    config = Config(width=700, height=350, directed=True, physics=True, hierarchical=False)
    agraph(nodes=nodes, edges=edges, config=config)
    st.caption("Node đỏ = ví trong danh sách trừng phạt (điểm bắt đầu đường đi đáng ngờ).")


def _render_report_preview(state: dict) -> None:
    """[Phần 10.d] Report Preview trước khi export -- hiển thị lại nội dung STR
    (risk breakdown, trích dẫn pháp lý) dạng Markdown ngay trong Streamlit, thay
    vì chỉ có link tải mù. KHÔNG export PDF ở bản MVP (xem lý do trong hướng dẫn:
    docx2pdf/LibreOffice thêm phụ thuộc môi trường rủi ro khi demo)."""
    with st.expander("📄 Xem trước nội dung báo cáo STR trước khi Approve", expanded=False):
        st.markdown(f"**Mã giao dịch:** `{state.get('tx_hash', 'UNKNOWN_HASH')}`")
        st.markdown(f"**Giá trị giao dịch:** {state.get('amount_vnd', 0):,.0f} VND")
        st.markdown(f"**Điểm rủi ro hợp nhất:** {state.get('final_risk_score')}")
        _render_explainable_panel(state)
        _render_legal_citations_panel(state)
        st.caption(
            "Đây là bản xem trước rút gọn. File `.docx` đầy đủ (đúng Mẫu số 04, Thông tư 27) "
            "sẽ có sẵn để tải sau khi Approve."
        )


def _render_chat_panel(state: dict) -> None:
    """[Phần 12.1] Chat hỏi-đáp -- tổng hợp/giải thích/khuyến nghị dựa trên context
    đã tính sẵn của giao dịch đang xem. Vai trò là TỔNG HỢP + GIẢI THÍCH, không
    phải đọc lại số panel. KHÔNG tự ý query lại Neo4j/ChromaDB/model theo câu hỏi
    tự do (xem Phần 12.2 trong hướng dẫn vì sao tách riêng do rủi ro bảo mật)."""
    st.markdown("#### 💬 Hỏi đáp về giao dịch này")

    question = st.text_input(
        "Đặt câu hỏi, ví dụ: 'Tại sao Risk cao?', 'PPR nghĩa là gì?', 'Có cần lập STR không?'",
        key="chat_question",
    )
    ask_clicked = st.button("Hỏi AI", key="chat_ask_btn")

    if ask_clicked and question.strip():
        from agents.regulation_rag import call_llm_api
        import json as _json

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

Câu hỏi của chuyên viên: "{question}"

Hãy tổng hợp, giải thích bằng ngôn ngữ nghiệp vụ dễ hiểu, và đưa khuyến nghị hành động cụ thể
nếu câu hỏi yêu cầu (VD: có nên lập STR không). CHỈ được dùng dữ liệu trong JSON trên, không suy
diễn hay bịa thêm số liệu/căn cứ pháp lý nào khác. Nếu câu hỏi hỏi về 1 thực thể/địa chỉ/dịch vụ
KHÔNG có trong dữ liệu trên (VD: không có trong kyc_flags), phải trả lời rõ "chưa được kiểm tra
trong phạm vi dữ liệu hiện có" -- TUYỆT ĐỐI không khẳng định "không liên quan" nếu chưa thực sự
được kiểm tra, vì đây là hồ sơ AML và 1 câu trả lời sai kiểu này là 1 false negative thật."""

        with st.spinner("Đang tổng hợp câu trả lời..."):
            answer = call_llm_api(prompt)
        st.session_state.setdefault("chat_history", []).append((question, answer))

    for q, a in reversed(st.session_state.get("chat_history", [])):
        st.markdown(f"**Hỏi:** {q}")
        st.markdown(f"**AI:** {a}")
        st.divider()


st.title("DigitalAsset Guard AI Copilot")
st.caption(
    "Demo luồng nghiệp vụ 8 bước (SPEC.md §2): Webhook → Privacy Layer → "
    "5 Assistants → Human-in-the-loop → STR."
)

# PipelineRun giữ app/thread_config của LangGraph (nếu có) -- BẮT BUỘC lưu
# trong session_state để resume() sau khi bấm Approve/Reject vẫn trỏ đúng
# 1 lượt chạy (Streamlit chạy lại toàn bộ script mỗi lần tương tác).
if "pipeline_run" not in st.session_state:
    st.session_state.pipeline_run = None
if "step_log" not in st.session_state:
    st.session_state.step_log = []

# ---------------------------------------------------------------------------
# FORM NHẬP GIAO DỊCH
# ---------------------------------------------------------------------------
with st.form("transaction_form"):
    st.subheader("1. Nhập giao dịch (mô phỏng webhook)")
    col1, col2 = st.columns(2)
    with col1:
        tx_hash = st.text_input("Tx Hash", value="")
        wallet_from = st.text_input("Ví nguồn (wallet_from)", value="0xbadwallet123")
        wallet_to = st.text_input("Ví đích (wallet_to)", value="0xdestination_wallet_demo")
        amount_vnd = st.number_input(
            "Giá trị giao dịch (VND)", min_value=0.0, value=620_000_000.0, step=1_000_000.0
        )
    with col2:
        st.markdown("**Thông tin định danh khách hàng (PII gốc)**")
        st.caption("Sẽ bị băm SHA-256 ngay tại Privacy Layer, không lưu bản gốc vào state.")
        fullname = st.text_input("Họ và tên", value="Nguyễn Văn A")
        id_number = st.text_input("Số CCCD/Hộ chiếu", value="001096001234")
        account_number = st.text_input("Số tài khoản ngân hàng", value="1903456789012")

    submitted = st.form_submit_button("Kích hoạt webhook / Chạy pipeline")

if submitted:
    raw_transaction = {
        "tx_hash": tx_hash or None,
        "wallet_from": wallet_from,
        "wallet_to": wallet_to,
        "amount_vnd": amount_vnd,
        "fullname": fullname,
        "id_number": id_number,
        "account_number": account_number,
    }

    st.subheader("2. Tiến trình xử lý từng Assistant")
    run = PipelineRun()
    step_log = []
    try:
        for step_key, label, snapshot in run.steps(raw_transaction):
            step_log.append((step_key, label, snapshot))
            with st.status(label, state="complete"):
                if step_key == "webhook" and snapshot.get("skipped"):
                    st.warning(snapshot["reason"])
                else:
                    st.json(snapshot)
    except RuntimeError as e:
        # Ví dụ: thiếu PII_SALT trong .env
        st.error(f"Lỗi cấu hình: {e}")
    except FileNotFoundError as e:
        st.error(
            f"Thiếu dữ liệu/model cần thiết cho pipeline: {e}. "
            "Kiểm tra lại models/xgboost_aml.pkl, data/processed/sample_ofac_wallet.txt..."
        )
    except Exception as e:
        st.error(f"Lỗi không mong muốn khi chạy pipeline: {e}")

    st.session_state.step_log = step_log
    st.session_state.pipeline_run = run

# ---------------------------------------------------------------------------
# ĐIỂM DỪNG CHỜ DUYỆT (HUMAN-IN-THE-LOOP)
# ---------------------------------------------------------------------------
run = st.session_state.pipeline_run
state = run.state if run else {}

if state:
    st.subheader("3. Kết quả & điểm dừng chờ duyệt")

    if state.get("skipped"):
        st.info("Giao dịch chưa vượt ngưỡng báo cáo -- không có gì để duyệt.")

    elif state.get("approval_status") == "pending":
        st.warning(
            f"⚠️ Giao dịch VƯỢT ngưỡng cảnh báo -- final_risk_score = "
            f"**{state.get('final_risk_score')}** (>= 0.7). Cần chuyên viên phê duyệt "
            f"trước khi gửi STR (Thông tư 27)."
        )
        st.write("**Cờ KYC/OFAC:**", state.get("kyc_flags") or "Không phát hiện")

        # [Bổ sung -- Phần 10.a, 10.b, 10.c] Thay 3 dòng st.write đơn giản cũ bằng
        # 3 panel hiển thị nâng cao, đọc cùng đúng những trường state đã có.
        _render_explainable_panel(state)
        _render_legal_citations_panel(state)
        _render_graph_panel(state)

        # [Bổ sung -- Phần 10.d] Report Preview trước khi Approve.
        _render_report_preview(state)

        col_a, col_b = st.columns(2)
        with col_a:
            approve_clicked = st.button("✅ Approve (Duyệt & gửi STR)", type="primary")
        with col_b:
            reject_clicked = st.button("❌ Reject (Từ chối)")

        if approve_clicked:
            run.resume("approved")
            st.rerun()
        if reject_clicked:
            run.resume("rejected")
            st.rerun()

    elif state.get("approval_status") == "approved":
        st.success("✅ Giao dịch đã được duyệt (approved).")
        report_path = state.get("report_path")
        if report_path and os.path.exists(report_path):
            with open(report_path, "rb") as f:
                st.download_button(
                    label="📄 Tải bản dự thảo STR (.docx)",
                    data=f.read(),
                    file_name=os.path.basename(report_path),
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
        else:
            st.caption(
                "Giao dịch dưới ngưỡng cảnh báo (final_risk_score < 0.7) -- "
                "tự động approved, không cần lập STR."
            )

    elif state.get("approval_status") == "rejected":
        st.error(
            "❌ Giao dịch đã bị TỪ CHỐI. Bản dự thảo STR (nếu có) vẫn được lưu lại "
            f"tại `{state.get('report_path')}` để lưu vết, nhưng KHÔNG được gửi đi."
        )

    # [Bổ sung -- Phần 12.1] Chat hỏi-đáp -- hiện với mọi trạng thái duyệt (pending/
    # approved/rejected), miễn pipeline đã chạy xong (có final_risk_score), để
    # chuyên viên có thể hỏi bất cứ lúc nào sau khi xem kết quả.
    if state.get("final_risk_score") is not None:
        st.divider()
        _render_chat_panel(state)

# ---------------------------------------------------------------------------
# NHẬT KÝ CHI TIẾT (tuỳ chọn, expand khi cần debug)
# ---------------------------------------------------------------------------
if st.session_state.step_log:
    with st.expander("Xem chi tiết state đầy đủ qua từng bước"):
        for step_key, label, snapshot in st.session_state.step_log:
            st.markdown(f"**{label}**")
            st.json(snapshot)