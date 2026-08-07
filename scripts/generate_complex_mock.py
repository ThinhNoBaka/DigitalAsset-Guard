"""
scripts/generate_complex_mock.py -- [V2-4, THAY_DOI_V2.md] Sinh 3 kịch bản mock
nâng cao cho demo trước hội đồng: Smurfing, Layering, Name Similarity.

Mỗi kịch bản xuất 1 file JSON riêng tại data/mock/scenario_<tên>.json, cấu trúc:
{
  "scenario": "smurfing" | "layering" | "name_similarity",
  "description": "...",
  "legs": [ {"from", "to", "amount_vnd", "timestamp"}, ... ],   # TOÀN BỘ giao
      dịch thật của kịch bản (để tham khảo/audit/demo trực quan hoá đồ thị)
  "graph_edges": [ [from, to, amount_vnd], ... ],   # dùng làm mock_graph_edges
      bơm vào agents/graph_aml.py (đã tổng hợp trùng cạnh -- xem hàm build)
  "blacklisted_wallets": [...],                     # dùng làm mock_blacklisted_wallets
  "evaluated_transaction": {                        # giao dịch THẬT chạy qua
      "tx_hash", "wallet_from", "wallet_to", "amount_vnd",
      "fullname", "id_number", "account_number"      # cả pipeline (Privacy
  },                                                  # Layer -> 5 Assistant)
  "expected": {...}   # kỳ vọng để scripts/demo_runner.py đối chiếu khi in bảng
}

Chạy: python -m scripts.generate_complex_mock (từ thư mục gốc dự án)
"""
import json
from pathlib import Path

OUT_DIR = Path("data/mock")

# Khách hàng "sạch" mượn lại từ data/mock/customers.json (Phần 3 mục 5) để
# demo dùng chung 1 nguồn dữ liệu khách hàng nhất quán, không bịa thêm.
_FILLER_CUSTOMER = {
    "fullname": "Nguyen Van A",
    "id_number": "012345678901",
    "account_number": "1000000001",
}

# Entry THẬT lấy từ data/raw/sdn.xml mẫu (uid 6707, Individual, chương trình
# SDNTK) -- dùng để kịch bản Name Similarity so khớp với 1 entity CÓ THẬT
# trong sdn.xml, đúng yêu cầu THAY_DOI_V2.md V2-4 (không phải tên tự bịa).
_REAL_SDN_NAME = "Rafael CARO QUINTERO"


def _aggregate_edges(legs: list) -> list:
    """Gộp các leg trùng (from, to) thành 1 cạnh tổng amount -- đồ thị NetworkX
    dùng nx.DiGraph (không phải MultiDiGraph), nên nhiều giao dịch nhỏ liên
    tiếp giữa cùng 1 cặp ví được cộng dồn vào 1 cạnh có trọng số = tổng tiền."""
    totals = {}
    for leg in legs:
        key = (leg["from"], leg["to"])
        totals[key] = totals.get(key, 0) + leg["amount_vnd"]
    return [[u, v, amount] for (u, v), amount in totals.items()]


