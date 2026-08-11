# PROJECT SUMMARY

> **Nguồn sự thật duy nhất của tài liệu này là code/configuration/test/pipeline hiện tại của project.**
> Mọi claim trong tài liệu đều được đối chiếu với file thực tế; không dựa trên summary cũ hoặc mô tả lý thuyết.
> Cập nhật lần cuối: 2026-08-09.

---

## 1. Project Overview

- **Tên project:** DigitalAsset Guard — AML AI Copilot.
- **Bài toán:** Hỗ trợ chuyên viên AML (tại ngân hàng / VASP / tổ chức tín dụng) phát hiện và điều tra giao dịch tài sản số (blockchain) đáng ngờ liên quan đến rửa tiền, theo khung pháp lý Việt Nam (Thông tư 27/2025/TT-NHNN, Luật Phòng chống rửa tiền 2022, Quyết định 11/2023/QĐ-TTg).
- **Mục tiêu:** Xây dựng một copilot AML kết hợp nhiều nguồn tín hiệu độc lập (ML classifier, phân tích đồ thị dòng tiền, sàng lọc sanctions, phát hiện structuring/smurfing) thành một workflow điều tra có con người trong vòng lặp (human-in-the-loop), kèm giải thích quyết định và trích dẫn pháp lý.
- **Đối tượng sử dụng:** Chuyên viên AML/Phòng chống rửa tiền; người điều tra giao dịch nghi vấn; người chuẩn bị Báo cáo giao dịch đáng ngờ (STR) theo Mẫu 04 Thông tư 27.
- **Trạng thái:** **Prototype/MVP có chạy được end-to-end.** Các pipeline, API, test, model chạy thực sự; Neo4j/GDS và ChromaDB/LLM đã được verify chạy thật (xem `logs/`). Tuy nhiên dữ liệu đầu vào cho graph và off-chain vẫn ở mức demo/mock, chưa đủ điều kiện production.

---

## 2. Problem & Motivation

- Tài sản số (blockchain) có tính **xuyên biên giới, giả danh (pseudonymous), tốc độ cao, không thể sửa đổi sau khi ghi**, khiến nó trở thành kênh rửa tiền và tài trợ khủng bố ngày càng được quan tâm.
- Dấu hiệu rửa tiền thường không nằm ở một giao dịch đơn lẻ mà nằm ở **cấu trúc dòng tiền đa hop** (layering), **chia nhỏ giao dịch để né ngưỡng báo cáo** (structuring/smurfing), **kết nối tới ví bị trừng phạt** (sanctions exposure), hoặc **đặc điểm hành vi ví** (mixing, chu kỳ bất thường).
- Quy định Việt Nam (Thông tư 27/2025/TT-NHNN → ngưỡng báo cáo 500 triệu VND / 1.000 USD; Luật PCRT 2022 + QĐ 11/2023/QĐ-TTg → ngưỡng 400 triệu VND cho giao dịch giá trị lớn) yêu cầu tổ chức phải **phát hiện, đánh giá, báo cáo kịp thời**. Việc này tốn nhiều công sức nếu làm thủ công trên dữ liệu blockchain lớn.
- Giải pháp là một hệ thống **tự động sàng lọc ban đầu + cung cấp bằng chứng + để chuyên viên quyết định cuối cùng**, thay vì cố gắng tự động thay thế con người.

---

## 3. Proposed Solution

- DigitalAsset Guard tổ chức một **investigation workflow** gồm 8 bước nghiệp vụ:
  1. Webhook/API nhận giao dịch (bao gồm PII của khách hàng).
  2. **Privacy Layer**: băm PII (SHA-256 + salt) + chạy fuzzy name-screening **trước khi băm**.
  3. **Aggregation Monitor**: phát hiện structuring/smurfing từ lịch sử off-chain.
  4. **Transaction Assistant**: chấm điểm rủi ro ML (XGBoost, 37 features) từ dữ liệu on-chain thật (Etherscan).
  5. **Graph Assistant**: phân tích đồ thị dòng tiền (PPR, Louvain, hop distance, suspicious path) qua Neo4j/GDS hoặc NetworkX mock.
  6. **Sanctions Assistant**: exact-match ví với danh sách OFAC SDN.
  7. **Decision Engine**: tổng hợp thành quyết định **PASS / REVIEW / REPORT** bằng rule-based composite (không dùng một con số risk tổng hợp).
  8. **Legal RAG + Report Assistant + Human-in-the-loop**: nếu REPORT, soạn dự thảo STR (Mẫu 04 .docx) kèm trích dẫn pháp lý, rồi để chuyên viên Approve/Reject trước khi gửi.
- Giá trị cốt lõi: chuyên viên không bị chôn vùi trong dữ liệu thô; hệ thống **giải thích lý do** (decision evidence, graph path, SHAP features, legal citations) và **để con người có quyết định cuối cùng**.

---

## 4. System Architecture

Kiến trúc được tái dựng trực tiếp từ code hiện tại:

### 4.1 Thành phần

| Thành phần | Vai trò | File chính |
|---|---|---|
| **Frontend** | Giao diện AML officer: nhập giao dịch, xem kết quả pipeline, Approve/Reject, tải STR, chat hỏi đáp | `frontend_html/index.html`, `frontend_html/app.js`, `frontend_html/styles.css` |
| **API Gateway** | FastAPI: auth, `/screen-wallet`, `/api/pipeline/run`, `/state`, `/decision`, `/report`, `/chat`, `/logs`, serve frontend | `api/main.py` |
| **Orchestration** | LangGraph StateGraph, `PipelineRun` là facade duy nhất để chạy pipeline và resume HITL | `core/graph_builder.py` |
| **Privacy Layer** | Băm PII (SHA-256 + salt), chạy fuzzy name-matching trước khi băm, chốt `assert_no_raw_pii` | `core/privacy_layer.py`, `core/name_screening.py` |
| **Aggregation Monitor** | Phát hiện structuring/smurfing từ lịch sử off-chain `wallet_tx_history` | `agents/aggregation_monitor.py` |
| **Transaction Assistant** | Build feature vector thật (37 features) từ `wallet_record` Etherscan → XGBoost → `classifier_score` + SHAP `top_features` | `agents/transaction_classifier.py`, `scripts/feature_builder.py`, `scripts/etherscan_fetcher.py` |
| **Graph Assistant** | PPR (Personalized PageRank), Louvain community, hop distance, suspicious path, fan-out; phân biệt 3 trạng thái graph (NO_GRAPH_DATA / AVAILABLE_NO_SANCTION_PATH / SANCTION_PATH_FOUND) | `agents/graph_aml.py`, `core/graph_provider.py` |
| **Sanctions Assistant** | Exact-match `wallet_from`/`wallet_to` với OFAC SDN list → `sanction_result` (fact, không điểm) | `agents/kyc_verification.py` |
| **Decision Engine** | Rule-based composite → `decision` = PASS/REVIEW/REPORT + `decision_evidence` | `agents/decision_engine.py` |
| **Regulation RAG** | Truy vấn ChromaDB (legal docs) + LLM (Groq/OpenAI-compatible) → `legal_citations` có trích dẫn nguồn | `agents/regulation_rag.py`, `db/vector_db.py` |
| **Report Assistant** | Sinh STR dự thảo Mẫu 04 (.docx) khi decision=REPORT, đặt `approval_status=pending` | `agents/alert_report.py` |
| **Audit Logger** | Ghi JSON audit trail cho mỗi bước (không ghi PII) | `core/audit_logger.py` |
| **Mock Core Banking** | Nguồn OFF-CHAIN giả lập cho `wallet_tx_history` (structuring) — chỉ 1 account demo | `scripts/mock_core_banking.py` |

