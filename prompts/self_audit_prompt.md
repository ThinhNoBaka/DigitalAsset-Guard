# 🔍 PROMPT TỰ ĐÁNH GIÁ NHƯỢC ĐIỂM — DigitalAsset Guard

> Prompt này dùng để gửi cho bất kỳ AI nào (ChatGPT, Claude, Gemini...) để thực hiện
> audit toàn diện nhược điểm của dự án. Kèm theo file `PROJECT_SUMMARY.md` khi gửi.

---

```
Bạn là một Senior System Architect đồng thời là chuyên gia AML (Anti-Money Laundering),
an ninh mạng và ML Engineer với 10+ năm kinh nghiệm. Nhiệm vụ của bạn là thực hiện
một cuộc AUDIT TOÀN DIỆN để chỉ ra NHƯỢC ĐIỂM của dự án dưới đây.

═══════════════════════════════════════════════════════════════
I. BỐI CẢNH DỰ ÁN
═══════════════════════════════════════════════════════════════

Dự án "DigitalAsset Guard" là AI Copilot chống rửa tiền (AML) cho giao dịch tài sản số
(crypto) tại Việt Nam:

- Tuân thủ Thông tư 27/2025/TT-NHNN (STR ≥ 500 triệu VND, human-in-the-loop),
  Thông tư 32/2026/TT-BTC (thuế 0.1%), tham chiếu FATF + OFAC SDN.
- Pipeline 8 bước: Webhook → Privacy Layer (SHA-256+salt PII) → Transaction Assistant
  (XGBoost + SHAP) → KYC Assistant (so khớp 940 ví OFAC) → Graph Assistant
  (Personalized PageRank + Louvain, NetworkX demo / Neo4j GDS prod) → RAG Assistant
  (ChromaDB + LLM) → Report Assistant (final = 0.2×CLF + 0.3×KYC + 0.5×Graph,
  sinh STR Mẫu 04 .docx) → Human Checkpoint (LangGraph interrupt_after).
- Orchestration DUY NHẤT qua core/graph_builder.py::PipelineRun (CLI demo_run.py
  và FastAPI api/main.py đều gọi chung).
- Đây là MVP/đồ án tốt nghiệp, KHÔNG phải production — ưu tiên đúng nghiệp vụ pháp lý
  VN + privacy-by-design + explainability hơn độ chính xác ML tuyệt đối.
- Điểm mạnh đã biết (không cần liệt kê lại, chỉ dùng làm ngữ cảnh): privacy by design
  nghiêm túc (assert_no_raw_pii() ở đầu mọi agent), kiến trúc orchestration tập trung,
  explainability (SHAP + risk_breakdown), temporal split đúng (train time_step 1-34,
  test 35-49), xử lý mất cân bằng bằng SMOTE (F1=0.8031, AUC-PR=0.8112).

Công nghệ: Python 3, XGBoost, scikit-learn, SHAP, NetworkX, Neo4j + GDS, ChromaDB,
LangGraph (MemorySaver, interrupt_after), FastAPI, python-docx, OpenAI-compatible LLM
(Groq/OpenRouter), Docker Compose, frontend thuần HTML/JS/CSS (không framework).

File chính cần đọc kỹ:
  core/  → config.py, state.py, privacy_layer.py, graph_builder.py, audit_logger.py
  agents/ → transaction_classifier.py, kyc_verification.py, graph_aml.py,
            regulation_rag.py, alert_report.py, train_classifier.py
  api/main.py, db/vector_db.py, db/neo4j_setup.py, frontend_html/app.js
  scripts/, tests/, docker-compose.yml, .env (chỉ đọc key, không đọc giá trị secret)

═══════════════════════════════════════════════════════════════
II. YÊU CẦU PHÂN TÍCH — TỐI THIỂU 8 GÓC NHÌN
═══════════════════════════════════════════════════════════════

Hãy phân tích lần lượt theo các khía cạnh sau, mỗi khía cạnh ít nhất 2-4 nhược điểm:

1. KIẾN TRÚC & THIẾT KẾ HỆ THỐNG
   - Coupling/cohesion giữa các module, single point of failure, xử lý lỗi & retry,
     khả năng mở rộng khi tải cao, thiếu event-driven/saga pattern cho pipeline dài.
2. BẢO MẬT & PRIVACY (ưu tiên cao nhất)
   - Lỗ hổng trong cơ chế băm PII, cách lưu salt, khả năng brute-force hash,
     MOCK_SECURE_VAULT, token auth trong RAM, thiếu rate limiting, CORS, input validation.
3. CHẤT LƯỢNG DỮ LIỆU & ML
   - Feature vector mock (zero-vector 166 chiều), độ phủ OFAC thấp (940/19169 ví),
     dữ liệu Elliptic không tương đồng với crypto VN, data leakage, threshold 0.7,
     trọng số 0.2/0.3/0.5 heuristic chưa kiểm chứng, khả năng adversarial attack.
4. NGHIỆP VỤ PHÁP LÝ & TUÂN THỦ
   - Rủi ro: LLM hallucination trong legal_citations, thiếu cơ chế xác minh trích dẫn
     pháp lý, quy trình HITL có đủ "không thể bypass" không, tính bất biến của audit
     trail (ai đó sửa file log được không), thiếu signature/checksum trên STR.
5. VẬN HÀNH & ĐỘ TIN CẬY (nếu đưa lên production)
   - State trong RAM (RUNS/TOKENS) mất khi restart, không multi-worker, Neo4j GDS
     cần mount jar thủ công, không có healthcheck/graceful shutdown, không CI/CD.
6. CHẤT LƯỢNG CODE & BẢO TRÌ
   - Dead code, magic number, thiếu type hint, thiếu docstring, code duplication,
     naming, exception handling nuốt lỗi (bare except), logging lộ thông tin.
7. TEST & ĐẢM BẢO CHẤT LƯỢNG
   - Độ bao phủ thực tế của tests/, test có kiểm tra đúng hành vi không hay chỉ match
     API, thiếu test cho audit_logger/name_screening, thiếu integration test end-to-end.
8. FRONTEND & UX
   - Bảo mật phía FE (XSS, lộ key), hiệu năng khi nhiều transaction, không có
     accessibility, không responsive, lỗi xử lý bất đồng bộ trong app.js.

Ngoài ra hãy bổ sung thêm góc nhìn thứ 9-10 mà bạn cho là quan trọng nhưng chưa nêu.

═══════════════════════════════════════════════════════════════
III. ĐỊNH DẠNG ĐẦU RA (BẮT BUỘC)
═══════════════════════════════════════════════════════════════

Với MỖI nhược điểm, trình bày theo cấu trúc:

  ### [Mức độ] Nhược điểm: <tên ngắn gọn>
  - File/Đoạn code liên quan: <đường dẫn file, dòng cụ thể nếu có>
  - Phân tích chi tiết: <tại sao đây là vấn đề, ảnh hưởng gì>
  - Tác động: <rủi ro/hậu quả thực tế nếu xảy ra>
  - Đề xuất khắc phục: <giải pháp cụ thể, khả thi>
  - Ước lượng độ khó: <Dễ/Trung bình/Khó> + <thời gian>

Phân loại mức độ: 🔴 Nghiêm trọng (cần xử lý ngay) / 🟡 Trung bình (nên xử lý)
/ 🟢 Nhẹ (cải thiện dần).

═══════════════════════════════════════════════════════════════
IV. QUY TẮC & RÀNG BUỘC
═══════════════════════════════════════════════════════════════

1. KHÔNG liệt kê nhược điểm chung chung như "thiếu document", "nên dùng Kubernetes" —
   mọi nhận định phải bám vào FILE/CODE cụ thể trong dự án, có dẫn chứng.
2. KHÔNG đề xuất "sửa" các đánh đổi có chủ đích (đã ghi rõ trong PROJECT_SUMMARY.md)
   mà không nói rõ đánh đổi đó gây rủi ro gì. Ví dụ: fuzzy name-matching phải chạy
   trước khi băm PII là chủ đích — nhưng phân tích xem nó còn lỗ hổng nào không.
3. Xét đúng bối cảnh: đây là MVP/đồ án tốt nghiệp. Ưu tiên nhược điểm nào ảnh hưởng
   nhiều nhất đến: (a) tính thuyết phục khi bảo vệ, (b) tính đúng đắn pháp lý,
   (c) con đường nâng cấp lên production.
4. Với mỗi nhược điểm, xác định AI cần đọc file nào để tự kiểm chứng thay vì đoán mò.
5. Cuối cùng: tổng hợp danh sách "TOP 10 nhược điểm nghiêm trọng nhất" xếp theo mức
   ảnh hưởng, kèm lý do 1 dòng cho mỗi cái.