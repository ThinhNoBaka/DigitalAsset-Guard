import os
from datetime import datetime
from typing import Dict, Any
from docx import Document
from docx.shared import Pt
from core.privacy_layer import assert_no_raw_pii  # Chốt bảo mật bắt buộc

"""
Report Assistant — sinh dự thảo báo cáo STR (Mẫu số 04, Thông tư 27/2025/TT-NHNN).

=== GHI CHÚ VỀ CÔNG THỨC WEIGHTED RISK SCORE (đọc trước khi sửa) ===

    Final = 0.2 * Classifier + 0.3 * KYC + 0.5 * Graph

Nguồn gốc: công thức và 3 trọng số này KHÔNG xuất hiện trong Nghiên cứu khả thi gốc
(AML_KYC.pdf) lẫn SPEC.md — cả hai chỉ mô tả CÁCH từng trợ lý tự tính điểm của nó
(XGBoost, PPR/Louvain, fuzzy-match OFAC), không mô tả cách HỢP NHẤT 3 điểm lại.
Đây là quyết định kỹ thuật tự thêm khi code. Trọng số 0.2/0.3/0.5 là lựa chọn
heuristic dựa trên lập luận định tính ("khó giả mạo hơn" => trọng số cao hơn),
KHÔNG phải kết quả suy ra từ dữ liệu hay AHP — cần nêu rõ điều này khi giải trình,
đừng để hội đồng hiểu nhầm đây là con số đã được kiểm chứng định lượng.

HẠN CHẾ của phép cộng có trọng số (linear weighted sum):
    Một tín hiệu rất cao có thể bị "pha loãng" bởi 2 tín hiệu thấp còn lại.
    Ví dụ: KYC khớp CHÍNH XÁC (exact match) với ví trong OFAC SDN — về nguyên tắc
    phải luôn bị chặn — nhưng nếu Graph/Classifier thấp, KYC chỉ đóng góp tối đa
    0.3 điểm và có thể không đủ vượt ngưỡng 0.7.

CÁC HƯỚNG THAY THẾ (tham khảo, chưa bắt buộc code toàn bộ trong MVP):
    1) Hard-override / rule-first: tín hiệu "cứng" (khớp chính xác sanctions list)
       không hoà trộn với tín hiệu "mềm" (dự đoán ML) — luôn ép STR bất kể điểm
       tổng. ĐÃ CÀI ĐẶT bên dưới dưới dạng cờ tuỳ chọn `kyc_exact_match`, mặc định
       tắt để không phá vỡ hành vi cũ.
    2) Max-based escalation: final = max(w_i * s_i) thay vì cộng — chống pha
       loãng nhưng mất khả năng phản ánh rủi ro cộng dồn đa tín hiệu.
    3) Học trọng số từ dữ liệu (meta-model, logistic regression/stacking) trên
       nhãn STR đã duyệt trong quá khứ — chính xác hơn nhưng cần dữ liệu nhãn
       thật, chưa khả thi ở giai đoạn MVP hiện tại.
    4) AHP (Analytic Hierarchy Process): phỏng vấn chuyên viên AML để xây ma
       trận so sánh cặp, suy ra trọng số có thể giải trình định lượng — tốn
       thời gian hơn nhưng thuyết phục hội đồng tốt hơn.

=> MVP hiện tại: giữ nguyên linear weighted sum (đúng như SPEC.md đã thống nhất),
   chỉ bổ sung thêm hướng (1) làm safety-net, KHÔNG thay toàn bộ công thức.
"""