### 4.2 Data flow

```
client (browser / POST API)
   │  RawTransactionRequest: tx_hash, wallet_from, wallet_to, amount_vnd,
   │                          fullname, id_number, account_number, scenario (demo-only)
   ▼
api/main.py (_build_initial_state)
   • fetch_wallet_record() bằng Etherscan API  → state["wallet_record"]
   • get_wallet_tx_history(account_number)      → state["wallet_tx_history"] (mock off-chain, nếu khớp)
   • get_graph_provider() (mock hoặc neo4j)     → state["mock_graph_edges"]/["mock_blacklisted_wallets"] (chỉ mock)
   ▼
PipelineRun.run()  (LangGraph)
   privacy_layer
      └─ fuzzy name-screening (TRƯỚC băm) → name_similarity_*
      └─ mask_pii → hashed_*  (loại bỏ raw PII khỏi state)
      ▼
   aggregation_monitor  → is_large_tx, aggregated_amount_7d,
                          near_threshold_count_30d, structuring_detected, aggregation_status
      ▼
   transaction_classifier → classifier_score, top_features (SHAP),
                            avg_time_between_tx, balance_clustering_flag, insufficient_data
      ▼
   graph_aml → graph_score (PPR), community_id, hop_distance_to_blacklist,
               suspicious_path, fan_out, graph_analysis_status, sanction_path_found
      ▼
   sanctions (verify_kyc) → sanction_result
      ▼
   decision_engine → decision (PASS/REVIEW/REPORT), decision_reason,
                     decision_evidence, case_status
      │
      ├─ PASS    → END (auto_cleared — không cần người xem)
      │
      ├─ REVIEW  → END (dừng tại decision engine; chuyên viên xem evidence trực tiếp,
      │             KHÔNG tự soạn STR — nghĩa vụ báo cáo chỉ khi REPORT)
      │
      └─ REPORT  → regulation_rag → alert_report → HUMAN CHECKPOINT
                     (is_paused: case_status=pending_review && approval_status=pending)
                     │
                     ▼
             chuyên viên Approve/Reject
                     │
                     ▼
             PipelineRun.resume() → cập nhật approval_status → END
```

**Ghi chú kiến trúc quan trọng:**
- `PipelineRun` là **facade duy nhất** để chạy pipeline (`demo_runner.py` và `api/main.py` đều dùng chung pattern này; riêng `scripts/demo_runner.py` gọi trực tiếp từng assistant để demo AI Core, có ghi rõ lý do trong docstring).
- PII plaintext chỉ tồn tại ở request boundary, sau đó được băm. Với STR .docx, PII plaintext được truyền **qua closure** từ `PipelineRun.__init__` → `build_pipeline` → node `alert_report`, KHÔNG nằm trong state channels (xem `core/graph_builder.py`, test `tests/test_report_context_isolation.py`).
- Routing sau decision engine dựa trên field `decision` (không dùng `case_status` vì REVIEW và REPORT đều có `case_status=pending_review`).

---

## 5. AI/ML & Risk Detection

### 5.1 Dataset

- **Dataset:** Ethereum Fraud Detection (Farrugia et al., file `Complete.csv`).
- **Tiền xử lý:** `scripts/01_prepare_ethereum_fraud_dataset.py` — dedup địa chỉ, loại địa chỉ có FLAG mâu thuẫn, giữ 37 features **mà production `feature_builder.py` tái tạo được** từ Etherscan (`txlist` + `tokentx`), loại các cột không reproducible / leakage.
- **Số liệu clean** (từ `data/processed/dataset_summary.txt`):
  - 4.675 ví (4.681 raw → 4.675 final).
  - Class distribution: licit = 2.497, illicit = 2.178 (gần cân bằng — dataset này KHÔNG giống Elliptic 2% illicit).
  - 37 features numeric (22 nhóm ETH + 15 nhóm ERC20), schema tại `data/processed/feature_schema.json`.
- **Split:** StratifiedSplit train/test (test_size=0.2, random_state=42), tách **sau khi** drop cột `Address` để chống rò rỉ. Test cố định lưu tại `data/processed/ethereum_fraud_test.csv`.

### 5.2 Model & Training

- **Model:** XGBoost (`models/xgboost_aml.pkl`).
- **Huấn luyện:** `agents/train_classifier.py` thử nghiệm **3 cấu hình xử lý mất cân bằng nhãn**: chỉ `scale_pos_weight`, chỉ SMOTE, cả hai. Chọn cấu hình tốt nhất theo **AUC-PR** (tie-break Recall).
- **Kết quả thật** (từ `tests/model_comparison_v2.json`, config được chọn = `smote_only`):

| Cấu hình | Precision | Recall | F1 | AUC-PR |
|---|---|---|---|---|
| scale_pos_weight_only | 0.9745 | 0.9633 | 0.9689 | 0.9950 |
| **smote_only (chọn)** | **0.9813** | **0.9633** | **0.9722** | **0.9955** |
| smote_plus_scale_pos_weight | 0.9813 | 0.9633 | 0.9722 | 0.9955 |

- **Lưu ý báo cáo:** SMOTE chỉ cải thiện Precision/F1 nhẹ, Recall/AUC-PR gần như không đổi so với baseline — đây là số liệu THẬT, cần trình bày trung thực (không che `model_comparison_v2.json`).

### 5.3 Inference (Transaction Assistant)

- `agents/transaction_classifier.py::analyze_transaction`:
  - **Bắt buộc** có `state["wallet_record"]` đúng schema Etherscan ở production path;
  - Build feature vector **thật** bằng `scripts/feature_builder.build_full_wallet_features()`, map theo ĐÚNG thứ tự `feature_schema.json` / `model.feature_names` (không hard-code index);
  - Chấm `classifier_score = P(illicit)` bằng `predict_proba`; ghi `top_features` = **SHAP per-transaction** (top 3 |shap value|, cả âm/dương — không lọc để "kể chuyện đẹp");
  - Ghi 2 đặc trưng hành vi (explainability, KHÔNG phải input model): `avg_time_between_tx`, `balance_clustering_flag`.
  - `allow_mock=True` chỉ hợp lệ cho demo/test (`scripts/demo_runner.py`, tests). Production path thiếu `wallet_record` → raise `NotImplementedError`, không âm thầm dùng mock.

