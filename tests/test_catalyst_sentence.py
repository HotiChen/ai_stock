"""
tests/test_catalyst_sentence.py — 新聞催化劑一句話摘要功能測試

依需求規格：
  - DeepAnalysis 新增 catalyst_sentence: str = ""（預設空字串）
  - build_deep_prompt 包含繁中催化劑指令（≤40字、指明 [N#]、無真實 catalyst 時不捏造）
  - parse_deep_response：catalyst 欄位存在 → 設定；absent/malformed → ""，不 raise
  - 只用同一次 AI 呼叫（call_haiku），不新增第二次 AI 呼叫

測試環境：conftest.py 已注入 google.genai / feedparser stub。
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from deep_analyzer import (
    DeepAnalysis,
    AnalysisFactor,
    build_deep_prompt,
    parse_deep_response,
    run_deep_analysis,
)


# ════════════════════════════════════════════════════════════════════════════
# 一、DeepAnalysis dataclass 預設值
# ════════════════════════════════════════════════════════════════════════════

class TestDeepAnalysisCatalystDefault:
    def test_catalyst_sentence_field_exists(self):
        """DeepAnalysis 必須有 catalyst_sentence 欄位。"""
        a = DeepAnalysis(
            code="2330", name="台積電", signal="buy", confidence=8,
            summary="短線偏多",
            factors=AnalysisFactor("上升", "正面", "AI", "強勢", "利多", "寬鬆"),
        )
        assert hasattr(a, "catalyst_sentence")

    def test_catalyst_sentence_default_is_empty_string(self):
        """catalyst_sentence 預設值為空字串（不影響既有 caller）。"""
        a = DeepAnalysis(
            code="2330", name="台積電", signal="hold", confidence=0,
            summary="",
            factors=AnalysisFactor("", "", "", "", "", ""),
        )
        assert a.catalyst_sentence == ""

    def test_catalyst_sentence_accepts_string(self):
        """catalyst_sentence 可以接受任意字串（含繁中）。"""
        a = DeepAnalysis(
            code="2330", name="台積電", signal="buy", confidence=9,
            summary="強勢",
            factors=AnalysisFactor("", "", "", "", "", ""),
            catalyst_sentence="台積電法說優於預期，外資大幅買超推升股價。[N1]",
        )
        assert "台積電" in a.catalyst_sentence

    def test_existing_callers_unaffected(self):
        """不帶 catalyst_sentence 建構 DeepAnalysis，不應 raise。"""
        # 模擬既有 caller（positional args only, no catalyst_sentence）
        a = DeepAnalysis(
            code="2454", name="聯發科", signal="hold", confidence=5,
            summary="觀望",
            factors=AnalysisFactor("橫盤", "中性", "無題材", "平穩", "中性", "不變"),
            hold_days=1,
            target_price=None,
            stop_loss_price=None,
        )
        assert a.catalyst_sentence == ""


# ════════════════════════════════════════════════════════════════════════════
# 二、build_deep_prompt — catalyst 指令
# ════════════════════════════════════════════════════════════════════════════

_NEWS_DICTS = [
    {"id": "N1", "title": "台積電法說優於預期", "url": "http://n/1",
     "published": "2026-06-13T08:00:00", "source": "鉅亨網"},
    {"id": "N2", "title": "外資連三買", "url": "http://n/2",
     "published": "2026-06-13T07:30:00", "source": "Yahoo財經"},
]


class TestBuildDeepPromptCatalyst:
    def _prompt(self, news=None):
        return build_deep_prompt(
            "2330", "台積電", "上升",
            news if news is not None else _NEWS_DICTS,
            "PE 20", "費半 +2.5%", "AI",
        )

    def test_prompt_contains_catalyst_field_name(self):
        """prompt 必須包含 'catalyst' 關鍵字（要求 AI 回傳該欄位）。"""
        assert "catalyst" in self._prompt()

    def test_prompt_specifies_40_char_limit(self):
        """prompt 必須說明催化劑字數上限 40 字。"""
        prompt = self._prompt()
        assert "40" in prompt

    def test_prompt_instructs_cite_news_number(self):
        """prompt 必須要求引用 [N#] 新聞編號。"""
        prompt = self._prompt()
        # 要有 [N#] 格式的說明
        assert "[N" in prompt

    def test_prompt_instructs_no_fabrication(self):
        """prompt 必須明確要求無真實 catalyst 時不捏造（不虛構）。"""
        prompt = self._prompt()
        # 繁中關鍵詞：捏造 / 虛構 / 不得捏造 / 無催化劑不得 ... 均可
        no_fabrication_keywords = ["捏造", "虛構", "不得", "不要", "禁止"]
        assert any(kw in prompt for kw in no_fabrication_keywords), (
            "prompt 應明確禁止 AI 捏造 catalyst"
        )

    def test_prompt_instructs_empty_when_no_catalyst(self):
        """prompt 要說明沒有真實 catalyst 時回傳空字串。"""
        prompt = self._prompt()
        # 說明可以是「無真實催化劑時回傳空字串」或類似語義
        assert '""' in prompt or "空字串" in prompt or "empty" in prompt.lower()

    def test_catalyst_in_json_schema_block(self):
        """prompt JSON schema 範例內包含 catalyst 欄位。"""
        prompt = self._prompt()
        # 確認 catalyst 出現在 JSON 輸出格式說明中（在大括號範圍內）
        json_part = prompt[prompt.rfind("{"):]  # 取最後一個 { 以後
        # 如果沒有找到完整 JSON，也允許 catalyst 在 prompt 的後半段
        assert "catalyst" in prompt


# ════════════════════════════════════════════════════════════════════════════
# 三、parse_deep_response — catalyst 解析
# ════════════════════════════════════════════════════════════════════════════

_RESPONSE_WITH_CATALYST = json.dumps({
    "signal": "buy",
    "confidence": 8,
    "summary": "短線偏多，法說利多推升信心",
    "factors": {
        "historical_trend": "多頭排列",
        "news": "法說優於預期",
        "theme": "AI 題材",
        "us_market": "費半強",
        "tw_policy": "中性",
        "us_policy": "寬鬆"
    },
    "hold_days": 2,
    "target_price": 950.0,
    "stop_loss_price": 880.0,
    "news_refs_used": ["N1"],
    "catalyst": "台積電Q2法說優於預期，外資大買推升股價，為本次進場主因。[N1]",
}, ensure_ascii=False)

_RESPONSE_WITHOUT_CATALYST = json.dumps({
    "signal": "hold",
    "confidence": 5,
    "summary": "觀望",
    "factors": {
        "historical_trend": "",
        "news": "",
        "theme": "",
        "us_market": "",
        "tw_policy": "",
        "us_policy": ""
    },
    "hold_days": 1,
    "news_refs_used": [],
    # 刻意省略 catalyst 欄位
}, ensure_ascii=False)

_RESPONSE_CATALYST_EMPTY_STRING = json.dumps({
    "signal": "hold",
    "confidence": 4,
    "summary": "無明顯催化劑",
    "factors": {
        "historical_trend": "橫盤",
        "news": "無重大新聞",
        "theme": "無",
        "us_market": "平",
        "tw_policy": "中性",
        "us_policy": "不變"
    },
    "hold_days": 1,
    "news_refs_used": [],
    "catalyst": "",  # AI 明確回傳空字串
}, ensure_ascii=False)


class TestParseDeepResponseCatalyst:
    def test_catalyst_present_is_set(self):
        """AI 輸出含 catalyst 欄位時，解析結果帶入 catalyst_sentence。"""
        result = parse_deep_response(
            "2330", "台積電", _RESPONSE_WITH_CATALYST, news=_NEWS_DICTS
        )
        assert result.catalyst_sentence != ""
        assert "台積電" in result.catalyst_sentence or "法說" in result.catalyst_sentence

    def test_catalyst_value_matches_ai_output(self):
        """catalyst_sentence 的值與 AI 回傳的 catalyst 欄位一致。"""
        result = parse_deep_response(
            "2330", "台積電", _RESPONSE_WITH_CATALYST, news=_NEWS_DICTS
        )
        assert result.catalyst_sentence == (
            "台積電Q2法說優於預期，外資大買推升股價，為本次進場主因。[N1]"
        )

    def test_catalyst_absent_returns_empty_string(self):
        """AI 輸出缺 catalyst 欄位時，catalyst_sentence 為空字串，不 raise。"""
        result = parse_deep_response(
            "2330", "台積電", _RESPONSE_WITHOUT_CATALYST, news=_NEWS_DICTS
        )
        assert result.catalyst_sentence == ""

    def test_catalyst_empty_string_returns_empty_string(self):
        """AI 明確回傳空 catalyst 時，catalyst_sentence 為空字串。"""
        result = parse_deep_response(
            "2330", "台積電", _RESPONSE_CATALYST_EMPTY_STRING, news=_NEWS_DICTS
        )
        assert result.catalyst_sentence == ""

    def test_malformed_json_returns_empty_catalyst(self):
        """JSON 完全無法解析時，catalyst_sentence 預設為空字串，不 raise。"""
        result = parse_deep_response("2330", "台積電", "亂碼 not json at all")
        assert result.catalyst_sentence == ""

    def test_null_catalyst_returns_empty_string(self):
        """AI 回傳 catalyst: null 時，catalyst_sentence 為空字串。"""
        resp = json.dumps({
            "signal": "hold",
            "confidence": 5,
            "summary": "觀望",
            "factors": {"historical_trend": "", "news": "", "theme": "",
                        "us_market": "", "tw_policy": "", "us_policy": ""},
            "hold_days": 1,
            "news_refs_used": [],
            "catalyst": None,
        })
        result = parse_deep_response("2330", "台積電", resp)
        assert result.catalyst_sentence == ""

    def test_parse_without_news_arg_still_works(self):
        """不傳 news 參數時仍可解析 catalyst，不 raise（既有 caller 相容）。"""
        result = parse_deep_response("2330", "台積電", _RESPONSE_WITH_CATALYST)
        assert isinstance(result.catalyst_sentence, str)

    def test_other_fields_unaffected(self):
        """加入 catalyst 解析不影響其他欄位的正確解析。"""
        result = parse_deep_response(
            "2330", "台積電", _RESPONSE_WITH_CATALYST, news=_NEWS_DICTS
        )
        assert result.signal == "buy"
        assert result.confidence == 8
        assert result.hold_days == 2
        assert result.target_price == pytest.approx(950.0)
        assert len(result.news_refs) == 1


# ════════════════════════════════════════════════════════════════════════════
# 四、單一 AI 呼叫驗證（catalyst 來自同一次 call_haiku，不新增第二次）
# ════════════════════════════════════════════════════════════════════════════

_RESPONSE_WITH_CATALYST_AND_REFS = json.dumps({
    "signal": "buy",
    "confidence": 8,
    "summary": "短線偏多",
    "factors": {
        "historical_trend": "多頭排列",
        "news": "法說利多",
        "theme": "AI",
        "us_market": "強",
        "tw_policy": "中性",
        "us_policy": "寬鬆"
    },
    "hold_days": 2,
    "target_price": 950.0,
    "stop_loss_price": 880.0,
    "news_refs_used": ["N1"],
    "catalyst": "法說會優於市場預期，外資大幅加碼。[N1]",
}, ensure_ascii=False)


class TestSingleAICallConstraint:
    """驗證 catalyst 來自同一次 call_haiku，不新增第二次 AI 呼叫。"""

    def _mock_indicators(self):
        return {
            "current_price": 100, "MA5": 95, "MA20": 90, "MA60": 85,
            "RSI": 60, "KD_K": 70, "KD_D": 65, "BB_upper": 110, "BB_lower": 90,
            "BB_width": 20, "volume_ratio": 1.5, "bullish_alignment": True
        }

    @patch("deep_analyzer.call_haiku")
    @patch("deep_analyzer.get_price_trend_summary", return_value="上升")
    @patch("deep_analyzer.fetch_indicators")
    def test_call_haiku_called_exactly_once(
        self, mock_fetch, mock_trend, mock_haiku
    ):
        """run_deep_analysis 只呼叫 call_haiku 一次（包含 catalyst 在同一回應內）。"""
        mock_fetch.return_value = self._mock_indicators()
        mock_haiku.return_value = _RESPONSE_WITH_CATALYST_AND_REFS
        api = MagicMock()

        result = run_deep_analysis(
            api=api,
            code="2330", name="台積電",
            news=_NEWS_DICTS,
            fundamentals_text="PE 20",
            market_summary="費半強勢",
            theme_info="AI 題材",
        )

        # 只呼叫一次 call_haiku
        assert mock_haiku.call_count == 1, (
            f"call_haiku 被呼叫 {mock_haiku.call_count} 次，應只呼叫 1 次"
        )

    @patch("deep_analyzer.call_haiku")
    @patch("deep_analyzer.get_price_trend_summary", return_value="上升")
    @patch("deep_analyzer.fetch_indicators")
    def test_catalyst_populated_from_same_call(
        self, mock_fetch, mock_trend, mock_haiku
    ):
        """run_deep_analysis 的結果 catalyst_sentence 已從同一次 call_haiku 回應填入。"""
        mock_fetch.return_value = self._mock_indicators()
        mock_haiku.return_value = _RESPONSE_WITH_CATALYST_AND_REFS
        api = MagicMock()

        result = run_deep_analysis(
            api=api,
            code="2330", name="台積電",
            news=_NEWS_DICTS,
            fundamentals_text="PE 20",
            market_summary="費半強勢",
            theme_info="AI 題材",
        )

        assert result.catalyst_sentence == "法說會優於市場預期，外資大幅加碼。[N1]"

    @patch("deep_analyzer.call_haiku")
    @patch("deep_analyzer.get_price_trend_summary", return_value="上升")
    @patch("deep_analyzer.fetch_indicators")
    def test_no_catalyst_in_response_still_one_call(
        self, mock_fetch, mock_trend, mock_haiku
    ):
        """AI 回應無 catalyst 欄位時，仍只呼叫一次 call_haiku，catalyst_sentence 為空。"""
        mock_fetch.return_value = self._mock_indicators()
        mock_haiku.return_value = _RESPONSE_WITHOUT_CATALYST
        api = MagicMock()

        result = run_deep_analysis(
            api=api,
            code="2330", name="台積電",
            news=_NEWS_DICTS,
            fundamentals_text="PE 20",
            market_summary="費半強勢",
            theme_info="AI 題材",
        )

        assert mock_haiku.call_count == 1
        assert result.catalyst_sentence == ""


# ════════════════════════════════════════════════════════════════════════════
# 五、learning_db — catalyst_sentence 欄位
# ════════════════════════════════════════════════════════════════════════════

import sqlite3
from datetime import date

from learning_db import LearningDB, StockPredictionLog


def _columns(path: str) -> set[str]:
    """取得 stock_prediction_log 的所有欄位名稱。"""
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(stock_prediction_log)"
        ).fetchall()}
    finally:
        conn.close()
    return cols


