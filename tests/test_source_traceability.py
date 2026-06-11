"""
tests/test_source_traceability.py — 來源可追溯（source-traceability）測試

依 docs/research_loop_design.md §2.1–2.3：
  件1 learning_db：stock_prediction_log 新增 4 欄位（reason / factors_json /
       news_refs / youtube_refs，全部 nullable），migration 冪等、舊 DB 可升級、
       save/load roundtrip（含與不含新欄位）。
  件2 news_agent：headline 結構化為 dict（title/url/published/source），
       get_news_context() 保留向後相容的 headlines_text（list[str]）。
  件3 deep_analyzer：build_deep_prompt 注入新聞編號 [N1]..[N5] + 三條邊界規則，
       要求 AI 回傳 news_refs_used；DeepAnalysis 新增 news_refs 欄位；
       解析容忍欄位缺漏；news 參數相容 legacy list[str]。
  件4 telegram_bot：盤前 stock_prediction_log 入庫點帶入 reason 與新欄位
       （pick 物件有則帶入，無則 None）。

測試環境：conftest.py 已注入 google.genai / feedparser stub；
import telegram_bot 比照 test_telegram_order_bridge.py 直接 import 即可。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from unittest.mock import MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════════════════
# 件1：learning_db — 新欄位 + migration
# ════════════════════════════════════════════════════════════════════════════

from learning_db import LearningDB, StockPredictionLog


NEW_COLUMNS = {"reason", "factors_json", "news_refs", "youtube_refs"}


def _columns(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(stock_prediction_log)"
        ).fetchall()}
    finally:
        conn.close()
    return cols


class TestLearningDbMigration:
    def test_new_columns_present_on_fresh_db(self, tmp_path):
        """全新 DB 初始化後，4 個新欄位都存在。"""
        path = str(tmp_path / "fresh.db")
        LearningDB(path)
        assert NEW_COLUMNS <= _columns(path)

    def test_migration_idempotent(self, tmp_path):
        """重複初始化同一 DB 不會 crash，也不會重複加欄位。"""
        path = str(tmp_path / "idem.db")
        LearningDB(path)
        LearningDB(path)
        LearningDB(path)
        assert NEW_COLUMNS <= _columns(path)

    def test_upgrades_legacy_db_without_new_columns(self, tmp_path):
        """模擬舊版 DB（無新欄位），LearningDB 開啟後自動補上欄位且保留原資料。"""
        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE stock_prediction_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                date                TEXT NOT NULL,
                code                TEXT NOT NULL,
                name                TEXT NOT NULL,
                action              TEXT NOT NULL,
                confidence          INTEGER NOT NULL,
                expected_return_pct REAL NOT NULL,
                entry_price         REAL NOT NULL,
                closing_price       REAL,
                actual_return_pct   REAL,
                was_correct         INTEGER,
                UNIQUE(date, code)
            );
        """)
        conn.execute(
            "INSERT INTO stock_prediction_log "
            "(date, code, name, action, confidence, expected_return_pct, entry_price) "
            "VALUES ('2026-05-01','2330','台積電','open',8,3.0,900.0)"
        )
        conn.commit()
        conn.close()

        # 舊欄位不存在
        assert not (NEW_COLUMNS <= _columns(path))

        db = LearningDB(path)
        # 開啟後欄位補齊
        assert NEW_COLUMNS <= _columns(path)
        # 原資料保留且可讀回
        preds = db.get_predictions_by_date(date(2026, 5, 1))
        assert len(preds) == 1
        assert preds[0].code == "2330"
        # 舊資料的新欄位為 None
        assert preds[0].reason is None
        assert preds[0].factors_json is None


