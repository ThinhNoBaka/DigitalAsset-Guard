"""
agents/regulation_rag.py
Agent tra cứu và trích dẫn căn cứ pháp lý dựa trên đặc trưng cấu trúc
từ Graph Assistant + Transaction Assistant.
"""
import os
import json
import re
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv
from core.privacy_layer import assert_no_raw_pii
from db.vector_db import query_legal_docs

# BẮT BUỘC: load biến môi trường từ .env — mỗi file Python muốn đọc .env
# đều phải tự gọi load_dotenv() riêng, không tự động chia sẻ giữa các module.
load_dotenv()

# Cấu hình nhà cung cấp LLM qua .env — TỔNG QUÁT cho mọi API chuẩn OpenAI
# (Groq, OpenRouter, Xiaomi MiMo, Together AI, v.v.). Đổi nhà cung cấp chỉ
# cần đổi 3 biến này trong .env, KHÔNG cần sửa code.
#
# Ví dụ Groq (free tier vĩnh viễn, không cần thẻ):
#   LLM_BASE_URL=https://api.groq.com/openai/v1
#   LLM_MODEL=llama-3.3-70b-versatile
#   LLM_API_KEY=gsk_...
#
# Ví dụ OpenRouter (nhiều model free, đánh dấu ":free" trong tên model):
#   LLM_BASE_URL=https://openrouter.ai/api/v1
#   LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
#   LLM_API_KEY=sk-or-...
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# [Bổ sung -- sửa lỗi LLM tự bịa sai tên cơ quan ban hành, VD trả về
# "Thông tư 32/2026/TT-NHNN" (sai) thay vì đúng "Thông tư 32/2026/TT-BTC"].
# Không tin LLM tự nhớ đúng tên -- ép nó chỉ được chọn "source_file" khớp
# ĐÚNG tên file đã truy xuất thật từ ChromaDB, sau đó code tự ánh xạ sang tên
# chính thức bằng dict cố định này, không phụ thuộc trí nhớ của model.
CANONICAL_SOURCE_NAMES = {
    "thong_tu_27_2025.txt": "Thông tư 27/2025/TT-NHNN",
    "thong_tu_32_2026.txt": "Thông tư 32/2026/TT-BTC",
    "fatf_recommendations.txt": "Khuyến nghị FATF (FATF Recommendations)",
    # [FIX 2026-08-09] Nguồn Luật PCRT 2022 + Quyết định 11/2023/QĐ-TTg (văn bản
    # có ngưỡng 400.000.000 đồng cho giao dịch giá trị lớn) — được thêm để RAG
    # trích ĐÚNG con số ngưỡng CTR thay vì chỉ trích dẫn chiếu từ TT27 (Điều 6).
    "luat_pcrt_2022_qd11_2023.txt": "Luật Phòng, chống rửa tiền 2022 + Quyết định 11/2023/QĐ-TTg",
}


def call_llm_api(prompt: str) -> str:
    """
    Gọi LLM thật qua API chuẩn OpenAI-compatible (thư viện `openai`),
    dùng được cho Groq, OpenRouter, Xiaomi MiMo, hay bất kỳ nhà cung cấp nào
    expose endpoint kiểu /v1/chat/completions.
    Nếu chưa có LLM_API_KEY trong .env -> fallback mock (test logic không cần key).
    Nếu có key nhưng gọi API lỗi -> báo lỗi rõ ràng, không âm thầm giả vờ ổn.

    LƯU Ý HỢP ĐỒNG API: hàm này được api/main.py (chat) import và gọi, trả `str`
    — KHÔNG được đổi signature. Nếu cần phân biệt trạng thái lỗi, gọi
    `_call_llm_api_internal` (trả (text, status)) — dùng trong `run_regulation_rag`
    để KHÔNG đẩy mock content vào STR.
    """
    return _call_llm_api_internal(prompt)[0]