### 5.4 Class imbalance & Threshold

- Threshold classifier θ = **0.8748** (`models/classifier_threshold.json`) — calibrate bằng **precision-recall curve** trên test set, target recall 0.9 (đạt recall 0.9014, precision 0.9874) — file `agents/calibrate_classifier_threshold.py`.
- **Chỉ số chính:** Recall và AUC-PR (phù hợp bài toán mất cân bằng nhãn), Accuracy chỉ mang tính tham khảo.

### 5.5 Decision Engine — rule-based composite (KHÔNG còn weighted-sum)

`agents/decision_engine.py`:
- **Đã bỏ** `risk_assessment_score = w1*classifier + w2*graph` (lý do: không có dữ liệu ghép cặp classifier+graph+label có ground-truth để calibrate weights/threshold — train trên dữ liệu mock sẽ tạo số giả). Field `risk_assessment_score` giữ = `None` chỉ để code cũ không crash.
- Thay bằng **5 rule độc lập, xét tuần tự**:
  1. **Rule 1 — Sanctions exact match** → REPORT.
  2. **Rule 2 — Structuring** (`structuring_detected is True`, từ Aggregation Monitor) → REPORT. `None` (chưa đánh giá) KHÔNG được coi như False.
  3. **Rule 3 — Classifier** `score >= θ` (θ=0.8748) → REPORT.
  4. **Rule 4 — Graph exposure** `hop_distance_to_blacklist <= 2` → REPORT (fact hình học, không cần label).
  5. **Rule 5 — 2 tín hiệu "medium" cùng lúc** (classifier medium `[θ*0.6, θ)` + graph medium hop `(2, 4]`) → REVIEW.
- **REVIEW** → dừng ở decision engine, chờ chuyên viên xem `decision_evidence`, KHÔNG tự soạn STR.
- **REPORT** → chạy RAG → Report → HITL checkpoint.
- **PASS** → `case_status=auto_cleared`, không cần người xem.
- **Cảnh báo chính thức:** ngưỡng "medium" (`_CLASSIFIER_MEDIUM_RATIO=0.6`, `_GRAPH_MEDIUM_HOP_MAX=4`) là **giả định tạm, chưa kiểm chứng bằng dữ liệu** — được đánh dấu rõ trong code bằng hằng số riêng.

### 5.6 insufficient_data (fix audit zero-tx wallet)

- `agents/transaction_classifier.py::_assess_insufficient_data`: ví có `0 tx (txlist) + 0 tx (tokentx)` → `insufficient_data=True` (đếm trên DỮ LIỆU THÔ, không dựa trên feature đã tính).
- Khi `insufficient_data=True`, Decision Engine **bắt buộc route REVIEW** (không REPORT/PASS tự động) vì XGBoost chấm vector toàn 0 ~1.0 (đặc thù tập train) — không phản ánh rủi ro thật (xem `tests/test_insufficient_data.py`).

---

## 6. Blockchain & Graph Intelligence

### 6.1 Dữ liệu blockchain

- **Nguồn:** Etherscan API V2 (`scripts/etherscan_fetcher.py`) — chỉ Ethereum (`chainid=1`); các chain khác bị bỏ do giới hạn gói trả phí.
- `wallet_record` schema: `{"address", "chains": {"ethereum": [txlist]}, "token_transfers": {"ethereum": [tokentx]}}`; dùng cho feature_builder / Transaction Assistant.
- **KHÔNG lưu toàn bộ blockchain**: chỉ fetch theo từng `wallet_from` của request (max 100 tx thường + 100 token tx mặc định qua API path).

### 6.2 Transaction graph

- **Graph data source selector:** `GRAPH_SOURCE` trong `.env` (`core/config.py::resolve_graph_source`) — "mock" hoặc "neo4j". Fallback theo `DEMO_MODE`.
- **`core/graph_provider.py`** tách DATA SOURCE khỏi thuật toán:
  - `MockGraphProvider` đọc `data/mock/scenario_*.json`, cung cấp edges `(from, to, amount_vnd)` + `blacklisted_wallets` — dữ liệu giả nhưng thuật toán chạy thật.
  - `Neo4jGraphProvider` là passthrough marker — graph_aml query Neo4j/GDS gốc, không đọc scenario mock.
- **Demo data:** `data/mock/scenario_graph_sanction.json`, `scenario_smurfing.json`, `scenario_layering.json`, `scenario_name_similarity.json`, `customers.json`.

### 6.3 Thuật toán graph (chạy thật — `agents/graph_aml.py`)

| Thành phần | Chi tiết |
|---|---|
| **Personalized PageRank (PPR)** | NetworkX `nx.pagerank` (alpha=0.85, personalization 0.7 vào blacklisted) hoặc Neo4j GDS `gds.pageRank.stream` (damping 0.85, `sourceNodes` = sanctioned wallets, weight = amount) |
| **Louvain community** | NetworkX `louvain_communities` (resolution=0.5, seed=42) hoặc GDS `gds.louvain.stream` |
| **Hop distance** | shortest path từ ví blacklisted/sanctioned tới `wallet_from` |
| **Suspicious path** | đường đi thật (bắt đầu từ ví bị sanction) |
| **Fan-out** | số ví nhận tiền trực tiếp |

### 6.4 Neo4j/GDS — trạng thái VERIFIED

- `docker-compose.yml`: Neo4j 5.26 Community + GDS (cài JAR thủ công, không auto-download do lỗi mạng).
- `db/neo4j_setup.py`: constraint `Wallet(address) UNIQUE`, index `Wallet(is_sanctioned)`, seed test.
- **Đã verify chạy thật trên Neo4j/GDS 2.13.2** (`logs/testd_neo4j_real.txt`):
  - `GRAPH_AVAILABLE_SANCTION_PATH_FOUND`: graph_score=0.108375, hop=2, suspicious_path=[sanctioned → intermediate → wallet_from], community_id=5.
  - `GRAPH_AVAILABLE_NO_SANCTION_PATH` (có data, không path): hop=None, suspicious_path=[], community_id=8 (`logs/testd_neo4j_bc.txt`).
  - `NO_GRAPH_DATA` (ví không tồn tại): graph_score=0.0, hop=None, community_id=0.
- **API path production đã verify** (`logs/testd_api_real.txt`): pipeline chạy qua Neo4j thật → decision=REPORT (Rule 4) → RAG → STR .docx → `approval_status=pending`.
- **Giới hạn:** data trong Neo4j hiện tại chỉ là demo case do script/test seed — **chưa có pipeline ingest đồ thị blockchain thực tế quy mô lớn**. `scripts/cleanup_demo_graph_data.py` dọn demo data trước go-live.