# Thư mục gốc của project — dùng để build đường dẫn output TUYỆT ĐỐI.
# Trước đây dùng "reports/output" (đường dẫn tương đối) sẽ phụ thuộc vào cwd lúc
# script được gọi (demo_run.py / ui/app.py / api/main.py có thể chạy từ thư mục
# khác nhau) => dễ ghi nhầm chỗ hoặc tạo nhiều thư mục reports rải rác.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Mô phỏng Hệ thống Lưu trữ Bảo mật On-Premise (Secure Vault Lookup Table)
# Trong môi trường sản xuất, đây là database nội bộ bảo mật cao, tách biệt hoàn
# toàn với AI State (không nằm chung file/module với logic report như ở đây).
MOCK_SECURE_VAULT = {
    "hashed_fullname": {
        "8c949c252445d4f6d0f5e55b1f50f4a2cfcb64d4b31a5e12be8f0e53a5c954e7": "NGUYỄN VĂN A",
        "d7a8fbb307d7809469ca9abcb0082e4f8d5651e4a54f261962f02219808d6d37": "TRẦN THỊ B"
    },
    "hashed_id_number": {
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855": "001096001234",
        "f5d3a35e2124536789abcd0123456789abcdef0123456789abcdef0123456789": "002095009876"
    },
    "hashed_account_number": {
        "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3": "1903456789012",
        "b886b56031533fa0528f5978fadc5fc9b15b2f4fff2fa08fa09f9f8f8f828bf4": "102987654321"
    }
}


def secure_lookup(field_type: str, hashed_value: str) -> str:
    """
    Hàm tra cứu ngược PII từ kho lưu trữ on-premise an toàn dựa trên bản băm.
    Tuyệt đối không đưa dữ liệu thô này ngược trở lại AMLState — chỉ dùng cục bộ
    để in vào file .docx cuối cùng (đây là điểm giải mã hợp lệ duy nhất theo §4).
    """
    if not hashed_value:
        return "[CHƯA CUNG CẤP]"
    return MOCK_SECURE_VAULT.get(field_type, {}).get(hashed_value, f"[Mã băm: {hashed_value[:8]}...]")


def _add_centered_line(doc: Document, text: str, bold: bool = False, italic: bool = False):
    """
    Thêm MỘT dòng dạng Paragraph riêng, căn giữa.

    LƯU Ý QUAN TRỌNG: KHÔNG nhét '\\n' vào bên trong run.text như bản cũ từng làm
    (vd add_run("...\\n")). python-docx không coi '\\n' trong text của một run là
    dấu xuống dòng — khi mở bằng Word, ký tự này chỉ là literal, khiến quốc hiệu/
    tiêu ngữ/tiêu đề dễ bị dính liền hoặc hiển thị sai. Mỗi dòng phải là một
    Paragraph riêng biệt (hoặc dùng run.add_break() nếu cần ngắt dòng trong cùng
    1 paragraph).
    """
    p = doc.add_paragraph()
    p.alignment = 1  # Center
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    return p