def _call_llm_api_internal(prompt: str) -> Tuple[str, str]:
    """
    [FIX 2026-08-09 — LLM lỗi phải được BÁO RÕ, không giấu thành data thật]
    Bản nội bộ của call_llm_api, trả về (text, status):
      - status == "OK": gọi LLM thành công, text là output thật.
      - status == "MOCK_NO_KEY": chưa có LLM_API_KEY trong .env — text là mock
        dành cho test logic (KHÔNG dùng cho STR). run_regulation_rag coi đây
        cũng là lỗi UNAVAILABLE.
      - status == "UNAVAILABLE": API call raise exception hoặc response rỗng —
        text là mock mô tả lỗi (KHÔNG dùng cho STR). run_regulation_rag coi đây
        là lỗi UNAVAILABLE → legal_citations = [] và STR in cảnh báo tường minh.
    """
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return ("[Mock LLM Output]: Dựa trên truy vấn, giao dịch vi phạm quy định báo cáo "
                "(Thông tư 27) hoặc phát sinh nghĩa vụ thuế (Thông tư 32)... "
                "(Vui lòng điền LLM_API_KEY vào .env để chạy thật)",
                "MOCK_NO_KEY")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=LLM_BASE_URL)
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        text = response.choices[0].message.content
        if not text:
            raise ValueError("LLM trả về response rỗng (message.content is None)")
        return text.strip(), "OK"

    except Exception as e:
        # KHÔNG âm thầm nuốt lỗi thành output giống mock bình thường —
        # ghi rõ gọi API thật bị lỗi + nguyên nhân, để dễ debug và phân biệt
        # với trường hợp "chưa điền key" ở nhánh trên.
        print(f"⚠️ Lỗi khi gọi Gemini API: {e}")
        return (f"[LLM API LỖI - dùng tạm Mock Output]: Không gọi được Gemini API ({e}). "
                f"Giao dịch vi phạm quy định báo cáo (Thông tư 27) hoặc phát sinh nghĩa vụ "
                f"thuế (Thông tư 32) — cần kiểm tra lại LLM_API_KEY hoặc kết nối mạng.",
                "UNAVAILABLE")


def _infer_asset_type(state: Dict[str, Any]) -> str:
    """
    SỬA LỖI Phần 7: 'asset_type' KHÔNG tồn tại trong AMLState (Phần 1-6 không
    field nào set nó), nên state.get("asset_type", "VND") sẽ luôn trả "VND"
    trong pipeline thật -> Thông tư 32 không bao giờ được kích hoạt.

    Tạm thời suy luận từ tín hiệu đã có sẵn: có địa chỉ ví on-chain
    (wallet_from/wallet_to không rỗng) => đây là giao dịch tài sản số.

    TODO: khi Transaction Assistant (Phần 4) hoặc webhook đầu vào (Phần 2)
    được nâng cấp để phân loại rõ loại giao dịch, thay thế hàm này bằng
    việc đọc trực tiếp state["asset_type"] do bước đó set.
    """
    explicit = state.get("asset_type")
    if explicit:
        return explicit
    has_wallet = bool(state.get("wallet_from") or state.get("wallet_to"))
    return "crypto" if has_wallet else "VND"


def _parse_legal_citations(raw_text: str) -> List[dict]:
    """
    [Bổ sung -- Thay đổi 4] Thử parse output LLM thành list[dict] theo schema
    legal_citations (source, dieu_khoan, noi_dung_tom_tat, ly_do_ap_dung), để UI
    (Phần 10.b) hiển thị đẹp thay vì nhận 1 blob text rồi tự parse bằng regex.

    Nếu LLM trả sai format (kể cả trường hợp mock/lỗi API ở call_llm_api trả về
    văn bản mô tả thường), KHÔNG raise lỗi -- giữ nguyên văn bản gốc trong 1 dict
    duy nhất {"raw_text": ...} để không mất dữ liệu và không làm gãy pipeline.
    """
    cleaned = raw_text.strip()

    # Một số model vẫn bọc JSON trong ```json ... ``` dù đã dặn không làm vậy
    # trong prompt -- bóc fence ra trước khi thử parse.
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)

    try:
        parsed = json.loads(cleaned)
        citations = parsed.get("legal_citations")
        if isinstance(citations, list) and citations:
            return citations
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    print("⚠️ RAG Assistant: LLM không trả đúng JSON schema legal_citations, dùng fallback raw_text.")
    return [{"raw_text": raw_text}]