### 6.5 Graph semantics (3 trạng thái)

- `graph_analysis_status`: `NO_GRAPH_DATA` / `GRAPH_AVAILABLE_NO_SANCTION_PATH` / `GRAPH_AVAILABLE_SANCTION_PATH_FOUND`.
- `graph_data_available`: True khi có edges; `sanction_path_found`: True/False/None tương ứng.
- Frontend dùng các field này làm tín hiệu chính, KHÔNG suy luận từ `graph_score=0` / `hop=None` (vì PPR=0/fan_out=0 là kết quả thuật toán hợp lệ khi graph có dữ liệu).

---

## 7. Regulatory RAG & Compliance Support

### 7.1 Nguồn pháp lý hiện tại

`data/legal_docs/`:
- `thong_tu_27_2025.txt` — Thông tư 27/2025/TT-NHNN (báo cáo giao dịch đáng ngờ STR, ngưỡng báo cáo, thời hạn).
- `luat_pcrt_2022_qd11_2023.txt` — Luật Phòng, chống rửa tiền 2022 + Quyết định 11/2023/QĐ-TTg (ngưỡng 400.000.000 VND cho giao dịch có giá trị lớn / CTR).
- `fatf_recommendations.txt` — tóm tắt Khuyến nghị FATF.
- **Đã bỏ `thong_tu_32_2026.txt`** (thuế tài sản mã hóa) — không còn nguồn trong `db/vector_db.py::REQUIRED_FILES` (xem comment "Chủ đích bỏ Thông tư 32").

### 7.2 RAG architecture

- **Vector DB:** ChromaDB local (`db/vector_db.py`, `db/chroma_db`), collection `legal_regulations`, chunk theo dòng trống, metadata `{"source": filename}`, embedding mặc định của Chroma.
- **Retrieval:** `agents/regulation_rag.py::run_regulation_rag`:
  - Tách **nhiều lượt query** để tránh pha trộn chủ đề: lượt STR (dựa trên evidence quyết định — hop/sanction/path), lượt "giá trị lớn" (ngưỡng 400tr), lượt thuế (chỉ giữ nếu ChromaDB thật sự trả chunk TT32 — thực tế không còn nguồn nên nhánh này rỗng).
  - KHÔNG đưa `amount_vnd` vào câu query ChromaDB (tránh lệch semantic về nhóm "giá trị lớn").
  - Context quyết định (`decision`, `decision_reason`, `decision_evidence`, `hop_distance`, `sanction_result`, `suspicious_path`) được đưa vào prompt LLM.
- **LLM:** OpenAI-compatible API qua `core` thư viện `openai`. `.env` hiện tại: `LLM_BASE_URL=https://api.groq.com/openai/v1`, `LLM_MODEL=llama-3.3-70b-versatile`. Đổi provider chỉ cần đổi 3 biến env.

### 7.3 Source attribution / Citation

- LLM bị **ép trả về đúng JSON schema** `legal_citations` với field `source_file` (chọn từ danh sách nguồn HỢP LỆ đã truy xuất) → code tự ánh xạ sang tên chính thức qua `CANONICAL_SOURCE_NAMES` (không tin LLM tự nhớ tên Thông tư).
- Mỗi citation gồm: `source`, `dieu_khoan`, `noi_dung_tom_tat`, `ly_do_ap_dung`.
- Prompt có "QUY TẮC BẮT BUỘC": chỉ trích điều khoản THỰC SỰ có trong context, đối chiếu đúng ngưỡng số (400tr), ưu tiên STR khi có evidence graph/sanction, tự kiểm tra lại phép so sánh số học.

### 7.4 Trạng thái lỗi — KHÔNG bao giờ giả vờ ổn

- `legal_rag_status`: `OK` hoặc `UNAVAILABLE`.
- Khi thiếu `LLM_API_KEY` hoặc API lỗi → `legal_citations = []`, `legal_rag_error` ghi rõ, STR in cảnh báo "Chuyên viên AML phải tự tra cứu" — **KHÔNG đẩy mock content vào STR** (`tests/test_legal_rag_status.py`).
- RAG **không ảnh hưởng** Decision Engine (REPORT/REVIEW/PASS quyết định độc lập trước).

### 7.5 Vai trò trong investigation

- RAG cung cấp **căn cứ pháp lý + lý do áp dụng** cho dự thảo STR và cho chat giải thích.
- **Hệ thống KHÔNG tự động quyết định pháp lý, KHÔNG thay thế luật sư/AML officer.** STR là *dự thảo*, bắt buộc chuyên viên kiểm tra và phê duyệt trước khi gửi (in rõ trên .docx).

---

## 8. Explainability & Human-in-the-Loop

### 8.1 Các lớp giải thích

| Lớp | Nội dung | Nguồn |
|---|---|---|
| **Risk explanation** | `decision`, `decision_reason`, `decision_evidence` (liệt kê rule nào đã kích hoạt + số liệu cụ thể) | `agents/decision_engine.py` |
| **Model explanation** | `top_features` — SHAP per-transaction (top 3 feature ảnh hưởng nhất, cả dương/âm), `classifier_score` | `agents/transaction_classifier.py` |
| **Graph evidence** | `suspicious_path` (SVG trong UI), `hop_distance_to_blacklist`, `community_id`, `fan_out`, `graph_analysis_status` | `agents/graph_aml.py`, `frontend_html/app.js` |
| **Regulatory evidence** | `legal_citations` (source + điều khoản + nội dung + lý do áp dụng) | `agents/regulation_rag.py` |
| **Reasoning** | Chat giải thích (`/api/pipeline/{tx}/chat`) — LLM chỉ được dùng dữ liệu trong state, không tự tạo số liệu/căn cứ pháp lý | `api/main.py` |

### 8.2 Human-in-the-loop

- **Checkpoint**: `is_paused()` = `case_status == "pending_review"` && `approval_status == "pending"`.
- **Approve/Reject**: `POST /api/pipeline/{tx_hash}/decision` với `{approval_status: "approved"|"rejected"}` → `PipelineRun.resume()` cập nhật checkpoint.
- **REVIEW vs REPORT**:
  - Cả 2 đều cần người xem (`pending_review`), nhưng **chỉ REPORT có STR draft**. REVIEW không tự soạn STR (case còn mơ hồ).
  - UI phân biệt bằng label decision; case `insufficient_data` hiển thị nhãn riêng "THIẾU DỮ LIỆU on-chain" để chuyên viên không hiểu nhầm mức độ rủi ro.
- **Báo cáo**: STR .docx Mẫu 04 có disclaimer "dự thảo, phải được chuyên viên kiểm tra/bổ sung/phê duyệt trước khi gửi".
- **Audit**: mỗi bước pipeline ghi `logs/audit_trail.log` (JSON: timestamp, agent, tx_hash, duration_ms, state_keys_present) — chỉ ghi tên key, không serialize giá trị; nếu phát hiện PII gốc thì ghi "ERROR: raw PII detected" thay vì ghi state (`core/audit_logger.py`).