class TestStockPredictionLogRoundtrip:
    def test_roundtrip_without_new_fields(self, tmp_path):
        """不帶新欄位插入（既有 caller 行為），讀回新欄位為 None。"""
        db = LearningDB(str(tmp_path / "a.db"))
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 5, 2), code="2454", name="聯發科",
            action="open", confidence=7, expected_return_pct=2.5,
            entry_price=1200.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        ))
        rows = db.get_predictions_by_date(date(2026, 5, 2))
        assert len(rows) == 1
        r = rows[0]
        assert r.reason is None
        assert r.factors_json is None
        assert r.news_refs is None
        assert r.youtube_refs is None

    def test_roundtrip_with_new_fields(self, tmp_path):
        """帶新欄位插入，讀回完整一致。"""
        db = LearningDB(str(tmp_path / "b.db"))
        factors = {"technical": "多頭排列", "chip": "外資買超",
                   "news": "法說會利多", "theme": "AI"}
        news_refs = [{"id": "N1", "title": "台積電擴產", "url": "http://x/1",
                      "published": "2026-05-02T08:00:00", "source": "鉅亨網"}]
        yt_refs = [{"video_id": "vid1", "channel": "頻道A",
                    "url": "http://yt/1", "published": "2026-05-01T20:00:00"}]
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 5, 3), code="2330", name="台積電",
            action="open", confidence=9, expected_return_pct=4.0,
            entry_price=900.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
            reason="多頭排列＋外資買超",
            factors_json=json.dumps(factors, ensure_ascii=False),
            news_refs=json.dumps(news_refs, ensure_ascii=False),
            youtube_refs=json.dumps(yt_refs, ensure_ascii=False),
        ))
        rows = db.get_predictions_by_date(date(2026, 5, 3))
        assert len(rows) == 1
        r = rows[0]
        assert r.reason == "多頭排列＋外資買超"
        assert json.loads(r.factors_json)["chip"] == "外資買超"
        assert json.loads(r.news_refs)[0]["id"] == "N1"
        assert json.loads(r.youtube_refs)[0]["video_id"] == "vid1"

    def test_default_construction_keeps_existing_callers_working(self):
        """StockPredictionLog 仍可用既有 positional/必填欄位建構（新欄位有預設）。"""
        log = StockPredictionLog(
            date=date(2026, 5, 4), code="2317", name="鴻海",
            action="open", confidence=6, expected_return_pct=1.5,
            entry_price=200.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        )
        assert log.reason is None
        assert log.factors_json is None
        assert log.news_refs is None
        assert log.youtube_refs is None


# ════════════════════════════════════════════════════════════════════════════
# 件2：news_agent — 結構化 headline + 向後相容
# ════════════════════════════════════════════════════════════════════════════

import news_agent


def _make_entry(title, link=None, published=None):
    e = MagicMock()
    e.title = title
    # MagicMock 預設對任何屬性都回傳 Mock；用 spec 控制有無 link/published
    if link is None:
        # 模擬缺 link：get / 屬性存取要安全
        e.link = ""
    else:
        e.link = link
    if published is not None:
        e.published = published
    else:
        e.published = ""
    return e


class TestFetchHeadlinesStructured:
    @patch("news_agent.feedparser.parse")
    def test_structured_shape(self, mock_parse):
        """結構化 headline 為 dict，含 title/url/published/source。"""
        entry = _make_entry("台積電創新高", link="http://news/1",
                            published="2026-05-02T09:00:00")
        mock_parse.return_value = MagicMock(entries=[entry])
        items = news_agent.fetch_headlines_structured(max_per_feed=1)
        assert isinstance(items, list)
        assert items, "should return at least one item"
        item = items[0]
        assert set(item.keys()) >= {"title", "url", "published", "source"}
        assert item["title"] == "台積電創新高"
        assert item["url"] == "http://news/1"
        assert item["source"] in ("鉅亨網", "Yahoo財經", "MoneyDJ")

    @patch("news_agent.feedparser.parse")
    def test_missing_link_handled_gracefully(self, mock_parse):
        """entry 缺 link 時不 crash，url 退化為空字串。"""
        entry = MagicMock(spec=["title"])
        entry.title = "無連結新聞"
        mock_parse.return_value = MagicMock(entries=[entry])
        items = news_agent.fetch_headlines_structured(max_per_feed=1)
        assert items
        assert items[0]["title"] == "無連結新聞"
        assert items[0]["url"] == ""

    @patch("news_agent.feedparser.parse")
    def test_feed_error_returns_empty(self, mock_parse):
        """feed 解析失敗回空 list，不 raise。"""
        mock_parse.side_effect = Exception("network error")
        assert news_agent.fetch_headlines_structured() == []


class TestGetNewsContextBackwardsCompat:
    @patch("news_agent.analyze_sentiment")
    @patch("news_agent.fetch_headlines_structured")
    def test_headlines_are_dicts_and_headlines_text_preserved(
        self, mock_struct, mock_analyze
    ):
        """headlines 為 dict list；同時保留 headlines_text（list[str]）。"""
        mock_struct.return_value = [
            {"title": "新聞A", "url": "http://a", "published": "t", "source": "鉅亨網"},
            {"title": "新聞B", "url": "http://b", "published": "t", "source": "Yahoo財經"},
        ]
        mock_analyze.return_value = {
            "sentiment": "positive", "score": 8, "reason": "利多",
            "headlines_used": 2,
        }
        ctx = news_agent.get_news_context()
        assert "headlines" in ctx
        assert isinstance(ctx["headlines"], list)
        assert all(isinstance(h, dict) for h in ctx["headlines"])
        assert ctx["headlines"][0]["title"] == "新聞A"
        # 向後相容鍵
        assert "headlines_text" in ctx
        assert ctx["headlines_text"] == ["新聞A", "新聞B"]
        assert all(isinstance(s, str) for s in ctx["headlines_text"])

    @patch("news_agent.analyze_sentiment")
    @patch("news_agent.fetch_headlines_structured")
    def test_keeps_sentiment_keys(self, mock_struct, mock_analyze):
        """既有 sentiment / fetched_at 鍵保留。"""
        mock_struct.return_value = []
        mock_analyze.return_value = {"sentiment": "neutral", "score": 5, "reason": ""}
        ctx = news_agent.get_news_context()
        assert "sentiment" in ctx
        assert "fetched_at" in ctx


