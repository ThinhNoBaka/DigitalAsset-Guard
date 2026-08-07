# 📋 PROJECT_SUMMARY.md — DigitalAsset Guard AI Copilot

> File này là **nguồn tóm tắt duy nhất** để bất kỳ ai (người hoặc AI khác) đọc vào là nắm được toàn bộ project trong 5 phút, không cần đọc lại toàn bộ code. Khi kiến trúc/logic thay đổi đáng kể, hãy cập nhật lại file này.

---

## 1. Project là gì

**DigitalAsset Guard** là một **AI Copilot chống rửa tiền (AML)** dành cho giao dịch tài sản số (crypto) tại Việt Nam, tuân thủ:
- **Thông tư 27/2025/TT-NHNN** — ngưỡng báo cáo giao dịch đáng ngờ (STR), 500 triệu VND, human-in-the-loop bắt buộc.
- **Thông tư 32/2026/TT-BTC** — thuế TNCN 0.1% với tài sản số.
- Tham chiếu khuyến nghị **FATF**, danh sách trừng phạt **OFAC SDN**.

Hệ thống nhận 1 giao dịch (ví nguồn, ví đích, số tiền, thông tin định danh khách hàng) → chấm điểm rủi ro theo 3 hướng độc lập (ML phân loại, sàng lọc danh sách đen, phân tích đồ thị dòng tiền) → tổng hợp điểm rủi ro có trọng số → nếu vượt ngưỡng thì tự soạn dự thảo báo cáo STR (.docx) → dừng lại chờ chuyên viên con người duyệt (Approve/Reject) trước khi coi là hoàn tất.

Đây là **MVP/đồ án tốt nghiệp/thi cử** (không phải production thật) — có nhiều mock data, nhiều hạn chế được ghi chú rõ ràng, chủ đích ưu tiên: **đúng nghiệp vụ pháp lý VN + privacy-by-design + explainability** hơn là độ chính xác ML tuyệt đối.

---

## 2. Luồng nghiệp vụ 8 bước (trái tim của toàn hệ thống)

```
1. Webhook           → giao dịch < 500tr VND thì BỎ QUA, không kích hoạt AML
2. Privacy Layer     → băm SHA-256+salt fullname/id_number/account_number
                        (fuzzy name-matching với OFAC SDN chạy Ở ĐÂY, TRƯỚC khi băm)
3. Transaction Assistant → XGBoost risk_score_classifier + SHAP top_features
4. KYC Assistant         → so khớp CHÍNH XÁC địa chỉ ví với OFAC SDN (940 ví) → kyc_flags
5. Graph Assistant       → Personalized PageRank + Louvain (NetworkX demo / Neo4j GDS prod)
                            → graph_risk_score, community_id, hop_distance, suspicious_path
6. RAG Assistant         → truy vấn ChromaDB (TT27 + TT32 + FATF) + LLM → legal_citations
7. Report Assistant      → Final = 0.2×Classifier + 0.3×KYC + 0.5×Graph (chuẩn hóa 0-1)
                            ≥ 0.7 → soạn STR Mẫu 04 (.docx), approval_status="pending"
                            < 0.7 → approval_status="approved" tự động, không cần duyệt
8. Human Checkpoint (HITL) → LangGraph interrupt_after, chỉ dừng khi "pending"
                              chuyên viên bấm Approve/Reject
```

Toàn bộ luồng này được orchestrate **DUY NHẤT** bởi `core/graph_builder.py::PipelineRun` — `demo_run.py`, `api/main.py` (FastAPI) đều gọi vào class này, **không** viết lại logic điều phối ở nơi khác.

---

## 3. Cấu trúc thư mục & vai trò từng file