def build_smurfing_scenario() -> dict:
    """
    1 giao dịch gốc 2 tỷ VND bị chia thành 20 giao dịch nhỏ (mỗi giao dịch
    dưới 500tr để né REPORT_THRESHOLD_VND), gửi vào 10 ví trung gian, sau đó
    cả 10 ví trung gian gộp dòng tiền về lại 1 ví đích.
    Mục tiêu: Graph Assistant (Louvain) phát hiện 10 ví trung gian cùng 1
    cộng đồng + dòng tiền hội tụ (xem agents/graph_aml.py resolution=0.5).
    """
    source = "0xsmurf_source_wallet"
    dest = "0xsmurf_convergence_dest"
    base_ts = 1_735_000_000  # mốc thời gian mock cố định (để kết quả tái lập được)

    legs = []
    for i in range(1, 11):
        mid = f"0xsmurf_mid_{i:02d}"
        # 2 giao dịch/ví trung gian = 20 giao dịch, mỗi giao dịch < 500tr
        legs.append({"from": source, "to": mid, "amount_vnd": 95_000_000 + i * 100_000,
                     "timestamp": base_ts + i * 60})
        legs.append({"from": source, "to": mid, "amount_vnd": 95_000_000 + i * 50_000,
                     "timestamp": base_ts + i * 60 + 30})
        # gộp dòng tiền về 1 ví đích, cũng < 500tr/giao dịch (né ngưỡng ở mỗi hop)
        legs.append({"from": mid, "to": dest, "amount_vnd": 185_000_000 + i * 100_000,
                     "timestamp": base_ts + 3600 + i * 60})

    # Giao dịch được đánh giá qua pipeline: 1 leg hội tụ tiêu biểu (mid_03 -> dest)
    evaluated = {
        "tx_hash": None,
        "wallet_from": "0xsmurf_mid_03",
        "wallet_to": dest,
        "amount_vnd": 185_300_000,
        **_FILLER_CUSTOMER,
    }

    return {
        "scenario": "smurfing",
        "description": (
            "1 giao dịch gốc 2 tỷ VND bị chia thành 20 giao dịch nhỏ (mỗi cái < 500tr "
            "để né ngưỡng báo cáo đơn lẻ Thông tư 27), gửi vào 10 ví trung gian, sau đó "
            "gộp dòng tiền về lại 1 ví đích."
        ),
        "legs": legs,
        "graph_edges": _aggregate_edges(legs),
        "blacklisted_wallets": [],  # kịch bản này test Louvain (community), không test PPR/blacklist
        "evaluated_transaction": evaluated,
        "expected": {
            "graph_assistant": (
                "10 ví 0xsmurf_mid_01..10 phải nằm CHUNG 1 community_id với "
                f"{dest} (Louvain resolution=0.5 cho đồ thị mock, xem agents/graph_aml.py). "
                "blacklisted_wallets rỗng nên hop_distance=None, graph_score=0.0 (không có "
                "ví đen để PPR personalization) — kịch bản này chủ đích test Louvain, không test "
                "graph exposure."
            ),
        },
    }


def build_layering_scenario() -> dict:
    """
    Dòng tiền đi qua 4 cấp độ ví trung gian (gắn nhãn 'sàn DEX' khác nhau)
    trước khi tới ví ngân hàng đích. Mục tiêu: PPR phải lan truyền rủi ro từ
    ví nguồn (gắn cờ đen) qua 4 hop tới ví đích, dù ví đích chưa từng có lịch
    sử vi phạm trực tiếp (sanction_result.is_match=False).
    """
    source_black = "0xlayering_source_blacklisted"
    dex_chain = [f"0xlayering_dex{i}" for i in range(1, 5)]  # 4 "sàn DEX"
    bank_target = "0xlayering_bank_target_wallet"
    base_ts = 1_735_100_000

    legs = []
    amount = 900_000_000
    prev = source_black
    for i, dex in enumerate(dex_chain, start=1):
        legs.append({"from": prev, "to": dex, "amount_vnd": amount, "timestamp": base_ts + i * 3600})
        prev = dex
        amount = int(amount * 0.97)  # trừ phí/slippage nhẹ qua mỗi hop, vẫn thực tế
    legs.append({"from": prev, "to": bank_target, "amount_vnd": amount, "timestamp": base_ts + 5 * 3600})

    # Giao dịch được đánh giá: hop cuối cùng, TỪ dex4 (KHÔNG PHẢI source_black)
    # -- đúng ý "ví đích chưa từng có lịch sử vi phạm trực tiếp": wallet_from
    # (dex4) không nằm trong danh sách blacklisted, sanctions exact-match trả
    # is_match=False, nhưng hop_distance_to_blacklist=4 cho thấy gần ví đen
    # (đủ để Review/Graph đánh giá, không kích hoạt Rule 4 vì > 2).
    evaluated = {
        "tx_hash": None,
        "wallet_from": dex_chain[-1],
        "wallet_to": bank_target,
        "amount_vnd": amount,
        **_FILLER_CUSTOMER,
    }

    return {
        "scenario": "layering",
        "description": (
            "Dòng tiền đi qua 4 cấp độ ví trung gian (giả lập các 'sàn DEX' khác nhau) "
            "trước khi tới ví ngân hàng đích -- nguồn bị gắn cờ đen, đích thì chưa từng "
            "vi phạm trực tiếp."
        ),
        "legs": legs,
        "graph_edges": _aggregate_edges(legs),
        "blacklisted_wallets": [source_black],
        "evaluated_transaction": evaluated,
        "expected": {
            "graph_assistant": (
                f"hop_distance_to_blacklist({dex_chain[-1]}) phải = 4 (> ngưỡng REPORT=2, "
                "nên KHÔNG kích hoạt Rule 4). Tuỳ classifier_score thật của model mà case "
                "có thể thành REVIEW (2 tín hiệu medium: classifier + graph ở 3-4 hop) hoặc "
                "PASS — đây là hành vi ĐÚNG của kiến trúc rule-based composite mới, không còn "
                "graph_risk_score tổng hợp. KYC không còn trả kyc_flags (bỏ scoring KYC) — chỉ "
                "sanction exact match + name fuzzy warning."
            ),
        },
    }


