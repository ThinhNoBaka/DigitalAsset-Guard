"""
agents/transaction_classifier.py
Agent độc lập chấm điểm rủi ro sơ bộ từ đặc trưng giao dịch.

[FIX 2026-08-08 — NỐI FEATURE_BUILDER THẬT, BỎ MOCK VECTOR Ở PRODUCTION]
Lỗi cũ: feature vector đưa vào model là MOCK thủ công — chỉ gán
amount_vnd/1_000_000 vào slot index 0, các slot khác để 0. Với model mới
(37 feature, xem data/processed/feature_schema.json), slot 0 là
"Avg min between sent tnx" (feature THỜI GIAN) → số tiền giao dịch bị hiểu
nhầm thành thời gian trung bình giữa các tnx → model luôn cho score gần 1.0.

Từ nay:
- `analyze_transaction` BẮT BUỘC nhận `state["wallet_record"]` đúng schema
  {"address", "chains": {"ethereum": [txlist]}, "token_transfers": {"ethereum": [tokentx]}}
  (output của scripts/02_fetch_etherscan_sample.py) và build feature vector THẬT
  bằng scripts/feature_builder.build_full_wallet_features(), map sang mảng theo
  ĐÚNG thứ tự feature_schema.json / model.feature_names.
- Nhánh mock CŨ chỉ còn dành cho demo/test, phải truyền tường minh
  `allow_mock=True` (scripts/demo_runner.py, tests/). Mọi đường dẫn production
  (core/graph_builder.py, api/main.py) KHÔNG được phép rơi vào nhánh mock — nếu
  thiếu wallet_record thì raise NotImplementedError thay vì âm thầm điền 0.

[V2-2 -- THAY_DOI_V2.md] Explainability nâng lên mức PER-TRANSACTION bằng SHAP
(shap.TreeExplainer), thay cho feature_importances_ toàn cục của V1. Trường
`top_features` trong AMLState GIỮ NGUYÊN tên, chỉ đổi nguồn dữ liệu bên trong
(không tính là thêm trường mới -- xem core/state.py).

[V2-1 -- THAY_DOI_V2.md] Thêm 2 đặc trưng hành vi:
  - avg_time_between_tx: thời gian trung bình (giây) giữa các giao dịch liên
    tiếp của cùng 1 ví -- đặc trưng cho hành vi smurfing (chu kỳ đều đặn bất
    thường).
  - balance_clustering_flag: cờ nhị phân, bật khi ví nhận 1 khoản tiền rồi
    chuyển đi >= 90% số dư đó trong cùng 1 block hoặc trong khung thời gian
    rất ngắn (< 10 phút) -- đặc trưng điển hình của mixing service.

2 đặc trưng hành vi trên được ghi vào state (explainability) nhưng KHÔNG phải
input của model 37-feature — model đã có sẵn "Avg min between sent tnx" /
"Avg min between received tnx" trong feature_builder thật. Không còn nhồi
chúng vào slot 1/2 của vector mock như bản cũ.
"""
import json
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

try:
    import shap
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False

# Import chuẩn từ nền tảng đã dọn dẹp ở Phần 1 & 2
from core.privacy_layer import assert_no_raw_pii
from core.state import AMLState

# VIỆC 1: Nối feature_builder THẬT (scripts/feature_builder.py).
# `_empty_erc20_feature_set` dùng để lấy danh sách nhóm ERC20 numeric được phép
# thiếu/NaN → điền 0.0 (convention của feature_builder, xem docstring file đó).
from scripts.feature_builder import build_full_wallet_features, _empty_erc20_feature_set

MODEL_PATH = Path("models/xgboost_aml.pkl")
FEATURE_SCHEMA_PATH = Path("data/processed/feature_schema.json")
_model = None
_explainer = None  # cache SHAP TreeExplainer cùng vòng đời với _model


def load_model():
    """Load model từ đĩa, cache lại trên memory."""
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Không tìm thấy model tại {MODEL_PATH}")
        _model = joblib.load(MODEL_PATH)
    return _model


def _get_explainer(model):
    """Cache shap.TreeExplainer theo model đang dùng (tránh dựng lại mỗi giao dịch)."""
    global _explainer
    if _explainer is None:
        _explainer = shap.TreeExplainer(model)
    return _explainer