# ════════════════════════════════════════════════════════════════════════════
# 件3：deep_analyzer — 新聞編號 + 邊界規則 + news_refs
# ════════════════════════════════════════════════════════════════════════════

from deep_analyzer import build_deep_prompt, parse_deep_response, DeepAnalysis


_NEWS_DICTS = [
    {"id": "N1", "title": "台積電法說優於預期", "url": "http://n/1",
     "published": "2026-05-02T08:00:00", "source": "鉅亨網"},
    {"id": "N2", "title": "外資連三買", "url": "http://n/2",
     "published": "2026-05-02T07:30:00", "source": "Yahoo財經"},
]


class TestBuildDeepPromptNewsRefs:
    def test_prompt_numbers_news_n1(self):
        """新聞以 [N1].. 編號注入 prompt。"""
        prompt = build_deep_prompt(
            "2330", "台積電", "上升", _NEWS_DICTS, "", "費半 +2.5%", "AI",
        )
        assert "[N1]" in prompt
        assert "[N2]" in prompt
        assert "台積電法說優於預期" in prompt

    def test_prompt_includes_published_time(self):
        """每則新聞附上 published 時間。"""
        prompt = build_deep_prompt(
            "2330", "台積電", "上升", _NEWS_DICTS, "", "", "",
        )
        assert "2026-05-02T08:00:00" in prompt

    def test_prompt_contains_three_boundary_rules(self):
        """prompt 含 §2.3 三條邊界規則（繁中要旨）。"""
        prompt = build_deep_prompt("2330", "台積電", "", _NEWS_DICTS, "", "", "")
        # 規則1：新聞/影片情緒僅作 catalyst 確認，單獨不得 +1 以上
        assert "catalyst" in prompt or "確認" in prompt
        assert "+1" in prompt
        # 規則2：與技術/籌碼矛盾，以技術/籌碼為準
        assert "矛盾" in prompt
        assert "技術" in prompt and "籌碼" in prompt
        # 規則3：超過 24 小時視為 price-in
        assert "24" in prompt
        assert "price-in" in prompt or "背景" in prompt

    def test_requests_news_refs_used(self):
        """prompt 要求 AI 回傳 news_refs_used。"""
        prompt = build_deep_prompt("2330", "台積電", "", _NEWS_DICTS, "", "", "")
        assert "news_refs_used" in prompt

    def test_legacy_str_list_still_works(self):
        """news 為 legacy list[str] 時仍可建 prompt（視為 title-only）。"""
        prompt = build_deep_prompt(
            "2330", "台積電", "", ["法人買超", "無重大利空"], "", "", "",
        )
        assert isinstance(prompt, str)
        assert "[N1]" in prompt
        assert "法人買超" in prompt


_SAMPLE_WITH_REFS = """{
  "signal": "buy",
  "confidence": 8,
  "summary": "短線偏多",
  "factors": {"historical_trend":"向上","news":"利多","theme":"AI",
              "us_market":"強","tw_policy":"利多","us_policy":"寬鬆"},
  "hold_days": 2,
  "target_price": 950.0,
  "stop_loss_price": 880.0,
  "news_refs_used": ["N1"]
}"""

_SAMPLE_NO_REFS = """{
  "signal": "hold",
  "confidence": 5,
  "summary": "觀望",
  "factors": {"historical_trend":"","news":"","theme":"",
              "us_market":"","tw_policy":"","us_policy":""},
  "hold_days": 1
}"""