```
core/
  config.py          Hằng số: REPORT_THRESHOLD_VND=500tr, DEMO_MODE flag
  state.py           AMLState (TypedDict) — "hộp dữ liệu" xuyên suốt pipeline.
                      QUY TẮC SẮT: không bao giờ chứa PII gốc, chỉ chứa hashed_*
  privacy_layer.py   mask_pii() (SHA-256+salt) + assert_no_raw_pii() (chốt chặn PII,
                      gọi ở ĐẦU MỌI agent)
  name_screening.py  Fuzzy match tên (Levenshtein) với SDN — chạy TRƯỚC khi băm PII
                      (bổ sung để lấp lỗ hổng wiring so với thiết kế gốc)
  audit_logger.py    Ghi logs/audit_trail.log (JSON lines) mỗi bước, lọc sạch PII,
                      timed_step() wrap agent đo thời gian + ghi log đầu/cuối
  graph_builder.py   TRÁI TIM: build_langgraph() + class PipelineRun (facade duy nhất)

agents/
  transaction_classifier.py  XGBoost + SHAP TreeExplainer (per-transaction)
                              [LƯU Ý: dùng feature vector MOCK (zero-vector 166 chiều,
                              chỉ gán amount_vnd vào slot 0) — chưa có crawl Etherscan thật]
  kyc_verification.py        Chỉ so khớp CHÍNH XÁC địa chỉ ví với OFAC (không so tên,
                              vì tên đã bị băm 1 chiều — xem name_screening.py)
  graph_aml.py                PPR (Personalized PageRank) + Louvain, NetworkX (demo)
                              hoặc Neo4j GDS (production), DEMO_MODE switch
  regulation_rag.py           ChromaDB query (2 lượt tách riêng AML/thuế) + LLM
                              (Groq/OpenRouter/OpenAI-compatible) → JSON legal_citations
  alert_report.py             Weighted risk score + python-docx sinh STR Mẫu 04 +
                              MOCK_SECURE_VAULT (tra ngược hash→tên thật, chỉ ở đây)
  train_classifier.py         Huấn luyện XGBoost, temporal split Elliptic, so sánh
                              3 cấu hình (scale_pos_weight / SMOTE / cả hai)

api/main.py          FastAPI: /screen-wallet (không cần auth), /api/pipeline/*
                      (cần Bearer token), /api/auth/login, /logs, phục vụ frontend_html/
frontend_html/        FE thuần HTML/JS/CSS (không React/npm) — do api/main.py
                      tự phục vụ tại http://localhost:8000 (GET / + /static)

db/
  vector_db.py        ChromaDB local, embed data/legal_docs/ (TT27 + TT32)
  neo4j_setup.py       Tạo constraint/schema Neo4j (node Wallet, rel TRANSFER)

data/
  raw/elliptic/        elliptic_clean.csv (train, time_step 1-34), elliptic_test.csv
                        (test, time_step 35-49) — ĐÃ tách sẵn theo thời gian
  raw/ofac/sdn.xml      Danh sách trừng phạt gốc (19.169 entry, XML)
  processed/            sample_ofac_wallet.txt (940 ví đã trích từ SDN), sdn_names.txt
  legal_docs/           thong_tu_27_2025.txt, thong_tu_32_2026.txt, fatf_recommendations.txt
  mock/                 customers.json + scenario_smurfing/layering/name_similarity.json
                        (kịch bản demo nâng cao, sinh bởi scripts/generate_complex_mock.py)

scripts/
  00_healthcheck.py, 01_check_ofac.py, 02_fetch_etherscan_sample.py, 03_load_elliptic.py
  05_gen_mock_data.py, generate_complex_mock.py, demo_runner.py (chạy 3 kịch bản
  mock qua toàn bộ pipeline, BYPASS cổng ngưỡng webhook có chủ đích)

tests/                 evaluate_model.py, test_classifier.py, test_decision_engine.py,
                       test_privacy.py, test_state.py, model_comparison_v2.json (số liệu thật)

models/xgboost_aml.pkl   Model đã train
reports/output/*.docx    STR sinh ra
demo_run.py              CLI mỏng, chỉ gọi PipelineRun (không viết logic riêng)
docker-compose.yml       Neo4j (5.26-community, GDS qua mount jar thủ công) + ChromaDB
```

---

## 4. AMLState — schema dữ liệu xuyên suốt (`core/state.py`)

Trường cốt lõi (theo SPEC): `tx_hash, wallet_from, wallet_to, amount_vnd, hashed_fullname, hashed_id_number, hashed_account_number, risk_score_classifier, kyc_flags, graph_risk_score, legal_citations, final_risk_score, report_path, approval_status`