def _normalize_citation_sources(citations: List[dict]) -> List[dict]:
    """
    [Bổ sung -- sửa lỗi LLM tự bịa sai tên cơ quan ban hành] Với mỗi citation có
    trường "source_file" (tên file LLM được yêu cầu chọn đúng từ danh sách nguồn
    đã truy xuất -- xem prompt), GHI ĐÈ trường "source" bằng tên chính thức lấy
    từ CANONICAL_SOURCE_NAMES, KHÔNG dùng "source" do LLM tự ghi (LLM hay nhớ
    nhầm TT-NHNN/TT-BTC giữa 2 thông tư). Nếu "source_file" không khớp map nào,
    hoặc không có (citation kiểu raw_text fallback), giữ nguyên không đổi.
    """
    for citation in citations:
        if "raw_text" in citation and len(citation) == 1:
            continue
        source_file = citation.get("source_file")
        if source_file in CANONICAL_SOURCE_NAMES:
            citation["source"] = CANONICAL_SOURCE_NAMES[source_file]
        elif not citation.get("source"):
            citation["source"] = "(Nguồn không xác định -- kiểm tra lại source_file)"
    return citations


def _merge_unique(results_list: List[Optional[dict]]) -> Tuple[List[str], List[str]]:
    """
    Gộp kết quả từ nhiều lượt truy vấn ChromaDB, loại trùng theo nội dung doc.
    Trả về (danh sách nội dung, danh sách nguồn) đã loại trùng, giữ thứ tự.
    """
    combined_docs, combined_sources = [], []
    for results in filter(None, results_list):
        if not results.get('documents') or not results['documents'][0]:
            continue
        for i, doc in enumerate(results['documents'][0]):
            if doc not in combined_docs:
                combined_docs.append(doc)
                combined_sources.append(results['metadatas'][0][i]['source'])
    return combined_docs, combined_sources


