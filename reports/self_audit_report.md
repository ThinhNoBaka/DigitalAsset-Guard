# 🔍 BÁO CÁO TỰ AUDIT TOÀN DIỆN NHƯỢC ĐIỂM — DigitalAsset Guard

> Ngày audit: 2026-08-09 · Phạm vi: `core/`, `agents/`, `api/main.py`, `frontend_html/`
> Phương pháp: đọc trực tiếp code + đối chiếu `PROJECT_SUMMARY.md` / `mistakes.md`
> Mức độ: 🔴 Nghiêm trọng · 🟡 Trung bình · 🟢 Nhẹ

---

## 1. KIẾN TRÚC & THIẾT KẾ HỆ THỐNG

### [🔴] Nhược điểm: PII plaintext rò rỉ vào memory qua closure

- **File/Đoạn code**: `core/graph_builder.py:408-414` — `report_customer_info` capture `fullname/id_number/account_number` plaintext; `PipelineRun` được lưu trong `RUNS` dict ở `api/main.py:357`.
- **Phân tích chi tiết**: PII plaintext được giữ trong closure của compiled LangGraph, không đi qua checkpoint (đúng comment). Nhưng object `PipelineRun` chứa closure đó được giữ **vô thời hạn** trong `RUNS[tx_hash]` của server → PII sống mãi trong RAM. Nếu có crash dump / core dump / memory inspection, PII sẽ lộ.
- **Tác động**: Rò rỉ PII qua memory — vi phạm tinh thần "privacy by design" của chính dự án (PII chỉ nên tồn tại ở request boundary).
- **Đề xuất khắc phục**: (a) Xóa `RUNS[tx_hash]` (và `report_customer_info`) sau khi HITL hoàn tất; (b) hoặc lưu report customer info vào DB mã hoá tách biệt, không giữ trong closure; (c) ít nhất ghi rõ thời gian sống của PII trong memory.
- **Độ khó**: Trung bình — 1-2 ngày.

### [🔴] Nhược điểm: Không có idempotency — cùng tx_hash chạy lại tạo STR trùng

- **File/Đoạn code**: `api/main.py:407` (`tx_hash = payload.tx_hash or secrets.token_hex(16)`) — không kiểm tra tx_hash đã tồn tại trong `RUNS` hay chưa.
- **Phân tích chi tiết**: Nếu cùng một giao dịch thật bị gửi 2 lần (webhook retry, client double-click), pipeline chạy 2 lần → sinh 2 file STR trùng, 2 case chờ duyệt độc lập → chuyên viên có thể duyệt cả 2 → nộp STR trùng cho NHNN.
- **Tác động**: Vi phạm quy định báo cáo (STR trùng lặp), tốn công xử lý thủ công.
- **Đề xuất khắc phục**: Check `tx_hash` tồn tại → trả về state đã có (idempotent); dùng unique constraint trên tx_hash nếu có DB.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: `RUNS` dict tăng vô hạn — memory leak

- **File/Đoạn code**: `api/main.py:357` `RUNS: Dict[str, PipelineRun] = {}` — không bao giờ xoá entry.
- **Phân tích chi tiết**: Mỗi request pipeline tạo 1 entry bất tử. Chạy lâu ngày → server hết RAM. `TOKENS` (dòng 78) cũng không expire → token vĩnh viễn.
- **Tác động**: Server chết sau thời gian dài vận hành; token không bao giờ hết hạn — attacker có token cũ vẫn truy cập mãi.
- **Đề xuất khắc phục**: (a) TTL cho `RUNS`/`TOKENS` (vd cleanup khi idle > 24h); (b) token JWT có expiry; (c) chuyển sang Redis/DB.
- **Độ khó**: Trung bình — 1-2 ngày.

### [🟡] Nhược điểm: Phụ thuộc hoàn toàn vào Etherscan API, không có retry/circuit-breaker

- **File/Đoạn code**: `api/main.py:169-181` & `419-433` — `fetch_wallet_record()` gọi trực tiếp, fail → HTTP 500 toàn pipeline.
- **Phân tích chi tiết**: Nếu Etherscan down / rate limit / API key hết quota → **mọi** request pipeline fail 100%, kể cả trường hợp chỉ cần sanctions check. Không có retry/backoff/cache/circuit-breaker.
- **Tác động**: Mất khả năng phục vụ AML khi external API lỗi; demo trước hội đồng dễ chết.
- **Đề xuất khắc phục**: Retry với exponential backoff, cache theo wallet, fallback sang RPC node khác, hoặc cho phép pipeline chạy với `insufficient_data=True` thay vì crash.
- **Độ khó**: Trung bình — 2-3 ngày.

