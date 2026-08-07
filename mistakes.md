Ghi vào báo cáo (phần "Hạn chế") — những gì code không tự giải quyết được

Về wallet clustering: không cần viết thành mục hạn chế riêng — chỉ cần 1 dòng ghi chú trong báo cáo (và trong code) rằng: "Wallet clustering không triển khai ở KYC Assistant mà gộp vào Graph Assistant (Louvain Community Detection, Phần 6), vì bản chất clustering ví dựa trên đồ thị giao dịch, tránh trùng lặp logic." Đây coi như đã giải quyết, không phải hạn chế thật.

Về độ phủ OFAC theo ví — đây là hạn chế THẬT, cần viết rõ vào báo cáo:

Hệ thống hiện tại chỉ gắn cờ cảnh báo qua địa chỉ ví nếu OFAC SDN có đính kèm sẵn địa chỉ ví crypto cho entity đó, và/hoặc nếu ví liên quan có liên kết đồ thị (1-2 hop) tới một ví đã biết trong SDN (qua Graph Assistant/PPR). Tuy nhiên, phần lớn entry trong sdn.xml chỉ có tên/thông tin định danh, không có địa chỉ ví — với các entity này, nếu không có bất kỳ liên kết giao dịch nào tới ví đã biết, Graph Assistant cũng không có "điểm neo" (seed) để lan truyền rủi ro, nên hệ thống sẽ không phát hiện được qua kênh ví. Việc so khớp tên (fuzzy match) đã được xử lý riêng ở tầng webhook trước khi băm PII nên phần tên vẫn được kiểm tra đầy đủ — hạn chế chỉ nằm ở kênh địa chỉ ví. Đây là đánh đổi có chủ đích để bảo vệ PII (không so khớp tên trên dữ liệu đã băm ở tầng agent), cần nêu rõ khi bảo vệ.

(Nếu muốn có con số % cụ thể entry nào có ví trong sdn.xml, mình có thể viết script đếm khi bạn upload file.)










# 📊 Tóm tắt Project: **DigitalAsset Guard (TPers_prj)**

## 1. Tổng quan

Đây là một **Hệ thống AI Copilot chống rửa tiền (AML - Anti-Money Laundering) cho tài sản số (crypto)** tại Việt Nam, tuân thủ **Thông tư 27/2025/TT-NHNN** (báo cáo giao dịch đáng ngờ - STR) và **Thông tư 32/2026/TT-BTC** (thuế tài sản số 0.1%). Hệ thống phân tích giao dịch tiền mã hóa theo 3 chiều: **AI phân loại (XGBoost)**, **sàng lọc danh sách trừng phạt (OFAC/KYC)** và **phân tích đồ thị dòng tiền (Graph/PPR/Louvain)**, sau đó tổng hợp thành báo cáo STR có chữ ký phê duyệt con người (Human-in-the-loop).

---

## 2. Kiến trúc tổng thể

```
Dữ liệu thô (Elliptic, OFAC SDN, Etherscan)
    │
    ▼
[Privacy Layer] — Băm SHA-256 PII (fullname, ID, số tài khoản)
    │
    ▼
┌───────────── LangGraph Orchestrator (8 bước) ─────────────┐
│  1. Webhook (kiểm tra ngưỡng 500 triệu VND)               │
│  2. Privacy Layer (PII → hash)                            │
│  3. Transaction Assistant (XGBoost risk score + SHAP)     │
│  4. KYC Assistant (so khớp OFAC SDN - 940 ví đen)         │
│  5. Graph Assistant (PPR + Louvain - Neo4j GDS)           │
│  6. RAG Assistant (tra cứu pháp lý - ChromaDB + LLM)      │
│  7. Report Assistant (Final = 0.2×Clf + 0.3×KYC + 0.5×Graph)│
│  8. Human Checkpoint (interrupt chờ chuyên viên duyệt)    │
└────────────────────────────────────────────────────────────┘
    │
    ▼
[STR Report .docx (Mẫu số 04)] → [FastAPI + HTML/JS (frontend_html/)]
```

---

## 3. Chi tiết các module

