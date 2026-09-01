"""
tests/test_shioaji_quotes.py — Shioaji 報價層（取代 yfinance 的即時價/股名/指數）

被取代的 yfinance 用途：
    telegram_bot._price          查現價
    daytrading_monitor._get_price 盤中價
    strategy_executor            最新收盤
    candidate_builder            盤前補價
    market_scanner               快照
    market_index                 加權指數漲跌幅
    futures_premium              指數現值
    stock_query / daytrading_report  股名補查

全部改走 api.snapshots / contract.name。取不到一律回 None 或 0.0（與原
yfinance 版本的失敗行為一致），但必須寫 log——8/21 的教訓是「降級了卻沒人知道」。
"""
import pytest

import shioaji_quotes as q


class _Contract:
    def __init__(self, code, name):
        self.code, self.name = code, name


class _Snap:
    """欄位需與真實 Shioaji snapshot 一致——batch_fetch_snapshots 會存取
    open/high/low，缺欄位會拋 AttributeError 而被它的 except 吞掉，
    測試就會看到空結果卻不知道為什麼。"""
    def __init__(self, code, close, change_rate=0.0, change_price=0.0, total_volume=0):
        self.code, self.close = code, close
        self.change_rate, self.change_price = change_rate, change_price
        self.total_volume = total_volume
        self.open = self.high = self.low = close


class _Api:
    """最小可用的假 Shioaji。"""
    def __init__(self, contracts=None, snaps=None, raises=False):
        self._contracts = contracts or {}
        self._snaps = snaps or {}
        self._raises = raises

        outer = self
        class Stocks:
            def get(self, code):
                if outer._raises:
                    raise RuntimeError("contracts not ready")
                return outer._contracts.get(code)
        class C:
            pass
        self.Contracts = C()
        self.Contracts.Stocks = Stocks()

    def snapshots(self, contracts):
        if self._raises:
            raise RuntimeError("quote session not ready")
        return [self._snaps[c.code] for c in contracts if c.code in self._snaps]


class TestStockName:
    def test_returns_contract_name(self):
        api = _Api(contracts={"2330": _Contract("2330", "台積電")})
        assert q.stock_name(api, "2330") == "台積電"

    def test_falls_back_to_code_when_contract_missing(self):
        assert q.stock_name(_Api(), "9999") == "9999"

    def test_falls_back_to_code_when_api_is_none(self):
        assert q.stock_name(None, "2330") == "2330"

    def test_survives_contracts_not_ready(self):
        """8:30 券商剛登入、合約未下載完成是常態，不得拋出。"""
        assert q.stock_name(_Api(raises=True), "2330") == "2330"


class TestLatestPrice:
    def test_returns_snapshot_close(self):
        api = _Api(contracts={"2330": _Contract("2330", "台積電")},
                   snaps={"2330": _Snap("2330", 1050.0)})
        assert q.latest_price(api, "2330") == 1050.0

    def test_none_when_no_snapshot(self):
        api = _Api(contracts={"2330": _Contract("2330", "台積電")})
        assert q.latest_price(api, "2330") is None

    def test_none_when_close_is_zero(self):
        """★ 報價 session 未暖機時 close 會是 0——那不是「股價 0 元」，
        是「沒有報價」。回 0.0 會讓下游算出無意義的損益。"""
        api = _Api(contracts={"2330": _Contract("2330", "台積電")},
                   snaps={"2330": _Snap("2330", 0.0)})
        assert q.latest_price(api, "2330") is None

    def test_none_when_api_is_none(self):
        assert q.latest_price(None, "2330") is None

    def test_survives_quote_session_not_ready(self):
        assert q.latest_price(_Api(raises=True), "2330") is None


class TestBatchPrices:
    def test_returns_map_of_close_prices(self):
        api = _Api(
            contracts={"2330": _Contract("2330", "台積電"),
                       "2454": _Contract("2454", "聯發科")},
            snaps={"2330": _Snap("2330", 1050.0), "2454": _Snap("2454", 1400.0)},
        )
        assert q.batch_prices(api, ["2330", "2454"]) == {"2330": 1050.0, "2454": 1400.0}

    def test_omits_codes_without_quote(self):
        """取不到的不要放進結果——放 0.0 會被下游當成真實價格。"""
        api = _Api(contracts={"2330": _Contract("2330", "台積電"),
                              "2454": _Contract("2454", "聯發科")},
                   snaps={"2330": _Snap("2330", 1050.0)})
        assert q.batch_prices(api, ["2330", "2454"]) == {"2330": 1050.0}

    def test_omits_zero_close(self):
        api = _Api(contracts={"2330": _Contract("2330", "台積電")},
                   snaps={"2330": _Snap("2330", 0.0)})
        assert q.batch_prices(api, ["2330"]) == {}

    def test_empty_when_api_is_none(self):
        assert q.batch_prices(None, ["2330"]) == {}


class TestIndex:
    def _api(self, close=21800.0, change_rate=0.85):
        class Idx:
            code = "001"
        idx = Idx()

        class C:
            pass
        class Api:
            Contracts = C()
            def snapshots(self, contracts):
                return [_Snap("001", close, change_rate=change_rate)]
        api = Api()
        api.Contracts.Indexs = C()
        api.Contracts.Indexs.TSE = {"001": idx}
        return api

    def test_index_change_pct(self):
        assert q.index_change_pct(self._api(change_rate=0.85)) == 0.85

    def test_index_price(self):
        assert q.index_price(self._api(close=21800.0)) == 21800.0

    def test_change_pct_zero_when_unavailable(self):
        """與 market_index.fetch_market_index_change 原本的失敗行為一致：
        回 0.0 讓 dt_rules 走「大盤缺值、不擋僅註記」的既有路徑。"""
        assert q.index_change_pct(None) == 0.0

    def test_price_none_when_unavailable(self):
        assert q.index_price(None) is None