### [🟡] Nhược điểm: `MemorySaver` checkpointer mới mỗi lần — không resume được sau restart

- **File/Đoạn code**: `core/graph_builder.py:394` `self.checkpointer = MemorySaver()` — tạo mới theo từng PipelineRun.
- **Phân tích chi tiết**: Nếu server restart giữa chừng khi case đang `pending_review`, toàn bộ state của case mất → không thể Approve/Reject nữa (đã ghi chú "server đã restart" trong `_get_run_or_404`).
- **Tác động**: Mất case đang xử lý khi deploy/restart — không chấp nhận được ở production.
- **Đề xuất khắc phục**: Dùng `SqliteSaver`/`PostgresSaver` của LangGraph hoặc persist state vào DB sau mỗi node.
- **Độ khó**: Trung bình — 2-3 ngày.

---

## 2. BẢO MẬT & PRIVACY (ưu tiên cao nhất)

### [🔴] Nhược điểm: `PII_SALT` có fallback dev hard-coded — mâu thuẫn với chính sách "no fallback"

- **File/Đoạn code**: `core/privacy_layer.py:25`
  ```python
  PII_SALT = os.getenv("PII_SALT", "DEV_ONLY_CHANGE_ME_IN_ENV")
  ```
- **Phân tích chi tiết**: PROJECT_SUMMARY.md mục 5 ghi rõ "không fallback, raise RuntimeError nếu thiếu" nhưng code fallback về chuỗi cố định công khai. Nếu deploy quên set `PII_SALT` trong `.env`, toàn bộ hash PII dùng salt nằm trong source code → attacker biết salt → brute-force SHA-256 trên danh sách tên/CCCD phổ biến là khả thi.
- **Tác động**: Toàn bộ PII băm có thể bị reverse qua dictionary attack. Đây là lỗ hổng **nghiêm trọng nhất** về privacy.
- **Đề xuất khắc phục**: Đổi fallback thành `raise RuntimeError("PII_SALT không được cấu hình")` đúng như chính sách ghi trong PROJECT_SUMMARY.
- **Độ khó**: Dễ — 15 phút.

### [🔴] Nhược điểm: Default credential `nhanvien1/123456789` hard-coded

- **File/Đoạn code**: `api/main.py:73-76`
  ```python
  AUTH_USERS = {
      os.environ.get("AML_AUTH_USERNAME", "nhanvien1"):
          os.environ.get("AML_AUTH_PASSWORD", "123456789"),
  }
  ```
- **Phân tích chi tiết**: Nếu deploy quên set env → tài khoản mặc định cực kỳ dễ đoán, ai cũng đăng nhập được để xem state/report/duyệt STR.
- **Tác động**: Toàn bộ HITL (Approve/Reject STR) có thể bị thao túng bởi attacker.
- **Đề xuất khắc phục**: Không có default — start server fail nếu thiếu env (fail-fast giống chuẩn PII_SALT).
- **Độ khó**: Dễ — 15 phút.

### [🔴] Nhược điểm: Token lộ trong URL query string khi tải STR

- **File/Đoạn code**: `frontend_html/app.js:240`
  ```js
  href: `/api/pipeline/${encodeURIComponent(state.tx_hash)}/report?token=${encodeURIComponent(authToken)}`
  ```
- **Phân tích chi tiết**: Token Bearer được nhét trong query string. URL bị ghi vào: server access log, browser history, proxy log, Referer header nếu trang có link ngoài. `api/main.py:708` (`token: Optional[str] = None`) đọc token từ query — anti-pattern bảo mật chuẩn.
- **Tác động**: Token bị đánh cắp qua log/history → attacker đăng nhập thay chuyên viên.
- **Đề xuất khắc phục**: Dùng `Authorization: Bearer` header (fetch với `responseType: blob`), không dùng query param.
- **Độ khó**: Dễ — 0.5 ngày.

### [🔴] Nhược điểm: CORS mở hoàn toàn `allow_origins=["*"]`

- **File/Đoạn code**: `api/main.py:926-932`.
- **Phân tích chi tiết**: Mọi origin được phép gọi API. Kết hợp với default credential + token trong RAM, attacker có thể dựng trang web độc hại gửi request tới API nội bộ (dù token không tự động gửi vì `allow_credentials=False`, nhưng nếu victim dán token vào trang độc hại thì vẫn bị).
- **Tác động**: Giảm an toàn trong môi trường nội bộ AML — nên giới hạn origin nội bộ.
- **Đề xuất khắc phục**: `allow_origins` = danh sách origin nội bộ cụ thể.
- **Độ khó**: Dễ — 30 phút.