Trường mở rộng (V2 / Explainability / Graph viz):
`top_features` (SHAP per-tx), `avg_time_between_tx`, `balance_clustering_flag` (đặc trưng hành vi smurfing/mixing), `name_similarity_warning/score`, `hop_distance_to_blacklist`, `fan_out`, `suspicious_path`, `community_id`, `risk_breakdown` (% đóng góp mỗi thành phần), `legal_sources_retrieved`, `thought`, `kyc_exact_match`.

**Quy tắc sắt:** không bao giờ thêm field PII gốc (`fullname`, `id_number`, `account_number` không tiền tố `hashed_`) vào state này.

---

## 5. Privacy & bảo mật (điểm mạnh nhất của project)

- `mask_pii()`: SHA-256(value.upper() + PII_SALT), salt đọc bắt buộc từ `.env`, **không fallback** giá trị mặc định (raise RuntimeError nếu thiếu).
- `assert_no_raw_pii()`: quét state, raise `ValueError` nếu thấy key PII gốc — gọi ở **đầu mọi agent** (transaction_classifier, kyc_verification, graph_aml, regulation_rag, alert_report) và trong audit_logger trước khi ghi log.
- Privacy Layer chạy **ngoài** LangGraph (Python thuần, trước `app.stream()`) — vì cơ chế merge state mặc định của LangGraph không "xoá" field, chỉ "giữ nguyên giá trị cũ", nên nếu để trong graph thì PII gốc có thể sống sót qua các node sau.
- Fuzzy name-matching (Levenshtein) với SDN chạy **trước khi băm** (ở `core/name_screening.py`, gọi trong `privacy_layer_node()`), vì so khớp mờ trên hash SHA-256 là vô nghĩa.
- Nơi DUY NHẤT được "giải mã ngược" PII: `agents/alert_report.py::secure_lookup()` — tra bảng lookup `MOCK_SECURE_VAULT` (hash→tên gốc), không phải giải mã hash, chỉ dùng khi in ra file .docx cuối cùng cho chuyên viên xem.
- Audit log (`logs/audit_trail.log`) chỉ ghi **tên field** (`state_keys_present`), không bao giờ ghi giá trị — kể cả field không nhạy cảm.

---

## 6. Điểm yếu / hạn chế đã biết (đọc trước khi sửa hoặc thi/bảo vệ)

| # | Vấn đề | Mức độ | Ghi chú |
|---|--------|--------|---------|
| 1 | **Feature vector mock** trong `transaction_classifier.py` | 🔴 Cao | Dùng zero-vector 166 chiều, chỉ gán `amount_vnd/1e6` vào slot 0 — risk score chưa phản ánh giao dịch thật. Cần crawl Etherscan thật để trích đặc trưng đầy đủ. |
| 2 | Trọng số `0.2/0.3/0.5` (weighted sum) | 🟡 TB | Là heuristic tự chọn, **không** kiểm chứng định lượng — đã ghi rõ trong docstring `alert_report.py`. Tín hiệu KYC exact-match có thể bị pha loãng (có safety-net `kyc_exact_match` nhưng mặc định tắt). |
| 3 | `tests/test_classifier.py` đã được sửa cho khớp API hiện tại | Đã xử lý | Import cũ `create_initial_state`/`classify_transaction` đã đổi thành `AMLState`/`analyze_transaction` + `analyze_aggregation`. Hạn chế #1 (feature vector mock) vẫn còn — model không phân biệt 50tr/600tr. |
| 4 | Auth & state trong RAM (`api/main.py`: `RUNS`, `TOKENS`) | 🟡 TB | Mất khi restart server, không multi-worker — chỉ hợp demo/nội bộ. |
| 5 | Chưa có crawl Etherscan thật cho `wallet_tx_history` | 🟡 TB | 2 đặc trưng hành vi (`avg_time_between_tx`, `balance_clustering_flag`) cần key này nhưng chưa có module crawl thật cấp dữ liệu. |
| 6 | Độ phủ OFAC theo ví | 🟢 Thấp (đã ghi rõ, chủ đích) | Chỉ 4.9% entry SDN (940/19169) có địa chỉ ví crypto — entity không có ví chỉ phát hiện được qua fuzzy tên (trước khi băm) hoặc liên kết đồ thị, không phải qua kênh ví trực tiếp. |
| 7 | LLM fallback mock khi thiếu `LLM_API_KEY` | 🟢 Thấp | An toàn (không crash) nhưng giảm sức thuyết phục demo. |
| 8 | `MOCK_SECURE_VAULT` hardcode | 🟢 Thấp | Cần thay bằng DB nội bộ thật cho production. |
| 9 | Đồ thị demo NetworkX mặc định cố định | 🟢 Thấp | `0xblacklisted_seed_wallet`, 6 cạnh cứng — trừ khi dùng kịch bản mock nâng cao (`scripts/generate_complex_mock.py`) thì đồ thị được dựng động. |