---

## 9. Frontend, API & User Workflow

### 9.1 Workflow thực tế (từ code `frontend_html/app.js` + `api/main.py`)

1. **Login** → `POST /api/auth/login` → token Bearer (default `nhanvien1` / `123456789`, config qua env `AML_AUTH_USERNAME`/`AML_AUTH_PASSWORD`).
2. **Nhập giao dịch** (form): tx_hash (tự sinh nếu trống), wallet_from, wallet_to, amount_vnd, fullname, id_number, account_number → `POST /api/pipeline/run`.
3. **API build initial state**: fetch `wallet_record` từ Etherscan, fetch mock off-chain history theo account_number, nạp graph scenario (nếu GRAPH_SOURCE=mock) → khởi tạo `PipelineRun` → `run()`.
4. **Hiển thị kết quả**:
   - Pipeline rail (từng assistant đã chạy).
   - Risk breakdown: classifier_score, graph_score (PPR), hop, fan-out, community, insufficient_data + giải thích ý nghĩa PPR.
   - Sanctions match + fuzzy name warning.
   - Suspicious path dạng **SVG node-link** (ví sanction → ví trung gian → ví đích).
   - Legal citations (điều khoản + lý do áp dụng).
   - Nút **Approve / Reject** khi `approval_status == "pending"`.
   - **Chat** hỏi đáp về giao dịch (`/api/pipeline/{tx}/chat`).
   - Step log chi tiết (debug).
5. **Approve** → nút tải STR .docx (`GET /api/pipeline/{tx}/report?token=...`).
6. **/screen-wallet** — endpoint sàng lọc nhanh 1 ví (KHÔNG chạy full LangGraph): trả `is_sanctioned` + `classifier_score` + `risk_level` (low/medium/high; ngưỡng dùng đúng θ calibrate).

### 9.2 API endpoints

- `POST /api/auth/login`
- `POST /screen-wallet`
- `GET /health`
- `GET /logs` (auth) — audit trail
- `POST /api/pipeline/run` (auth)
- `GET /api/pipeline/{tx_hash}/state` (auth)
- `POST /api/pipeline/{tx_hash}/decision` (auth) — `{approval_status}`
- `GET /api/pipeline/{tx_hash}/report?token=...` — tải .docx
- `POST /api/pipeline/{tx_hash}/chat` (auth)
- `GET /` — serve frontend

---

## 10. Data Sources & External Integrations

| Nguồn | Loại | Cách dùng hiện tại | Trạng thái |
|---|---|---|---|
| **Etherscan API V2** (`ETHERSCAN_API_KEY`) | External API | Fetch `txlist` + `tokentx` cho `wallet_from` mỗi request → feature vector thật | Thật |
| **OFAC SDN** (`data/raw/ofac/sdn.xml`) | External dataset (tải về) | Parse trước → `data/processed/sample_ofac_wallet.txt` (exact match) + `sdn_names.txt` (fuzzy name) | Thật (dataset đã có trên đĩa) |
| **Ethereum Fraud Detection dataset** (Farrugia) | External dataset | Huấn luyện XGBoost + calibrate threshold | Thật (đã clean) |
| **Neo4j Community + GDS 2.13.2** | Database/Graph | Query PPR/Louvain/shortest-path cho graph_aml | Thật (đã verify chạy, data demo) |
| **ChromaDB local** | Vector DB | Chứa legal docs chunks | Thật (nếu đã ingest) |
| **LLM (Groq — llama-3.3-70b)** | External API | RAG trích dẫn + chat giải thích | Thật, cần `LLM_API_KEY` |
| **Mock Core Banking** (`scripts/mock_core_banking.py`) | Mock off-chain | `wallet_tx_history` cho Aggregation Monitor (chỉ 1 account `0123456789`) | **Mock** |
| **Mock graph scenarios** (`data/mock/scenario_*.json`) | Mock | Cung cấp edges/blacklisted cho Graph khi `GRAPH_SOURCE=mock` | **Mock** |
| **Mock customers** (`data/mock/customers.json`) | Mock | Dữ liệu khách hàng giả lập | **Mock** |

---

## 11. Innovation & Differentiation

| So với | Khác biệt |
|---|---|
| **Rule-based AML truyền thống** | Không chỉ dùng ngưỡng tĩnh (500tr); kết hợp ML classifier (XGBoost + SHAP), graph exposure (hop tới ví sanction), structuring detection — đa tín hiệu độc lập, có explainability |
| **Blockchain intelligence đơn thuần** (chỉ trace dòng tiền) | Tích hợp trace với ML scoring + sanctions + regulation RAG + STR drafting + HITL — biến graph thành **bằng chứng quyết định** (decision evidence), không chỉ là công cụ xem |
| **Generic LLM chatbot** | LLM chỉ được dùng dữ liệu có trong state (bound context), không tự tạo số liệu/căn cứ pháp lý; citation được ép nguồn hợp lệ qua `CANONICAL_SOURCE_NAMES`; khi LLM lỗi thì `legal_rag_status=UNAVAILABLE` — không giả vờ ổn |
| **Traditional RAG** | RAG không chỉ retrieval — nó dùng **evidence quyết định thực tế** (hop_distance/sanction/path) để định hướng retrieval + prompt, tách nhiều lượt query theo chủ đề pháp lý, ép LLM đối chiếu ngưỡng số |
| **AML case management** | Đây là **AI copilot** sinh dự thảo STR + giải thích + chờ duyệt, không phải hệ thống quản lý hồ sơ thủ công |

**Điểm tích hợp đáng nói nhất:** một workflow investigation hoàn chỉnh — từ dữ liệu blockchain thô → feature vector → ML score → graph evidence → rule-based decision → legal citation → STR draft → con người quyết định.

---

## 12. Current Limitations

Chỉ nêu các giới hạn có ý nghĩa cấp project (không phải danh sách audit):

- **Technical**
  - Graph data hiện tại là demo scenarios hoặc demo seed vào Neo4j — **chưa có pipeline ingest đồ thị blockchain thực tế quy mô lớn**.
  - Data off-chain (Core Banking) cho Aggregation Monitor là **mock 1 account** — structuring không đánh giá được với dữ liệu thật (không được coi là "sạch").
  - `wallet_tx_history` chỉ có từ mock; 2 đặc trưng hành vi (`avg_time_between_tx`, `balance_clustering_flag`) thường là None trong pipeline thật.
- **Data**
  - Chỉ Ethereum; các chain khác bị bỏ (giới hạn Etherscan free).
  - Etherscan fetch 100 tx/token mặc định mỗi request — chưa cover lịch sử đầy đủ cho ví hoạt động nhiều.
  - Feature ERC20 cộng gộp số lượng mọi token (không quy đổi USD), "total ether balance" không tính internal tx, heuristic `to contract` — đã ghi rõ trong `feature_builder.py`.