### [🟡] Nhược điểm: `/logs` endpoint trả raw audit log không lọc

- **File/Đoạn code**: `api/main.py:319-350` — `get_audit_logs` trả nguyên từng dòng JSON log.
- **Phân tích chi tiết**: Audit log ghi `state_keys_present` + `tx_hash` — không chứa PII (đúng), nhưng vẫn là thông tin vận hành nội bộ. Chỉ cần token (dễ lấy nếu default credential) là đọc được toàn bộ lịch sử case.
- **Tác động**: Rò rỉ thông tin vận hành nội bộ.
- **Đề xuất khắc phục**: Lọc bớt field nhạy cảm, giới hạn cho admin role riêng.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: Đường dẫn audit log không nhất quán giữa 2 module

- **File/Đoạn code**: `core/audit_logger.py:27` dùng `Path("logs/audit_trail.log")` (relative theo CWD), `api/main.py:305-312` dùng `os.path.abspath(...)` (absolute theo file location).
- **Phân tích chi tiết**: Nếu server chạy từ thư mục khác ngoài `d:/TPers_prj`, `audit_logger` ghi log ở thư mục khác với nơi API `/logs` đọc → endpoint `/logs` trả rỗng dù pipeline vẫn ghi. Rất khó debug.
- **Tác động**: Audit trail vận hành bị "mất" khi CWD khác — bằng chứng kiểm toán không đáng tin cậy.
- **Đề xuất khắc phục**: Dùng chung 1 constant `AUDIT_LOG_PATH` từ `core/audit_logger.py` (absolute từ `BASE_DIR`), import vào API.
- **Độ khó**: Dễ — 30 phút.

### [🟢] Nhược điểm: Không có rate limiting trên login/screen-wallet

- **File/Đoạn code**: `api/main.py:91-110, 197-287`.
- **Phân tích chi tiết**: `/api/auth/login` và `/screen-wallet` không giới hạn số lần gọi → brute-force password và abuse Etherscan API (tốn quota).
- **Tác động**: Password bị brute-force; API key Etherscan hết quota nhanh.
- **Đề xuất khắc phục**: Thêm `slowapi`/middleware rate-limit cho endpoint công khai.
- **Độ khó**: Dễ — 0.5 ngày.

---

## 3. CHẤT LƯỢNG DỮ LIỆU & ML

### [🔴] Nhược điểm: Ngưỡng `_DEFAULT_CLASSIFIER_THRESHOLD = 0.7` fallback chưa calibrate

- **File/Đoạn code**: `agents/decision_engine.py:62-101` — thiếu `models/classifier_threshold.json` → fallback 0.7 "CHƯA KIỂM CHỨNG".
- **Phân tích chi tiết**: Nếu chưa chạy `calibrate_classifier_threshold.py`, Decision Engine dùng θ=0.7 bất kỳ → REPORT/PASS sai hệ thống mà chỉ in cảnh báo ra console (không ai đọc).
- **Tác động**: Tỷ lệ false positive/negative của toàn bộ quyết định REPORT/REVIEW lệch khỏi thực tế.
- **Đề xuất khắc phục**: Fail-fast nếu thiếu threshold (giống PII_SALT), hoặc bundle file threshold đã calibrate vào repo.
- **Độ khó**: Dễ — 0.5 ngày.

### [🔴] Nhược điểm: `_CLASSIFIER_MEDIUM_RATIO = 0.6` — "giả định tạm, chưa kiểm chứng" nhưng đang quyết định REVIEW

- **File/Đoạn code**: `agents/decision_engine.py:40-45, 119-122, 247-255` (Rule 5).
- **Phân tích chi tiết**: Ngưỡng medium (θ×0.6, hop 3-4) là số "chọn tạm" theo chính docstring — nhưng Rule 5 dùng nó để route REVIEW (pending_review → HITL). Hàng loạt case thật có thể bị đẩy vào REVIEW chỉ vì 2 tín hiệu "vừa" theo ngưỡng bịa.
- **Tác động**: Overload chuyên viên AML với case không đáng, hoặc bỏ sót case đáng REPORT.
- **Đề xuất khắc phục**: Nếu không có dữ liệu kiểm chứng, (a) tắt Rule 5 hoặc (b) ghi rõ "EXPERIMENTAL" và cho phép cấu hình qua env.
- **Độ khó**: Trung bình — 1 ngày + cần dữ liệu.

### [🟡] Nhược điểm: `_threshold_cache` và `_model` là global không thread-safe