---

## 7. Công nghệ sử dụng

Python 3 · **XGBoost** + scikit-learn + **SHAP** (explainability) · **NetworkX** (demo graph) / **Neo4j + GDS** (production graph: PPR + Louvain) · **ChromaDB** (vector DB luật) · **LangGraph** (StateGraph, MemorySaver, interrupt_after) · **FastAPI** (API Gateway + auth token, tự phục vụ `frontend_html/`) · **python-docx** (sinh STR) · OpenAI-compatible LLM API (Groq/OpenRouter) · Docker Compose (Neo4j + ChromaDB).

---

## 8. Cách chạy nhanh

```bash
# CLI demo full pipeline (1 giao dịch mẫu, ví nguồn nằm trong blacklist demo)
python demo_run.py

# Chạy 3 kịch bản mock nâng cao (smurfing/layering/name_similarity) qua toàn pipeline
python -m scripts.demo_runner

# FastAPI (mở http://localhost:8000, tự phục vụ frontend_html/)
uvicorn api.main:app --reload --port 8000

# Huấn luyện lại model (cần data/raw/elliptic/*.csv)
python -m agents.train_classifier
python -m tests.evaluate_model

# Neo4j + ChromaDB qua Docker (cần GDS jar tải thủ công vào ./neo4j/plugins/)
docker compose up -d
```

Biến môi trường bắt buộc trong `.env`: `PII_SALT` (không có sẽ raise RuntimeError khi chạy pipeline), tuỳ chọn: `DEMO_MODE`, `NEO4J_URI/USER/PASSWORD`, `LLM_BASE_URL/MODEL/API_KEY`, `AML_AUTH_USERNAME/PASSWORD`, `ETHERSCAN_API_KEY`.

---

## 9. Số liệu mô hình thật (Elliptic, temporal split, `tests/model_comparison_v2.json`)

| Cấu hình | Precision | Recall | F1 | AUC-PR |
|---|---|---|---|---|
| scale_pos_weight only (baseline) | 0.8534 | 0.7313 | 0.7877 | 0.7993 |
| **smote_only (đã chọn)** | **0.9002** | **0.7248** | **0.8031** | **0.8112** |
| smote + scale_pos_weight | 0.9002 | 0.7248 | 0.8031 | 0.8112 |

Train: time_step 1-34, Test: time_step 35-49 (temporal split, không random). AUC-PR ưu tiên hơn AUC-ROC/Accuracy vì dữ liệu mất cân bằng (~2% illicit).

---

## 10. Nguyên tắc làm việc với AI khi sửa project này

1. **Không sửa nhiều phần cùng lúc.** Mỗi lần chỉ làm 1 module/1 phần theo đúng SPEC/HUONG_DAN_XAY_DUNG, không "tiện thể" gộp việc.
2. **Không thêm field PII gốc vào `AMLState`** — mọi field mới phải đối chiếu lại với quy tắc ở mục 4.
3. **`assert_no_raw_pii(state)` phải là dòng đầu tiên** trong bất kỳ agent mới nào xử lý `AMLState`.
4. **`core/graph_builder.py::PipelineRun` là nguồn orchestration DUY NHẤT** — không viết lại luồng điều phối ở `demo_run.py`, `api/main.py`.
5. Khi sửa 1 agent, chạy thử độc lập bằng `python -m agents.<tên_agent>` (mỗi file đều có block `if __name__ == "__main__":` tự test) trước khi ghép vào graph.
6. Đọc `mistakes.md` để biết các hạn chế đã được ghi nhận có chủ đích (đừng "sửa" lại thành hành vi khác mà không hiểu lý do).
