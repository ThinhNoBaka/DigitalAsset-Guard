# Workflow THẬT của AML Copilot — rút ra từ chạy thử (2026-08-07)

> Tài liệu này mô tả workflow dựa trên **QUAN SÁT CHẠY THẬT** (console/API/log),
> không phải từ SPEC/README.
> Những node chưa verify được đánh dấu rõ `(chưa verify)`.

## 1. Sơ đồ A — Mock/Demo mode (chạy qua terminal, DEMO_MODE=true)

Trạng thái đã verify bằng lệnh:
`$env:DEMO_MODE="true"; python -m scripts.demo_runner`

```
flowchart TD
    A["privacy_layer_node<br/>(core/privacy_layer.py)<br/>VERIFIED — chạy thật"] -->|state đã băm PII + name screening| B
    B["aggregation_monitor<br/>(KHÔNG có trong demo_runner — BỎ QUA)<br/>graph_builder có, demo_runner không gọi"] -.-> C
    C["transaction_classifier<br/>thay 'aggregation_monitor'"]

    C --> D["graph_aml (NetworkX mock)<br/>VERIFIED — đọc mock_graph_edges<br/>+ mock_blacklisted_wallets<br/>=> CHẠY THẬT với data GIẢ"]
    D --> E["sanctions (verify_kyc)<br/>VERIFIED — chỉ exact-match<br/>với sample_ofac_wallet.txt"]
    E --> F["decision_engine<br/>VERIFIED — cả 3 kịch bản<br/>đều PASS/auto_cleared"]
    F -->|decision=PASS| G["END<br/>VERIFIED — case_status=auto_cleared"]
    F -.->|decision=REPORT| H["regulation_rag + alert_report<br/>(chưa verify — không có scenario REPORT)"]
    H -.-> I["END"]

    style H fill:#ffe0e0,stroke:#f00
    style B fill:#ffe0e0,stroke:#f00,stroke-dasharray: 5 5
```

**Node đã verify bằng chạy thật (3 scenario mock, DEMO_MODE=true):**

| Node | Trạng thái | Ghi chú quan sát thật |
|---|---|---|
| privacy_layer_node | ✅ Chạy | Băm PII + fuzzy name screening (name_similarity ra True/90.0 cho scenario name_similarity) |
| transaction_classifier | ✅ Chạy | Cả 3 scenario cho **cùng đúng** classifier_score=0.0002841002424247563 (do vector feature = zero, chỉ gán amount_vnd/1e6 vào 1 slot — model không phân biệt được 3 giao dịch) |
| graph_aml (NetworkX) | ✅ Chạy | smurfing graph_score=0.0491, layering graph_score=0.1257/hop=4/community=1, name_similarity graph_score=0.0 |
| sanctions | ✅ Chạy | Không scenario nào có ví trùng OFAC → is_match=False |
| decision_engine | ✅ Chạy | Cả 3 scenario → **decision=PASS, case_status=auto_cleared** |

**Điểm quan trọng phát hiện khi chạy thật:**

1. **demo_runner.py KHÔNG gọi aggregation_monitor** dù `graph_builder.PipelineRun` có node này.
   → `structuring_flag` (Rule 2 — REPORT) sẽ luôn là `False` trong demo_runner.
   → Scenario smurfing vốn để test structuring nhưng KHÔNG hề kích hoạt Rule 2.
2. **Cả 3 scenario đều PASS** — mock data hiện có **KHÔNG test được nhánh REVIEW/REPORT**.
   - Kỳ vọng trong file scenario chỉ test `graph_assistant`/`name_similarity`, không test decision.
   - classifier_score real = 0.00028 < θ=0.0009 → Rule 3 không kích hoạt.
   - hop_distance layering = 4 > 2 → Rule 4 không kích hoạt.
3. **`.env` có `DEMO_MODE=False`** — mặc định khi chạy `python -m scripts.demo_runner`
   (không set biến) thì graph_aml sẽ đi vào nhánh Neo4j thật và **THẤT BẠI** với lỗi
   GDS (`Unable to inject component to field facade ... SpdBuiltInProcedures`) khi
   có instance Neo4j chạy nhưng không tương thích GDS.

---

## 2. Sơ đồ B — Full deploy mode (UI/API + Neo4j/DB thật)

Trạng thái đã verify:
- API FastAPI `api/main.py` khởi động OK (`/health` = ok).
- `GET /` serve UI (frontend_html) OK.
- Login + `/screen-wallet` OK.
- `/api/pipeline/run` → **500** (lỗi thiết kế: `assert_no_raw_pii(state)` gọi trên state còn raw PII trước khi qua privacy layer).
- Neo4j/ChromaDB: docker-compose có service, chưa bật trong lần chạy này → `(chưa verify)`.