- **File/Đoạn code**: `agents/decision_engine.py:60` (`_threshold_cache`), `agents/transaction_classifier.py:65-66` (`_model`, `_explainer`).
- **Phân tích chi tiết**: Trong multi-worker (uvicorn `--workers 2+`) mỗi process có copy riêng nên OK, nhưng nếu dùng thread pool trong 1 process (FastAPI async), 2 thread cùng đọc/ghi global → race condition (dựng model 2 lần, đọc file giữa chừng). Hiện tại dùng sync def nên ít rủi ro, nhưng rất dễ vỡ khi refactor.
- **Tác động**: Latency spike, lỗi khó lường khi scale.
- **Đề xuất khắc phục**: Dùng `functools.lru_cache` hoặc khởi tạo lazy với lock.
- **Độ khó**: Trung bình — 1 ngày.

### [🟡] Nhược điểm: SHAP TreeExplainer per-transaction có latency cao

- **File/Đoạn code**: `agents/transaction_classifier.py:79-84, 415-429`.
- **Phân tích chi tiết**: Mỗi transaction chạy `explainer.shap_values()` — trên model XGBoost lớn, latency có thể 100ms-1s+ per tx. Trong pipeline đồng bộ, toàn bộ request phải chờ.
- **Tác động**: API chậm, khó scale realtime.
- **Đề xuất khắc phục**: (a) Chạy SHAP bất đồng bộ (chỉ khi cần explain), (b) giới hạn top-3 features bằng cách tính shap cho subset feature.
- **Độ khó**: Trung bình — 1-2 ngày.

### [🟢] Nhược điểm: `_build_mock_feature_vector` vẫn tồn tại với semantic sai

- **File/Đoạn code**: `agents/transaction_classifier.py:232-263` — gán `amount_vnd/1_000_000` vào slot 0 (vốn là "Avg min between sent tnx" của model 37-feature).
- **Phân tích chi tiết**: Đã ghi rõ trong comment "SAI về semantic" nhưng demo/test vẫn cho phép `allow_mock=True`. Kết quả demo chạy với mock sẽ cho score gần 1.0 cho mọi ví — gây hiểu nhầm nếu ai đó không đọc kỹ.
- **Tác động**: Thiếu thuyết phục khi demo; nếu vô tình bật mock ở production thì thảm hoạ.
- **Đề xuất khắc phục**: Cân nhắc xoá hẳn mock path sau khi demo xong, hoặc thêm guard chặn mock khi `DEMO_MODE=false`.
- **Độ khó**: Dễ — 0.5 ngày.

---

## 4. NGHIỆP VỤ PHÁP LÝ & TUÂN THỦ

### [🔴] Nhược điểm: LLM lỗi → fallback mock output có thể được in vào STR chính thức

- **File/Đoạn code**: `agents/regulation_rag.py:77-84` — lỗi API trả về `"[LLM API LỖI - dùng tạm Mock Output]: ..."`; `alert_report.py` in `legal_citations` nguyên văn vào Phụ lục B.
- **Phân tích chi tiết**: Khi LLM gọi lỗi, chuỗi "[LLM API LỖI - dùng tạm Mock Output]" được parse thành `{"raw_text": ...}` và hiển thị trong báo cáo STR dự thảo như một trích dẫn pháp lý → chuyên viên có thể tưởng là căn cứ pháp lý thật.
- **Tác động**: STR nộp NHNN chứa nội dung "mock" — cực kỳ rủi ro pháp lý và uy tín.
- **Đề xuất khắc phục**: Khi LLM lỗi, **không** sinh STR — báo lỗi rõ ràng cho HITL biết "chưa có legal citations", hoặc đánh dấu đỏ rõ ràng cả trong docx.
- **Độ khó**: Trung bình — 1 ngày.

### [🟡] Nhược điểm: Hard-code ngưỡng 400 triệu trong câu query ChromaDB

- **File/Đoạn code**: `agents/regulation_rag.py:282-287` — `large_tx_query` chứa "ngưỡng 400.000.000 đồng trở lên".
- **Phân tích chi tiết**: Nếu Quyết định 11/2023/QĐ-TTg được thay thế/sửa ngưỡng, con số này phải sửa trong code. Không nên hard-code pháp lý trong logic retrieval.
- **Tác động**: Trích dẫn sai ngưỡng khi quy định thay đổi.
- **Đề xuất khắc phục**: Đưa ngưỡng vào config file/DB, đọc động khi dựng query.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: `has_graph_or_sanction_evidence` dùng string matching brittle

