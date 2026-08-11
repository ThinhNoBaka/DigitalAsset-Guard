"""
core/graph_provider.py
Graph Data Provider — tách DATA SOURCE khỏi Graph Analysis business logic.

Kiến trúc:

    Graph Analysis (agents/graph_aml.py)
        │
        └── GraphDataProvider (ABC)
                 /           \
        MockGraphProvider   Neo4jGraphProvider
              │                    │
        Mock Scenario          Real Neo4j
              │                    │
              └─────────┬──────────┘
                        ▼
                  SAME Graph Logic (PPR / Hop / Path)
                        ▼
              core/graph_aml.py::analyze_graph()

Nguyên tắc:
- Provider CHỈ cung cấp DATA (edges + blacklisted wallets + scenario metadata),
  KHÔNG tự tính PPR / hop / suspicious_path / fan_out / community.
- Mọi thuật toán nằm ở agents/graph_aml.py — DÙNG CHUNG cho demo lẫn production.
- MockGraphProvider đọc data/mock/scenario_*.json, trả data đúng CONTRACT
  production (edges `(from, to, amount_vnd)` + blacklisted wallets) — giống
  `db/neo4j_setup.py` / query Neo4j thật.
- Neo4jGraphProvider là "passthrough marker": graph_aml vẫn query Neo4j/GDS
  gốc như hiện tại (xem agents/graph_aml.py::_analyze_via_neo4j). Provider này
  trả None — KHÔNG thêm data vào state, production path giữ nguyên semantics.

Vì sao MockGraphProvider không được import bởi production path:
- Khi GRAPH_SOURCE=neo4j, api/main.py KHÔNG gọi MockGraphProvider.
- TEST D (Phase 4) assert điều này: MockGraphProvider.get_graph_data
  không được gọi, scenario JSON không bị đọc khi chạy production config.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BASE_DIR


# =============================================================================
# CONTRACT
# =============================================================================

@dataclass
class GraphData:
    """
    Dữ liệu đồ thị MỘT wallet_from — đúng contract production.

    `edges`: list[tuple[str, str, float]] — (wallet_from, wallet_to, amount_vnd).
        Trùng với format `mock_graph_edges` / `graph_edges` trong scenario JSON
        và format `(u, v, weight=float(amount))` networkx.DiGraph.
    `blacklisted_wallets`: list[str] — địa chỉ ví thuộc danh sách trừng phạt
        (is_sanctioned=true trong Neo4j; blacklisted_wallets trong scenario mock).
    `scenario_id`: tên scenario (chỉ mock). None cho production.
    `metadata`: dict thông tin bổ sung (mock: description...).
    """
    edges: List[Tuple[str, str, float]] = field(default_factory=list)
    blacklisted_wallets: List[str] = field(default_factory=list)
    scenario_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """Không có dữ liệu đồ thị cho wallet này (edges rỗng)."""
        return not self.edges


class GraphDataProvider(ABC):
    """Interface cho mọi nguồn đồ thị (mock / neo4j)."""

    @abstractmethod
    def get_graph_data(
        self,
        *,
        wallet_from: str,
        wallet_to: Optional[str] = None,
        scenario: Optional[str] = None,
    ) -> Optional[GraphData]:
        """
        Trả về GraphData cho wallet_from, hoặc None nếu KHÔNG có dữ liệu.

        None = NO_GRAPH_DATA (wallet không có trong nguồn dữ liệu này).
        KHÔNG bao giờ tự suy diễn / tự vẽ đồ thị giả khi không có dữ liệu.
        """
        raise NotImplementedError


# =============================================================================
# MOCK PROVIDER
# =============================================================================

class MockGraphProvider(GraphDataProvider):
    """
    Đọc data/mock/scenario_*.json, trả data đúng contract production.

    Scenario selection — KHÔNG hard-code wallet address:
      1. `scenario` (từ request demo, field tùy chọn) → load scenario_<tên>.json.
      2. `scenario=None` → deterministic mapping: tìm scenario đầu tiên có
         evaluated_transaction.wallet_from == wallet_from.
         - Sắp xếp theo TÊN FILE (Path.name) để kết quả KHÔNG phụ thuộc thứ tự
           đọc file hệ thống.
      3. Không khớp → None (NO_GRAPH_DATA).
    """
    MOCK_DIR = BASE_DIR / "data" / "mock"

    def _load_scenario(self, filename: str) -> Optional[Dict[str, Any]]:
        path = self.MOCK_DIR / filename
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _scenario_files(self) -> List[str]:
        if not self.MOCK_DIR.exists():
            return []
        return sorted(
            p.name for p in self.MOCK_DIR.glob("scenario_*.json")
        )  # sort theo tên file = deterministic

    def _data_from_scenario(self, scenario: Dict[str, Any]) -> GraphData:
        return GraphData(
            edges=[
                (str(u), str(v), float(amount))
                for u, v, amount in scenario.get("graph_edges", [])
            ],
            blacklisted_wallets=[
                str(w) for w in scenario.get("blacklisted_wallets", [])
            ],
            scenario_id=scenario.get("scenario"),
            metadata={
                "description": scenario.get("description", ""),
            },
        )

    def get_graph_data(
        self,
        *,
        wallet_from: str,
        wallet_to: Optional[str] = None,
        scenario: Optional[str] = None,
    ) -> Optional[GraphData]:
        # 1. Tường minh: scenario=<tên>
        if scenario:
            scenario_payload = self._load_scenario(f"scenario_{scenario}.json")
            if scenario_payload is not None:
                return self._data_from_scenario(scenario_payload)
            return None

        # 2. Deterministic mapping theo wallet_from (sorted theo tên file)
        for filename in self._scenario_files():
            scenario_payload = self._load_scenario(filename)
            if scenario_payload is None:
                continue
            evaluated = scenario_payload.get("evaluated_transaction", {})
            if evaluated.get("wallet_from") == wallet_from:
                return self._data_from_scenario(scenario_payload)

        # 3. Không khớp → NO_GRAPH_DATA
        return None

    @staticmethod
    def assert_wallet_from_unique_across_scenarios() -> None:
        """
        TEST E / build-time guard: wallet_from của evaluated_transaction phải
        DUY NHẤT giữa các scenario — nếu 2+ scenario cùng chứa 1 wallet_from,
        mapping §2.2 sẽ không deterministic và phải báo lỗi ngay lập tức.
        """
        seen: Dict[str, str] = {}
        provider = MockGraphProvider()
        for filename in provider._scenario_files():
            payload = provider._load_scenario(filename)
            if payload is None:
                continue
            wf = payload.get("evaluated_transaction", {}).get("wallet_from")
            if not wf:
                continue
            if wf in seen:
                raise AssertionError(
                    f"wallet_from={wf!r} xuất hiện ở CẢ {seen[wf]} và {filename} — "
                    "không được phép trùng giữa các scenario mock. Đổi địa chỉ ví "
                    "hoặc tách scenario."
                )
            seen[wf] = filename


# =============================================================================
# NEO4J PROVIDER
# =============================================================================

class Neo4jGraphProvider(GraphDataProvider):
    """
    Production — passthrough marker.

    KHÔNG đọc scenario mock, KHÔNG thêm data vào state. graph_aml vẫn query
    Neo4j/GDS trực tiếp qua _analyze_via_neo4j (giữ nguyên semantics hiện tại —
    minimal safe refactor). get_graph_data trả None; graph_aml biết phải gọi
    nhánh Neo4j khi provider loại này.
    """

    def get_graph_data(
        self,
        *,
        wallet_from: str,
        wallet_to: Optional[str] = None,
        scenario: Optional[str] = None,
    ) -> Optional[GraphData]:
        # Production path: KHÔNG nhận graph giả từ request (field scenario bị bỏ qua).
        return None


# =============================================================================
# FACTORY
# =============================================================================

def get_graph_provider() -> GraphDataProvider:
    """
    Factory DUY NHẤT — chọn provider theo config (xem core/config.py::resolve_graph_source).
        GRAPH_SOURCE=mock   → MockGraphProvider (demo)
        GRAPH_SOURCE=neo4j  → Neo4jGraphProvider (production)
    """
    from core.config import resolve_graph_source

    source = resolve_graph_source()
    if source == "mock":
        return MockGraphProvider()
    return Neo4jGraphProvider()