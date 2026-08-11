(function () {
  "use strict";

  const BASE_URL = window.location.origin;
  let authToken = null;
  let authUsername = null;
  let currentResult = null; // { tx_hash, skipped, steps, state }

  // ---------------------------------------------------------------------
  // Helpers HTTP
  // ---------------------------------------------------------------------
  async function apiRequest(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
    const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || JSON.stringify(body);
      } catch (_) {}
      if (res.status === 401) doLogout();
      throw new Error(detail);
    }
    return res.json();
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    (children || []).forEach((c) => node.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function fmtVND(n) {
    return Number(n || 0).toLocaleString("vi-VN");
  }

  // ---------------------------------------------------------------------
  // Đăng nhập / đăng xuất
  // ---------------------------------------------------------------------
  const loginScreen = document.getElementById("login-screen");
  const appScreen = document.getElementById("app-screen");
  const loginForm = document.getElementById("login-form");
  const loginError = document.getElementById("login-error");

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    loginError.hidden = true;
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    try {
      const data = await apiRequest("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      authToken = data.token;
      authUsername = data.username;
      loginScreen.hidden = true;
      appScreen.hidden = false;
      document.getElementById("whoami").textContent = `Đăng nhập: ${authUsername}`;
    } catch (err) {
      loginError.textContent = err.message;
      loginError.hidden = false;
    }
  });

  function doLogout() {
    authToken = null;
    authUsername = null;
    currentResult = null;
    appScreen.hidden = true;
    loginScreen.hidden = false;
    document.getElementById("password").value = "";
  }

  document.getElementById("logout-btn").addEventListener("click", doLogout);

  // ---------------------------------------------------------------------
  // Bước 1: form giao dịch -> chạy pipeline
  // ---------------------------------------------------------------------
  const txForm = document.getElementById("tx-form");
  const submitBtn = document.getElementById("submit-btn");
  const runError = document.getElementById("run-error");

  txForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    runError.hidden = true;
    submitBtn.disabled = true;
    submitBtn.textContent = "Đang chạy pipeline...";

    const payload = {
      tx_hash: document.getElementById("tx_hash").value.trim() || null,
      wallet_from: document.getElementById("wallet_from").value,
      wallet_to: document.getElementById("wallet_to").value,
      amount_vnd: Number(document.getElementById("amount_vnd").value) || 0,
      fullname: document.getElementById("fullname").value,
      id_number: document.getElementById("id_number").value,
      account_number: document.getElementById("account_number").value,
    };

    try {
      const data = await apiRequest("/api/pipeline/run", { method: "POST", body: JSON.stringify(payload) });
      currentResult = data;
      renderAll();
    } catch (err) {
      runError.textContent = "Lỗi: " + err.message;
      runError.hidden = false;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Kích hoạt webhook / Chạy pipeline";
    }
  });

  // ---------------------------------------------------------------------
  // Render tổng
  // ---------------------------------------------------------------------
  function renderAll() {
    if (!currentResult) return;
    const { state, skipped } = currentResult;
    renderPipelineRail();
    renderSkipped();
    if (state && !skipped) {
      renderApprovalActions();
      renderRiskBreakdown();
      renderLegalCitations();
      renderSuspiciousPath();
      renderReportPreview();
      renderChat();
    } else {
      clear(document.getElementById("approval-container"));
      clear(document.getElementById("risk-breakdown-container"));
      clear(document.getElementById("legal-citations-container"));
      clear(document.getElementById("suspicious-path-container"));
      clear(document.getElementById("report-preview-container"));
      clear(document.getElementById("chat-container"));
    }
    renderStepLog();
  }

  // ---- 02: Pipeline rail ----
  function renderPipelineRail() {
    const container = document.getElementById("pipeline-rail-container");
    clear(container);
    const { steps } = currentResult;
    if (!steps || steps.length === 0) return;

    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, [el("span", { class: "step-number" }, ["02"]), "Tiến trình xử lý từng Assistant"])]));
    const rail = el("div", { class: "pipeline-rail" });
    steps.forEach((step, i) => {
      const isWarn = step.step_key === "webhook" && step.snapshot && step.snapshot.skipped;
      rail.appendChild(
        el("div", { class: `pipeline-node done ${isWarn ? "warn" : ""}` }, [
          el("span", { class: "line" }),
          el("span", { class: "dot" }, [String(i + 1).padStart(2, "0")]),
          el("span", { class: "node-label" }, [step.label]),
        ])
      );
    });
    card.appendChild(rail);
    container.appendChild(card);
  }

  // ---- skipped banner ----
  function renderSkipped() {
    const container = document.getElementById("skipped-container");
    clear(container);
    if (!currentResult.skipped) return;
    const reason =
      (currentResult.steps[0] && currentResult.steps[0].snapshot && currentResult.steps[0].snapshot.reason) ||
      "Giao dịch chưa vượt ngưỡng báo cáo — không có gì để duyệt.";
    container.appendChild(el("div", { class: "card" }, [el("p", { class: "empty-note" }, [reason])]));
  }

  // ---- Approval actions ----
  function renderApprovalActions() {
    const container = document.getElementById("approval-container");
    clear(container);
    const state = currentResult.state;
    const status = state.approval_status;
    const decision = state.decision;
    const card = el("div", { class: "card" });

    if (status === "pending") {
      // Pivot kiến trúc: Decision Engine dùng rule-based composite, KHÔNG còn
      // 1 điểm risk tổng hợp với ngưỡng 0.7. Lý do chờ duyệt dựa trên
      // `decision` (REPORT/REVIEW — cả 2 đều case_status=pending_review):
      //   - REPORT: vượt ngưỡng sanctions/structuring/classifier(θ)/graph hop
      //   - REVIEW: 2 tín hiệu vừa cùng lúc, cần chuyên viên xem decision_evidence
      // FIX 2026-08-08 — AUDIT ZERO-TX WALLET: khi insufficient_data=True,
      // REVIEW là do THIẾU DỮ LIỆU on-chain (ví chưa có lịch sử Etherscan),
      // KHÔNG phải do rủi ro cao thật sự — hiển thị nhãn riêng để chuyên viên
      // không hiểu nhầm mức độ nghiêm trọng (xem decision_evidence).
      const insufficientData = state.insufficient_data === true;
      const decisionLabel =
        decision === "REPORT" ? "REPORT — vượt ngưỡng quyết định (sanctions/structuring/classifier/graph)"
        : decision === "REVIEW" && insufficientData
          ? "REVIEW — THIẾU DỮ LIỆU on-chain (ví chưa có lịch sử Etherscan), cần xác minh thủ công"
        : decision === "REVIEW" ? "REVIEW — 2 tín hiệu vừa (classifier + graph) cùng lúc"
        : "Cần chuyên viên xem xét";
      card.appendChild(
        el("div", { class: "risk-banner high" }, [
          el("span", { class: "risk-score-chip" }, [decision || status]),
          el("span", {}, [`[${decisionLabel}] ${decision === "REVIEW" ? (insufficientData ? "Case dừng tại Decision Engine do thiếu dữ liệu — classifier_score KHÔNG được dùng làm căn cứ; chưa tự soạn STR. Xem Decision Evidence để biết lý do." : "Case dừng tại Decision Engine — xem Decision Evidence; chưa tự soạn STR.") : "Cần chuyên viên phê duyệt trước khi gửi STR (Thông tư 27)."}`]),
        ])
      );
      const sanctionMatch = state.sanction_result && state.sanction_result.is_match
        ? `OFAC SDN MATCH (${state.sanction_result.matched_wallet || "?"})`
        : "Không có match sanctions chính xác";
      const fuzzy = state.name_similarity_warning
        ? ` + Fuzzy name warning (${state.name_similarity_score ?? "?"}%)`
        : "";
      card.appendChild(
        el("p", { style: "font-size:13.5px;margin-top:0" }, [
          el("strong", {}, ["Sanctions: "]),
          sanctionMatch + fuzzy,
        ])
      );
      const btnRow = el("div", { class: "btn-row" });
      const approveBtn = el("button", { class: "btn btn-approve" }, ["Approve (Duyệt & gửi STR)"]);
      const rejectBtn = el("button", { class: "btn btn-reject" }, ["Reject (Từ chối)"]);
      approveBtn.addEventListener("click", () => submitDecision("approved", approveBtn, rejectBtn));
      rejectBtn.addEventListener("click", () => submitDecision("rejected", approveBtn, rejectBtn));
      btnRow.appendChild(approveBtn);
      btnRow.appendChild(rejectBtn);
      card.appendChild(btnRow);
    } else if (status === "approved") {
      card.appendChild(el("div", { class: "risk-banner low" }, [el("span", {}, ["Giao dịch đã được duyệt (approved)."])]));
      if (state.report_path) {
        const link = el("a", { class: "btn btn-primary", href: `/api/pipeline/${encodeURIComponent(state.tx_hash)}/report?token=${encodeURIComponent(authToken)}`, download: "" }, ["Tải bản dự thảo STR (.docx)"]);
        card.appendChild(link);
      } else {
        card.appendChild(el("p", { class: "empty-note" }, ["Case không có STR draft (v.d. REVIEW được duyệt nhưng không tự soạn STR, hoặc PASS auto-cleared) — không cần lập STR."]));
      }
    } else if (status === "rejected") {
      card.appendChild(
        el("div", { class: "risk-banner high" }, [
          el("span", {}, [`Giao dịch đã bị TỪ CHỐI. Bản dự thảo STR (nếu có) vẫn được lưu lại tại ${state.report_path || "—"} để lưu vết, nhưng KHÔNG được gửi đi.`]),
        ])
      );
    } else {
      return;
    }
    container.appendChild(card);
  }

  async function submitDecision(decision, approveBtn, rejectBtn) {
    if (!currentResult || !currentResult.tx_hash) return;
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    try {
      // API /api/pipeline/{tx_hash}/decision nhận { approval_status: "approved"|"rejected" },
      // KHÔNG phải { decision } — bản trước gửi sai tên field nên Approve/Reject luôn 422.
      const updatedState = await apiRequest(`/api/pipeline/${encodeURIComponent(currentResult.tx_hash)}/decision`, {
        method: "POST",
        body: JSON.stringify({ approval_status: decision }),
      });
      currentResult.state = updatedState;
      renderAll();
    } catch (err) {
      runError.textContent = "Lỗi khi gửi quyết định: " + err.message;
      runError.hidden = false;
      approveBtn.disabled = false;
      rejectBtn.disabled = false;
    }
  }

  // ---- Risk breakdown ----
  function renderRiskBreakdown() {
    const container = document.getElementById("risk-breakdown-container");
    clear(container);
    const state = currentResult.state;
    const topFeatures = state.top_features || [];

    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, ["Explainable Risk Assessment"])]));

    // Pivot kiến trúc: KHÔNG còn risk_breakdown (công thức gộp điểm tổng hợp
    // 0.2/0.3/0.5 đã bỏ). Hiển thị trực tiếp 2 điểm nguồn + hop distance —
    // quyết định REPORT/REVIEW dựa trên rule-based composite (xem Decision
    // Evidence) chứ không phải 1 con số tổng.
    // [Phase 2] Graph semantics: graph_data_available là TÍN HIỆU CHÍNH.
    // Khi false (NO_GRAPH_DATA), KHÔNG hiển thị graph_score=0 / hop=N/A /
    // fan_out=0 / community_id=0 như số liệu thật — đó chỉ là fallback nội bộ.
    const graphDataAvailable = state.graph_data_available === true;
    const graphNoDataLabel = "— (không có dữ liệu graph)";
    const scoreRows = [
      ["Classifier score", state.classifier_score ?? "N/A"],
      ["Graph score (PPR)", graphDataAvailable ? (state.graph_score ?? "N/A") : graphNoDataLabel],
      ["Hop tới ví bị sanction", graphDataAvailable ? (state.hop_distance_to_blacklist ?? "N/A") : graphNoDataLabel],
      ["Fan-out", graphDataAvailable ? (state.fan_out ?? "N/A") : graphNoDataLabel],
      ["Cộng đồng Louvain", graphDataAvailable ? (state.community_id ?? "N/A") : graphNoDataLabel],
      ["Đủ dữ liệu on-chain", state.insufficient_data === true ? "KHÔNG (ví chưa có lịch sử Etherscan)" : "Có"],
    ];
    const grid = el("div", { class: "breakdown-grid" });
    scoreRows.forEach(([label, value]) => {
      grid.appendChild(
        el("div", { class: "breakdown-item" }, [
          el("div", { class: "label" }, [label]),
          el("div", { class: "value" }, [String(value)]),
        ])
      );
    });
    card.appendChild(grid);
    card.appendChild(
      el("p", { class: "card-subtitle", style: "margin-top:-6px" }, [
        "Quyết định REPORT/REVIEW dùng rule-based composite (từng tín hiệu xét độc lập) — KHÔNG còn công thức gộp 1 điểm tổng hợp. Xem Decision Evidence để biết rule cụ thể đã kích hoạt.",
      ])
    );

    // [UX FIX] Graph interpretation — làm rõ PPR (Personalized PageRank) và
    // "hop tới ví sanction" là 2 khái niệm ĐỘC LẬP với "có cạnh/giao dịch
    // trực tiếp trên graph hay không". Người xem hay hiểu nhầm PPR=0 nghĩa
    // là "graph không có dữ liệu", trong khi PPR=0 hoàn toàn có thể xảy ra
    // dù graph có cạnh — PPR đo mức liên quan tới node seed/risk theo thuật
    // toán Personalized PageRank, không đo số hop/cạnh. Tương tự, hop=N/A
    // nghĩa là graph hiện có không tìm được đường tới ví sanction — không
    // mâu thuẫn với việc có 1 giao dịch trực tiếp giữa wallet_from/wallet_to.
    // [Phase 2] Phân biệt 3 trạng thái bằng graph_analysis_status — KHÔNG đoán
    // từ PPR=0 / hop=None / fan_out=0 / community_id=0 (các số đó có thể là
    // kết quả thuật toán hợp lệ khi graph có dữ liệu).
    const graphStatus = state.graph_analysis_status;
    const graphInterpretationText =
      graphStatus === "GRAPH_AVAILABLE_SANCTION_PATH_FOUND"
        ? `Đã tìm thấy đường đi tới ví trong danh sách trừng phạt, cách ${state.hop_distance_to_blacklist} hop giao dịch.`
        : graphStatus === "GRAPH_AVAILABLE_NO_SANCTION_PATH"
          ? "Graph có dữ liệu cho ví này nhưng KHÔNG tìm thấy đường đi tới ví thuộc danh sách trừng phạt (OFAC/UN/NHNN) trong phạm vi graph hiện có."
          : "KHÔNG có dữ liệu đồ thị cho ví này trong nguồn dữ liệu graph hiện tại (NO_GRAPH_DATA) — không thể tính PPR / hop / fan-out / community, và không có đường đi đáng ngờ rút ra từ graph.";
    card.appendChild(
      el("div", { class: "breakdown-item", style: "margin-bottom:18px" }, [
        el("div", { class: "label" }, ["Graph interpretation"]),
        el("p", { style: "font-size:13px;margin:8px 0 0;color:var(--ink-muted)" }, [
          graphInterpretationText + (graphStatus === "GRAPH_AVAILABLE_NO_SANCTION_PATH" ? " Hop=N/A ở đây là kết quả PHÂN TÍCH (không có path), không phải thiếu dữ liệu." : ""),
        ]),
        el("p", { style: "font-size:13px;margin:4px 0 0;color:var(--ink-muted)" }, [
          `Giao dịch trực tiếp đang xét: ${shorten(state.wallet_from || "")} → ${shorten(state.wallet_to || "")}`,
        ]),
        el("p", { style: "font-size:12px;margin:8px 0 0;color:var(--ink-faint)" }, [
          "Lưu ý: PPR = 0 KHÔNG có nghĩa graph không có cạnh/giao dịch. PPR (Personalized PageRank) đo mức độ liên quan của ví này tới các ví seed/rủi ro theo thuật toán riêng, khác với số cạnh hay số hop tới ví sanction — 2 chỉ số này có thể độc lập với nhau.",
        ]),
      ])
    );

    if (topFeatures.length > 0) {
      card.appendChild(el("p", { style: "font-weight:600;font-size:13.5px;margin-bottom:8px" }, ["Đặc trưng ảnh hưởng lớn nhất tới điểm phân loại (top feature importance toàn cục)"]));
      const list = el("ul", { class: "feature-list" });
      topFeatures.forEach(([name, score]) => {
        list.appendChild(el("li", {}, [el("span", { class: "feature-name" }, [name]), el("span", {}, [Number(score).toFixed(3)])]));
      });
      card.appendChild(list);
    }

    // [Phase 2] Gate toàn bộ graph notes theo graph_data_available — fan_out=0 /
    // community_id=0 khi NO_GRAPH_DATA không được hiển thị như số liệu thật.
    const notes = [];
    if (graphDataAvailable && state.hop_distance_to_blacklist !== null && state.hop_distance_to_blacklist !== undefined)
      notes.push(`Cách ví trong danh sách trừng phạt ${state.hop_distance_to_blacklist} hop giao dịch`);
    if (graphDataAvailable && state.fan_out !== null && state.fan_out !== undefined) notes.push(`Fan-out = ${state.fan_out}`);
    if (graphDataAvailable && state.community_id !== null && state.community_id !== undefined) notes.push(`Thuộc cộng đồng Louvain #${state.community_id}`);
    if (notes.length > 0) {
      const notesRow = el("div", { class: "graph-notes" });
      notes.forEach((n) => notesRow.appendChild(el("span", { class: "note-chip" }, [n])));
      card.appendChild(notesRow);
    }

    card.appendChild(
      el("p", { class: "disclaimer" }, [
        "Feature importance ở trên là chỉ số TOÀN CỤC của model, không phải lý do riêng cho giao dịch này (per-instance explanation cần SHAP — để dành hướng phát triển).",
      ])
    );
    container.appendChild(card);
  }

  // ---- Legal citations ----
  function renderLegalCitations() {
    const container = document.getElementById("legal-citations-container");
    clear(container);
    const citations = currentResult.state.legal_citations;
    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, ["Căn cứ pháp lý (RAG)"])]));

    if (!citations || citations.length === 0) {
      card.appendChild(el("p", { class: "empty-note" }, ["Chưa có trích dẫn pháp lý."]));
    } else if (typeof citations === "string") {
      card.appendChild(el("p", {}, [citations]));
    } else {
      citations.forEach((c, i) => {
        if (c.raw_text && Object.keys(c).length === 1) {
          card.appendChild(el("p", {}, [c.raw_text]));
          return;
        }
        const source = c.source || "(không rõ nguồn)";
        const dieuKhoan = c.dieu_khoan ? ` — ${c.dieu_khoan}` : "";
        const details = el("details", { class: "citation" });
        if (i === 0) details.setAttribute("open", "");
        details.appendChild(el("summary", {}, [source + dieuKhoan]));
        const body = el("div", { class: "body" });
        if (c.noi_dung_tom_tat) body.appendChild(el("p", {}, [el("strong", {}, ["Nội dung: "]), c.noi_dung_tom_tat]));
        if (c.ly_do_ap_dung) body.appendChild(el("p", {}, [el("strong", {}, ["Vì sao áp dụng cho giao dịch này: "]), c.ly_do_ap_dung]));
        details.appendChild(body);
        card.appendChild(details);
      });
    }
    container.appendChild(card);
  }

  // ---- Suspicious path ----
  function shorten(addr) {
    if (!addr) return "";
    return addr.length > 14 ? `${addr.slice(0, 10)}…` : addr;
  }

  // [Phase 2] Phân biệt 3 trạng thái graph. BỎ fallback [walletFrom, walletTo]
  // giả tạo trước đây — flow SVG chỉ vẽ ĐÚNG suspicious_path do Graph Agent
  // tìm (path thật bắt đầu từ ví trong danh sách trừng phạt).
  function renderSuspiciousPath() {
    const container = document.getElementById("suspicious-path-container");
    clear(container);
    const state = currentResult.state;
    const walletFrom = state.wallet_from || "";
    const walletTo = state.wallet_to || "";
    const graphDataAvailable = state.graph_data_available === true;
    const suspiciousPath = graphDataAvailable ? (state.suspicious_path || []) : [];
    const pathFound = state.sanction_path_found === true && suspiciousPath.length > 0;

    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, ["Sơ đồ dòng tiền (đường đi đáng ngờ)"])]));

    if (!graphDataAvailable) {
      // Trạng thái 1: NO_GRAPH_DATA — không được gọi là "suspicious path".
      card.appendChild(
        el("p", { class: "card-subtitle", style: "margin-top:-4px" }, [
          "KHÔNG có dữ liệu đồ thị cho ví này trong nguồn dữ liệu graph hiện tại — không thể rút ra đường đi đáng ngờ nào từ graph.",
        ])
      );
      card.appendChild(el("p", { class: "empty-note" }, [
        "Không có graph evidence cho giao dịch này (NO_GRAPH_DATA).",
      ]));
      container.appendChild(card);
      return;
    }

    if (!pathFound) {
      // Trạng thái 2: GRAPH_AVAILABLE_NO_SANCTION_PATH — graph có dữ liệu,
      // thuật toán chạy thật, nhưng không có path tới ví sanction.
      card.appendChild(
        el("p", { class: "card-subtitle", style: "margin-top:-4px" }, [
          "Graph có dữ liệu cho ví này nhưng KHÔNG tìm thấy đường đi tới ví trong danh sách trừng phạt (OFAC/UN/NHNN) trong phạm vi graph hiện có — không có suspicious path để vẽ.",
        ])
      );
      const notesEmpty = [];
      if (state.fan_out !== null && state.fan_out !== undefined) notesEmpty.push(`Fan-out = ${state.fan_out}`);
      if (state.community_id !== null && state.community_id !== undefined) notesEmpty.push(`Cộng đồng Louvain #${state.community_id}`);
      if (notesEmpty.length > 0) {
        const notesRow = el("div", { class: "graph-notes" });
        notesEmpty.forEach((n) => notesRow.appendChild(el("span", { class: "note-chip" }, [n])));
        card.appendChild(notesRow);
      }
      container.appendChild(card);
      return;
    }

    // Trạng thái 3: GRAPH_AVAILABLE_SANCTION_PATH_FOUND — vẽ SUSPICIOUS_PATH
    // THẬT (phần tử đầu = ví sanitized, phần tử cuối = wallet_from).
    const pathNodes = suspiciousPath.includes(walletTo)
      ? suspiciousPath
      : [...suspiciousPath, walletTo].filter(Boolean);
    const flaggedNode = suspiciousPath[0];

    card.appendChild(
      el("p", { class: "card-subtitle", style: "margin-top:-4px" }, [
        `Graph phát hiện ${pathNodes.length - 1} bước (hop) từ ví trong danh sách trừng phạt tới ví đích của giao dịch này.`,
      ])
    );
    card.appendChild(buildFlowSvg(pathNodes, flaggedNode, walletFrom, walletTo, state.amount_vnd));

    const legend = el("div", { class: "flow-legend" }, [
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dot flagged" }), "Ví trong danh sách trừng phạt (OFAC/UN/NHNN)"]),
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dot endpoint" }), "Ví nguồn / ví đích của giao dịch đang xét"]),
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dot mid" }), "Ví trung gian trên đường đi"]),
    ]);
    card.appendChild(legend);

    const notes = [];
    if (state.hop_distance_to_blacklist !== null && state.hop_distance_to_blacklist !== undefined)
      notes.push(`${state.hop_distance_to_blacklist} hop tới ví trong danh sách trừng phạt`);
    if (state.fan_out !== null && state.fan_out !== undefined) notes.push(`Fan-out = ${state.fan_out}`);
    if (state.community_id !== null && state.community_id !== undefined) notes.push(`Cộng đồng Louvain #${state.community_id}`);
    if (notes.length > 0) {
      const notesRow = el("div", { class: "graph-notes" });
      notes.forEach((n) => notesRow.appendChild(el("span", { class: "note-chip" }, [n])));
      card.appendChild(notesRow);
    }

    container.appendChild(card);
  }

  // Vẽ sơ đồ dòng tiền dạng node-link bằng SVG thuần (không dùng thư viện
  // ngoài, khớp chủ trương "HTML/JS thuần, không cần Node.js" của bản build
  // này). Node đầu tiên = ví đen (nếu có đường đi đáng ngờ), node cuối = ví
  // đích của giao dịch, các node giữa = ví trung gian trên đường lan truyền
  // rủi ro (fan-in/fan-out) mà Graph Assistant tìm được.
  function buildFlowSvg(nodes, flaggedNode, walletFrom, walletTo, amountVnd) {
    const NS = "http://www.w3.org/2000/svg";
    const spacing = 190;
    const width = Math.max(560, (nodes.length - 1) * spacing + 120);
    const height = 130;
    const cy = 56;

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("class", "flow-svg");
    svg.setAttribute("preserveAspectRatio", "xMinYMid meet");

    const defs = document.createElementNS(NS, "defs");
    const marker = document.createElementNS(NS, "marker");
    marker.setAttribute("id", "flowArrow");
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "8");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "7");
    marker.setAttribute("markerHeight", "7");
    marker.setAttribute("orient", "auto-start-reverse");
    const arrowPath = document.createElementNS(NS, "path");
    arrowPath.setAttribute("d", "M0,0 L10,5 L0,10 z");
    arrowPath.setAttribute("class", "flow-arrowhead");
    marker.appendChild(arrowPath);
    defs.appendChild(marker);
    svg.appendChild(defs);

    const positions = nodes.map((_, i) => 60 + i * spacing);

    // Vẽ các cạnh (edge) trước để nằm dưới node.
    for (let i = 0; i < nodes.length - 1; i++) {
      const x1 = positions[i] + 26;
      const x2 = positions[i + 1] - 26;

      const line = document.createElementNS(NS, "line");
      line.setAttribute("x1", x1);
      line.setAttribute("y1", cy);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", cy);
      line.setAttribute("class", "flow-edge");
      line.setAttribute("marker-end", "url(#flowArrow)");
      svg.appendChild(line);

      const hopLabel = document.createElementNS(NS, "text");
      hopLabel.setAttribute("x", (x1 + x2) / 2);
      hopLabel.setAttribute("y", cy - 10);
      hopLabel.setAttribute("text-anchor", "middle");
      hopLabel.setAttribute("class", "flow-hop-label");
      hopLabel.textContent = `hop ${i + 1}`;
      svg.appendChild(hopLabel);

      if (i === nodes.length - 2 && amountVnd) {
        const amtLabel = document.createElementNS(NS, "text");
        amtLabel.setAttribute("x", (x1 + x2) / 2);
        amtLabel.setAttribute("y", cy + 26);
        amtLabel.setAttribute("text-anchor", "middle");
        amtLabel.setAttribute("class", "flow-amount-label");
        amtLabel.textContent = `${fmtVND(amountVnd)} VND`;
        svg.appendChild(amtLabel);
      }
    }

    // Vẽ node.
    nodes.forEach((addr, i) => {
      const cx = positions[i];
      const isFlagged = addr === flaggedNode;
      const isEndpoint = !isFlagged && (addr === walletFrom || addr === walletTo);
      const cls = isFlagged ? "flagged" : isEndpoint ? "endpoint" : "mid";

      const circle = document.createElementNS(NS, "circle");
      circle.setAttribute("cx", cx);
      circle.setAttribute("cy", cy);
      circle.setAttribute("r", 22);
      circle.setAttribute("class", `flow-node ${cls}`);
      const titleEl = document.createElementNS(NS, "title");
      titleEl.textContent = addr;
      circle.appendChild(titleEl);
      svg.appendChild(circle);

      const icon = document.createElementNS(NS, "text");
      icon.setAttribute("x", cx);
      icon.setAttribute("y", cy + 5);
      icon.setAttribute("text-anchor", "middle");
      icon.setAttribute("class", `flow-node-icon ${cls}`);
      icon.textContent = isFlagged ? "⚠" : i === 0 ? "S" : i === nodes.length - 1 ? "Đ" : "•";
      svg.appendChild(icon);

      const label = document.createElementNS(NS, "text");
      label.setAttribute("x", cx);
      label.setAttribute("y", cy + 44);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "flow-node-label");
      label.textContent = shorten(addr);
      svg.appendChild(label);
    });

    const wrap = el("div", { class: "flow-svg-wrap" });
    wrap.appendChild(svg);
    return wrap;
  }

  // ---- Report preview ----
  function renderReportPreview() {
    const container = document.getElementById("report-preview-container");
    clear(container);
    const state = currentResult.state;
    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, ["Xem trước nội dung báo cáo STR"])]));
    const preview = el("div", { class: "report-preview" });
    preview.appendChild(el("div", { class: "row" }, [el("span", { class: "k" }, ["Mã giao dịch"]), el("span", { class: "v mono" }, [state.tx_hash || "UNKNOWN_HASH"])]));
    preview.appendChild(el("div", { class: "row" }, [el("span", { class: "k" }, ["Giá trị giao dịch"]), el("span", { class: "v" }, [fmtVND(state.amount_vnd) + " VND"])]));
    preview.appendChild(el("div", { class: "row" }, [el("span", { class: "k" }, ["Decision"]), el("span", { class: "v" }, [state.decision ?? "—"])]));
    card.appendChild(preview);
    card.appendChild(
      el("p", { class: "disclaimer" }, [
        "Đây là bản xem trước rút gọn (chi tiết đầy đủ xem panel Explainable Risk và Căn cứ pháp lý phía trên). File .docx đầy đủ theo Mẫu số 04, Thông tư 27 sẽ có sẵn để tải sau khi Approve.",
      ])
    );
    container.appendChild(card);
  }

  // ---- Chat ----
  const chatSuggestions = ["Tại sao quyết định REPORT?", "PPR nghĩa là gì?", "Có cần lập STR không?"];
  let chatHistory = [];

  function renderChat() {
    const state = currentResult.state;
    const container = document.getElementById("chat-container");
    clear(container);
    // Pivot kiến trúc: gate theo `decision` (giống API /chat: yêu cầu pipeline
    // đã chạy tới Decision Engine), KHÔNG theo final_risk_score — field đó
    // thuộc kiến trúc weighted-sum cũ, không còn được pipeline ghi.
    if (!state.decision) return;
    chatHistory = [];

    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, ["Hỏi đáp về giao dịch này"])]));

    const suggestRow = el("div", { class: "chat-suggestions" });
    chatSuggestions.forEach((s) => {
      const btn = el("button", {}, [s]);
      btn.addEventListener("click", () => askChat(s, threadDiv, input, askBtn, chatErr));
      suggestRow.appendChild(btn);
    });
    card.appendChild(suggestRow);

    const threadDiv = el("div", { class: "chat-thread" });
    card.appendChild(threadDiv);

    const chatErr = el("div", { class: "error-banner", hidden: "" });
    card.appendChild(chatErr);

    const inputRow = el("div", { class: "chat-input-row" });
    const input = el("input", { placeholder: "Đặt câu hỏi, ví dụ: 'Tại sao quyết định REPORT?'" });
    const askBtn = el("button", { class: "btn btn-primary" }, ["Hỏi AI"]);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") askChat(input.value, threadDiv, input, askBtn, chatErr);
    });
    askBtn.addEventListener("click", () => askChat(input.value, threadDiv, input, askBtn, chatErr));
    inputRow.appendChild(input);
    inputRow.appendChild(askBtn);
    card.appendChild(inputRow);

    container.appendChild(card);
  }

  async function askChat(question, threadDiv, input, askBtn, chatErr) {
    const q = (question || "").trim();
    if (!q || askBtn.disabled) return;
    askBtn.disabled = true;
    chatErr.hidden = true;
    try {
      const { answer } = await apiRequest(`/api/pipeline/${encodeURIComponent(currentResult.tx_hash)}/chat`, {
        method: "POST",
        body: JSON.stringify({ question: q }),
      });
      chatHistory.push({ question: q, answer });
      threadDiv.appendChild(el("div", { class: "chat-bubble question" }, [q]));
      threadDiv.appendChild(el("div", { class: "chat-bubble answer" }, [answer]));
      input.value = "";
    } catch (err) {
      chatErr.textContent = err.message;
      chatErr.hidden = false;
    } finally {
      askBtn.disabled = false;
    }
  }

  // ---- Step log (debug) ----
  function renderStepLog() {
    const container = document.getElementById("step-log-container");
    clear(container);
    const { steps } = currentResult;
    if (!steps || steps.length === 0) return;
    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-header" }, [el("h2", {}, ["Nhật ký chi tiết state qua từng bước"])]));
    card.appendChild(el("p", { class: "card-subtitle" }, ["Mở từng bước để xem dữ liệu thô — hữu ích khi cần debug trước buổi demo."]));
    steps.forEach((step, i) => {
      const details = el("details", { class: "step-detail" });
      details.appendChild(el("summary", {}, [`${String(i + 1).padStart(2, "0")} · ${step.label}`]));
      details.appendChild(el("pre", {}, [JSON.stringify(step.snapshot, null, 2)]));
      card.appendChild(details);
    });
    container.appendChild(card);
  }
})();