- **File/Đoạn code**: `agents/regulation_rag.py:207` — `any(("graph exposure" in ev.lower() or "sanctioned" in ev.lower()) for ev in decision_evidence)`.
- **Phân tích chi tiết**: Nếu `decision_engine.py` đổi wording evidence (vd "Graph proximity" thay "Graph exposure"), RAG sẽ không nhận diện được tín hiệu graph → ưu tiên sai điều luật STR→CTR.
- **Tác động**: Trích dẫn pháp lý sai trọng tâm, chuyên viên dễ hiểu nhầm.
- **Đề xuất khắc phục**: Dùng structured field (vd `decision_evidence_types: List[str]`) thay cho parse chuỗi.
- **Độ khó**: Trung bình — 1 ngày.

### [🟢] Nhược điểm: Audit trail không bất biến — ai có quyền ghi file log có thể sửa

- **File/Đoạn code**: `core/audit_logger.py:35-39` — append plaintext JSON vào file local, không có hash-chain/checksum/signature.
- **Phân tích chi tiết**: Trong môi trường kiểm toán NHNN, audit trail phải chống chối cãi (non-repudiation). File log hiện tại có thể bị sửa/xoá dòng mà không phát hiện.
- **Tác động**: Bằng chứng kiểm toán không đủ vững để đối chất.
- **Đề xuất khắc phục**: Thêm checksum chain (mỗi dòng chứa hash dòng trước) hoặc ghi vào hệ thống append-only (WORM storage/DB).
- **Độ khó**: Trung bình — 2 ngày.

---

## 5. VẬN HÀNH & ĐỘ TIN CẬY

### [🟡] Nhược điểm: Projection Neo4j GDS bị leak khi exception giữa chừng

- **File/Đoạn code**: `agents/graph_aml.py:165-245` — `gds.graph.project` được gọi, nhưng `gds.graph.drop` nằm trong flow bình thường, **không có `try/finally`**. Nếu lỗi giữa chừng (vd PPR fail), projection `wallet_graph_tmp` tồn tại mãi → lần sau project lại sẽ lỗi.
- **Tác động**: Pipeline sản xuất tê liệt do state tạm dơ trong Neo4j.
- **Đề xuất khắc phục**: Wrap toàn bộ trong `try/finally` luôn drop projection, hoặc dùng tên projection unique theo tx_hash.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: Mock data có thể bị ghi vào Neo4j production

- **File/Đoạn code**: `agents/graph_aml.py:136-153` — khi `mock_edges` tồn tại, code `MERGE`/`CREATE` node/relationship và set `is_sanctioned=true` ngay trên Neo4j thật.
- **Phân tích chi tiết**: Nếu ai đó bật `GRAPH_SOURCE=neo4j` nhưng vẫn gửi scenario mock từ API (`payload.scenario`), dữ liệu giả bị trộn vào đồ thị production → contaminates graph, ảnh hưởng PPR/hop của wallet thật khác.
- **Tác động**: Kết quả graph analysis sai do dữ liệu mock lẫn trong graph thật.
- **Đề xuất khắc phục**: Chặn hoàn toàn `mock_edges` khi `GRAPH_SOURCE=neo4j` (raise hoặc bỏ qua).
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: NetworkX fallback nuốt lỗi — silent wrong result

- **File/Đoạn code**: `agents/graph_aml.py:83-85`:
  ```python
  try:
      ppr_scores = nx.pagerank(G, alpha=0.85, personalization=personalization, weight="weight")
  except Exception:
      ppr_scores = nx.pagerank(G, alpha=0.85, weight="weight")
  ```
- **Phân tích chi tiết**: Nếu `personalization` gây lỗi, fallback chạy pagerank **không** personalization → graph_score hoàn toàn không phản ánh seed/blacklist, nhưng không có log/báo lỗi → report hiển thị con số "hợp lệ" sai nghĩa.
- **Tác động**: Điểm graph_score sai âm thầm — chuyên viên tin vào số liệu sai.
- **Đề xuất khắc phục**: Log warning rõ ràng, hoặc trả `graph_analysis_status = "ERROR_PPR"` thay vì fallback.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟢] Nhược điểm: Sai nguồn dữ liệu off-chain/on-chain giữa `wallet_tx_history` và `wallet_record`

- **File/Đoạn code**: `api/main.py:435-448` — `account_number` RAW dùng để tra `get_wallet_tx_history` (trước Privacy Layer); `wallet_record` từ Etherscan (on-chain public).
- **Phân tích chi tiết**: Đây là khoảng cách có chủ đích đã ghi rõ, nhưng `wallet_tx_history` phụ thuộc `mock_core_banking` giả lập — nếu account_number không khớp record demo, aggregation_status="not_assessed" → rule structuring không bao giờ chạy → toàn bộ tính năng smurfing chỉ là placeholder.
- **Tác động**: Tính năng structuring detection chưa hoạt động với dữ liệu thật.
- **Đề xuất khắc phục**: Nối nguồn Core Banking thật hoặc ghi rõ trong báo cáo "chức năng demo".
- **Độ khó**: Khó — tùy nguồn dữ liệu.