# =============================================================================
# --- VIỆC 1: Load feature schema + build feature vector THẬT ---
# =============================================================================

def load_feature_schema() -> list:
    """
    Đọc data/processed/feature_schema.json, trả về list dict theo thứ tự
    `order` tăng dần. Mỗi phần tử:
        {"schema_name": <key json>, "order": int, "production_feature_name": str, ...}
    `schema_name` chính là tên cột model đã train (khớp model.feature_names).
    `production_feature_name` là key trong dict trả về bởi build_full_wallet_features().
    """
    with open(FEATURE_SCHEMA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    ordered = [{"schema_name": name, **meta} for name, meta in raw.items()]
    ordered.sort(key=lambda e: e["order"])
    return ordered


def _get_model_feature_names(model) -> list:
    """
    Lấy ĐÚNG thứ tự feature lúc train. Ưu tiên model.feature_names (XGBoost
    lưu sẵn, khớp 100% thứ tự lúc train — chính là các key của
    feature_schema.json). Fallback về order trong feature_schema.json nếu model
    cũ không lưu feature_names.
    """
    booster = model.get_booster()
    if booster.feature_names:
        return list(booster.feature_names)
    return [e["schema_name"] for e in load_feature_schema()]


def _build_feature_vector_from_wallet(wallet_record: dict, model) -> pd.DataFrame:
    """
    VIỆC 1 — Build feature vector THẬT từ wallet_record.

    1. Gọi build_full_wallet_features(wallet_record, chain="ethereum") → dict
       {production_feature_name: value}.
    2. Map dict sang mảng theo ĐÚNG thứ tự feature_schema.json:
       - Lấy thứ tự từ model.feature_names (nếu model lưu sẵn) — đây là các key
         của feature_schema.json, đã đúng thứ tự `order` lúc train.
       - Fallback về order đọc từ file JSON.
       Không hard-code bất kỳ index nào.
    3. Nếu thiếu feature KHÔNG thuộc nhóm ERC20 numeric → raise ValueError rõ
       ràng, KHÔNG âm thầm điền 0. Nhóm ERC20 numeric thiếu → điền 0.0 (convention
       _empty_erc20_feature_set() trong feature_builder.py).

    Trả về DataFrame 1 dòng, cột = đúng tên feature model đã train.
    """
    if not isinstance(wallet_record, dict) or "address" not in wallet_record:
        raise ValueError(
            "wallet_record không đúng schema. Cần dict dạng "
            "{'address': str, 'chains': {'ethereum': [txlist]}, "
            "'token_transfers': {'ethereum': [tokentx]}} — xem output của "
            "scripts/02_fetch_etherscan_sample.py."
        )

    # Bước 3a: feature dict THẬT từ data Etherscan.
    feature_dict = build_full_wallet_features(wallet_record, chain="ethereum")

    # Bước 3b: thứ tự feature — ưu tiên model.feature_names, fallback schema JSON.
    schema_entries = {e["schema_name"]: e for e in load_feature_schema()}
    model_feature_names = _get_model_feature_names(model)

    # An toàn: nếu model lưu feature_names thì phải khớp chính xác key của
    # feature_schema.json (cùng thứ tự). Lệch → raise, không đoán bừa.
    schema_names = list(schema_entries.keys())
    if model_feature_names != schema_names:
        raise RuntimeError(
            "Thứ tự feature của model KHÔNG khớp feature_schema.json. "
            f"model.feature_names (đầu) = {model_feature_names[:5]}, "
            f"schema keys (đầu) = {schema_names[:5]}."
        )

    allowed_erc20_zero = set(_empty_erc20_feature_set().keys())

    values = []
    missing_non_erc20 = []
    for schema_name in model_feature_names:
        prod_name = schema_entries[schema_name]["production_feature_name"]
        if prod_name not in feature_dict:
            if prod_name in allowed_erc20_zero:
                # Convention: ERC20 thiếu/không có dữ liệu = 0.0
                # (xem _empty_erc20_feature_set() trong feature_builder.py).
                values.append(0.0)
            else:
                missing_non_erc20.append(prod_name)
            continue
        values.append(float(feature_dict[prod_name]))

    # Bước 3c: KHÔNG âm thầm điền 0 cho feature bắt buộc (ngoài ERC20).
    if missing_non_erc20:
        raise ValueError(
            "build_full_wallet_features() thiếu feature bắt buộc có trong "
            "feature_schema.json: " + ", ".join(missing_non_erc20) +
            ". Không điền 0 cho feature ngoài nhóm ERC20 numeric — kiểm tra "
            "dữ liệu txlist/tokentx của wallet_record."
        )

    df_features = pd.DataFrame(np.asarray([values]), columns=model_feature_names)
    return df_features


def _assess_insufficient_data(wallet_record: dict) -> bool:
    """
    Kiểm tra "đủ dữ liệu on-chain" DỰA TRÊN DỮ LIỆU THÔ của wallet_record
    (không dựa trên feature đã tính — tránh lẫn với case ví CÓ hoạt động
    nhưng giá trị thật sự bằng 0).

    [FIX 2026-08-08 — AUDIT ZERO-TX WALLET]
    Bối cảnh: ví không có giao dịch nào (0 tx txlist + 0 tx tokentx) khiến
    build_full_wallet_features() trả về vector toàn 0.0, và XGBoost chấm điểm
    gần 1.0 (0.9997) — do đặc thù tập train Farrugia (ví illicit thường có ít
    hoạt động). Hệ thống có thể tự động REPORT một ví chỉ vì nó mới/chưa có
    lịch sử, không phải vì nó thật sự đáng ngờ.

    Điều kiện insufficient_data = True (đúng yêu cầu):
        Sent tnx == 0 VÀ Received_Tnx == 0 VÀ Total ERC20 tnxs == 0
    tức ví CHƯA TỪNG gửi/nhận ETH lẫn ERC20 trên Etherscan — không phải
    "ví có hoạt động nhưng giá trị nhỏ/bằng 0".

    Cách đếm dùng ĐÚNG quy ước feature_builder (scripts/feature_builder.py):
    - "Sent tnx": tx thành công (isError=="0") có from == address.
    - "Received Tnx": tx thành công (isError=="0") có to == address.
    - "Total ERC20 tnxs": len(token_transfers.ethereum).
    Nhưng KHÔNG dùng giá trị feature đã tính làm tiêu chí — chỉ đếm số lượng
    giao dịch THÔ (count), do đó ví có hoạt động (dù giá trị = 0) vẫn được
    coi là CÓ dữ liệu.
    """
    address = str(wallet_record.get("address", "")).lower()
    txs = wallet_record.get("chains", {}).get("ethereum", []) or []
    # Giữ nguyên quy ước feature_builder: chỉ tính giao dịch thành công.
    successful_txs = [tx for tx in txs if tx.get("isError", "0") == "0"]

    sent_count = sum(
        1 for tx in successful_txs if str(tx.get("from", "")).lower() == address
    )
    received_count = sum(
        1 for tx in successful_txs if str(tx.get("to", "")).lower() == address
    )
    erc20_count = len(wallet_record.get("token_transfers", {}).get("ethereum", []) or [])

    return sent_count == 0 and received_count == 0 and erc20_count == 0


def _build_mock_feature_vector(
    state: dict,
    avg_time_between_tx: Optional[float],
    balance_clustering_flag: Optional[bool],
    model,
) -> pd.DataFrame:
    """
    [DEMO/TEST ONLY] Tái hiện ĐÚNG logic mock CŨ (trước khi nối feature_builder).

    VIỆC 2 — Guard: hàm này CHỈ được gọi khi caller truyền `allow_mock=True`
    tường minh (scripts/demo_runner.py, tests/). KHÔNG bao giờ dùng trong
    production path (core/graph_builder.py, api/main.py).

    Lưu ý: gán amount_vnd vào slot 0 SAI về semantic với model 37-feature
    (slot 0 là "Avg min between sent tnx"), nhưng demo/test giữ nguyên hành vi
    cũ để so sánh trước/sau khi nối feature_builder.
    """
    num_features = model.n_features_in_
    mock_features = np.zeros((1, num_features))

    # Mô phỏng cũ: Gán amount_vnd đã quy đổi vào slot đầu tiên.
    mock_features[0, 0] = state.get("amount_vnd", 0) / 1_000_000

    # Gán 2 đặc trưng hành vi vào slot 1/2 (như bản cũ) — KHÔNG gán nếu None.
    if num_features > 1 and avg_time_between_tx is not None:
        mock_features[0, 1] = avg_time_between_tx
    if num_features > 2 and balance_clustering_flag is not None:
        mock_features[0, 2] = float(balance_clustering_flag)

    feature_names = _get_model_feature_names(model)

    return pd.DataFrame(mock_features, columns=feature_names)


# =============================================================================
# --- [V2-1] Feature engineering hành vi ---
# =============================================================================

def _compute_avg_time_between_tx(tx_timestamps: list) -> Optional[float]:
    """
    Thời gian trung bình (giây) giữa các giao dịch liên tiếp của cùng 1 ví.

    Input: tx_timestamps -- list[float|int] các mốc thời gian (unix epoch,
    giây) của các giao dịch mà ví đó tham gia (không cần sắp xếp sẵn).
    Trả về None nếu không đủ dữ liệu (< 2 giao dịch) để tính khoảng cách.
    """
    if not tx_timestamps or len(tx_timestamps) < 2:
        return None
    sorted_ts = sorted(float(t) for t in tx_timestamps)
    diffs = [t2 - t1 for t1, t2 in zip(sorted_ts, sorted_ts[1:])]
    return sum(diffs) / len(diffs)


def _compute_balance_clustering_flag(
    wallet_history: list,
    *,
    threshold_pct: float = 0.9,
    window_seconds: int = 600,
) -> Optional[bool]:
    """
    Cờ mixing-service: bật khi ví nhận 1 khoản tiền rồi chuyển đi >= threshold_pct
    số dư đó trong cùng 1 block HOẶC trong window_seconds giây sau đó.

    Input: wallet_history -- list[dict], mỗi phần tử:
        {"timestamp": <unix epoch giây>, "direction": "in" | "out",
         "amount": <float>, "block": <int, optional>}
    Trả về None nếu không có dữ liệu; False nếu có dữ liệu nhưng không khớp mẫu.
    """
    if not wallet_history:
        return None

    incoming = [tx for tx in wallet_history if tx.get("direction") == "in"]
    outgoing = [tx for tx in wallet_history if tx.get("direction") == "out"]
    if not incoming or not outgoing:
        return False

    for in_tx in incoming:
        in_amount = float(in_tx.get("amount", 0) or 0)
        if in_amount <= 0:
            continue
        in_ts = in_tx.get("timestamp")
        in_block = in_tx.get("block")

        # Gom các khoản ra trong cùng block HOẶC trong window_seconds sau khi nhận
        matched_out_total = 0.0
        for out_tx in outgoing:
            same_block = in_block is not None and out_tx.get("block") == in_block
            within_window = (
                in_ts is not None
                and out_tx.get("timestamp") is not None
                and 0 <= (out_tx["timestamp"] - in_ts) <= window_seconds
            )
            if same_block or within_window:
                matched_out_total += float(out_tx.get("amount", 0) or 0)

        if in_amount > 0 and (matched_out_total / in_amount) >= threshold_pct:
            return True

    return False


def analyze_transaction(state: AMLState, *, allow_mock: bool = False) -> AMLState:
    """
    Nhận AMLState, build feature vector TỪ WALLET_RECORD THẬT (Việc 1 —
    feature_builder đã nối), chấm classifier_score + top_features (SHAP
    per-transaction, V2-2) + 2 đặc trưng hành vi (V2-1).

    Việc 2 — Guard mock:
    - `allow_mock` mặc định False (bắt buộc tường minh).
    - Nếu state không có `wallet_record` và allow_mock=False → raise
      NotImplementedError kèm message rõ ràng. KHÔNG âm thầm dùng mock vector.
    - Chỉ demo/test (scripts/demo_runner.py, tests/) mới được truyền
      allow_mock=True một cách tường minh tại code gọi.
    - Mọi production path (core/graph_builder.py, api/main.py) đi qua Việc 1;
      nếu chưa nối dây wallet_record thì production path LỖI RÕ RÀNG thay vì
      cho score giả từ mock.
    """
    # 1. CHỐT KIỂM TRA BẮT BUỘC: Raise Error ngay nếu lọt PII gốc
    assert_no_raw_pii(state)

    # 2. Load model
    model = load_model()

    # 3. [V2-1] Tính 2 đặc trưng hành vi cho state EXPLAINABILITY — KHÔNG phải
    #    input của model 37-feature (model đã có feature thời gian riêng trong
    #    feature_builder thật). None nếu không có wallet_tx_history.
    wallet_history = state.get("wallet_tx_history")
    avg_time_between_tx = (
        _compute_avg_time_between_tx(
            [tx.get("timestamp") for tx in wallet_history if tx.get("timestamp") is not None]
        )
        if wallet_history
        else None
    )
    balance_clustering_flag = _compute_balance_clustering_flag(wallet_history) if wallet_history else None

    # 4. VIỆC 1 — Build feature vector:
    #    - Production path: bắt buộc có wallet_record → feature_builder THẬT.
    #    - Demo/test: nếu không có wallet_record, CHỈ được dùng mock khi caller
    #      truyền allow_mock=True.
    wallet_record = state.get("wallet_record")
    if wallet_record is None:
        if not allow_mock:
            raise NotImplementedError(
                "Không tìm thấy wallet_record trong state — production path bắt "
                "buộc đi qua feature_builder thật (build_full_wallet_features), "
                "KHÔNG được dùng mock feature vector. Truyền state['wallet_record'] "
                "đúng schema {'address': str, 'chains': {'ethereum': [txlist]}, "
                "'token_transfers': {'ethereum': [tokentx]}} (xem output của "
                "scripts/02_fetch_etherscan_sample.py) — hoặc, CHỈ trong demo/test, "
                "gọi analyze_transaction(state, allow_mock=True) một cách tường minh."
            )
        df_features = _build_mock_feature_vector(
            state, avg_time_between_tx, balance_clustering_flag, model
        )
        # Mock path (demo/test): không có dữ liệu on-chain thô để đánh giá →
        # mặc định coi là đủ dữ liệu (False), KHÔNG đổi hành vi demo cũ.
        state["insufficient_data"] = False
    else:
        df_features = _build_feature_vector_from_wallet(wallet_record, model)
        # [FIX 2026-08-08 — AUDIT ZERO-TX WALLET] Kiểm tra "đủ dữ liệu" TRÊN DỮ
        # LIỆU THÔ (count tx/tokentx), KHÔNG dựa trên feature đã tính. Khi ví
        # chưa từng gửi/nhận ETH lẫn ERC20 (0 tx cả 2 nguồn) → insufficient_data
        # = True để Decision Engine KHÔNG dùng classifier_score (vốn bị model
        # chấm ~1.0 cho vector toàn 0) tự động REPORT. VẪN chấm classifier_score
        # bên dưới để giữ tham khảo/audit — không raise, không crash pipeline.
        state["insufficient_data"] = _assess_insufficient_data(wallet_record)

    feature_names = list(df_features.columns)

    # 5. Chấm điểm rủi ro (lấy xác suất thuộc class 1 - illicit)
    risk_score = float(model.predict_proba(df_features)[0, 1])

    # 6. [V2-2 -- Explainable AI PER-TRANSACTION bằng SHAP, thay feature_importances_]
    # KHÁC V1: đây không còn là mức độ quan trọng TOÀN CỤC của model, mà là
    # đóng góp thật của từng đặc trưng cho ĐÚNG giao dịch đang xét (df_features,
    # 1 dòng). shap_value dương = đẩy risk_score lên, âm = kéo xuống -- được
    # phép và NÊN hiển thị cả giá trị âm, không lọc để "kể câu chuyện đẹp".
    top_features = []
    if not SHAP_AVAILABLE:
        print("⚠️ Chưa cài `shap` (pip install shap) -- bỏ qua top_features cho giao dịch này.")
    else:
        try:
            explainer = _get_explainer(model)
            shap_values = explainer.shap_values(df_features)
            # TreeExplainer trên XGBClassifier nhị phân: tuỳ phiên bản shap trả
            # về mảng (1, n_features) cho class dương, hoặc list [class0, class1].
            if isinstance(shap_values, list):
                sv_row = np.asarray(shap_values[1])[0]
            else:
                sv_row = np.asarray(shap_values)[0]

            ranked = sorted(
                zip(feature_names, sv_row), key=lambda x: abs(float(x[1])), reverse=True
            )
            top_features = [(name, round(float(val), 4)) for name, val in ranked[:3]]
        except Exception as e:
            print(f"⚠️ Lỗi khi tính SHAP values (bỏ qua top_features): {e}")
            top_features = []

    # 7. Cập nhật state và trả về
    # SPEC_v2 §2: đổi tên risk_score_classifier -> classifier_score (breaking change).
    state["classifier_score"] = risk_score
    state["top_features"] = top_features
    state["avg_time_between_tx"] = avg_time_between_tx
    state["balance_clustering_flag"] = balance_clustering_flag

    return state


if __name__ == "__main__":
    # Test độc lập: Tạo state giả lập ĐÃ QUA Privacy Layer.
    # wallet_record dùng sample trong __main__ của scripts/feature_builder.py
    # (đúng schema output của scripts/02_fetch_etherscan_sample.py) → đi qua
    # feature_builder THẬT (Việc 1), KHÔNG dùng mock.
    test_state = AMLState(
        tx_hash="0xabcd1234",
        wallet_from="0x11112222",
        wallet_to="0x33334444",
        amount_vnd=550_000_000,
        hashed_fullname="e3b0c44298fc1c14",
        hashed_id_number="a591a6d40bf42040",
        hashed_account_number="2c26b46b68ffc68f",
        classifier_score=None,
        graph_score=None,
        sanction_result=None,
        legal_citations=None,
        risk_assessment_score=None,
        report_path=None,
        approval_status=None,
    )
    test_state["wallet_record"] = {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "chains": {
            "ethereum": [
                {
                    "timeStamp": "1619073240", "from": "0x00799bbc833d5b168f0410312d2a8fd9e0e3079c",
                    "to": "0x28c6c06298d514db089934071355e5743bf21d60",
                    "value": "1000000000000000000", "input": "0x",
                    "contractAddress": "", "isError": "0",
                },
                {
                    "timeStamp": "1619091803", "from": "0x28c6c06298d514db089934071355e5743bf21d60",
                    "to": "0x0cf0ee63788a0849fe5297f3407f701e122cc023",
                    "value": "0",
                    "input": "0xa9059cbb000000000000000000000000b7b544f4fcb62941f8d6fbcc61e0265c6ae4462600000000000000000000000000000000000000000000000089e917994f71c0000",
                    "contractAddress": "", "isError": "0",
                },
            ]
        },
        "token_transfers": {
            "ethereum": [
                {
                    "timeStamp": "1619091803", "from": "0x28c6c06298d514db089934071355e5743bf21d60",
                    "to": "0xb7b544f4fcb62941f8d6fbcc61e0265c6ae4462",
                    "value": "10000000000000000000", "tokenName": "DATAcoin",
                    "tokenSymbol": "DATA", "tokenDecimal": "18",
                },
            ]
        },
    }
    # Lịch sử ví mẫu để test 2 đặc trưng hành vi (V2-1) -- không phải trường
    # chuẩn của AMLState, chỉ dùng cho test độc lập ở đây.
    test_state["wallet_tx_history"] = [
        {"timestamp": 1_700_000_000, "direction": "in", "amount": 500_000_000, "block": 100},
        {"timestamp": 1_700_000_300, "direction": "out", "amount": 480_000_000, "block": 100},
        {"timestamp": 1_700_003_600, "direction": "in", "amount": 10_000_000, "block": 101},
    ]

    print("Đang chạy thử Transaction Agent (feature_builder THẬT)...")
    updated_state = analyze_transaction(test_state)
    print(f"Hoàn thành! Điểm rủi ro sơ bộ (classifier_score): {updated_state['classifier_score']:.4f}")
    print(f"Top features (SHAP per-transaction, V2-2): {updated_state.get('top_features')}")
    print(f"avg_time_between_tx (V2-1): {updated_state.get('avg_time_between_tx')}")
    print(f"balance_clustering_flag (V2-1): {updated_state.get('balance_clustering_flag')}")