```
flowchart TD
    U["Browser — frontend_html/index.html<br/>VERIFIED — serve qua GET /"] -->|fetch /api/auth/login| A1
    subgraph API["FastAPI (api/main.py)"]
        A1["POST /api/auth/login<br/>VERIFIED — trả token"]
        A2["POST /screen-wallet<br/>VERIFIED — trả risk_level<br/>(KHÔNG đi qua LangGraph)"]
        A3["POST /api/pipeline/run<br/>LỖI 500 — assert_no_raw_pii<br/>trên state còn raw PII"]
        A3 --> P["PipelineRun (LangGraph)"]
    end

    P --> PL["privacy_layer_node"]
    PL --> AG["aggregation_monitor<br/>(CÓ trong graph_builder)"]
    AG --> TC["transaction_classifier"]
    TC --> GA["graph_aml"]
    GA -->|DEMO_MODE=false| NEO["Neo4j GDS thật<br/>(chưa verify — chưa có instance<br/>tương thích GDS để test)"]
    GA -->|DEMO_MODE=true| NX["NetworkX mock<br/>(đã verify ở Sơ đồ A)"]
    GA --> SD["sanctions (exact-match OFAC)"]
    SD --> DE["decision_engine"]

    DE -->|PASS| END1["END — auto_cleared (VERIFIED)"]
    DE -->|REVIEW| END2["END — dừng tại decision,<br/>chuyên viên xem evidence<br/>(chưa verify end-to-end)"]
    DE -->|REPORT| RAG["regulation_rag<br/>(chưa verify — cần ChromaDB<br/>service + LLM)"] 
    RAG --> REP["alert_report → STR .docx<br/>(chưa verify)"]
    REP --> HITL["HUMAN CHECKPOINT<br/>is_paused() = case_status<br/>== pending_review &&<br/>approval_status == pending<br/>(cơ chế đọc từ code,<br/>chưa verify qua UI vì pipeline/run fail)"]
    HITL -->|POST /api/pipeline/{tx}/decision<br/>approve/reject| RES["resume() — cập nhật<br/>approval_status + invoke(None)"]

    style NEO fill:#ffe0e0,stroke:#f00,stroke-dasharray: 5 5
    style RAG fill:#ffe0e0,stroke:#f00,stroke-dasharray: 5 5
    style REP fill:#ffe0e0,stroke:#f00,stroke-dasharray: 5 5
    style HITL fill:#fff3cd,stroke:#f00,stroke-dasharray: 5 5
    style A3 fill:#ffcccc,stroke:#f00
```

**Luồng thao tác UI thật (từ code frontend_html/app.js + API contract — UI chưa chạy được do pipeline/run 500):**

UI cho phép (thiết kế trong code, gọi API thật qua fetch):
1. Login (`POST /api/auth/login`) → lưu token.
2. Nhập giao dịch (tx_hash/wallet_from/wallet_to/amount_vnd/fullname/id_number/account_number)
   → submit `POST /api/pipeline/run`.
3. Nếu thành công: hiển thị Pipeline rail, Risk breakdown (classifier/graph/hop),
   sanctions + fuzzy name, suspicious path SVG, legal citations, chat, và nút
   **Approve/Reject** khi `approval_status == "pending"`.
4. Approve/Reject → `POST /api/pipeline/{tx_hash}/decision` với `{approval_status}`.
5. Nếu approved và có report_path → nút tải `.docx` qua
   `GET /api/pipeline/{tx_hash}/report?token=...`.

**Trên thực tế lúc chạy thử**: do `/api/pipeline/run` trả 500, **không thể hoàn thành
luồng UI Approve/Reject** — HITL chưa verify được trực tiếp qua UI.

---

## 3. Khác biệt giữa demo_runner (terminal) và full UI/API

| Khía cạnh | `scripts/demo_runner.py` | `api/main.py` (full UI/API) |
|---|---|---|
| Orchestration | Gọi thẳng từng Assistant (KHÔNG dùng PipelineRun/LangGraph) — có chủ đích ghi trong docstring | Dùng **PipelineRun** (LangGraph) duy nhất |
| aggregation_monitor | **KHÔNG gọi** → structuring_flag luôn False | CÓ trong graph_builder (node chạy giữa privacy và classifier) |
| Graph engine | NetworkX (khi DEMO_MODE=true) | Neo4j GDS (khi DEMO_MODE=false — file .env hiện tại) |
| Interrupt/LangGraph checkpoint | Không dùng | Có (interrupt_after=["alert_report"], dùng cho HITL) |
| Review/Report | Không verify được (mọi scenario PASS) | Không verify được (pipeline/run 500) |
| HITL approve/reject | Không có — chỉ in state | Có endpoint + UI, nhưng không chạy được do pipeline/run 500 |