---

## 6. CHẤT LƯỢNG CODE & BẢO TRÌ

### [🟡] Nhược điểm: Dùng `print()` thay vì logging module

- **File/Đoạn code**: `agents/decision_engine.py:88-98`, `agents/regulation_rag.py:81, 133, 232`, `agents/graph_aml.py:229, 242, 271`.
- **Phân tích chi tiết**: `print()` rác stdout, không có timestamp/level/context, không thể cấu hình log level trong production. Stdout của uvicorn trộn lẫn với access log.
- **Tác động**: Khó debug production; cảnh báo quan trọng (vd threshold fallback) dễ bị bỏ qua.
- **Đề xuất khắc phục**: Chuyển sang `logging.getLogger(__name__)` với format chuẩn.
- **Độ khó**: Dễ — 1 ngày (toàn repo).

### [🟡] Nhược điểm: Tên module/agent không đồng bộ — `kyc_verification.py` ghi "Sanctions Assistant"

- **File/Đoạn code**: `agents/kyc_verification.py:5` — docstring "Sanctions Assistant (trước đây là KYC Assistant)" nhưng tên file/import là `verify_kyc`, node trong graph là `"sanctions"`.
- **Phân tích chi tiết**: 3 tên khác nhau cho 1 agent — gây khó hiểu khi bảo trì/test.
- **Tác động**: Chi phí đọc hiểu code tăng, dễ nhầm lẫn trong code review.
- **Đề xuất khắc phục**: Đổi tên nhất quán (vd `sanctions.py` + `verify_sanctions` hoặc giữ nguyên và sửa docstring).
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: PROJECT_SUMMARY.md không còn khớp code (ghi `MOCK_SECURE_VAULT` nhưng code không có)

- **File/Đoạn code**: `PROJECT_SUMMARY.md:123` (mục 5, đường dẫn `agents/alert_report.py::secure_lookup`) — file `agents/alert_report.py` 1660 dòng đã đọc **không có** hàm `secure_lookup` hay `MOCK_SECURE_VAULT`.
- **Phân tích chi tiết**: Kiến trúc đã pivot sang closure PII (2026-08-09) nhưng PROJECT_SUMMARY.md chưa cập nhật → người đọc sau tin có secure vault trong khi thực tế không còn. Vi phạm chính mục đích của file ("nguồn tóm tắt duy nhất").
- **Tác động**: Thông tin sai lệch → quyết định thiết kế dựa trên trạng thái cũ.
- **Đề xuất khắc phục**: Cập nhật PROJECT_SUMMARY.md mục 5/6 phản ánh cơ chế PII qua closure hiện tại.
- **Độ khó**: Dễ — 1 ngày (viết lại doc).

### [🟢] Nhược điểm: Transaction metadata (tx_hash/date...) chưa được kiểm tra tính hợp lệ

- **File/Đoạn code**: `api/main.py:360-386` (`RawTransactionRequest`) — `wallet_from/wallet_to` chỉ là str, không validate format address.
- **Phân tích chi tiết**: Không ép buộc wallet address đúng checksum/format → string rác vẫn vào pipeline, fetch Etherscan fail hoặc graph không match.
- **Tác động**: Lỗi không cần thiết, khó debug người dùng gõ sai.
- **Đề xuất khắc phục**: Thêm validator pydantic (prefix 0x, độ dài 42).
- **Độ khó**: Dễ — 0.5 ngày.

---

## 7. TEST & ĐẢM BẢO CHẤT LƯỢNG

### [🟡] Nhược điểm: Thiếu test cho `audit_logger.py` và `name_screening.py` — 2 module bảo mật cốt lõi

- **File/Đoạn code**: `tests/` có `test_privacy.py`, `test_classifier.py`, `test_decision_engine.py`, `test_graph_provider.py`, `test_aggregation_monitor.py`, `test_feature_vector.py`, `test_insufficient_data.py`, `test_report_context_isolation.py`, `test_state.py` — **không có** `test_audit_logger.py`, `test_name_screening.py`.
- **Phân tích chi tiết**: `assert_no_raw_pii` được test (qua test_privacy), nhưng `log_step()` có nhánh "log entry suppressed khi PII detected" chưa được verify tự động. `name_screening` (Levenshtein fuzzy match với SDN) chưa có test cho đúng/sai threshold, Unicode, chữ hoa/thường.
- **Tác động**: Lỗi tương lai trong 2 module bảo mật sẽ không được phát hiện sớm.
- **Đề xuất khắc phục**: Thêm 2 test file tối thiểu, chạy vào CI.
- **Độ khó**: Trung bình — 1-2 ngày.