- **ML**
  - Ngưỡng "medium" cho Rule 5 (REVIEW) là **giả định tạm, chưa calibrate** bằng dữ liệu.
  - Mô hình train trên Ethereum Fraud dataset (đặc thù gần cân bằng 53% licit / 47% illicit) — khác biệt phân bố so với dữ liệu thực tế khi áp dụng.
  - SHAP giải thích có sẵn nhưng mới hiển thị top 3; không có threshold giải thích cho rule-based composite.
- **Regulatory**
  - Legal docs là bản tổng hợp/văn bản được cung cấp trong repo — **cần rà soát tính đầy đủ/chính xác với văn bản gốc** trước khi dùng cho báo cáo chính thức.
  - RAG phụ thuộc LLM API; khi lỗi hoặc thiếu key thì `legal_citations=[]` (có cảnh báo rõ trong STR).
  - Hệ thống không tự xác định nghĩa vụ pháp lý cuối cùng.
- **Security**
  - Salt PII cố định trong `.env` cho MVP (có nhắc xoay salt hàng quý trong `core/audit_logger.py` nhưng chưa có cơ chế xoay).
  - Auth đơn giản in-memory (token mất khi restart), chưa có RBAC phân quyền, JWT, hay khóa phiên bền.
- **Scalability / Production readiness**
  - Graph projection dựng mới mỗi lần chạy (`gds.graph.project`) — chưa tối ưu cho luồng real-time.
  - Không có queue (Redis/Kafka), không có streaming, không chiến lược cập nhật graph liên tục.
  - Frontend HTML/JS thuần — phù hợp demo/MVP, chưa phải sản phẩm hoàn thiện.

---

## 13. Future Development

Các hướng phát triển hợp lý dựa trên kiến trúc hiện tại:

1. **Ingest đồ thị blockchain thực tế** vào Neo4j liên tục (indexer) — thay cho demo seed; xây fan-out n-hop query scale cho production.
2. **Nối Core Banking thật / API off-chain** cho `wallet_tx_history` — biến Aggregation Monitor thành rule structuring có dữ liệu thật.
3. **Calibrate ngưỡng "medium"** của Rule 5 bằng dữ liệu thật (thay vì giả định 0.6 / hop 3–4).
4. **Mở rộng multi-chain** (BSC, Base, Polygon...) khi có API key trả phí; thêm `txlistinternal` để tính balance chính xác hơn.
5. **Tăng số lượng feature** (quy đổi ERC20 theo USD, categorization token...), retrain định kỳ, giám sát drift.
6. **Cải thiện HITL**: thêm bình luận/chú thích của chuyên viên, workflow nâng REVIEW → REPORT thủ công, lưu trữ quyết định lâu dài.
7. **Auth nâng cấp**: JWT + RBAC + persistent session; lưu audit log vào database thay vì file.
8. **Real-time monitoring + alerting** trên đồ thị liên tục (không chỉ trigger theo từng giao dịch đơn lẻ).
9. **Hiển thị SHAP đầy đủ** (waterfall/force plot) trong UI.
10. **Chunking pháp lý tốt hơn** (theo điều khoản, không theo dòng trống) và đối chiếu văn bản gốc để tăng độ chính xác citation.

---

## 14. Technology Stack

| Technology | Purpose | Current Usage |
|---|---|---|
| Python 3 | Ngôn ngữ chính | Toàn bộ backend/agents/scripts |
| FastAPI + Uvicorn | API Gateway + serve frontend | `api/main.py` |
| LangGraph | Orchestration pipeline + checkpoint HITL | `core/graph_builder.py::build_pipeline`, `PipelineRun` |
| XGBoost | ML classifier (illicit probability) | `models/xgboost_aml.pkl`, `agents/transaction_classifier.py` |
| scikit-learn | Train/test split, metrics, PR curve | `agents/train_classifier.py`, `agents/calibrate_classifier_threshold.py` |
| imbalanced-learn (SMOTE) | Xử lý mất cân bằng nhãn (config mô hình chính thức) | `agents/train_classifier.py` |
| SHAP | Explainability per-transaction | `agents/transaction_classifier.py::_get_explainer` |
| pandas / numpy | Xử lý dữ liệu, feature vector | `scripts/feature_builder.py`, data scripts |
| NetworkX | Graph algorithms cho demo (PPR, Louvain, shortest path) | `agents/graph_aml.py::_analyze_via_networkx` |
| Neo4j 5.26 + GDS 2.13.2 | Graph database + GDS (production path) | `agents/graph_aml.py::_analyze_via_neo4j`, `docker-compose.yml` |
| ChromaDB | Vector DB cho legal docs RAG | `db/vector_db.py` |
| OpenAI SDK (OpenAI-compatible) | LLM cho RAG + chat (Groq/OpenRouter...) | `agents/regulation_rag.py::call_llm_api` |
| python-docx | Sinh STR .docx Mẫu 04 | `agents/alert_report.py` |
| pytest | Unit/integration tests | `tests/` |
| requests | Etherscan API fetch | `scripts/etherscan_fetcher.py` |
| python-dotenv | Cấu hình `.env` | `core/config.py`, các module |
| Docker Compose | Neo4j + ChromaDB services | `docker-compose.yml` |
| HTML/CSS/JS (thuần) | Frontend AML officer | `frontend_html/` |

---

## 15. Evidence / Claims Matrix

> Status: Implemented = code hoạt động, có test/log verify. Partial = hoạt động một phần hoặc phụ thuộc điều kiện. Prototype = có nhưng chưa hoàn thiện. Mock = dữ liệu giả lập. Planned = chỉ trong doc/spec. Not implemented = không có trong code hiện tại.