### 📁 `core/` — Nền tảng
| File | Chức năng |
|------|-----------|
| `config.py` | Cấu hình: ngưỡng báo cáo 500tr VND, DEMO_MODE |
| `state.py` | `AMLState` (TypedDict) — state máy trạng thái, chỉ chứa PII đã băm |
| `privacy_layer.py` | Băm SHA-256 + salt, `assert_no_raw_pii()` chặn PII gốc xâm nhập |
| `audit_logger.py` | Audit trail JSON-lines (`logs/audit_trail.log`) cho kiểm toán nội bộ |
| `graph_builder.py` | **Trái tim** — LangGraph StateGraph + `PipelineRun` facade, dùng chung cho CLI/API |

### 📁 `agents/` — 5 "Assistant" + huấn luyện
| Agent | Công nghệ | Đầu ra |
|-------|-----------|--------|
| `transaction_classifier` | XGBoost (Elliptic dataset) + **SHAP** per-transaction | `risk_score_classifier`, `top_features` |
| `kyc_verification` | So khớp chính xác 940 ví OFAC từ SDN | `kyc_flags` |
| `graph_aml` | NetworkX (demo) / **Neo4j GDS** (prod): Personalized PageRank + Louvain | `graph_risk_score`, `community_id`, `suspicious_path` |
| `regulation_rag` | ChromaDB (Thông tư 27/32+ FATF) + LLM (Groq/OpenRouter) | `legal_citations` (JSON có cấu trúc) |
| `alert_report` | python-docx, tạo STR Mẫu 04, `risk_breakdown` | `final_risk_score`, `report_path` |
| `train_classifier` | XGBoost, temporal split, so sánh **3 config** (scale_pos_weight / SMOTE / cả hai) | `models/xgboost_aml.pkl` + `model_comparison_v2.json` |

### 📁 `api/` & `ui/` — Giao diện
- **`api/main.py`** (FastAPI): `/screen-wallet` (tra cứu ví, không cần auth), `/api/pipeline/*` (full pipeline, yêu cầu Bearer token), `/api/auth/login`, `/logs` (audit trail), phục vụ luôn `frontend_html/`
- **`frontend_html/`**: FE thuần HTML/JS/CSS, không cần React/Vite/npm — do `api/main.py` (FastAPI) tự phục vụ tại `http://localhost:8000`

### 📁 `db/`, `scripts/`, `data/`
- `vector_db.py`: ChromaDB local chứa văn bản pháp luật
- `neo4j_setup.py`: Tạo constraint/index + seed dữ liệu test
- `data/`: Elliptic (train/test tách theo time_step), OFAC SDN, Etherscan samples
- `scripts/`: Healthcheck, check OFAC, fetch Etherscan, load Elliptic, gen mock

---

## 4. Luồng nghiệp vụ (SPEC §2 — 8 bước)

1. **Webhook**: Giao dịch < 500tr VND → bỏ qua (hợp lệ theo quy định)
2. **Privacy Layer**: Băm PII, không bao giờ để PII gốc vào state
3. **Transaction Assistant**: XGBoost chấm điểm rủi ro sơ bộ (scale_pos_weight xử lý mất cân bằng 2% illicit)
4. **KYC Assistant**: Kiểm tra ví gửi/nhận có trong danh sách OFAC 940 ví không
5. **Graph Assistant**: PPR lan truyền rủi ro từ ví đen qua nhiều hop + Louvain phát hiện fraud ring
6. **RAG Assistant**: Truy vấn ChromaDB → LLM → trích dẫn đúng điều khoản pháp lý
7. **Report Assistant**: Final Score = `0.2×CLF + 0.3×KYC + 0.5×Graph`; ≥ 0.7 → tự động soạn STR và set `pending`
8. **Human Checkpoint**: LangGraph `interrupt_after` — chuyên viên Approve/Reject trước khi gửi STR

---

## 5. Điểm mạnh ✅

