"""
scripts/demo_runner.py -- [V2-4, THAY_DOI_V2.md] Chạy tự động cả 3 kịch bản
mock (data/mock/scenario_*.json, sinh bởi scripts/generate_complex_mock.py)
qua TOÀN BỘ pipeline AML (Privacy Layer -> Classifier -> Graph -> Sanctions
-> Decision Engine -> RAG -> Report) tới điểm chờ duyệt, in ra kết quả để
đối chiếu với "expected" trong từng file kịch bản.

*** VÌ SAO KHÔNG DÙNG core.graph_builder.PipelineRun TRỰC TIẾP ***
Ghi chú vận hành mô tả 1 cổng chặn ngưỡng webhook (giao dịch dưới
REPORT_THRESHOLD_VND bị SKIP hoàn toàn) cho luồng sản xuất thật. Cổng chặn
đó (nếu có) nằm ở tầng webhook/API, KHÔNG nằm trong core.graph_builder.py
đã cung cấp -- PipelineRun ở đây bắt đầu thẳng từ privacy_layer, không tự
lọc theo amount_vnd. Dù vậy demo_runner.py vẫn KHÔNG dùng PipelineRun mà
gọi trực tiếp từng Assistant, để (a) không phụ thuộc LangGraph
checkpointer/interrupt khi chỉ cần chạy 1 lượt in kết quả, và (b) giữ ý đồ
gốc: kịch bản Smurfing cố tình chia nhỏ giao dịch để né ngưỡng báo cáo đơn
lẻ -- việc demo chạy thẳng qua AI Core (không qua bất kỳ cổng chặn ngưỡng
nào) mới chứng minh được Graph Assistant phát hiện ra pattern nhiều giao
dịch nhỏ cộng dồn, đúng mục đích kịch bản.

=> demo_runner.py gọi trực tiếp privacy_layer_node() (core/privacy_layer.py)
+ từng Assistant theo ĐÚNG thứ tự SPEC_v2 §1 (Classifier -> Graph ->
Sanctions -> Decision Engine -> RAG -> Report), có audit logging qua
core.audit_logger.timed_step. Đây là lựa chọn có chủ đích để demo khả năng
CỦA AI CORE, không phải để thay thế logic trigger production -- ghi rõ ở
đây để hội đồng/người bảo trì không hiểu nhầm đây là cách hệ thống chạy
trong thực tế (thực tế cần giám sát đồ thị liên tục, không chỉ trigger theo
từng giao dịch đơn lẻ vượt ngưỡng -- xem "Hướng phát triển" trong báo cáo).

Chạy: python -m scripts.demo_runner (từ thư mục gốc dự án, sau khi đã có
model tại models/xgboost_aml.pkl và chạy scripts/generate_complex_mock.py).
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict

from core.privacy_layer import assert_no_raw_pii, privacy_layer_node
from core.audit_logger import timed_step

from agents.transaction_classifier import analyze_transaction
from agents.graph_aml import analyze_graph
from agents.kyc_verification import verify_kyc
from agents.decision_engine import make_decision
from agents.regulation_rag import run_regulation_rag
from agents.alert_report import generate_alert_report

MOCK_DIR = Path("data/mock")
SCENARIO_FILES = [
    "scenario_smurfing.json",
    "scenario_layering.json",
    "scenario_name_similarity.json",
]


def _run_full_pipeline(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Chạy 1 kịch bản qua toàn bộ pipeline, trả về state cuối cùng."""
    evaluated_tx = dict(scenario["evaluated_transaction"])
    scenario_name = scenario["scenario"]
    evaluated_tx["tx_hash"] = evaluated_tx.get("tx_hash") or f"DEMO_{scenario_name}"

    # Bước 1: Privacy Layer (bao gồm fuzzy name-matching -- core/name_screening.py
    # -- chạy TRƯỚC khi băm, xem core/privacy_layer.py::privacy_layer_node)
    state = privacy_layer_node(evaluated_tx)

    # Bước 1b: bơm dữ liệu đồ thị kịch bản mock vào state -- KHÔNG phải PII,
    # hợp lệ để thêm sau assert_no_raw_pii (xem agents/graph_aml.py để biết
    # 2 key này được dùng thế nào). Đã khai báo trong core/state.py dưới tên
    # mock_graph_edges / mock_blacklisted_wallets.
    state["mock_graph_edges"] = [tuple(edge) for edge in scenario.get("graph_edges", [])]
    state["mock_blacklisted_wallets"] = scenario.get("blacklisted_wallets", [])

    tx_hash = state.get("tx_hash")

    # Bước 2: Transaction Classifier
    state = timed_step("transaction_classifier", tx_hash, analyze_transaction, state)

    # Bước 3: Graph Assistant (đọc mock_graph_edges/mock_blacklisted_wallets ở trên).
    # SPEC_v2 §1: Graph chạy TRƯỚC Sanctions -- current_wallet_is_sanctioned tự
    # query blacklisted độc lập, không cần đợi kyc_verification.verify_kyc chạy
    # trước (xem ghi chú "Về việc Graph chạy trước Sanctions" trong SPEC_v2 §1).
    state = timed_step("graph_aml", tx_hash, analyze_graph, state)

    # Bước 4: Sanctions Assistant (file kyc_verification.py, đổi logic bên trong
    # theo SPEC_v2 §3 -- trả sanction_result, KHÔNG trả risk số nào).
    state = timed_step("sanctions", tx_hash, verify_kyc, state)

    # Bước 5: Decision Engine -- MODULE MỚI, đọc classifier_score/graph_score/
    # sanction_result/name_similarity_warning, ghi decision/case_status vào
    # state. Đây là bước trước đây demo_runner.py bỏ sót hoàn toàn, khiến
    # alert_report phía dưới luôn thấy case_status=None -> không bao giờ sinh
    # STR bất kể rủi ro cao thế nào.
    state = timed_step("decision_engine", tx_hash, make_decision, state)

    # Bước 6-7: RAG -> Report Assistant -- CHỈ chạy khi decision == "REPORT"
    # (đồng bộ với core.graph_builder._route_after_decision). REVIEW dừng lại
    # ngay sau Decision Engine, chờ chuyên viên xem decision_evidence -- KHÔNG
    # tự soạn sẵn dự thảo STR (case còn mơ hồ, có thể chuyên viên đóng hồ sơ).
    # PASS cũng dừng tại đây. alert_report.py KHÔNG tự tính điểm hay tự quyết
    # định gì (SPEC_v2 §6) -- điểm dừng của demo tuỳ theo decision.
    if state.get("decision") == "REPORT":
        state = timed_step("regulation_rag", tx_hash, run_regulation_rag, state)
        state = timed_step("alert_report", tx_hash, generate_alert_report, state)

    return state


