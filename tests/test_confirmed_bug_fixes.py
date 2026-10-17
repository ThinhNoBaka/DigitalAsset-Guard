# tests/test_confirmed_bug_fixes.py
"""
Test hồi quy cho các lỗi chức năng đã được xác nhận bằng chạy thật:

1. Privacy Layer không nhận PII (LangGraph bỏ key không khai báo trong AMLState).
2. Sanctions so khớp phân biệt hoa/thường (địa chỉ EIP-55 checksum).
3. Decision Engine: thứ tự insufficient_data, chỉ ghi 1 evidence, name
   similarity chỉ "for information", evidence ghi sai dataset (Elliptic).
4. Etherscan fetcher: sort=asc lấy giao dịch cũ nhất, nuốt lỗi API, không timeout.
5. API HITL: tải STR khi chưa duyệt, không lưu người duyệt, tx_hash không validate.
6. Calibrate θ: comment "ngưỡng thấp nhất" nhưng code lấy argmax(threshold).

Không gọi mạng, không cần model XGBoost: các agent cần model/Etherscan được
thay bằng stub, phần đang kiểm tra luôn chạy code thật.
"""
import json
import re

import numpy as np
import pytest

import core.graph_builder as graph_builder
from agents.decision_engine import make_decision, reset_threshold_cache
from agents.kyc_verification import get_ofac_wallets, verify_kyc
from core.privacy_layer import PII_FIELDS


@pytest.fixture(autouse=True)
def _reset_threshold():
    reset_threshold_cache()
    yield
    reset_threshold_cache()


# Tên gần giống entity thật "Rafael CARO QUINTERO" trong data/processed/sdn_names.txt
# (xem data/mock/scenario_name_similarity.json).
NEAR_SDN_FULLNAME = "Rafael Caro Kintero"


def _raw_state(tx_hash="TXFIX001"):
    """State thô giống api/main.py::_build_initial_state (có placeholder hashed_*=None)."""
    return {
        "tx_hash": tx_hash,
        "wallet_from": "0x1111111111111111111111111111111111111111",
        "wallet_to": "0x2222222222222222222222222222222222222222",
        "amount_vnd": 1_000_000.0,
        "fullname": NEAR_SDN_FULLNAME,
        "id_number": "079099001234",
        "account_number": "3000000099",
        "hashed_fullname": None,
        "hashed_id_number": None,
        "hashed_account_number": None,
        "name_similarity_warning": False,
        "name_similarity_score": None,
        "approval_status": "pending",
    }


@pytest.fixture
def stub_model_agents(monkeypatch):
    """Thay các agent cần model/Neo4j bằng stub — giữ nguyên privacy/sanctions/decision thật."""
    def fake_classifier(state):
        state["classifier_score"] = 0.0
        state["insufficient_data"] = False
        return state

    monkeypatch.setattr(graph_builder, "analyze_aggregation", lambda state: state)
    monkeypatch.setattr(graph_builder, "analyze_transaction", fake_classifier)
    monkeypatch.setattr(graph_builder, "analyze_graph", lambda state: state)


# =============================================================================
# 1. Privacy Layer chạy trước graph.invoke()
# =============================================================================

def test_pipeline_run_hashes_pii_and_runs_name_screening(stub_model_agents):
    run = graph_builder.PipelineRun(_raw_state(), thread_id="TXFIX001")
    result = run.run()

    for field in ("hashed_fullname", "hashed_id_number", "hashed_account_number"):
        assert re.fullmatch(r"[0-9a-f]{64}", result[field] or ""), field

    assert result["name_similarity_score"] is not None
    assert result["name_similarity_score"] >= 85.0
    assert result["name_similarity_warning"] is True
    assert result["name_similarity_matched_name"]

    assert not (PII_FIELDS & set(result.keys()))
    assert NEAR_SDN_FULLNAME not in json.dumps(result, ensure_ascii=False, default=str)


# =============================================================================
# 2. Sanctions: địa chỉ 0x không phân biệt hoa/thường
# =============================================================================

