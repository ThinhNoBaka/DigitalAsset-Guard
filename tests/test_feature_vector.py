"""
tests/test_feature_vector.py -- Kiểm thử build feature vector THẬT (Việc 1).

Mục tiêu: đảm bảo `_build_feature_vector_from_wallet` trong
agents/transaction_classifier.py map dict feature từ
scripts/feature_builder.build_full_wallet_features() sang mảng theo ĐÚNG thứ
tự trong data/processed/feature_schema.json (không hard-code index).

Input dùng sample wallet_record trong __main__ của scripts/feature_builder.py
(đúng schema output của scripts/02_fetch_etherscan_sample.py).
"""
import pytest

import pandas as pd

from agents.transaction_classifier import (
    load_feature_schema,
    _get_model_feature_names,
    _build_feature_vector_from_wallet,
    _build_mock_feature_vector,
    load_model,
)
from scripts.feature_builder import build_full_wallet_features


# =============================================================================
# Sample wallet_record — GIỮ NGUYÊN từ __main__ của scripts/feature_builder.py
# =============================================================================

SAMPLE_WALLET_RECORD = {
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


@pytest.fixture(scope="module")
def model():
    return load_model()


def test_feature_schema_json_has_37_ordered_features():
    """feature_schema.json phải có đủ 37 feature, order 0..36 liên tục, key trùng model."""
    schema = load_feature_schema()
    model = load_model()
    assert len(schema) == 37
    assert [e["order"] for e in schema] == list(range(37))
    model_names = _get_model_feature_names(model)
    assert [e["schema_name"] for e in schema] == model_names


def test_feature_builder_returns_all_production_feature_names():
    """build_full_wallet_features trả về đủ key trùng production_feature_name của schema."""
    feature_dict = build_full_wallet_features(SAMPLE_WALLET_RECORD, chain="ethereum")
    schema = load_feature_schema()
    missing = [
        e["production_feature_name"]
        for e in schema
        if e["production_feature_name"] not in feature_dict
    ]
    assert missing == []


def test_build_feature_vector_matches_schema_order():
    """Output vector phải đúng thứ tự feature_schema.json — không hard-code index."""
    model = load_model()
    df = _build_feature_vector_from_wallet(SAMPLE_WALLET_RECORD, model)

    # 1 dòng, đủ 37 cột, tên cột = key feature_schema.json theo đúng order.
    assert isinstance(df, pd.DataFrame)
    assert df.shape == (1, 37)
    assert list(df.columns) == [e["schema_name"] for e in load_feature_schema()]

    # So khớp giá trị từng cột với dict feature thật (production_feature_name).
    feature_dict = build_full_wallet_features(SAMPLE_WALLET_RECORD, chain="ethereum")
    schema = load_feature_schema()
    for i, entry in enumerate(schema):
        expected = float(feature_dict[entry["production_feature_name"]])
        assert df.iloc[0, i] == pytest.approx(expected), (
            f"Feature '{entry['schema_name']}' tại cột {i} không khớp "
            f"build_full_wallet_features() -> {expected}"
        )


def test_build_feature_vector_zero_for_empty_wallet():
    """
    Wallet không có txlist/tokentx: toàn bộ 37 feature = 0.0 (feature_builder
    trả _empty_feature_set + _empty_erc20_feature_set, không raise).
    """
    model = load_model()
    empty_wallet = {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "chains": {"ethereum": []},
        "token_transfers": {"ethereum": []},
    }
    df = _build_feature_vector_from_wallet(empty_wallet, model)
    assert df.shape == (1, 37)
    assert list(df.columns) == [e["schema_name"] for e in load_feature_schema()]
    assert (df.iloc[0] == 0.0).all()


def test_build_feature_vector_raises_on_missing_non_erc20():
    """
    Nếu build_full_wallet_features THIẾU feature ngoài nhóm ERC20 -> raise
    ValueError rõ ràng, KHÔNG âm thầm điền 0.
    """
    import agents.transaction_classifier as tc

    model = load_model()

    class _BrokenBuilder:
        def __call__(self, wallet_record, chain="ethereum"):
            d = build_full_wallet_features(wallet_record, chain=chain)
            d.pop("Avg min between sent tnx", None)
            return d

    original = tc.build_full_wallet_features
    tc.build_full_wallet_features = _BrokenBuilder()
    try:
        with pytest.raises(ValueError) as excinfo:
            _build_feature_vector_from_wallet(SAMPLE_WALLET_RECORD, model)
        assert "Avg min between sent tnx" in str(excinfo.value)
        assert "Không điền 0" in str(excinfo.value)
    finally:
        tc.build_full_wallet_features = original


def test_mock_feature_vector_columns_match_schema():
    """
    Hàm mock (demo/test only) trả đúng số cột/tên cột như feature_schema.json.
    Guard chặn dùng mock ở production được test riêng trong test_classifier.py.
    """
    model = load_model()
    state = {"amount_vnd": 550_000_000.0}
    df = _build_mock_feature_vector(state, None, None, model)
    assert df.shape == (1, 37)
    assert list(df.columns) == [e["schema_name"] for e in load_feature_schema()]
    # Slot 0 = amount/1e6 = 550.0 (hành vi mock cũ, chỉ để demo/test so sánh).
    assert df.iloc[0, 0] == pytest.approx(550.0)