---

## 4. Hạn chế phát hiện từ CHẠY THẬT

### Nhóm 1 — Thiếu dữ liệu/hạ tầng
1. **Chưa có Neo4j thật tương thích GDS** — demo luôn chạy NetworkX mock (kể cả khi `DEMO_MODE=True`).
   Ảnh hưởng: **chặn production**.
2. **`DEMO_MODE=False` trong .env nhưng không có Neo4j service chạy** → chạy demo_runner không set biến sẽ
   crash Graph Assistant ngay (đã quan sát lỗi GDS thật). Ảnh hưởng: **chặn demo** nếu ai quên set env.
3. **Mock scenario không đủ đa dạng để test 3 nhánh PASS/REVIEW/REPORT** — cả 3 chạy ra PASS.
   Không test được: sanctions exact match, structuring (Rule 2), classifier ≥ θ (Rule 3), hop ≤ 2 (Rule 4),
   2 tín hiệu medium (Rule 5/REVIEW). Ảnh hưởng: **chặn demo** (không chứng minh được REPORT branch).
4. **Không có dữ liệu ghép cặp classifier+graph+label** → ensemble bỏ, dùng rule-based (thực trạng vẫn đúng
   như decision_engine.py đã mô tả). Ảnh hưởng: **chỉ đúng ở mức rule heuristic, chưa calibrate**.
5. **wallet_tx_history chưa có nguồn crawl Etherscan thật** (chỉ là key non-standard, không có trong mock) →
   aggregation_monitor + 2 đặc trưng hành vi classifier chỉ trả None/False. Ảnh hưởng: **chặn production**.

### Nhóm 2 — Lỗi/thiếu sót phát hiện khi chạy thử
6. **`POST /api/pipeline/run` luôn trả 500**: `api/main.py:452` gọi `assert_no_raw_pii(state)`
   trên state chưa đi qua privacy_layer_node → lỗi `ValueError: PHÁT HIỆN VI PHẠM PRIVACY LAYER`
   (traceback thật trong logs/api_stderr.log). **Chặn toàn bộ luồng UI/API/HITL.**
7. **classifier_score giống hệt nhau cho 3 giao dịch khác nhau** (0.0002841) — do feature vector mostly
   zero + model train trên Elliptic không map được amount_vnd trực tiếp. Ảnh hưởng: **chặn demo
   thuyết phục** (không phân biệt được rủi ro thật).
8. **demo_runner bỏ aggregation_monitor** dù graph_builder có — Rule 2 không bao giờ kích hoạt trong demo.
   Ảnh hưởng: **thiếu sót demo** (scenario smurfing không test được đúng mục đích).
9. **Chạy demo lần đầu (không set DEMO_MODE) không báo "thiếu Neo4j" rõ ràng mà crash bằng lỗi GDS
   kỹ thuật** `SpdBuiltInProcedures` — khó hiểu cho người mới. Ảnh hưởng: **thiếu sót nhỏ (UX)**.

### Nhóm 3 — Vận hành/quy trình
10. **REVIEW không tự sinh document** — chuyên viên phải self-serve qua decision_evidence/API, không có
    file để đọc (đúng như code `_route_after_decision` mô tả). Ảnh hưởng: **chặn production UX**.
11. **UI không phân biệt rõ REVIEW vs REPORT trong banner duyệt** (cả 2 đều là "pending_review" + nút
    Approve/Reject) — chỉ khác label decision. Ảnh hưởng: **thiếu sót nhỏ**.
12. **Chưa có test coverage cho: regression_to_the_mean / approval cho REVIEW / report download** —
    pytest hiện tại 14 test pass nhưng chỉ unit test decision_engine/privacy/state/classifier, không có
    test graph_aml/kyc/aggregation/rag/alert_report/api. Ảnh hưởng: **rủi ro khi thay đổi**.
13. **STR template báo cáo dùng nhiều field của các node khác** (reporting_entity_name...) — không thấy
    node nào set các field này trong pipeline → báo cáo sẽ in "[CHƯA CẤU HÌNH]" hoặc "[CHƯA CUNG CẤP]".
    Ảnh hưởng: **chặn production**.

---
*Ghi chú: đây là snapshot từ lần chạy thử 2026-08-07; không sửa bất kỳ file code nào trong quá trình khảo sát.*