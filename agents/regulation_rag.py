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
}


def call_llm_api(prompt: str) -> str:
    """
    Gọi LLM thật qua API chuẩn OpenAI-compatible (thư viện `openai`),
    dùng được cho Groq, OpenRouter, Xiaomi MiMo, hay bất kỳ nhà cung cấp nào
    expose endpoint kiểu /v1/chat/completions.
    Nếu chưa có LLM_API_KEY trong .env -> fallback mock (test logic không cần key).
    Nếu có key nhưng gọi API lỗi -> báo lỗi rõ ràng, không âm thầm giả vờ ổn.
    """
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return ("[Mock LLM Output]: Dựa trên truy vấn, giao dịch vi phạm quy định báo cáo "
                "(Thông tư 27) hoặc phát sinh nghĩa vụ thuế (Thông tư 32)... "
                "(Vui lòng điền LLM_API_KEY vào .env để chạy thật)")

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
        return text.strip()

    except Exception as e:
        # KHÔNG âm thầm nuốt lỗi thành output giống mock bình thường —
        # ghi rõ gọi API thật bị lỗi + nguyên nhân, để dễ debug và phân biệt
        # với trường hợp "chưa điền key" ở nhánh trên.
        print(f"⚠️ Lỗi khi gọi Gemini API: {e}")
        return (f"[LLM API LỖI - dùng tạm Mock Output]: Không gọi được Gemini API ({e}). "
                f"Giao dịch vi phạm quy định báo cáo (Thông tư 27) hoặc phát sinh nghĩa vụ "
                f"thuế (Thông tư 32) — cần kiểm tra lại LLM_API_KEY hoặc kết nối mạng.")


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
    amount_vnd = state.get("amount_vnd", 0)
    risk_score_clf = state.get("risk_score_classifier", 0.0)
    graph_risk = state.get("graph_risk_score", 0.0) or 0.0

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

    # 5a. Luôn truy vấn nhánh AML/báo cáo (Thông tư 27) — câu hỏi riêng, không
    # lẫn từ khóa thuế, để đảm bảo luôn tìm đúng ngưỡng báo cáo/STR.
    aml_query = ("Ngưỡng báo cáo giao dịch đáng ngờ (STR/CTR), dấu hiệu đáng ngờ "
                 "liên quan đến rủi ro dòng tiền và thời hạn nộp báo cáo.")
    aml_results = query_legal_docs(query_text=aml_query, n_results=2)

    # 5b. Nếu có yếu tố crypto/thuế -> truy vấn RIÊNG, chỉ tập trung câu hỏi
    # thuế (không lẫn từ ngữ AML) để đảm bảo Thông tư 32 luôn được tìm thấy.
    tax_results = None
    if is_crypto_tax_case:
        tax_query = ("Công thức tính thuế thu nhập cá nhân 0.1% đối với chuyển "
                      "nhượng, giao dịch tài sản mã hóa.")
        tax_results = query_legal_docs(query_text=tax_query, n_results=2)

    # 5c. Gộp kết quả, loại trùng theo nội dung
    combined_docs, combined_sources = _merge_unique([aml_results, tax_results])

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
    unique_source_files = sorted(set(combined_sources)) or ["(không có nguồn nào được truy xuất)"]
    prompt = f"""Bạn là một chuyên viên pháp lý và phòng chống rửa tiền (AML).
Dựa trên thông tin giao dịch đáng ngờ sau đây:
{context_query}

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
      "dieu_khoan": "Điều khoản cụ thể, ví dụ: Điều 12",
      "noi_dung_tom_tat": "Tóm tắt ngắn gọn nội dung điều khoản liên quan",
      "ly_do_ap_dung": "Vì sao điều khoản này áp dụng cho giao dịch đang xét"
    }}
  ]
}}
Chỉ liệt kê các điều khoản THỰC SỰ có trong phần căn cứ pháp lý cung cấp bên trên.
Không bịa đặt luật nếu không có trong phần căn cứ pháp lý. Không tự đặt tên thông
tư/nghị định nào ngoài "source_file" đã cho ở trên.
"""

    # 7. Gọi LLM + parse thành cấu trúc + chuẩn hoá tên nguồn (Thay đổi 4 + sửa lỗi)
    raw_llm_output = call_llm_api(prompt)
    legal_citations = _parse_legal_citations(raw_llm_output)
    legal_citations = _normalize_citation_sources(legal_citations)

    # 8. Cập nhật state
    state["legal_citations"] = legal_citations
    state["legal_sources_retrieved"] = combined_sources

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
        "risk_score_classifier": 0.75,
        "graph_risk_score": 0.85,
        "community_id": 6,
        "kyc_flags": [],
    }
    result = run_regulation_rag(test_state)
    print("\n=== KẾT QUẢ ===")
    print("legal_sources_retrieved:", result["legal_sources_retrieved"])
    print("legal_citations:", result["legal_citations"])