class TestParseDeepResponseNewsRefs:
    def test_deep_analysis_has_news_refs_field(self):
        """DeepAnalysis 有 news_refs 欄位，預設空 list。"""
        a = DeepAnalysis(
            code="2330", name="台積電", signal="hold", confidence=0,
            summary="", factors=parse_deep_response("2330", "X", "x").factors,
        )
        assert hasattr(a, "news_refs")
        assert a.news_refs == []

    def test_parse_resolves_news_refs_used(self):
        """news_refs_used=[N1] 被解析回 _NEWS_DICTS 中對應的完整 dict。"""
        result = parse_deep_response(
            "2330", "台積電", _SAMPLE_WITH_REFS, news=_NEWS_DICTS,
        )
        assert len(result.news_refs) == 1
        assert result.news_refs[0]["id"] == "N1"
        assert result.news_refs[0]["title"] == "台積電法說優於預期"

    def test_parse_without_news_refs_used(self):
        """AI 輸出缺 news_refs_used 時，news_refs 為空 list，不 crash。"""
        result = parse_deep_response(
            "2330", "台積電", _SAMPLE_NO_REFS, news=_NEWS_DICTS,
        )
        assert result.news_refs == []

    def test_parse_backwards_compat_no_news_arg(self):
        """不傳 news 參數時仍可解析（既有 caller），news_refs 為空。"""
        result = parse_deep_response("2330", "台積電", _SAMPLE_WITH_REFS)
        assert isinstance(result, DeepAnalysis)
        assert result.news_refs == []

    def test_parse_legacy_str_news(self):
        """news 為 legacy list[str] 時，引用 N1 解析為 title-only dict。"""
        result = parse_deep_response(
            "2330", "台積電", _SAMPLE_WITH_REFS, news=["法人買超", "無重大利空"],
        )
        assert len(result.news_refs) == 1
        assert result.news_refs[0]["title"] == "法人買超"


# ════════════════════════════════════════════════════════════════════════════
# 件4：telegram_bot — 入庫點帶入 reason / 新欄位
# ════════════════════════════════════════════════════════════════════════════

import telegram_bot as bot


class _FakePick:
    """模擬 StockPick：有 reason / factors_json / news_refs / youtube_refs。"""
    def __init__(self, with_traceability=True):
        self.code = "2330"
        self.name = "台積電"
        self.action = "open"
        self.confidence = 8
        self.expected_return_pct = 3.0
        self.reason = "多頭排列" if with_traceability else None
        if with_traceability:
            self.factors_json = '{"technical":"多頭"}'
            self.news_refs = '[{"id":"N1"}]'
            self.youtube_refs = '[{"video_id":"v1"}]'


def _capture_insert(monkeypatch):
    """patch LearningDB.insert_prediction 攔截寫入的 StockPredictionLog。"""
    captured = []
    import learning_db

    def fake_insert(self, pred):
        captured.append(pred)

    monkeypatch.setattr(learning_db.LearningDB, "insert_prediction", fake_insert)
    monkeypatch.setattr(learning_db.LearningDB, "upsert_chip_snapshot",
                        lambda self, snap: None)
    monkeypatch.setattr(learning_db.LearningDB, "_init_schema", lambda self: None)
    return captured


class TestTelegramInsertTraceability:
    def _run_premarket_insert(self, pick):
        """直接重現入庫點的邏輯片段（建構 StockPredictionLog 並 insert）。

        以實際 telegram_bot 模組函式為主，但因該函式 (_handle_confirm) 牽涉大量
        外部依賴，這裡透過呼叫模組內負責入庫的 helper。若 helper 不存在，
        測試會 fail，提示需以最小重構暴露入庫邏輯。
        """
        raise NotImplementedError

    def test_insert_carries_reason_when_present(self, monkeypatch):
        """pick 有 reason/新欄位時，寫入的 StockPredictionLog 帶入這些值。"""
        captured = _capture_insert(monkeypatch)
        pick = _FakePick(with_traceability=True)
        log = bot._build_prediction_log(pick, date(2026, 5, 5), entry_price=900.0)
        assert log.reason == "多頭排列"
        assert log.factors_json == '{"technical":"多頭"}'
        assert log.news_refs == '[{"id":"N1"}]'
        assert log.youtube_refs == '[{"video_id":"v1"}]'
        assert log.code == "2330"
        assert log.entry_price == 900.0

    def test_insert_none_when_absent(self):
        """pick 無 traceability 欄位時（getattr None），帶入 None，不 raise。"""
        pick = _FakePick(with_traceability=False)
        # 移除屬性，模擬舊 pick 物件
        for attr in ("factors_json", "news_refs", "youtube_refs"):
            if hasattr(pick, attr):
                delattr(pick, attr)
        pick.reason = None
        log = bot._build_prediction_log(pick, date(2026, 5, 6), entry_price=100.0)
        assert log.reason is None
        assert log.factors_json is None
        assert log.news_refs is None
        assert log.youtube_refs is None