def run_regulation_rag(state: Dict[str, Any]) -> Dict[str, Any]:
    """Agent tra cứu và trích dẫn căn cứ pháp lý dựa trên ngữ cảnh giao dịch."""
    # 1. Chốt kiểm tra PII (bắt buộc theo SPEC)
    assert_no_raw_pii(state)

    # 2. Lấy đặc trưng từ state
    # SPEC_v2 §2: đổi tên risk_score_classifier -> classifier_score,
    # graph_risk_score -> graph_score (breaking change).
    amount_vnd = state.get("amount_vnd", 0)
    risk_score_clf = state.get("classifier_score", 0.0) or 0.0
    graph_risk = state.get("graph_score", 0.0) or 0.0

    # [FIX 2026-08-09 — RAG trích sai điều luật] Bổ sung trích xuất BẰNG CHỨNG
    # QUYẾT ĐỊNH thực tế (graph/sanction). Các field này do agents/decision_engine.py
    # ghi vào state TRƯỚC khi RAG chạy (xem core/graph_builder.py: decision_engine
    # -> regulation_rag). Dùng để (a) dựng câu query retrieval đúng hướng và
    # (b) đưa vào prompt để LLM đối chiếu — trước đây RAG chỉ thấy amount + điểm số.
    hop_distance = state.get("hop_distance_to_blacklist")
    suspicious_path = state.get("suspicious_path")
    sanction_result = state.get("sanction_result", {}) or {}
    sanction_path_found = state.get("sanction_path_found")
    decision = state.get("decision")
    decision_reason = state.get("decision_reason", "")
    decision_evidence = state.get("decision_evidence") or []

    # Tín hiệu graph/sanction = lý do REPORT thực sự (Rule 1/4 decision_engine):
    # sanction match, hoặc hop_distance <= 2 tới ví sanctioned trên đồ thị.
    # Khi có tín hiệu này, RAG phải ưu tiên tìm điều luật STR (Điều 7 TT27 /
    # Điều 26 Luật PCRT 2022), KHÔNG phải điều luật "giá trị lớn".
    decision_reason_lower = (decision_reason or "").lower()
    has_graph_or_sanction_evidence = (
        (hop_distance is not None and hop_distance <= 2)
        or sanction_result.get("is_match", False)
        or sanction_path_found is True
        or any(("graph exposure" in ev.lower() or "sanctioned" in ev.lower()) for ev in decision_evidence)
        or "sanctioned" in decision_reason_lower
        or "hop" in decision_reason_lower
    )

    # SỬA LỖI Phần 7: community_id trước đây đọc key "community_id" nhưng
    # graph_aml.py (Phần 6) không ghi key này ra state -> luôn "unknown".
    # Cần agents/graph_aml.py thêm dòng: state["community_id"] = comm_id
    community_id = state.get("community_id", "unknown")

    # SỬA LỖI Phần 7: dùng hàm suy luận thay vì đọc field không tồn tại
    crypto_type = _infer_asset_type(state)

    # 3. Mã hóa đặc trưng thành câu ngữ cảnh (dùng để hiển thị + đưa vào prompt LLM)
    context_query = f"Giao dịch trị giá {amount_vnd} VND."
    if graph_risk > 0.7:
        context_query += f" Có PPR score cao ({graph_risk}), thuộc cộng đồng nghi vấn (ID: {community_id})."
    if risk_score_clf > 0.7:
        context_query += f" Điểm rủi ro ML cơ sở cao ({risk_score_clf})."

    # 4. Logic phân biệt nguồn luật: yếu tố tài sản số / thuế
    is_crypto_tax_case = crypto_type == "crypto"
    if is_crypto_tax_case:
        context_query += " Giao dịch có yếu tố tài sản số phát sinh thu nhập."

    print(f"[*] RAG Query Context: {context_query}")

    # 5. Truy vấn ChromaDB — TÁCH RIÊNG 2 lượt để tránh câu truy vấn dài
    # bị pha trộn chủ đề (AML + Thuế), khiến embedding model nhỏ lệch kết quả
    # toàn bộ về phía chủ đề chiếm nhiều từ ngữ hơn (thường là AML).

    # 5a. Truy vấn nhánh AML/báo cáo — [FIX 2026-08-09] query ĐỘNG theo bằng
    # chứng quyết định thực tế + top_k=5. Trước đây query là câu STATIC giống
    # hệt cho mọi giao dịch và top_k=2: nó không hề biết lý do REPORT thật (VD
    # hop=2 tới ví sanctioned) nên 2 chunk trúng thường là Điều 6 (giá trị lớn)
    # + Điều 10.2 (thời hạn) — chunk Điều 7 (STR) không bao giờ vào context.
    #
    # Nguyên tắc: KHÔNG đưa amount_vnd vào câu query ChromaDB (con số này làm
    # lệch semantic search về nhóm "giá trị lớn" thay vì nhóm "đáng ngờ").
    # Thay vào đó dùng SIGNAL QUYẾT ĐỊNH (hop/sanction/path) làm trọng tâm.
    #
    # Tách 2 lượt query RIÊNG:
    #   - lượt STR: luôn chạy; nếu có bằng chứng graph/sanction, từ khóa STR
    #     ("kết nối tới ví bị trừng phạt/sanctioned, báo cáo không phụ thuộc
    #     lượng tiền") để kéo Điều 7 TT27 / Điều 26 Luật PCRT 2022 lên đầu;
    #   - lượt "giá trị lớn": chạy khi cần xác minh ngưỡng CTR (amount >= 400tr
    #     hoặc hàm lượng graph/sanction không rõ) — kéo Điều 6 TT27 / QĐ 11/2023.
    str_query = (
        "Nghĩa vụ báo cáo giao dịch đáng ngờ (STR): dấu hiệu đáng ngờ liên quan "
        "đến rửa tiền, tài trợ khủng bố; báo cáo không phụ thuộc vào lượng tiền "
        "giao dịch."
    )
    if has_graph_or_sanction_evidence:
        str_query += (
            " Giao dịch có bằng chứng quyết định là kết nối tới ví bị trừng phạt "
            "(sanctioned wallet) trên đồ thị dòng tiền — căn cứ báo cáo STR."
        )

    aml_results = query_legal_docs(query_text=str_query, n_results=5)

    # Lượt "giá trị lớn" — truy vấn song song khi need:
    #   - amount >= 400tr: cần xác minh ngưỡng CTR từ QĐ 11/2023 để trích Điều 6;
    #   - KHÔNG cần khi có bằng chứng graph/sanction (STR là căn cứ chính, giá trị
    #     nhỏ vẫn phải báo cáo STR — xem Điều 26 Luật PCRT 2022: "không phụ thuộc
    #     giá trị giao dịch"), nhưng vẫn chạy để LLM có đủ context đối chiếu và
    #     nói ĐÚNG "không đạt ngưỡng 400 triệu" thay vì im lặng.
    # [FIX 2026-08-09 v2 — chunk ngưỡng không được retrieval] File
    # luat_pcrt_2022_qd11_2023.txt được chunk theo dòng trống, nên câu chứa
    # "400.000.000 đồng trở lên" (QĐ 11/2023) bị tách thành chunk riêng và
    # đứng NGOÀI top-5 khi query chung chung (chỉ header "QUYẾT ĐỊNH..." và
    # "Đối tượng phải báo cáo khi đạt mức trên..." được trả về). LLM không
    # có con số ngưỡng trong context -> tự khẳng định sai "không đạt ngưỡng".
    # Sửa: đưa CHÍNH XÁC con số ngưỡng vào câu query (đúng mục đích xác minh
    # ngưỡng CTR — không phải hard-code điều luật) + tăng n_results để chắc
    # chắn chunk chứa số được retrieval.
    large_tx_query = (
        "Mức giao dịch có giá trị lớn phải báo cáo (CTR): ngưỡng 400.000.000 "
        "đồng trở lên theo Quyết định 11/2023/QĐ-TTg, thẩm quyền Thủ tướng "
        "Chính phủ, Điều 25 Luật PCRT 2022."
    )
    large_tx_results = query_legal_docs(query_text=large_tx_query, n_results=8)

    # 5b. Nếu có yếu tố crypto -> truy vấn RIÊNG nhánh thuế tài sản mã hóa.
    # [2026-08-09] Tính năng thuế đã chủ đích BỎ (Thông tư 32/2026/TT-BTC không còn
    # nguồn trong data/legal_docs). Giữ nhánh này nhưng CHỈ dùng kết quả nếu ChromaDB
    # thật sự trả về chunk có source=thong_tu_32_2026.txt:
    #   - nếu KHÔNG có chunk TT32, gán tax_results=None để không đẩy chunk TT27/FATF
    #     "gần nghĩa thuế" vào context (tránh LLM trích dẫn nhầm nguồn làm cơ sở thuế);
    #   - đồng thời gỡ cụm "phát sinh thu nhập" khỏi context_query để LLM không bị
    #     dẫn dắt suy diễn nghĩa vụ thuế khi không có nội dung thật trong context.
    tax_results = None
    if is_crypto_tax_case:
        tax_query = ("Công thức tính thuế thu nhập cá nhân 0.1% đối với chuyển "
                      "nhượng, giao dịch tài sản mã hóa.")
        tax_results_raw = query_legal_docs(query_text=tax_query, n_results=2)
        if tax_results_raw.get('documents') and tax_results_raw['documents'][0]:
            tax_docs, tax_metas = tax_results_raw['documents'][0], tax_results_raw['metadatas'][0]
            kept_pairs = [
                (d, m) for d, m in zip(tax_docs, tax_metas)
                if m.get('source') == 'thong_tu_32_2026.txt'
            ]
            if kept_pairs:
                tax_results = {
                    'documents': [[d for d, _ in kept_pairs]],
                    'metadatas': [[m for _, m in kept_pairs]],
                }
        if tax_results is None:
            context_query = context_query.replace(
                " Giao dịch có yếu tố tài sản số phát sinh thu nhập.", ""
            )

    # 5c. Gộp kết quả, loại trùng theo nội dung
    # [FIX 2026-08-09] Gộp thêm large_tx_results (ngưỡng CTR từ QĐ 11/2023 / Điều 6
    # TT27) — để LLM có cả context STR lẫn context ngưỡng giá trị, đủ cơ sở đối
    # chiếu amount trước khi khẳng định một giao dịch "thuộc/không thuộc" diện CTR.
    combined_docs, combined_sources = _merge_unique(
        [aml_results, large_tx_results, tax_results]
    )

    retrieved_laws = ""
    if combined_docs:
        for doc, source in zip(combined_docs, combined_sources):
            retrieved_laws += f"- [Nguồn: {source}]: {doc}\n"
    else:
        retrieved_laws = "(Không tìm thấy căn cứ pháp lý phù hợp trong cơ sở dữ liệu.)"

    # 6. Xây dựng prompt cho LLM
    # [Bổ sung -- Thay đổi 4] Yêu cầu trả về JSON đúng schema thay vì đoạn văn tự
    # do, để UI (Phần 10.b) hiển thị dạng "Điều X -> Thông tư Y -> Vì vậy..." mà
    # không cần tự parse bằng regex (dễ vỡ).
    # [Bổ sung -- sửa lỗi bịa sai cơ quan ban hành] KHÔNG để LLM tự ghi tên nguồn
    # ("source") theo trí nhớ -- bắt nó chọn "source_file" đúng khớp 1 trong các
    # tên file đã truy xuất thật bên dưới; code sẽ tự ánh xạ sang tên chính thức
    # (xem _normalize_citation_sources), không tin LLM nhớ đúng TT-NHNN/TT-BTC.
    # [FIX 2026-08-09 — RAG trích sai điều luật] Dựng phần "BẰNG CHỨNG QUYẾT ĐỊNH
    # THỰC TẾ" từ decision_engine — đưa decision/decision_reason/decision_evidence
    # (hop_distance, sanction_result, suspicious_path) vào prompt để LLM biết lý do
    # hệ thống báo cáo case này là gì, không phải tự suy diễn từ amount đơn lẻ.
    evidence_lines = []
    if decision:
        evidence_lines.append(f"- Quyết định của hệ thống: {decision}")
    if decision_reason:
        evidence_lines.append(f"- Lý do quyết định (decision_reason): {decision_reason}")
    if decision_evidence:
        for ev in decision_evidence:
            evidence_lines.append(f"- Bằng chứng quyết định: {ev}")
    if hop_distance is not None:
        evidence_lines.append(
            f"- Bằng chứng graph: hop_distance_to_blacklist={hop_distance} "
            f"(số hop ngắn nhất tới ví đã biết bị sanction/blacklist trên đồ thị dòng tiền)"
        )
    if suspicious_path:
        evidence_lines.append(f"- Đường đi nghi vấn (suspicious_path): {' -> '.join(str(p) for p in suspicious_path)}")
    if sanction_result.get("is_match"):
        evidence_lines.append(
            f"- Sanction result: wallet {sanction_result.get('matched_wallet', 'N/A')} "
            f"trùng khớp chính xác danh sách {sanction_result.get('source', 'N/A')} "
            f"(type={sanction_result.get('match_type', 'N/A')}, program={sanction_result.get('program', 'N/A')})"
        )
    elif sanction_path_found is True:
        evidence_lines.append(
            "- Sanction result: không trùng khớp chính xác nhưng tồn tại đường đi "
            "tới ví sanctioned trên đồ thị (sanction_path_found=True)"
        )
    decision_evidence_block = "\n".join(evidence_lines) if evidence_lines else "(không có)"

    unique_source_files = sorted(set(combined_sources)) or ["(không có nguồn nào được truy xuất)"]
    prompt = f"""Bạn là một chuyên viên pháp lý và phòng chống rửa tiền (AML).
Dựa trên thông tin giao dịch đáng ngờ sau đây:
{context_query}

[BẰNG CHỨNG QUYẾT ĐỊNH THỰC TẾ CỦA HỆ THỐNG — dùng làm căn cứ ưu tiên chọn điều luật]
{decision_evidence_block}

Và các căn cứ pháp lý được cung cấp từ cơ sở dữ liệu (mỗi dòng có ghi rõ tên file nguồn):
{retrieved_laws}

Danh sách tên file nguồn HỢP LỆ (chỉ được chọn "source_file" từ đúng các tên này,
không tự đặt tên khác): {", ".join(unique_source_files)}

Hãy trả lời DUY NHẤT bằng JSON đúng schema sau, KHÔNG thêm markdown code fence,
KHÔNG thêm bất kỳ chữ nào khác ngoài JSON:
{{
  "legal_citations": [
    {{
      "source_file": "Tên file nguồn ĐÚNG NGUYÊN VĂN từ danh sách hợp lệ ở trên",
      "dieu_khoan": "Điều khoản cụ thể, ví dụ: Điều 6",
      "noi_dung_tom_tat": "Tóm tắt ngắn gọn nội dung điều khoản liên quan",
      "ly_do_ap_dung": "Vì sao điều khoản này áp dụng cho giao dịch đang xét"
    }}
  ]
}}

QUY TẮC BẮT BUỘC — ĐỐI CHIẾU LOGIC TRƯỚC KHI KẾT LUẬN:
1. Chỉ liệt kê các điều khoản THỰC SỰ có trong phần căn cứ pháp lý cung cấp bên trên.
   Không bịa đặt luật nếu không có trong phần căn cứ pháp lý. Không tự đặt tên thông
   tư/nghị định nào ngoài "source_file" đã cho ở trên.
2. NẾU BẰNG CHỨNG QUYẾT ĐỊNH có tín hiệu graph/sanction (hop_distance_to_blacklist
   <= 2, đường đi tới ví bị trừng phạt, sanction match, hoặc decision_reason nói về
   "hop"/"sanctioned"/"Graph exposure"), căn cứ pháp lý CHÍNH phải là quy định về
   GIAO DỊCH ĐÁNG NGỜ (STR): Điều 7 Thông tư 27/2025/TT-NHNN hoặc Điều 26 Luật PCRT
   2022 (nếu nguồn đó có trong phần căn cứ). STR không phụ thuộc vào lượng tiền —
   KHÔNG được lấy quy định "giá trị lớn" làm căn cứ chính trong trường hợp này.
3. Trước khi khẳng định một giao dịch "thuộc diện báo cáo giao dịch có giá trị lớn
   (CTR)", bạn PHẢI tìm NGƯỠNG SỐ CỤ THỂ nêu trong các căn cứ pháp lý được cung cấp
   (ví dụ "400.000.000 đồng" trong Quyết định 11/2023/QĐ-TTg / Điều 25 Luật PCRT
   2022 — nếu nguồn này có trong context) rồi đối chiếu amount_vnd của giao dịch:
   BẮT BUỘC nêu tường minh TRONG ly_do_ap_dung: "amount_vnd = X VND ... >= ngưỡng Y
   VND nêu tại ... -> thuộc diện CTR" hoặc "amount_vnd = X VND ... < ngưỡng Y VND
   nêu tại ... -> KHÔNG thuộc diện CTR". PHẢI tự kiểm tra lại phép so sánh 2 con số
   một lần nữa TRƯỚC khi viết kết luận — cấm nói "thấp hơn/không đạt ngưỡng" khi
   amount_vnd THỰC SỰ >= ngưỡng, và cấm nói "đạt ngưỡng/thuộc diện" khi amount_vnd
   THỰC SỰ < ngưỡng:
   - Nếu amount_vnd >= ngưỡng: giao dịch THUỘC diện báo cáo giá trị lớn (CTR) theo
     Điều 6 TT27 / Điều 25 Luật PCRT / QĐ 11/2023.
   - Nếu amount_vnd < ngưỡng: KHÔNG thuộc diện CTR — nói rõ "KHÔNG đạt ngưỡng ...".
   - Nếu KHÔNG có ngưỡng số nào trong căn cứ: không được tự bịa con số ngưỡng, chỉ
     nêu "việc xác định ngưỡng do Thủ tướng Chính phủ quy định" nếu nội dung có.
4. Khi có cả đối tượng STR lẫn đối tượng "giá trị lớn" trong context, ưu tiên điều luật
   khớp ĐÚNG bằng chứng quyết định thực tế (mục 2), không chọn điều luật chỉ vì con số
   amount trông "lớn".
5. ÔN TẬP CUỐI: rà soát lại TỪNG ly_do_ap_dung xem mọi phép so sánh số liệu có đúng
   chiều không — ví dụ nói "450.000.000 thấp hơn 400.000.000" là SAI SỐ HỌC. Sửa
   trước khi trả lời.
"""

    # 7. Gọi LLM + parse thành cấu trúc + chuẩn hoá tên nguồn (Thay đổi 4 + sửa lỗi)
    # [FIX 2026-08-09 — LLM lỗi phải BÁO RÕ, không giấu thành data thật] Dùng bản
    # nội bộ để nhận cả status. Khi lỗi (thiếu LLM_API_KEY *hoặc* API call thất bại)
    # → KHÔNG đẩy mock content vào legal_citations (ngăn STR/UI in "căn cứ pháp lý"
    # giả như thật), thay vào đó đặt legal_citations = [] + legal_rag_status =
    # "UNAVAILABLE" + legal_rag_error. alert_report.py sẽ in cảnh báo tường minh.
    # Legal RAG KHÔNG ảnh hưởng Decision Engine — REPORT/REVIEW/PASS đã được
    # decision_engine.py quyết định trước khi RAG chạy.
    raw_llm_output, llm_status = _call_llm_api_internal(prompt)

    if llm_status in ("MOCK_NO_KEY", "UNAVAILABLE"):
        if llm_status == "MOCK_NO_KEY":
            rag_error = ("Không thể truy xuất căn cứ pháp lý tự động: chưa cấu hình "
                         "LLM_API_KEY trong .env — không gọi được LLM để đối chiếu "
                         "điều khoản pháp lý với giao dịch.")
        else:
            rag_error = (
                "Không thể truy xuất căn cứ pháp lý tự động: gọi LLM API thất bại "
                "(timeout / rate limit / sai API key / model trả lỗi). Chi tiết "
                "kỹ thuật đã được ghi vào log hệ thống khi gọi LLM."
            )
        # LƯU Ý: legal_rag_error KHÔNG được chứa raw_llm_output (mock text) — nếu
        # không, STR sẽ in lẫn mock content dù có cảnh báo. Mock text CHỈ tồn tại
        # trong log stdout (print bên dưới) để debug.
        print(f"⚠️ RAG Status = UNAVAILABLE (mock text chỉ in log, không vào STR): "
              f"{llm_status} — {raw_llm_output}")
        print(f"⚠️ RAG Status = UNAVAILABLE: {rag_error}")

        state["legal_citations"] = []
        state["legal_sources_retrieved"] = combined_sources
        state["legal_rag_status"] = "UNAVAILABLE"
        state["legal_rag_error"] = rag_error
        return state

    legal_citations = _parse_legal_citations(raw_llm_output)
    legal_citations = _normalize_citation_sources(legal_citations)

    # 8. Cập nhật state
    state["legal_citations"] = legal_citations
    state["legal_sources_retrieved"] = combined_sources
    state["legal_rag_status"] = "OK"
    state["legal_rag_error"] = None

    return state


if __name__ == "__main__":
    print("--- Đang chạy kiểm thử RAG Assistant ---")
    test_state = {
        "tx_hash": "0xtest_rag",
        "wallet_from": "0xuser_target_wallet",
        "wallet_to": "0xdestination_wallet",
        "amount_vnd": 620_000_000,
        "hashed_fullname": "test_hash_1",
        "hashed_id_number": "test_hash_2",
        "hashed_account_number": "test_hash_3",
        "classifier_score": 0.75,
        "graph_score": 0.85,
        "community_id": 6,
        "sanction_result": {"is_match": False, "matched_wallet": None, "source": "OFAC SDN", "match_type": None, "program": None},
    }
    result = run_regulation_rag(test_state)
    print("\n=== KẾT QUẢ ===")
    print("legal_sources_retrieved:", result["legal_sources_retrieved"])
    print("legal_citations:", result["legal_citations"])