def build_name_similarity_scenario() -> dict:
    """
    Khách hàng mock có fullname GẦN GIỐNG (không trùng tuyệt đối) với 1 entity
    THẬT trong sdn.xml mẫu (uid 6707: "Rafael CARO QUINTERO") -- kiểm tra bước
    fuzzy matching (Levenshtein, core/name_screening.py) ở tầng webhook,
    TRƯỚC khi Privacy Layer băm dữ liệu.
    """
    mock_fullname = "Rafael Caro Kintero"  # đổi "Qu" -> "K" -- gần giống, không trùng tuyệt đối

    evaluated = {
        "tx_hash": None,
        "wallet_from": "0xname_similarity_wallet_from",
        "wallet_to": "0xname_similarity_wallet_to",
        "amount_vnd": 650_000_000,
        "fullname": mock_fullname,
        "id_number": "079099001234",
        "account_number": "3000000099",
    }

    return {
        "scenario": "name_similarity",
        "description": (
            f'Khách hàng mock fullname="{mock_fullname}" gần giống entity THẬT trong sdn.xml '
            f'mẫu ("{_REAL_SDN_NAME}", uid 6707) -- không trùng tuyệt đối, dùng để kiểm tra '
            "fuzzy matching Levenshtein ở core/name_screening.py TRƯỚC khi băm PII."
        ),
        "legs": [],
        "graph_edges": [],
        "blacklisted_wallets": [],
        "evaluated_transaction": evaluated,
        "expected": {
            "name_similarity": (
                f'name_similarity_warning phải = True (so khớp mờ với "{_REAL_SDN_NAME}" '
                "thật trong sdn.xml), % tương đồng Levenshtein thật ghi lại ở "
                "state[\"name_similarity_score\"] (core/name_screening.py) -- không làm tròn tuỳ ý. "
                "Đây là tín hiệu THÔNG TIN cho Decision Engine (evidence), KHÔNG tự kích hoạt "
                "REPORT — kyc_flags/kyc_risk_score đã bỏ khỏi kiến trúc."
            ),
        },
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scenarios = {
        "scenario_smurfing.json": build_smurfing_scenario(),
        "scenario_layering.json": build_layering_scenario(),
        "scenario_name_similarity.json": build_name_similarity_scenario(),
    }
    for filename, payload in scenarios.items():
        out_path = OUT_DIR / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Đã sinh {out_path} ({len(payload.get('legs', []))} legs, "
              f"{len(payload.get('graph_edges', []))} cạnh đồ thị đã gộp).")


if __name__ == "__main__":
    main()