### [🟡] Nhược điểm: Không có integration test E2E cho pipeline đầy đủ (LangGraph → report .docx → HITL resume)

- **File/Đoạn code**: `tests/` chỉ test từng module độc lập; `logs/testd_*` là script chạy tay không phải test tự động.
- **Phân tích chi tiết**: Không có test nào: build graph, invoke với state giả REPORT, kiểm tra DOCX tồn tại + content, gọi resume("approved"), verify state cuối. Lỗi wiring giữa các node chỉ được phát hiện khi chạy hand.
- **Tác động**: Pipeline vỡ toàn cục không ai biết đến khi demo.
- **Đề xuất khắc phục**: Thêm 1 `test_pipeline_integration.py` dùng mock graph provider + mock fetch.
- **Độ khó**: Trung bình — 2 ngày.

### [🟡] Nhược điểm: Relative path trong test khiến test phụ thuộc CWD

- **File/Đoạn code**: `agents/transaction_classifier.py:63` (`Path("models/xgboost_aml.pkl")`), `agents/kyc_verification.py:13` (`os.path.join("data", ...)`) — nếu chạy pytest từ thư mục con, đường dẫn vỡ.
- **Phân tích chi tiết**: `pytest.ini` có thể set rootdir nhưng các module dùng relative path nên phụ thuộc CWD lúc chạy thực tế.
- **Tác động**: Test pass trên máy dev nhưng fail trong CI nếu chạy từ workspace khác.
- **Đề xuất khắc phục**: Dùng `BASE_DIR` từ `core/config.py` cho mọi path.
- **Độ khó**: Dễ — 0.5 ngày.

---

## 8. FRONTEND & UX

### [🔴] Nhược điểm: Helper `el()` cho phép `innerHTML` — nguy cơ XSS

- **File/Đoạn code**: `frontend_html/app.js:32` — `else if (k === "html") node.innerHTML = v;`.
- **Phân tích chi tiết**: Pattern này cho phép inject HTML string. Hiện tại chưa dùng với dữ liệu người dùng trực tiếp, nhưng `renderStepLog` (dòng 714) render `JSON.stringify(step.snapshot)` vào `textContent` an toàn. Tuy nhiên nếu sau này ai đó dùng `html:` với field từ state (vd legal_citations có `noi_dung_tom_tat` do LLM sinh), sẽ thành XSS → attacker chiếm session.
- **Tác động**: XSS toàn trang, đánh cắp token.
- **Đề xuất khắc phục**: Bỏ hoàn toàn option `html`, hoặc chỉ dùng cho constant và validate thật kỹ.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: Chat không có timeout — LLM chậm thì user chờ vô hạn

- **File/Đoạn code**: `frontend_html/app.js:680-700` (`askChat`) — `fetch` không có `AbortController` timeout.
- **Phân tích chi tiết**: Nếu LLM API treo (network hang), button disabled mãi, user không thể hỏi tiếp hay thao tác gì.
- **Tác động**: UX tệ, chuyên viên mất thao tác.
- **Đề xuất khắc phục**: `AbortController` với timeout 30-60s + retry/backoff.
- **Độ khó**: Dễ — 0.5 ngày.

### [🟡] Nhược điểm: `authToken` lưu trong memory JS — refresh trang là mất session

- **File/Đoạn code**: `frontend_html/app.js:5` — `let authToken = null;` không persist vào `sessionStorage`.
- **Phân tích chi tiết**: User đang xem case pending, bấm F5 → phải login lại, mất vị trí. Không phải lỗi bảo mật (persist sẽ tệ hơn nếu XSS), nhưng UX kém cho demo và vận hành.
- **Tác động**: Giảm trải nghiệm demo trước hội đồng (F5 giữa chừng rất tệ).
- **Đề xuất khắc phục**: `sessionStorage` (tự xoá khi đóng tab) — chấp nhận đánh đổi an toàn thấp.
- **Độ khó**: Dễ — 15 phút.

### [🟢] Nhược điểm: Không có accessibility / responsive tối ưu

