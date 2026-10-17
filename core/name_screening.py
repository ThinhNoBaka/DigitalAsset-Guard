"""
core/name_screening.py -- [BỔ SUNG] Webhook fuzzy name-matching, chạy TRƯỚC
Privacy Layer (trước khi băm PII).

*** VÌ SAO FILE NÀY TỒN TẠI ***
agents/kyc_verification.py đọc thẳng `state["name_similarity_warning"]` và ghi
rõ trong docstring: "So khớp mờ theo tên được thực hiện ở tầng WEBHOOK, TRƯỚC
khi băm PII. Agent này chỉ đọc lại kết quả dưới dạng cờ boolean có sẵn trong
state." Nhưng trong bộ file gốc được cung cấp, KHÔNG có module nào thực sự
tính ra cờ đó -- core/graph_builder.py::privacy_layer_node() chỉ băm PII, không
gọi fuzzy-match. Đây là lỗ hổng thật (thiếu wiring), không phải tính năng V2 --
được bổ sung ở đây để agents/kyc_verification.py hoạt động đúng như thiết kế,
và để kịch bản "Name Similarity" của V2-4 (THAY_DOI_V2.md) có cái để chạy qua.
Nếu dự án đã có sẵn module này ở nơi khác, hãy gửi để hợp nhất, tránh trùng lặp.

Dùng Levenshtein similarity ratio của rapidfuzz (C++, đã có trong
requirements.txt) -- cùng công thức (1 - distance / max_len) * 100 với bản
thuần Python trước đây, nhưng đủ nhanh để quét ~19k tên SDN mỗi giao dịch.
"""
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

SDN_XML_PATH = Path("data/raw/ofac/sdn.xml")
SDN_NAMES_CACHE_PATH = Path("data/processed/sdn_names.txt")

# Ngưỡng % tương đồng để coi là "gần giống đáng ngờ" -- >= ngưỡng NHƯNG < 100%
# (100% đã là khớp tuyệt đối, không phải "gần giống"). 85% là mức phổ biến cho
# fuzzy KYC name screening (tương đương World-Check/Dow Jones dùng ~80-90%).
SIMILARITY_THRESHOLD_PCT = 85.0

_sdn_names_cache: Optional[List[str]] = None
# Bản đã chuẩn hoá của _sdn_names_cache -- tránh chuẩn hoá lại ~19k tên mỗi giao dịch.
_normalized_sdn_cache: Optional[List[str]] = None


def _normalize_name(name: str) -> str:
    """Chuẩn hoá upper + gộp khoảng trắng thừa trước khi so khớp."""
    return re.sub(r"\s+", " ", name.strip().upper())


def levenshtein_similarity_pct(a: str, b: str) -> float:
    """
    % tương đồng Levenshtein giữa 2 chuỗi (đã chuẩn hoá upper + strip khoảng
    trắng thừa trước khi so, để "Nguyen  Van A" vs "NGUYEN VAN A" không bị
    trừ điểm oan vì hoa/thường hoặc khoảng trắng). Trả về giá trị THẬT tính
    được, KHÔNG làm tròn tuỳ ý (yêu cầu THAY_DOI_V2.md V2-4).
    """
    norm_a = _normalize_name(a)
    norm_b = _normalize_name(b)
    if not norm_a and not norm_b:
        return 100.0
    return Levenshtein.normalized_similarity(norm_a, norm_b) * 100


def load_sdn_names(xml_path: Path = SDN_XML_PATH, cache_path: Path = SDN_NAMES_CACHE_PATH) -> List[str]:
    """
    Trích "firstName lastName" (hoặc chỉ lastName với entity) của toàn bộ
    sdnEntry trong sdn.xml, cache ra file text (1 tên/dòng) để không phải parse
    lại XML 19k+ entry mỗi lần khởi động -- cùng tinh thần với
    agents/kyc_verification.py::load_ofac_wallets().
    """
    global _sdn_names_cache
    if _sdn_names_cache is not None:
        return _sdn_names_cache

    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            _sdn_names_cache = [line.strip() for line in f if line.strip()]
        return _sdn_names_cache

    if not xml_path.exists():
        print(f"[CẢNH BÁO] Không tìm thấy {xml_path} lẫn cache {cache_path} -- "
              f"name screening sẽ không phát hiện được gì (danh sách rỗng).")
        _sdn_names_cache = []
        return _sdn_names_cache

    print(f"Đang trích xuất tên từ {xml_path} (chỉ chạy 1 lần, sẽ cache ra {cache_path})...")
    content = xml_path.read_text(encoding="utf-8")
    entries = re.findall(r"<sdnEntry>.*?</sdnEntry>", content, re.S)
    names = []
    for entry in entries:
        first = re.search(r"<firstName>([^<]*)</firstName>", entry)
        last = re.search(r"<lastName>([^<]*)</lastName>", entry)
        parts = [m.group(1).strip() for m in (first, last) if m and m.group(1).strip()]
        if parts:
            names.append(" ".join(parts))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write("\n".join(names))

    _sdn_names_cache = names
    print(f"Đã trích xuất {len(names)} tên từ SDN, cache tại {cache_path}.")
    return _sdn_names_cache


def screen_name_against_sdn(
    fullname: str,
    sdn_names: Optional[List[str]] = None,
    threshold_pct: float = SIMILARITY_THRESHOLD_PCT,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    So khớp mờ 1 fullname (PII GỐC, chưa băm) với toàn bộ tên trong SDN.

    QUAN TRỌNG: hàm này CHỈ được gọi TRƯỚC khi PII bị băm (đúng vị trí "tầng
    webhook" theo docstring agents/kyc_verification.py) -- gọi SAU khi đã băm
    là vô nghĩa vì hash SHA-256 không giữ được độ tương tự chuỗi gốc.

    Trả về (warning: bool, best_score_pct: float|None, matched_name: str|None).
    warning=True khi similarity >= threshold_pct NHƯNG < 100% (gần giống,
    không phải trùng tuyệt đối -- trùng tuyệt đối nên được coi là 1 dạng khác,
    nhưng ở đây vẫn tính là warning vì rõ ràng còn đáng ngờ hơn "gần giống").
    """
    global _normalized_sdn_cache

    if not fullname or not fullname.strip():
        return False, None, None

    if sdn_names is not None:
        names = sdn_names
        normalized_names = [_normalize_name(candidate) for candidate in names]
    else:
        names = load_sdn_names()
        if _normalized_sdn_cache is None or len(_normalized_sdn_cache) != len(names):
            _normalized_sdn_cache = [_normalize_name(candidate) for candidate in names]
        normalized_names = _normalized_sdn_cache
    if not names:
        return False, None, None

    best = process.extractOne(
        _normalize_name(fullname),
        normalized_names,
        scorer=Levenshtein.normalized_similarity,
        processor=None,
    )
    best_score = best[1] * 100
    best_name = names[best[2]]

    warning = best_score >= threshold_pct
    return warning, round(best_score, 2), (best_name if warning else None)