def test_sanctions_matches_checksum_cased_address():
    listed = sorted(w for w in get_ofac_wallets() if w.startswith("0x"))[0]
    checksum_cased = "0x" + listed[2:].upper()

    result = verify_kyc({"wallet_from": "0x" + "9" * 40, "wallet_to": checksum_cased})

    assert result["sanction_result"]["is_match"] is True
    assert result["sanction_result"]["matched_wallet"] == checksum_cased


# =============================================================================
# 3. Decision Engine
# =============================================================================

def test_insufficient_data_does_not_mask_sanctions_or_structuring():
    sanctioned = make_decision({
        "insufficient_data": True,
        "classifier_score": 0.99,
        "sanction_result": {"is_match": True, "matched_wallet": "0xabc"},
    })
    assert sanctioned["decision"] == "REPORT"
    assert any("Exact OFAC SDN match" in ev for ev in sanctioned["decision_evidence"])
    assert any("THIẾU DỮ LIỆU" in ev for ev in sanctioned["decision_evidence"])
    # classifier vẫn không được dùng làm căn cứ khi thiếu dữ liệu
    assert not any("Classifier score" in ev for ev in sanctioned["decision_evidence"])

    structuring = make_decision({
        "insufficient_data": True,
        "sanction_result": {"is_match": False},
        "structuring_detected": True,
    })
    assert structuring["decision"] == "REPORT"


def test_decision_collects_evidence_from_every_triggered_rule():
    result = make_decision({
        "classifier_score": 0.999,
        "hop_distance_to_blacklist": 1,
        "sanction_result": {"is_match": True, "matched_wallet": "0xabc"},
        "structuring_detected": True,
    })
    evidence = " | ".join(result["decision_evidence"])

    assert result["decision"] == "REPORT"
    assert "Exact OFAC SDN match" in evidence
    assert "structuring" in evidence
    assert "vượt ngưỡng calibrate" in evidence
    assert "Graph exposure" in evidence


def test_name_similarity_warning_routes_to_review():
    result = make_decision({
        "classifier_score": 0.0,
        "sanction_result": {"is_match": False},
        "name_similarity_warning": True,
        "name_similarity_score": 90.0,
    })
    assert result["decision"] == "REVIEW"
    assert result["case_status"] == "pending_review"


def test_classifier_evidence_names_correct_dataset():
    result = make_decision({"classifier_score": 0.999, "sanction_result": {"is_match": False}})
    evidence = " ".join(result["decision_evidence"])
    assert "Ethereum Fraud" in evidence
    assert "Elliptic" not in evidence


