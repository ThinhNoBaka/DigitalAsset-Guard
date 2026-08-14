# DigitalAsset Guard

> Hỗ trợ chuyên viên AML ngân hàng / VASP / tổ chức tín dụng phát hiện và điều tra **giao dịch tài sản số (blockchain) đáng ngờ** liên quan đến rửa tiền — từ sàng lọc ban đầu đến **dự thảo báo cáo giao dịch đáng ngờ (STR)** theo khung pháp lý Việt Nam, với **human-in-the-loop**.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-1C3C3C)
![XGBoost](https://img.shields.io/badge/ML-XGBoost-orange?logo=xgboost&logoColor=white)
![Neo4j](https://img.shields.io/badge/Graph-Neo4j%20%2B%20GDS-4581C3?logo=neo4j&logoColor=white)
![Status](https://img.shields.io/badge/Status-Prototype%2FMVP-green)

---

## Giao diện web

### Đăng nhập 

<p align="center">
  <img src="pictures/Screenshot 2026-08-14 222521.png" alt="Giao diện - Dashboard" width="800"/>
</p>

### Dashboard 

<!-- 👇 CHÈN ẢNH GIAO DIỆN 1 (VÍ DỤ: TRANG CHÍNH / KẾT QUẢ PIPELINE) -->
<p align="center">
  <img src="pictures/Screenshot 2026-08-14 222659.png" alt="Giao diện - Dashboard" width="800"/>
</p>

### Chi tiết rủi ro 

<!-- 👇 CHÈN ẢNH GIAO DIỆN 2 (VÍ DỤ: RISK BREAKDOWN / GRAPH PATH SVG) -->
<p align="center">
  <img src="pictures/Screenshot 2026-08-14 222717.png" alt="Giao diện - Chi tiết rủi ro" width="800"/>
</p>

### Căn cứ pháp lý 

<!-- 👇 CHÈN ẢNH GIAO DIỆN 3 (VÍ DỤ: MÀN HÌNH APPROVE/REJECT & GIAO DIỆN STR) -->
<p align="center">
  <img src="pictures/Screenshot 2026-08-14 222727.png" alt="Giao diện - Báo cáo STR" width="800"/>
</p>

---

## Tính năng chính

- **Privacy by Design** — PII (tên, CCCD, số tài khoản) bị **băm SHA-256 + salt ngay tại request boundary**, có chốt kiểm tra tự động `assert_no_raw_pii`; fuzzy name-screening chạy **trước khi băm**.
- **5 Assistant độc lập**:
  - **Aggregation Monitor** — phát hiện **structuring/smurfing** (chia nhỏ giao dịch né ngưỡng báo cáo).
  - **Transaction Assistant** — chấm điểm rủi ro bằng **XGBoost** trên feature vector **thật (37 features)** từ Etherscan, kèm giải thích **SHAP per-transaction**.
  - **Graph Assistant** — phân tích đồ thị dòng tiền: **Personalized PageRank**, **Louvain community**, hop distance tới ví bị trừng phạt, suspicious path, fan-out (NetworkX cho demo / Neo4j GDS cho production).
  - **Sanctions Assistant** — sàng lọc chính xác địa chỉ ví với danh sách **OFAC SDN**.
  - **Decision Engine** — tổng hợp thành quyết định **PASS / REVIEW / REPORT** bằng **rule-based composite** (5 rule độc lập, không dùng một con số rủi ro tổng hợp).
- **Regulation RAG** — truy vấn ChromaDB + LLM để trích dẫn đúng điều khoản (Thông tư 27/2025/TT-NHNN, Luật PCRT 2022 + QĐ 11/2023/QĐ-TTg), **không bao giờ giả vờ ổn** khi LLM lỗi (`legal_rag_status=UNAVAILABLE`).
- **STR Draft — Mẫu 04** — sinh báo cáo giao dịch đáng ngờ `.docx` tự động khi `decision=REPORT`, có disclaimer "dự thảo, phải chuyên viên phê duyệt".
- **Human-in-the-Loop** — pipeline dừng tại checkpoint chờ chuyên viên **Approve/Reject** trước khi STR được coi là hợp lệ.
- **Chat giải thích** — LLM chỉ được dùng dữ liệu trong state, không tự tạo số liệu / căn cứ pháp lý.
- **Frontend AML dashboard** — nhập giao dịch, xem pipeline rail, risk breakdown, sơ đồ dòng tiền SVG, citations, phê duyệt, tải STR.

---

## Kiến trúc hệ thống

```
client (browser / POST API)
   │  RawTransactionRequest: tx_hash, wallet_from, wallet_to, amount_vnd,
   │                          fullname, id_number, account_number
   ▼
api/main.py (_build_initial_state)
   • fetch_wallet_record() bằng Etherscan API  → state["wallet_record"]
   • get_wallet_tx_history(account_number)      → state["wallet_tx_history"] (mock off-chain)
   • get_graph_provider() (mock / neo4j)        → state["mock_graph_edges"] / blacklisted
   ▼
PipelineRun.run()  (LangGraph)
   privacy_layer
      └─ fuzzy name-screening (TRƯỚC băm) → name_similarity_*
      └─ mask_pii → hashed_*  (loại raw PII khỏi state)
      ▼
   aggregation_monitor  → structuring_detected, aggregation_status
      ▼
   transaction_classifier → classifier_score, top_features (SHAP), insufficient_data
      ▼
   graph_aml → graph_score (PPR), community_id, hop_distance_to_blacklist,
               suspicious_path, fan_out, graph_analysis_status
      ▼
   sanctions (verify_kyc) → sanction_result
      ▼
   decision_engine → decision (PASS / REVIEW / REPORT), decision_evidence
      │
      ├─ PASS    → END (auto_cleared)
      ├─ REVIEW  → END (chuyên viên xem evidence, KHÔNG tự soạn STR)
      └─ REPORT  → regulation_rag → alert_report → HUMAN CHECKPOINT
                      (is_paused: case_status=pending_review && approval_status=pending)
                      │
                      ▼
              chuyên viên Approve/Reject
                      ▼
              PipelineRun.resume() → cập nhật approval_status → END
```

### Pipeline điều tra 8 bước

| Bước | Thành phần | Kết quả |
|---|---|---|
| 1 | Webhook/API nhận giao dịch (kèm PII) | yêu cầu thô |
| 2 | **Privacy Layer** — băm PII SHA-256 + salt, fuzzy name-screening trước khi băm | `hashed_*`, `name_similarity_*` |
| 3 | **Aggregation Monitor** — phát hiện structuring/smurfing | `structuring_detected` |
| 4 | **Transaction Assistant** — XGBoost + SHAP | `classifier_score`, `top_features` |
| 5 | **Graph Assistant** — PPR, Louvain, hop distance | `graph_score`, `suspicious_path` |
| 6 | **Sanctions Assistant** — OFAC SDN exact match | `sanction_result` |
| 7 | **Decision Engine** — rule-based composite | `PASS / REVIEW / REPORT` |
| 8 | **Legal RAG + Report + HITL** — soạn STR Mẫu 04, chờ duyệt | STR `.docx`, `approval_status` |

---

## Công nghệ sử dụng

| Công nghệ | Mục đích |
|---|---|
| Python 3.10+ | Ngôn ngữ chính |
| FastAPI + Uvicorn | API Gateway + serve frontend |
| LangGraph | Orchestration pipeline + HITL checkpoint |
| XGBoost + SHAP | ML classifier + explainability per-transaction |
| imbalanced-learn (SMOTE) | Xử lý mất cân bằng nhãn |
| NetworkX | Graph algorithms demo (PPR, Louvain, shortest path) |
| Neo4j 5.26 + GDS 2.13.2 | Graph database + GDS production path |
| ChromaDB | Vector DB cho Legal RAG |
| OpenAI SDK (compatible) | LLM cho RAG + chat (Groq / OpenRouter...) |
| python-docx | Sinh STR `.docx` Mẫu 04 |
| pytest | Kiểm thử |
| Docker Compose | Neo4j + ChromaDB services |
| HTML/CSS/JS (thuần) | Frontend AML dashboard |

---

## Bắt đầu nhanh

### 1. Yêu cầu

- Python 3.10+
- (Tùy chọn) Docker + Docker Compose cho Neo4j / ChromaDB
- API key Etherscan (`.env`), API key LLM (`.env`)

### 2. Cài đặt

```bash
# Clone repo
git clone https://github.com/ThinhNoBaka/DigitalAsset-Guard.git
cd DigitalAsset-Guard

# Tạo virtual environment
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

# Cài dependencies
pip install -r requirements.txt

# Copy .env mẫu và điền key
cp .env.example .env   # (tạo file .env, xem cấu hình bên dưới)
```

### 3. Cấu hình `.env`

| Biến | Mô tả |
|---|---|
| `ETHERSCAN_API_KEY` | API key Etherscan (fetch lịch sử giao dịch) |
| `LLM_API_KEY` | API key LLM (Groq / OpenAI-compatible) |
| `LLM_BASE_URL` | Base URL LLM (mặc định Groq) |
| `LLM_MODEL` | Model LLM (mặc định `llama-3.3-70b-versatile`) |
| `PII_SALT` | Salt cố định cho SHA-256 băm PII |
| `DEMO_MODE` | `True` — dùng NetworkX + mock; `False` — dùng Neo4j |
| `GRAPH_SOURCE` | `"mock"` hoặc `"neo4j"` |

### 4. Chạy

```bash
# Khởi động API + frontend
uvicorn api.main:app --reload --port 8000
```

Mở trình duyệt: **http://localhost:8000**

Tài khoản mặc định: `nhanvien1` / `123456789` (cấu hình qua `AML_AUTH_USERNAME` / `AML_AUTH_PASSWORD`).

### 5. Chạy test

```bash
pytest tests/ -v
```

### 6. (Tùy chọn) Neo4j + GDS

```bash
docker-compose up -d
python db/neo4j_setup.py
```

---

## Cấu trúc thư mục

```
TPers_prj/
├── agents/                # 5 Assistant + Alert Report + Training
│   ├── aggregation_monitor.py
│   ├── alert_report.py
│   ├── calibrate_classifier_threshold.py
│   ├── decision_engine.py
│   ├── graph_aml.py
│   ├── kyc_verification.py      # Sanctions Assistant
│   ├── regulation_rag.py
│   ├── train_classifier.py
│   ├── transaction_classifier.py
│   └── ...
├── api/
│   └── main.py            # FastAPI + serve frontend
├── core/
│   ├── config.py          # Cấu hình .env
│   ├── graph_builder.py   # LangGraph pipeline (PipelineRun)
│   ├── graph_provider.py  # Mock / Neo4j data source
│   ├── privacy_layer.py   # SHA-256 băm PII
│   ├── name_screening.py  # Fuzzy name-matching
│   ├── state.py           # AMLState
│   └── audit_logger.py
├── db/
│   ├── neo4j_setup.py
│   └── vector_db.py       # ChromaDB
├── data/
│   ├── legal_docs/        # Thông tư 27, Luật PCRT 2022, FATF
│   ├── mock/              # Scenario mock + customers
│   ├── processed/         # Dataset clean, feature schema
│   └── raw/               # Dữ liệu thô (git-ignored)
├── frontend_html/         # HTML/CSS/JS dashboard
├── models/                # XGBoost .pkl, threshold
├── pictures/              # Ảnh minh họa / screenshot
├── prompts/
├── reports/
│   └── output/            # STR .docx (git-ignored)
├── scripts/               # Data pipeline, feature builder...
├── tests/                 # pytest
├── docker-compose.yml
├── requirements.txt
└── ...
```

---

## Kết quả mô hình

**Dataset:** Ethereum Fraud Detection (Farrugia) — 4.675 ví, 37 features, stratify 20% test.

| Cấu hình | Precision | Recall | F1 | AUC-PR |
|---|---|---|---|---|
| scale_pos_weight_only | 0.9745 | 0.9633 | 0.9689 | 0.9950 |
| **smote_only (chọn)** | **0.9813** | **0.9633** | **0.9722** | **0.9955** |
| smote_plus_scale_pos_weight | 0.9813 | 0.9633 | 0.9722 | 0.9955 |

- **Threshold θ = 0.8748** — calibrate bằng precision-recall curve, target recall 0.9 (đạt recall 0.9014, precision 0.9874).
- Chỉ số chính: **Recall** và **AUC-PR** (phù hợp bài toán mất cân bằng nhãn).

---

## Kiểm thử

Các test tự động bao phủ phần lõi:

- `tests/test_state.py` — AMLState
- `tests/test_privacy.py` — Privacy Layer (băm, chốt PII)
- `tests/test_classifier.py`, `tests/test_feature_vector.py` — XGBoost + feature thật
- `tests/test_decision_engine.py` — 5 rule PASS/REVIEW/REPORT
- `tests/test_aggregation_monitor.py` — structuring/smurfing
- `tests/test_insufficient_data.py` — zero-tx wallet → forced REVIEW
- `tests/test_graph_provider.py` — mock/neo4j không trộn lẫn
- `tests/test_legal_rag_status.py` — RAG OK/UNAVAILABLE không giả vờ ổn
- `tests/test_report_context_isolation.py` — PII không lẫn giữa requests

---

## Trạng thái & Giới hạn

Project ở mức **Prototype/MVP chạy được end-to-end**:

- Pipeline, API, tests, model chạy thực sự; Neo4j/GDS + ChromaDB + LLM đã verify chạy thật (xem `logs/`).
- Dữ liệu graph là demo scenarios / seed test — **chưa có pipeline ingest blockchain thực tế quy mô lớn**.
- Dữ liệu off-chain (Core Banking) cho Aggregation Monitor là **mock 1 account**.
- Ngưỡng "medium" Rule 5 (REVIEW) là giả định tạm, chưa calibrate bằng dữ liệu thật.
- Chỉ hỗ trợ Ethereum (giới hạn Etherscan free).

---

## Định hướng phát triển

1. Ingest đồ thị blockchain thực tế vào Neo4j liên tục (indexer).
2. Nối Core Banking thật cho `wallet_tx_history`.
3. Calibrate ngưỡng "medium" Rule 5 bằng dữ liệu thật.
4. Mở rộng multi-chain (BSC, Base, Polygon...).
5. Hiển thị SHAP đầy đủ (waterfall/force plot) trong UI.
6. Auth nâng cấp: JWT + RBAC + persistent session.
7. Real-time monitoring + alerting trên đồ thị.

---

## Giấy phép

Dự án phục vụ mục đích học tập / nghiên cứu / demo. Vui lòng không sử dụng cho mục đích sản xuất khi chưa rà soát với văn bản pháp luật gốc và chuyên gia pháp lý.