| Claim | Implementation Evidence | File/Module | Status | Safe to claim in report? |
|---|---|---|---|---|
| Pipeline orchestration LangGraph đầy đủ với HITL | `StateGraph`, interrupt_after=["alert_report"], `PipelineRun` run/resume/is_paused | `core/graph_builder.py` | Implemented | ✅ Có |
| PII băm SHA-256 + salt trước khi vào state | `mask_pii`, `assert_no_raw_pii`, `privacy_layer_node` | `core/privacy_layer.py` | Implemented | ✅ Có |
| Fuzzy name-matching chạy TRƯỚC khi băm PII | `screen_name_against_sdn`, gọi trong `privacy_layer_node` | `core/name_screening.py`, `core/privacy_layer.py` | Implemented | ✅ Có (lưu ý: Levenshtein thuần Python, O(n) trên 19k+ tên SDN — chỉ phù hợp MVP) |
| ML classifier XGBoost chạy thật | `models/xgboost_aml.pkl`, `analyze_transaction` build feature vector thật | `agents/transaction_classifier.py` | Implemented | ✅ Có |
| Feature engineering tái tạo được từ Etherscan | 37 features, schema, feature builder | `scripts/feature_builder.py`, `data/processed/feature_schema.json` | Implemented | ✅ Có |
| SHAP explainability per-transaction | `shap.TreeExplainer`, `top_features` | `agents/transaction_classifier.py` | Implemented | ✅ Có |
| Threshold calibrate bằng PR curve | `models/classifier_threshold.json` (θ=0.8748, recall 0.9014) | `agents/calibrate_classifier_threshold.py` | Implemented | ✅ Có |
| Decision Engine rule-based composite (bỏ weighted-sum) | 5 rule độc lập, `risk_assessment_score=None` | `agents/decision_engine.py` | Implemented | ✅ Có (nhấn mạnh đây là thiết kế chủ động vì thiếu dữ liệu ghép cặp) |
| Ngưỡng "medium" Rule 5 (REVIEW) | Hằng số `_CLASSIFIER_MEDIUM_RATIO=0.6`, `_GRAPH_MEDIUM_HOP_MAX=4` chưa calibrate | `agents/decision_engine.py` | Partial | ⚠️ Có thể nói "đã implement" nhưng PHẢI ghi rõ "giả định tạm, chưa kiểm chứng" |
| Graph analysis PPR + Louvain + hop | NetworkX + Neo4j/GDS query | `agents/graph_aml.py` | Implemented | ✅ Có |
| Neo4j + GDS hoạt động (verify thật) | `GDS_VERSION=2.13.2`, log chạy thật | `logs/testd_neo4j_real.txt`, `logs/testd_api_real.txt` | Implemented | ✅ Có (với điều kiện: data demo do test seed) |
| ChromaDB RAG truy xuất legal docs | `db/vector_db.py`, `run_regulation_rag` | `db/vector_db.py`, `agents/regulation_rag.py` | Implemented (cần DB/LLM hoạt động) | ✅ Có |
| RAG không bao giờ đẩy mock vào STR khi lỗi | `legal_rag_status=UNAVAILABLE`, `legal_citations=[]`, cảnh báo trong STR | `agents/regulation_rag.py`, `agents/alert_report.py`, `tests/test_legal_rag_status.py` | Implemented | ✅ Có |
| STR draft Mẫu 04 .docx | `generate_alert_report` tạo `.docx` | `agents/alert_report.py` | Implemented | ✅ Có |
| PII không lẫn giữa các request STR | Closure capture, không nằm state channels | `core/graph_builder.py`, `tests/test_report_context_isolation.py` | Implemented | ✅ Có |
| Sanctions exact-match OFAC | `verify_kyc` đọc `sample_ofac_wallet.txt` | `agents/kyc_verification.py` | Implemented | ✅ Có (chỉ exact match; fuzzy name là tín hiệu thông tin riêng) |
| Aggregation Monitor phát hiện structuring/smurfing | Rule cộng dồn 7 ngày + đếm gần ngưỡng 30 ngày | `agents/aggregation_monitor.py` | Implemented (nhưng data off-chain là MOCK) | ⚠️ Có thể nói "rule implemented", PHẢI ghi rõ dữ liệu là mock |
| Insufficient data → forced REVIEW (zero-tx wallet) | `_assess_insufficient_data`, decision engine | `agents/transaction_classifier.py`, `tests/test_insufficient_data.py` | Implemented | ✅ Có |
| Chat giải thích giao dịch | `/api/pipeline/{tx}/chat` dùng `call_llm_api` | `api/main.py`, `agents/regulation_rag.py` | Implemented (cần LLM key) | ✅ Có |
| Audit log không chứa PII | JSON lines `logs/audit_trail.log`, chỉ ghi key names | `core/audit_logger.py` | Implemented | ✅ Có |
| Frontend HTML/JS/CSS AML dashboard | render pipeline, risk breakdown, SVG path, approve/reject, chat | `frontend_html/` | Prototype | ✅ Có (prototype) |
| Mock Core Banking off-chain history | Chỉ 1 account `0123456789` | `scripts/mock_core_banking.py` | Mock | ❌ Không claim là data thật |
| Graph data production (blockchain thật ingest) | Chỉ demo scenarios + test seed | `data/mock/scenario_*.json`, `logs/testd_neo4j_real.py` | **Không có pipeline ingest thật** | ❌ KHÔNG claim "production graph data" |
| Multi-chain (BSC, Base...) | `CHAINS` chỉ có ethereum | `scripts/etherscan_fetcher.py` | Not implemented | ❌ KHÔNG claim |
| Real-time alerting / streaming (Kafka/Redis) | Không có code | — | Not implemented | ❌ KHÔNG claim |
| HSM signing / ký số STR | Không có code | — | Not implemented | ❌ KHÔNG claim |
| Thông tư 32/2026/TT-BTC (thuế tài sản số) | Đã bỏ khỏi `REQUIRED_FILES` | `db/vector_db.py` | Not implemented (đã gỡ) | ❌ KHÔNG claim (trừ khi nói rõ "đã bỏ") |
| Eigen/khuyến nghị FATF là nguồn chính thức đầy đủ | Chỉ là bản tóm tắt trong repo | `data/legal_docs/fatf_recommendations.txt` | Partial | ⚠️ Nói rõ là "tóm tắt, cần đối chiếu văn bản gốc" |

---

## 16. Report-Ready Facts

> Mỗi fact kèm evidence file trực tiếp. Đây là các điểm đã kiểm chứng trong code hiện tại, an toàn để đưa vào báo cáo/bảo vệ.

1. **Tên & mục tiêu:** DigitalAsset Guard là AML AI Copilot hỗ trợ chuyên viên AML điều tra giao dịch tài sản số đáng ngờ, từ sàng lọc ban đầu đến dự thảo báo cáo STR, với human-in-the-loop. *(Evidence: `README.md` hướng dẫn; toàn bộ `core/`, `agents/`)*

2. **Pipeline 8 bước:** Webhook → Privacy Layer → 5 Assistant (Aggregation, Transaction, Graph, Sanctions, Decision) → RAG → Report → HITL. *(Evidence: `core/graph_builder.py::build_pipeline`)*

3. **Privacy bằng thiết kế:** Mọi PII bị băm SHA-256 + salt trước khi vào state; fuzzy name-screening chạy TRƯỚC khi băm; có chốt tự động `assert_no_raw_pii`. *(Evidence: `core/privacy_layer.py`, `core/name_screening.py`)*

4. **PII không lẫn giữa requests:** PII plaintext cho STR chỉ tồn tại qua closure `PipelineRun → build_pipeline → alert_report`, không nằm trong state channels, có test cô lập. *(Evidence: `core/graph_builder.py` (FIX 2026-08-09), `tests/test_report_context_isolation.py`)*