# =============================================================================
# 4. Etherscan fetcher
# =============================================================================

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_etherscan_fetches_latest_first_with_timeout_and_raises_on_api_error(monkeypatch):
    from scripts import etherscan_fetcher

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"params": params, "timeout": timeout})
        if params["action"] == "txlist":
            return _FakeResponse({"status": "1", "message": "OK", "result": [{"hash": "0x1"}]})
        return _FakeResponse({"status": "0", "message": "No transactions found", "result": []})

    monkeypatch.setenv("ETHERSCAN_API_KEY", "dummy")
    monkeypatch.setattr(etherscan_fetcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(etherscan_fetcher.requests, "get", fake_get)

    record = etherscan_fetcher.fetch_wallet_record("0xabc")
    assert record["chains"]["ethereum"] == [{"hash": "0x1"}]
    assert record["token_transfers"]["ethereum"] == []
    assert all(c["params"]["sort"] == "desc" for c in calls)
    assert all(c["timeout"] for c in calls)

    monkeypatch.setattr(
        etherscan_fetcher.requests, "get",
        lambda url, params=None, timeout=None: _FakeResponse(
            {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
        ),
    )
    with pytest.raises(etherscan_fetcher.EtherscanFetchError):
        etherscan_fetcher.fetch_wallet_record("0xabc")


# =============================================================================
# 5. API HITL
# =============================================================================

@pytest.fixture
def api_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api.main as api_main
    import core.audit_logger as audit_logger

    monkeypatch.setattr(audit_logger, "AUDIT_LOG_PATH", tmp_path / "audit_trail.log")
    monkeypatch.setitem(api_main.TOKENS, "test-token", "chuyenvien_a")
    monkeypatch.setattr(api_main, "RUNS", {})

    client = TestClient(api_main.app)
    client.headers.update({"Authorization": "Bearer test-token"})
    return client, api_main, tmp_path


class _StubRun:
    def __init__(self, state):
        self.state = state


def test_report_download_requires_approval(api_client):
    client, api_main, tmp_path = api_client
    report = tmp_path / "STR_REPORT_TXDL.docx"
    report.write_bytes(b"fake docx")

    run = _StubRun({"report_path": str(report), "approval_status": "pending"})
    api_main.RUNS["TXDL"] = run

    for status in ("pending", "rejected"):
        run.state["approval_status"] = status
        resp = client.get("/api/pipeline/TXDL/report", params={"token": "test-token"})
        assert resp.status_code == 403, status

    run.state["approval_status"] = "approved"
    resp = client.get("/api/pipeline/TXDL/report", params={"token": "test-token"})
    assert resp.status_code == 200


def test_submit_decision_records_reviewer_and_time(api_client, stub_model_agents):
    client, api_main, tmp_path = api_client

    run = graph_builder.PipelineRun(_raw_state("TXREVIEW"), thread_id="TXREVIEW")
    run.run()
    assert run.state["decision"] == "REVIEW"  # name similarity → REVIEW
    api_main.RUNS["TXREVIEW"] = run

    resp = client.post("/api/pipeline/TXREVIEW/decision", json={"approval_status": "approved"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "approved"
    assert body["reviewed_by"] == "chuyenvien_a"
    assert body["reviewed_at"]
    assert run.state["reviewed_by"] == "chuyenvien_a"

    audit_lines = [json.loads(line) for line in (tmp_path / "audit_trail.log").read_text(encoding="utf-8").splitlines()]
    review_lines = [line for line in audit_lines if line.get("agent") == "human_review"]
    assert review_lines and review_lines[-1]["reviewed_by"] == "chuyenvien_a"
    assert review_lines[-1]["tx_hash"] == "TXREVIEW"


def test_run_pipeline_rejects_unsafe_or_duplicate_tx_hash(api_client):
    client, api_main, _ = api_client
    payload = {
        "wallet_from": "0x1111111111111111111111111111111111111111",
        "wallet_to": "0x2222222222222222222222222222222222222222",
        "amount_vnd": 1000,
        "fullname": "A",
        "id_number": "1",
        "account_number": "2",
    }

    resp = client.post("/api/pipeline/run", json={**payload, "tx_hash": "../../evil"})
    assert resp.status_code == 422

    api_main.RUNS["TXDUP"] = _StubRun({})
    resp = client.post("/api/pipeline/run", json={**payload, "tx_hash": "TXDUP"})
    assert resp.status_code == 409


# =============================================================================
# 6. Calibrate θ
# =============================================================================

def test_select_threshold_maximizes_precision_not_threshold():
    from agents.calibrate_classifier_threshold import select_threshold

    # 4 positive. Với target_recall=0.75:
    #   θ=0.5 (ngưỡng CAO NHẤT còn đạt recall — logic cũ): TP=3 FP=2 → P=0.60, R=0.75
    #   θ=0.4: TP=4 FP=2 → P≈0.667, R=1.0  ← precision cao hơn VÀ ít FN hơn
    y_true = np.array([1, 1, 0, 0, 1, 1])
    y_scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])

    chosen = select_threshold(y_true, y_scores, target_recall=0.75)

    assert chosen["threshold"] == pytest.approx(0.4)
    assert chosen["recall"] == pytest.approx(1.0)
    assert chosen["precision"] == pytest.approx(4 / 6)