1. **Privacy by design**: PII băm SHA-256 + salt bắt buộc, `assert_no_raw_pii()` chốt ở đầu mỗi agent, audit log lọc sạch PII, salt bắt buộc từ .env (không fallback yếu)
2. **Kiến trúc sạch**: `PipelineRun` là **nguồn orchestration duy nhất** — CLI, API đều gọi chung, tránh lệch logic
3. **Giải trình được (Explainable AI)**: SHAP per-transaction, `risk_breakdown` % đóng góp, `suspicious_path`, document hóa rõ nguồn gốc heuristic (0.2/0.3/0.5 là lựa chọn, không phải số kiểm chứng)
4. **Đúng nghiệp vụ pháp lý VN**: Mẫu STR 04, ngưỡng 500tr theo TT27, thuế TT32, interrupt HITL đúng chỗ (chỉ khi cần nộp STR, không duyệt tay mọi giao dịch)
5. **Audit trail vận hành**: Mỗi agent được wrap `timed_step()` — ghi 2 dòng JSON/bước, bằng chứng kiểm toán nội bộ
6. **Xử lý mất cân bằng nhãn nghiêm túc**: So sánh 3 cấu hình, chọn theo AUC-PR + Rec tải, **không che số liệu xấu**
7. **Temporal split đúng đắn**: train time_step 1-34, test 35-49, không random split, SMOTE chỉ trên train

---

## 6. Điểm yếu & rủi ro cần lưu ý ⚠️

| Vấn đề | Mức độ | Chi tiết |
|--------|--------|----------|
| **Feature vector mock** | 🔴 Cao | `analyze_transaction` dùng zero-vector 166 chiều thay vì dữ liệu giao dịch thật — model chỉ nhận `amount_vnd/1e6` vào slot 0. Risk score chưa phản ánh giao dịch thật |
| **Weighted sum heuristic** | 🟡 Trung bình | `0.2/0.3/0.5` không được kiểm chứng định lượng; tín hiệu KYC mạnh có thể bị pha loãng (đã có safety-net `kyc_exact_match` nhưng mặc định tắt) |
| **Test đã được sửa cho khớp API** | Đã xử lý | `tests/test_classifier.py` import cũ `create_initial_state`/`classify_transaction` đã đổi thành `AMLState`/`analyze_transaction` + `analyze_aggregation`. Hạn chế "feature vector mock" vẫn còn — xem dòng trên |
| **Auth & state trong RAM** | 🟡 Trung bình | `RUNS`, `TOKENS` lưu trong dict bộ nhớ — mất khi restart, không multi-worker; đã ghi chú rõ là demo-scale |
| **Chưa có luồng crawl Etherscan thật** | 🟡 Trung bình | 2 đặc trưng hành vi (smurfing/mixing) đọc `wallet_tx_history` nhưng không có module crawl thật cung cấp |
| **LLM phụ thuộc mock fallback** | 🟢 Thấp | Không có `LLM_API_KEY` → dùng output mock; an toàn nhưng giảm sức thuyết phục báo cáo |
| **Secure Vault giả lập** | 🟢 Thấp | `MOCK_SECURE_VAULT` hardcode lookup bảng — cần thay bằng DB nội bộ thật trong production |
| **Demo graph cố định** | 🟢 Thấp | NetworkX demo dùng đồ thị mẫu cứng `0xblacklisted_seed_wallet` |

---

## 7. Công nghệ

**Python 3** + **XGBoost**, **scikit-learn**, **SHAP**, **NetworkX**, **Neo4j + GDS**, **ChromaDB**, **LangGraph** (MemorySaver, interrupt), **FastAPI** (+ `frontend_html/`), **python-docx**, **OpenAI-compatible LLM API** (Groq/OpenRouter).

---

## 8. Kết luận

Đây là một **MVP chặt chẽ và có chiều sâu nghiệp vụ** — không chỉ là demo kỹ thuật mà đã bám sát quy định pháp lý Việt Nam (TT27/32, FATF, OFAC SDN), có privacy layer nghiêm túc, explainability, audit trail và HITL đúng chỗ. Kiến trúc tách biệt core/agent/UI sạch sẽ, dễ mở rộng. **Điểm chính cần hoàn thiện trước production**: thay feature-mock bằng đặc trưng thực từ dữ liệu Etherscan thật, cập nhật test cũ, đưa trọng số risk score thành tham số cấu hình có kiểm chứng, và chuyển auth/state sang cơ chế bền vững (JWT/Redis hoặc DB).