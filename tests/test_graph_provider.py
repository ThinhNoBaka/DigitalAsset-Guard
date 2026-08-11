"""
tests/test_graph_provider.py
Unit tests cho core/graph_provider.py — Graph Data Provider layer (Phase 2).

Cover:
- MockGraphProvider: khớp scenario theo wallet_from (TEST A), NO_GRAPH_DATA khi
  không khớp (TEST C), scenario tường minh, deterministic sorting theo tên file.
- assert_wallet_from_unique_across_scenarios: tie-break guard (feedback #3) —
  wallet_from của evaluated_transaction phải DUY NHẤT giữa các scenario mock.
- Neo4jGraphProvider: trả None (passthrough marker), KHÔNG đọc scenario JSON.
- get_graph_provider factory (TEST D): GRAPH_SOURCE=neo4j -> Neo4jGraphProvider,
  GRAPH_SOURCE=mock -> MockGraphProvider. Và khi production config, mock code
  không được gọi (monkeypatch raise nếu MockGraphProvider.get_graph_data gọi).

Lưu ý: test KHÔNG đụng Neo4j thật — chỉ assert provider SELECTION + data contract.
"""
from core.config import resolve_graph_source
from core.graph_provider import (
    GraphData,
    MockGraphProvider,
    Neo4jGraphProvider,
    get_graph_provider,
)

# Ví thật trong data/mock/scenario_graph_sanction.json (evaluated_transaction)
DEMO_WALLET_FROM = "0x28C6c06298d514Db089934071355E5743bf21d60"
DEMO_WALLET_TO = "0x000000000000000000000000000000000000dEaD"
# Ví không có trong bất kỳ scenario nào -> NO_GRAPH_DATA
UNKNOWN_WALLET = "0xNOT_IN_ANY_SCENARIO_000000000000000000000000"


# =============================================================================
# resolve_graph_source
# =============================================================================

class TestResolveGraphSource:
    def test_default_from_demo_mode(self, monkeypatch):
        # Xoá GRAPH_SOURCE -> fallback DEMO_MODE (đọc tại import time).
        # Với DEMO_MODE=False trong .env -> neo4j.
        monkeypatch.delenv("GRAPH_SOURCE", raising=False)
        assert resolve_graph_source() == "neo4j"

    def test_explicit_mock(self, monkeypatch):
        monkeypatch.setenv("GRAPH_SOURCE", "mock")
        assert resolve_graph_source() == "mock"

    def test_explicit_neo4j(self, monkeypatch):
        monkeypatch.setenv("GRAPH_SOURCE", "neo4j")
        assert resolve_graph_source() == "neo4j"


# =============================================================================
# MockGraphProvider
# =============================================================================

class TestMockGraphProvider:
    def setup_method(self):
        self.provider = MockGraphProvider()

    # --- TEST A: khớp scenario theo wallet_from (deterministic) ---
    def test_match_demo_wallet_from_deterministic(self):
        data = self.provider.get_graph_data(wallet_from=DEMO_WALLET_FROM)
        assert data is not None
        assert data.scenario_id == "graph_sanction"
        assert data.is_empty() is False
        # Contract đúng production schema: edges (from, to, amount_vnd)
        assert all(len(e) == 3 and isinstance(e[0], str) and isinstance(e[1], str) and isinstance(e[2], float) for e in data.edges)
        # Blacklisted chứa ví nguồn bị gắn cờ đen
        assert "0xSANCTIONED_DEMO_SOURCE" in data.blacklisted_wallets
        # wallet_from phải nằm trong graph (để thuật toán tính PPR/hop được)
        assert any(v == DEMO_WALLET_FROM for e in data.edges for v in e[:2])

    def test_match_is_deterministic_sorted(self, monkeypatch):
        # Gọi 2 lần -> cùng scenario (không phụ thuộc thứ tự đọc file hệ thống
        # vì _scenario_files() sort theo tên file).
        data1 = self.provider.get_graph_data(wallet_from=DEMO_WALLET_FROM)
        data2 = self.provider.get_graph_data(wallet_from=DEMO_WALLET_FROM)
        assert data1.scenario_id == data2.scenario_id == "graph_sanction"
        assert data1.edges == data2.edges

    # --- TEST C: NO_GRAPH_DATA ---
    def test_no_graph_data_unknown_wallet(self):
        data = self.provider.get_graph_data(wallet_from=UNKNOWN_WALLET)
        assert data is None  # NO_GRAPH_DATA — KHÔNG tự vẽ đồ thị giả

    # --- scenario tường minh ---
    def test_explicit_scenario_full_name(self):
        data = self.provider.get_graph_data(
            wallet_from=UNKNOWN_WALLET,  # wallet_from không trùng cũng tìm được
            scenario="graph_sanction",
        )
        assert data is not None
        assert data.scenario_id == "graph_sanction"

    def test_explicit_scenario_not_found(self):
        data = self.provider.get_graph_data(
            wallet_from=DEMO_WALLET_FROM,
            scenario="khong_ton_tai",
        )
        assert data is None

    # --- Tie-break guard (feedback #3) ---
    def test_wallet_from_unique_across_scenarios(self):
        # Nếu có 2 scenario cùng wallet_from -> AssertionError. Hiện tại data/mock
        # chỉ có 1 scenario chứa DEMO_WALLET_FROM nên hàm phải pass.
        MockGraphProvider.assert_wallet_from_unique_across_scenarios()

    def test_graph_data_contract(self):
        data = self.provider.get_graph_data(wallet_from=DEMO_WALLET_FROM)
        assert isinstance(data, GraphData)
        # Edges rỗng => is_empty() True (NO_GRAPH_DATA semantics)
        empty = GraphData()
        assert empty.is_empty() is True
        assert data.is_empty() is False