def _print_scenario_result(scenario: Dict[str, Any], state: Dict[str, Any]) -> None:
    print(f"\n{'=' * 70}")
    print(f"KỊCH BẢN: {scenario['scenario'].upper()}")
    print(f"{'=' * 70}")
    print(f"Mô tả: {scenario['description']}")
    print(f"Kỳ vọng: {json.dumps(scenario.get('expected', {}), ensure_ascii=False, indent=2)}")
    print("-" * 70)
    print(f"tx_hash                    : {state.get('tx_hash')}")
    print(f"classifier_score           : {state.get('classifier_score')}")
    print(f"graph_score                : {state.get('graph_score')}")
    print(f"top_features (SHAP)        : {state.get('top_features')}")
    print(f"sanction_result            : {state.get('sanction_result')}")
    print(f"current_wallet_is_sanctioned: {state.get('current_wallet_is_sanctioned')}")
    print(f"name_similarity_warning    : {state.get('name_similarity_warning')}")
    print(f"name_similarity_score      : {state.get('name_similarity_score')}")
    print(f"community_id               : {state.get('community_id')}")
    print(f"hop_distance_to_blacklist  : {state.get('hop_distance_to_blacklist')}")
    print(f"fan_out                    : {state.get('fan_out')}")
    print(f"suspicious_path            : {state.get('suspicious_path')}")
    print(f"decision                   : {state.get('decision')}")
    print(f"decision_reason            : {state.get('decision_reason')}")
    print(f"decision_evidence          : {state.get('decision_evidence')}")
    print(f"case_status                : {state.get('case_status')}")
    print(f"report_path                : {state.get('report_path')}")
    print(f"approval_status            : {state.get('approval_status')}")
    if state.get("case_status") == "pending_review":
        print("  -> STR đã tạo, CHỜ chuyên viên duyệt (HITL).")
    print("-" * 70)

    # Chốt an toàn: không log/in raw PII kể cả trong demo
    try:
        assert_no_raw_pii(state)
    except ValueError as e:
        print(f"  [CẢNH BÁO NGHIÊM TRỌNG] {e}")


def main():
    missing = [f for f in SCENARIO_FILES if not (MOCK_DIR / f).exists()]
    if missing:
        print("LỖI: Thiếu file kịch bản mock:", missing)
        print("Chạy trước: python -m scripts.generate_complex_mock")
        sys.exit(1)

    for filename in SCENARIO_FILES:
        with open(MOCK_DIR / filename, "r", encoding="utf-8") as f:
            scenario = json.load(f)
        try:
            state = _run_full_pipeline(scenario)
            _print_scenario_result(scenario, state)
        except FileNotFoundError as e:
            print(f"\n[BỎ QUA] Kịch bản '{scenario.get('scenario')}' lỗi thiếu file: {e}")
        except Exception as e:
            print(f"\n[LỖI] Kịch bản '{scenario.get('scenario')}' chạy thất bại: {e}")

    print(f"\n{'=' * 70}\nHoàn tất demo 3 kịch bản. Xem logs/audit_trail.log để đối chiếu "
          f"audit trail (V2-6) của từng bước.\n{'=' * 70}")


if __name__ == "__main__":
    main()