- **File/Đoạn code**: `frontend_html/app.js` + `index.html`/`styles.css` — không dùng semantic aria, một số fixed width trong SVG.
- **Phân tích chi tiết**: `buildFlowSvg` dùng `width = Math.max(560, ...)` — có scale qua viewBox nhưng trên mobile vẫn chật. Không có `aria-label` trên button/input.
- **Tác động**: Giảm khả năng dùng cho màn hình nhỏ, người khuyết tật.
- **Đề xuất khắc phục**: Thêm `viewport` meta (nếu chưa có), responsive CSS breakpoint, aria-label cơ bản.
- **Độ khó**: Trung bình — 1-2 ngày.

---

## 9. GÓC NHÌN BỔ SUNG — VẬN HÀNH DỰ ÁN / TỔ CHỨC

### [🟡] Nhược điểm: Chưa có CI/CD — mọi thay đổi phụ thuộc chạy tay

- **File/Đoạn code**: Không có `.github/workflows/`, không có script build pipeline tự động.
- **Phân tích chi tiết**: Khoá luận/đồ án thường không yêu cầu CI, nhưng với dự án có 20+ test + model training riêng, CI sẽ: chạy pytest tự động, kiểm tra path, verify model schema khớp feature_schema.
- **Tác động**: Hồi quy không bị phát hiện cho đến khi demo.
- **Đề xuất khắc phục**: Thêm GitHub Actions đơn giản: `pytest` + `flake8` trên push.
- **Độ khó**: Dễ — 1 ngày.

### [🟢] Nhược điểm: `mistakes.md` chứa cả nội dung chat cũ + hướng dẫn chưa cập nhật

- **File/Đoạn code**: `mistakes.md` dòng 1-9 (ghi chú cũ "Về wallet clustering..." không còn liên quan đến kiến trúc Decision Engine mới), dòng 20+ là bản tóm tắt project cũ trùng PROJECT_SUMMARY.md.
- **Phân tích chi tiết**: File bị "thừa kế" nhiều lớp nội dung — người đọc mới khó biết đâu là trạng thái hiện tại.
- **Tác động**: Nhầm lẫn khi làm việc — AI khác (hoặc chính mình sau này) đọc lại sẽ hiểu sai.
- **Đề xuất khắc phục**: Dọn file: giữ lại phần "hạn chế cần ghi vào báo cáo", xoá phần trùng lặp.
- **Độ khó**: Dễ — 0.5 ngày.

---

## 📊 TOP 10 NHƯỢC ĐIỂM NGHIÊM TRỌNG NHẤT

| # | Nhược điểm | Mức độ | Lý do 1 dòng |
|---|-----------|--------|--------------|
| 1 | PII_SALT fallback dev hard-coded (`privacy_layer.py:25`) | 🔴 | Toàn bộ hash PII có thể bị reverse nếu thiếu env — đánh sập privacy by design |
| 2 | Default credential `nhanvien1/123456789` (`api/main.py:75`) | 🔴 | Attacker đăng nhập được, thao túng Approve/Reject STR |
| 3 | PII plaintext sống trong memory qua closure + `RUNS` bất tử | 🔴 | Core dump/memory inspection → lộ PII khách hàng |
| 4 | LLM lỗi → mock output được in vào STR chính thức | 🔴 | STR nộp NHNN có thể chứa "căn cứ pháp lý" giả tạo |
| 5 | Token lộ trong URL query (`app.js:240`) | 🔴 | Token bị ghi trong access log/history → bị đánh cắp |
| 6 | Không có idempotency cho tx_hash | 🔴 | Gửi 2 lần → 2 STR trùng nộp NHNN |
| 7 | Ngưỡng category medium (0.6) chưa kiểm chứng nhưng quyết định REVIEW | 🔴 | Hàng loạt case bị route sai do ngưỡng bịa |
| 8 | Threshold classifier fallback 0.7 chưa calibrate | 🟡 | REPORT/PASS sai hệ thống khi thiếu file threshold |
| 9 | Projection Neo4j GDS leak khi exception — không try/finally | 🟡 | Pipeline production tê liệt do graph tạm bị dơ |
| 10 | CORS `*` + `/logs` raw không filter + không rate-limit login | 🟡 | Bề mặt tấn công API nội bộ mở rộng không cần thiết |

---

> **Kết luận**: Dự án có nền tảng privacy-by-design và explainability tốt (điểm mạnh của `assert_no_raw_pii`, rule-based composite, SHAP). Nhưng tồn tại **6 lỗ hổng mức nghiêm trọng** về bảo mật vận hành (salt fallback, default credential, PII in-memory, token in URL, LLM-mock-in-report, idempotency) — đây là những điểm cần ưu tiên xử lý **trước khi bảo vệ** nếu muốn tránh bị hội đồng chấm điểm khó. Các vấn đề còn lại là chất lượng vận hành và bảo trì hợp lý cho MVP.