# =============================================================================
# Neo4jGraphProvider
# =============================================================================

class TestNeo4jGraphProvider:
    def test_returns_none_no_mock_read(self):
        """Production: trả None, KHÔNG đọc scenario mock (feedback #4)."""
        provider = Neo4jGraphProvider()

        # Field scenario (demo-only) bị bỏ qua hoàn toàn — provider không đọc
        # file JSON nào, đảm bảo mock data không lẫn vào production runtime.
        data = provider.get_graph_data(
            wallet_from=DEMO_WALLET_FROM,
            scenario="graph_sanction",
        )
        # State sẽ KHÔNG chứa mock_graph_edges/mock_blacklisted_wallets —
        # api/main.py chỉ set 2 field này khi graph_data không None.
        assert data is None


# =============================================================================
# Factory — TEST D
# =============================================================================

class TestFactory:
    def test_factory_mock(self, monkeypatch):
        monkeypatch.setenv("GRAPH_SOURCE", "mock")
        provider = get_graph_provider()
        assert isinstance(provider, MockGraphProvider)

    def test_factory_neo4j(self, monkeypatch):
        monkeypatch.setenv("GRAPH_SOURCE", "neo4j")
        provider = get_graph_provider()
        assert isinstance(provider, Neo4jGraphProvider)

    def test_neo4j_production_never_invokes_mock(self, monkeypatch):
        """
        Feedback #4 — TEST D bổ sung: khi GRAPH_SOURCE=neo4j, MockGraphProvider
        KHÔNG được gọi. Monkeypatch MockGraphProvider.get_graph_data để raise —
        nếu factory/API vô tình gọi tới, pipeline production sẽ crash rõ ràng
        thay vì âm thầm chạy mock.
        """
        monkeypatch.setenv("GRAPH_SOURCE", "neo4j")

        original = MockGraphProvider.get_graph_data
        called = {"count": 0}

        def _boom(*args, **kwargs):
            called["count"] += 1
            raise AssertionError(
                "MockGraphProvider.get_graph_data bị gọi khi GRAPH_SOURCE=neo4j — "
                "mock code lẫn vào production runtime!"
            )

        monkeypatch.setattr(MockGraphProvider, "get_graph_data", _boom)

        provider = get_graph_provider()
        assert isinstance(provider, Neo4jGraphProvider)

        # Gọi như _build_initial_state sẽ gọi — nếu nhầm provider thì _boom nổ.
        provider.get_graph_data(wallet_from=DEMO_WALLET_FROM)
        assert called["count"] == 0

        monkeypatch.setattr(MockGraphProvider, "get_graph_data", original)

    def test_loader_rejects_pydantic_scenario_field_presence(self):
        """
        Đảm bảo RawTransactionRequest.scenario (demo-only) không làm vỡ production:
        Neo4jGraphProvider.get_graph_data nhận scenario=None mặc định và bỏ qua.
        """
        provider = Neo4jGraphProvider()
        data = provider.get_graph_data(wallet_from=DEMO_WALLET_FROM)
        assert data is None