5. **Transaction Assistant dùng feature vector THẬT (37 features)** từ Etherscan qua `feature_builder`, không dùng mock ở production; mock chỉ cho demo/test với cờ tường minh. *(Evidence: `agents/transaction_classifier.py`, `scripts/feature_builder.py`, `tests/test_feature_vector.py`)*

6. **Model:** XGBoost train trên dataset Ethereum Fraud Detection (Farrugia) đã clean (4.675 ví, 37 features, stratify 20% test); kết quả thật: Recall 0.9633, Precision 0.9813, F1 0.9722, AUC-PR 0.9955 (config `smote_only`). *(Evidence: `tests/model_comparison_v2.json`, `data/processed/dataset_summary.txt`)*

7. **Threshold θ=0.8748** cho classifier được calibrate bằng precision-recall curve trên test set, target recall 0.9 (ưu tiên không bỏ lọt rửa tiền thật). *(Evidence: `models/classifier_threshold.json`, `agents/calibrate_classifier_threshold.py`)*

8. **Explainability ML là SHAP per-transaction** (top 3 |shap value|, hiển thị cả giá trị âm) — không phải feature_importance toàn cục. *(Evidence: `agents/transaction_classifier.py`, frontend `app.js`)*

9. **Decision Engine là rule-based composite, KHÔNG còn một điểm risk tổng hợp:** 5 rule độc lập (sanctions exact → structuring → classifier ≥ θ → graph hop ≤ 2 → 2 tín hiệu medium). Lý do thiết kế: thiếu dữ liệu ghép cặp để calibrate weighted-sum; dùng rule/CALIBRATED threshold thay vì số giả. *(Evidence: `agents/decision_engine.py` docstring + code)*

10. **Graph Intelligence thực sự chạy:** Personalized PageRank (personalization vào ví blacklisted), Louvain community, shortest-path hop tới ví sanction, suspicious path, fan-out. Cả NetworkX (demo) và Neo4j GDS (production) đều query cùng semantics. *(Evidence: `agents/graph_aml.py`, `logs/testd_neo4j_real.txt`, `logs/testd_neo4j_bc.txt`)*

11. **Neo4j + GDS 2.13.2 đã được verify chạy thật** (không chỉ config): kịch bản hop=2 → decision=REPORT (Rule 4) → RAG → STR → pending. *(Evidence: `logs/testd_neo4j_real.txt`, `logs/testd_api_real.txt`)*

12. **Phân biệt 3 trạng thái graph** (NO_GRAPH_DATA / AVAILABLE_NO_SANCTION_PATH / SANCTION_PATH_FOUND) để UI không hiểu nhầm PPR=0 là "không có dữ liệu". *(Evidence: `core/state.py`, `agents/graph_aml.py`, `frontend_html/app.js`)*

13. **Aggregation Monitor phát hiện structuring/smurfing** (chia nhỏ giao dịch né ngưỡng 500tr): rule cộng dồn 7 ngày + đếm số giao dịch gần ngưỡng trong 30 ngày; phân biệt rõ "chưa đánh giá được" (not_assessed) vs "đã kiểm tra sạch" (assessed=False). *(Evidence: `agents/aggregation_monitor.py`, `tests/test_aggregation_monitor.py`) — **lưu ý: nguồn off-chain hiện là MOCK**.*

14. **Zero-tx wallet không bị tự động REPORT:** ví chưa có lịch sử on-chain → `insufficient_data=True` → bắt buộc REVIEW thủ công (fix lỗi XGBoost chấm vector 0 ~1.0). *(Evidence: `agents/transaction_classifier.py`, `tests/test_insufficient_data.py`)*

15. **RAG pháp lý với source attribution nghiêm ngặt:** ChromaDB truy xuất, LLM bị ép trả JSON `source_file` khớp nguồn truy xuất, code tự ánh xạ sang tên chính thức; prompt buộc đối chiếu ngưỡng số (400tr) và ưu tiên STR khi có evidence graph/sanction. *(Evidence: `agents/regulation_rag.py`, `db/vector_db.py`)*

16. **RAG không bao giờ giả vờ ổn:** khi LLM lỗi/thiếu key → `legal_rag_status=UNAVAILABLE`, `legal_citations=[]`, STR in cảnh báo chuyên viên tự tra cứu; KHÔNG đẩy mock vào tài liệu nộp. *(Evidence: `agents/regulation_rag.py`, `tests/test_legal_rag_status.py`)*

17. **Nguồn pháp lý hiện tại (3):** Thông tư 27/2025/TT-NHNN, Luật PCRT 2022 + QĐ 11/2023/QĐ-TTg, tóm tắt FATF. Đã chủ đích bỏ Thông tư 32/2026/TT-BTC. *(Evidence: `data/legal_docs/`, `db/vector_db.py::REQUIRED_FILES`)*

18. **STR là DỰ THẢO, con người quyết định cuối:** Report Assistant không tự quyết định STR; .docx Mẫu 04 in disclaimer "phải chuyên viên kiểm tra/phê duyệt trước khi gửi"; pipeline có HUMAN CHECKPOINT (Approved/Rejected). *(Evidence: `agents/alert_report.py`, `core/graph_builder.py::PipelineRun`)*

19. **Frontend workflow đầy đủ:** login → nhập giao dịch → pipeline → xem risk breakdown / suspicious path SVG / legal citations / chat → Approve/Reject → tải .docx. *(Evidence: `frontend_html/app.js`, `api/main.py`)*

20. **Audit trail có bằng chứng vận hành:** `logs/audit_trail.log` JSON (timestamp, agent, tx_hash, duration_ms, state_keys_present), không serialize giá trị; phát hiện PII gốc thì chặn ghi. *(Evidence: `core/audit_logger.py`)*

21. **Sanctions screening = exact match OFAC** (ví); fuzzy name là tín hiệu thông tin riêng (không phải sanctions match). *(Evidence: `agents/kyc_verification.py`, `core/privacy_layer.py`, `agents/decision_engine.py`)*

22. **Chat hỏi đáp có ràng buộc:** LLM chỉ được dùng dữ liệu trong state; cấm tự tạo số liệu/căn cứ pháp lý/thay đổi decision. *(Evidence: `api/main.py::chat_about_transaction`)*

23. **Hệ thống hiện ở mức MVP/demo, không production-ready:** graph data là demo seed, off-chain là mock, auth in-memory, chưa có streaming/queue/multi-chain. *(Evidence: toàn bộ mục 12 + Evidence Matrix)*

24. **Có test tự động bao phủ các phần lõi:** state, privacy, classifier/feature vector, decision engine (5 rule + threshold), aggregation monitor (3 trạng thái structuring), insufficient_data, graph provider (mock/neo4j not mix), legal rag status (OK/UNAVAILABLE), report PII isolation. *(Evidence: `tests/`)*

25. **Hướng phát triển chính:** ingest graph blockchain thật, nối Core Banking thật, calibrate ngưỡng medium, multi-chain, real-time alerting. *(Evidence: mục 13 Future Development)*