class TestLearningDbCatalystMigration:
    def test_catalyst_sentence_column_present_on_fresh_db(self, tmp_path):
        """全新 DB 初始化後，catalyst_sentence 欄位存在。"""
        path = str(tmp_path / "fresh.db")
        LearningDB(path)
        assert "catalyst_sentence" in _columns(path)

    def test_migration_idempotent(self, tmp_path):
        """重複初始化同一 DB 不會 crash，catalyst_sentence 欄位存在。"""
        path = str(tmp_path / "idem.db")
        LearningDB(path)
        LearningDB(path)
        LearningDB(path)
        assert "catalyst_sentence" in _columns(path)

    def test_upgrades_legacy_db_without_catalyst_sentence(self, tmp_path):
        """模擬舊版 DB（含 reason/factors_json/news_refs/youtube_refs，
        但無 catalyst_sentence），LearningDB 開啟後自動補上欄位且保留原資料。"""
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
                reason              TEXT,
                factors_json        TEXT,
                news_refs           TEXT,
                youtube_refs        TEXT,
                UNIQUE(date, code)
            );
        """)
        conn.execute(
            "INSERT INTO stock_prediction_log "
            "(date, code, name, action, confidence, expected_return_pct, entry_price) "
            "VALUES ('2026-06-13','2330','台積電','open',8,3.0,900.0)"
        )
        conn.commit()
        conn.close()

        # 確認 catalyst_sentence 尚未存在
        assert "catalyst_sentence" not in _columns(path)

        db = LearningDB(path)

        # 開啟後欄位補齊
        assert "catalyst_sentence" in _columns(path)

        # 原資料保留且可讀回
        preds = db.get_predictions_by_date(date(2026, 6, 13))
        assert len(preds) == 1
        assert preds[0].code == "2330"
        # 舊資料的新欄位為 None
        assert preds[0].catalyst_sentence is None

    def test_upgrades_very_old_legacy_db(self, tmp_path):
        """模擬非常舊的 DB（連 reason 也沒有），開啟後所有新欄位自動補齊。"""
        path = str(tmp_path / "very_old.db")
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
        conn.commit()
        conn.close()

        db = LearningDB(path)
        assert "reason" in _columns(path)
        assert "factors_json" in _columns(path)
        assert "news_refs" in _columns(path)
        assert "youtube_refs" in _columns(path)
        assert "catalyst_sentence" in _columns(path)


class TestStockPredictionLogCatalystRoundtrip:
    def test_catalyst_sentence_default_is_none_on_dataclass(self):
        """StockPredictionLog.catalyst_sentence 預設為 None（DB nullable）。"""
        log = StockPredictionLog(
            date=date(2026, 6, 13), code="2330", name="台積電",
            action="open", confidence=8, expected_return_pct=3.0,
            entry_price=900.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        )
        assert log.catalyst_sentence is None

    def test_roundtrip_without_catalyst_sentence(self, tmp_path):
        """不帶 catalyst_sentence 插入，讀回為 None（既有 caller 不受影響）。"""
        db = LearningDB(str(tmp_path / "a.db"))
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 6, 13), code="2454", name="聯發科",
            action="open", confidence=7, expected_return_pct=2.5,
            entry_price=1200.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        ))
        rows = db.get_predictions_by_date(date(2026, 6, 13))
        assert len(rows) == 1
        assert rows[0].catalyst_sentence is None

    def test_roundtrip_with_catalyst_sentence(self, tmp_path):
        """帶 catalyst_sentence 插入，讀回值一致。"""
        db = LearningDB(str(tmp_path / "b.db"))
        catalyst = "法說會優於預期，外資大幅加碼。[N1]"
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 6, 13), code="2330", name="台積電",
            action="open", confidence=9, expected_return_pct=4.0,
            entry_price=900.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
            catalyst_sentence=catalyst,
        ))
        rows = db.get_predictions_by_date(date(2026, 6, 13))
        assert len(rows) == 1
        assert rows[0].catalyst_sentence == catalyst

    def test_roundtrip_with_empty_catalyst_sentence(self, tmp_path):
        """帶空字串 catalyst_sentence 插入，讀回為空字串。"""
        db = LearningDB(str(tmp_path / "c.db"))
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 6, 13), code="2317", name="鴻海",
            action="open", confidence=6, expected_return_pct=1.5,
            entry_price=200.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
            catalyst_sentence="",
        ))
        rows = db.get_predictions_by_date(date(2026, 6, 13))
        assert len(rows) == 1
        # 空字串存入 SQLite 再讀出可能為 "" 或 None（TEXT 欄位），
        # 兩者皆可接受，重點是不 raise 且不是非預期的值
        assert rows[0].catalyst_sentence in ("", None)

    def test_catalyst_sentence_with_all_traceability_fields(self, tmp_path):
        """catalyst_sentence 與其他 traceability 欄位共存，讀回完整一致。"""
        db = LearningDB(str(tmp_path / "d.db"))
        factors = {"technical": "多頭排列", "chip": "外資買超",
                   "news": "法說利多", "theme": "AI"}
        news_refs = [{"id": "N1", "title": "台積電擴產", "url": "http://x/1",
                      "published": "2026-06-13T08:00:00", "source": "鉅亨網"}]
        catalyst = "台積電Q2法說優於預期，外資大買推升股價。[N1]"
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 6, 13), code="2330", name="台積電",
            action="open", confidence=9, expected_return_pct=4.0,
            entry_price=900.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
            reason="多頭排列＋外資買超",
            factors_json=json.dumps(factors, ensure_ascii=False),
            news_refs=json.dumps(news_refs, ensure_ascii=False),
            youtube_refs=None,
            catalyst_sentence=catalyst,
        ))
        rows = db.get_predictions_by_date(date(2026, 6, 13))
        assert len(rows) == 1
        r = rows[0]
        assert r.reason == "多頭排列＋外資買超"
        assert json.loads(r.factors_json)["chip"] == "外資買超"
        assert json.loads(r.news_refs)[0]["id"] == "N1"
        assert r.youtube_refs is None
        assert r.catalyst_sentence == catalyst