def generate_alert_report(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Report Assistant: Chịu trách nhiệm tổng hợp trọng số rủi ro, kiểm tra ngưỡng
    cảnh báo và biên soạn dự thảo báo cáo STR (Mẫu số 04 theo Thông tư
    27/2025/TT-NHNN). Xem docstring module ở đầu file để biết lý do chọn trọng số
    và các hướng thay thế đã cân nhắc.
    """
    # 1. Quét bảo mật đầu vào - Đảm bảo state không chứa PII gốc
    assert_no_raw_pii(state)

    # 2. Thu thập dữ liệu thành phần từ các Agent trước.
    # Dùng "or default" nhất quán cho MỌI trường số — bản cũ chỉ áp dụng cho
    # clf_score/kyc_flags/graph_risk, còn amount_vnd/tx_hash thì không, dẫn tới
    # nguy cơ TypeError khi state có key nhưng giá trị là None.
    clf_score = state.get("risk_score_classifier", 0.0) or 0.0
    kyc_flags = state.get("kyc_flags", []) or []
    graph_risk = state.get("graph_risk_score", 0.0) or 0.0
    amount_vnd = state.get("amount_vnd", 0) or 0
    tx_hash = state.get("tx_hash") or "UNKNOWN_HASH"

    # 3. Chuẩn hóa các thành phần về cùng thang điểm từ 0.0 đến 1.0
    # Chuẩn hóa kyc_flags: cứ mỗi flag tương ứng 0.5 điểm, tối đa 1.0.
    # LƯU Ý: 0.5 điểm/flag là quy ước tự chọn khi code, KHÔNG có trong SPEC hay
    # báo cáo khả thi (vd: từ 2 flag trở lên điểm KYC luôn = 1.0 tối đa) — nêu rõ
    # khi giải trình, cùng tinh thần với ghi chú trọng số ở đầu file.
    KYC_POINTS_PER_FLAG = 0.5
    kyc_score_normalized = min(len(kyc_flags) * KYC_POINTS_PER_FLAG, 1.0)
    clf_score_normalized = min(max(float(clf_score), 0.0), 1.0)
    graph_score_normalized = min(max(float(graph_risk), 0.0), 1.0)

    # 4. Tính điểm rủi ro hợp nhất theo công thức quy chuẩn (xem docstring đầu file)
    final_risk_score = (
        (0.2 * clf_score_normalized)
        + (0.3 * kyc_score_normalized)
        + (0.5 * graph_score_normalized)
    )
    state["final_risk_score"] = round(final_risk_score, 4)

    # 4b. Safety-net override (MẶC ĐỊNH TẮT): nếu KYC Assistant xác nhận khớp
    # CHÍNH XÁC (exact match — không phải fuzzy match) với danh sách trừng phạt,
    # không để weighted sum pha loãng tín hiệu cứng này — ép luôn vượt ngưỡng STR.
    # Bật bằng cách để KYC Assistant set state["kyc_exact_match"] = True khi tìm
    # thấy khớp tuyệt đối trên OFAC SDN / UN / NHNN.
    kyc_exact_match = bool(state.get("kyc_exact_match", False))
    if kyc_exact_match:
        state["final_risk_score"] = max(state["final_risk_score"], 1.0)
        final_risk_score = state["final_risk_score"]
        print("[!] Khớp CHÍNH XÁC danh sách trừng phạt — override bắt buộc STR bất kể điểm tổng hợp.")

    print(f"[*] Điểm rủi ro hợp nhất tính toán được: {state['final_risk_score']}")

    # 4c. [Bổ sung -- Thay đổi 5] Explainable Risk Assessment: % đóng góp THẬT của
    # từng thành phần vào final_risk_score, suy trực tiếp từ công thức 3 trọng số
    # đã công bố ở trên (0.2/0.3/0.5) -- KHÁC với việc gán % tùy ý cho từng yếu tố
    # đồ thị con (PPR/hop/fan-out/community) vốn không có căn cứ định lượng.
    # Tính cho MỌI giao dịch (không chỉ khi vượt ngưỡng STR) để UI (Phần 10.a)
    # luôn hiển thị được, kể cả với giao dịch dưới ngưỡng.
    if final_risk_score > 0:
        classifier_contribution_pct = round((0.2 * clf_score_normalized) / final_risk_score * 100, 1)
        kyc_contribution_pct = round((0.3 * kyc_score_normalized) / final_risk_score * 100, 1)
        graph_contribution_pct = round((0.5 * graph_score_normalized) / final_risk_score * 100, 1)
    else:
        classifier_contribution_pct = kyc_contribution_pct = graph_contribution_pct = 0.0

    state["risk_breakdown"] = {
        "classifier_contribution_pct": classifier_contribution_pct,
        "kyc_contribution_pct": kyc_contribution_pct,
        "graph_contribution_pct": graph_contribution_pct,
    }

    # 5. Kiểm tra ngưỡng kích hoạt lập báo cáo STR (Ngưỡng quy định >= 0.7)
    if final_risk_score >= 0.7:
        print(f"[!] Điểm vượt ngưỡng an toàn (>= 0.7). Bắt đầu biên soạn dự thảo STR...")

        # Đường dẫn xuất file báo cáo — TUYỆT ĐỐI, không phụ thuộc cwd
        output_dir = os.path.join(_PROJECT_ROOT, "reports", "output")
        os.makedirs(output_dir, exist_ok=True)
        report_filename = f"STR_REPORT_{tx_hash}.docx"
        report_path = os.path.join(output_dir, report_filename)

        # Khởi tạo python-docx dựng mẫu báo cáo số 04
        doc = Document()

        # Định dạng style chung cho văn bản hành chính Việt Nam
        style = doc.styles['Normal']
        font = style.font
        font.name = 'Times New Roman'
        font.size = Pt(12)

        # Quốc hiệu / Tiêu ngữ — mỗi dòng MỘT paragraph riêng (không dùng \n)
        _add_centered_line(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bold=True)
        _add_centered_line(doc, "Độc lập - Tự do - Hạnh phúc", bold=True)
        _add_centered_line(doc, "---------------***---------------")
        doc.add_paragraph()

        # Tên báo cáo theo biểu mẫu Thông tư 27
        _add_centered_line(doc, "BÁO CÁO GIAO DỊCH ĐÁNG NGỜ (STR)", bold=True)
        _add_centered_line(doc, "(Mẫu số 04 - Ban hành kèm theo Thông tư số 27/2025/TT-NHNN)", italic=True)
        _add_centered_line(doc, f"Ngày lập báo cáo: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        doc.add_paragraph()

        # PHẦN I: THÔNG TIN ĐỐI TƯỢNG BỊ BÁO CÁO
        doc.add_heading("PHẦN I: THÔNG TIN KHÁCH HÀNG / ĐỐI TƯỢNG BỊ BÁO CÁO", level=2)

        # ĐÂY LÀ NƠI DUY NHẤT GIẢI MÃ LOOKUP ĐỂ HIỂN THỊ CHO CHUYÊN VIÊN
        raw_name = secure_lookup("hashed_fullname", state.get("hashed_fullname", ""))
        raw_id = secure_lookup("hashed_id_number", state.get("hashed_id_number", ""))
        raw_account = secure_lookup("hashed_account_number", state.get("hashed_account_number", ""))

        doc.add_paragraph(f"- Họ và tên khách hàng: {raw_name}")
        doc.add_paragraph(f"- Số CCCD/Hộ chiếu: {raw_id}")
        doc.add_paragraph(f"- Số tài khoản ngân hàng liên kết: {raw_account}")
        doc.add_paragraph(f"- Địa chỉ ví nhận tài sản số (On-chain): {state.get('wallet_to', '[CHƯA RÕ]')}")

        # PHẦN II: THÔNG TIN KỸ THUẬT VÀ CHẤM ĐIỂM RỦI RO
        doc.add_heading("PHẦN II: KẾT QUẢ PHÂN TÍCH VÀ CHẤM ĐIỂM RỦI RO", level=2)

        table = doc.add_table(rows=1, cols=2)
        table.style = 'Light Shading Accent 1'
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = 'Chỉ số phân tích kỹ thuật'
        hdr_cells[1].text = 'Giá trị ghi nhận'

        metrics = [
            ("Mã định danh giao dịch (Tx Hash)", str(tx_hash)),
            ("Giá trị quy đổi giao dịch (VND)", f"{amount_vnd:,} VND"),
            ("Điểm rủi ro phân lớp sơ bộ (XGBoost)", str(round(clf_score, 4))),
            ("Danh sách cờ vi phạm (KYC/OFAC Flags)", ", ".join(kyc_flags) if kyc_flags else "Không phát hiện"),
            ("Điểm lan truyền đồ thị (Neo4j local PPR)", str(round(graph_risk, 4))),
            ("Mã định danh cộng đồng Louvain", str(state.get("community_id", "unknown"))),
            ("Khớp chính xác danh sách trừng phạt (override)", "Có" if kyc_exact_match else "Không"),
            ("ĐIỂM RỦI RO HỢP NHẤT HỆ THỐNG", str(state["final_risk_score"])),
        ]

        for metric_name, val in metrics:
            row_cells = table.add_row().cells
            row_cells[0].text = metric_name
            row_cells[1].text = val

        doc.add_paragraph()

        # PHẦN III: LẬP LUẬN PHÁP LÝ (TỪ RAG AGENT)
        doc.add_heading("PHẦN III: CĂN CỨ PHÁP LÝ VÀ PHÂN TÍCH TUÂN THỦ", level=2)
        # [Bổ sung -- Thay đổi 4] regulation_rag.py (Phần 7) giờ trả legal_citations
        # dạng list[dict] có cấu trúc thay vì 1 chuỗi văn bản tự do. Vẫn giữ nhánh
        # xử lý string để tương thích ngược nếu state cũ (trước bản structured
        # output) được truyền vào đây.
        legal_citations = state.get("legal_citations")
        if isinstance(legal_citations, str):
            doc.add_paragraph(legal_citations or "Không có trích dẫn pháp lý tự động.")
        elif legal_citations:
            for citation in legal_citations:
                if "raw_text" in citation and len(citation) == 1:
                    doc.add_paragraph(citation["raw_text"])
                    continue
                source = citation.get("source", "(không rõ nguồn)")
                dieu_khoan = citation.get("dieu_khoan", "")
                p_cite = doc.add_paragraph()
                p_cite.add_run(source + (f" — {dieu_khoan}" if dieu_khoan else "")).bold = True
                if citation.get("noi_dung_tom_tat"):
                    doc.add_paragraph(f"Nội dung: {citation['noi_dung_tom_tat']}")
                if citation.get("ly_do_ap_dung"):
                    doc.add_paragraph(f"Áp dụng vì: {citation['ly_do_ap_dung']}")
        else:
            doc.add_paragraph("Không có trích dẫn pháp lý tự động.")

        # PHẦN IV: MÔ TẢ HÀNH VI ĐÁNG NGỜ VÀ DÒNG TIỀN ĐA HOP
        doc.add_heading("PHẦN IV: MÔ TẢ HÀNH VI ĐÁNG NGỜ VÀ DÒNG TIỀN ĐA HOP", level=2)
        p_flow = doc.add_paragraph()
        p_flow.add_run(
            f"Hệ thống phát hiện ví mục tiêu {state.get('wallet_to', '')} có liên hệ cấu trúc chặt chẽ "
            f"với cụm cộng đồng mang Louvain ID {state.get('community_id', 'unknown')}. "
        ).italic = True
        p_flow.add_run(
            "Hành vi này có dấu hiệu cố tình chia nhỏ dòng tiền từ các ví thuộc danh sách đen cấm vận "
            "quốc tế trước khi chuyển đổi ngược lại sang hệ thống tiền pháp định ngân hàng Việt Nam, "
            "vi phạm các dấu hiệu đáng ngờ cơ bản."
        )

        # PHẦN V: [Bổ sung -- Thay đổi 5] GIẢI TRÌNH MINH BẠCH RỦI RO
        doc.add_heading("PHẦN V: GIẢI TRÌNH MINH BẠCH RỦI RO (EXPLAINABLE RISK ASSESSMENT)", level=2)

        breakdown = state.get("risk_breakdown") or {}
        p_breakdown = doc.add_paragraph()
        p_breakdown.add_run(
            f"Điểm rủi ro hợp nhất {state['final_risk_score']} được cấu thành từ 3 nguồn tín hiệu theo "
            f"công thức Final = 0.2×Classifier + 0.3×KYC + 0.5×Graph (xem docstring module đầu file để "
            f"biết lý do chọn trọng số này):"
        ).italic = True
        doc.add_paragraph(
            f"- Đóng góp từ mô hình phân loại (Transaction Assistant / XGBoost): "
            f"{breakdown.get('classifier_contribution_pct', 0)}%"
        )
        doc.add_paragraph(
            f"- Đóng góp từ sàng lọc danh sách trừng phạt (KYC Assistant): "
            f"{breakdown.get('kyc_contribution_pct', 0)}%"
        )
        doc.add_paragraph(
            f"- Đóng góp từ phân tích đồ thị dòng tiền (Graph Assistant): "
            f"{breakdown.get('graph_contribution_pct', 0)}%"
        )

        top_features = state.get("top_features") or []
        if top_features:
            features_text = ", ".join(f"{name} ({round(score, 3)})" for name, score in top_features)
            doc.add_paragraph(
                f"Đặc trưng có ảnh hưởng lớn nhất trong mô hình phân loại (top feature importance "
                f"TOÀN CỤC, không phải giải thích riêng cho giao dịch này): {features_text}"
            )

        hop = state.get("hop_distance_to_blacklist")
        fan_out_val = state.get("fan_out")
        graph_notes = []
        if hop is not None:
            graph_notes.append(f"cách ví trong danh sách trừng phạt {hop} hop giao dịch")
        if fan_out_val is not None:
            graph_notes.append(f"fan-out = {fan_out_val}")
        graph_notes.append(f"thuộc cộng đồng Louvain #{state.get('community_id', 'unknown')}")
        doc.add_paragraph("Yếu tố đồ thị đáng chú ý: " + "; ".join(graph_notes) + ".")

        # Lưu file xuống đĩa
        doc.save(report_path)

        state["report_path"] = report_path
        state["approval_status"] = "pending"
    else:
        print(f"[*] Điểm rủi ro nằm trong ngưỡng an toàn ({state['final_risk_score']} < 0.7). Bỏ qua bước lập STR.")
        state["report_path"] = None
        state["approval_status"] = "approved"  # Tự động duyệt đối với các giao dịch an toàn

    return state    


if __name__ == "__main__":
    # Chạy thử độc lập: python -m agents.alert_report
    demo_state = {
        "tx_hash": "0xDEMO_ALERT_REPORT",
        "amount_vnd": 650_000_000,
        "risk_score_classifier": 0.62,
        "kyc_flags": ["OFAC_SDN_FUZZY_MATCH", "UN_LIST_PARTIAL"],
        "graph_risk_score": 0.81,
        "community_id": "louvain_cluster_17",
        "wallet_to": "0xDEADBEEF00000000000000000000000000CAFE",
        "hashed_fullname": "8c949c252445d4f6d0f5e55b1f50f4a2cfcb64d4b31a5e12be8f0e53a5c954e7",
        "hashed_id_number": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "hashed_account_number": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
        # [Bổ sung -- Thay đổi 4] legal_citations giờ là list[dict] có cấu trúc
        "legal_citations": [
            {
                "source": "Thông tư 27/2025/TT-NHNN",
                "dieu_khoan": "Điều 6",
                "noi_dung_tom_tat": "Quy định về dấu hiệu giao dịch đáng ngờ và ngưỡng báo cáo.",
                "ly_do_ap_dung": "Giao dịch vượt ngưỡng 500 triệu VND và có dấu hiệu lan truyền rủi ro qua đồ thị.",
            }
        ],
        # [Bổ sung -- Thay đổi 2, 3] dữ liệu Explainable AI để test PHẦN V
        "top_features": [("in_degree", 0.31), ("total_value", 0.24), ("out_degree", 0.18)],
        "hop_distance_to_blacklist": 2,
        "fan_out": 47,
    }
    result = generate_alert_report(demo_state)
    print("report_path:", result["report_path"])
    print("approval_status:", result["approval_status"])
    print("final_risk_score:", result["final_risk_score"])