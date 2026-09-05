"""
tests/test_twse_restrictions.py — 處置股／注意股過濾

為什麼這是最該補的一個缺口
--------------------------
其他缺口最多讓你少賺或多賠一點；買到處置股會讓你**出不掉**：

  處置股改為分盤集合競價（每 5 或 20 分鐘撮合一次），且多半禁止當沖。
  買進當下就註定要留到隔天 → T+2 交割義務 → 戶頭錢不夠就是違約交割。
  違約交割不是罰錢了事：券商註記、聯合徵信通報、可能的法律責任。

不對稱性極端明顯：漏擋一檔 → 可能違約交割；誤擋一檔 → 少賺一筆。
所以「抓不到資料」必須當成「不能買」，不能當成「沒有限制」。

失敗模式：靜默回空集合
----------------------
chip_data.fetch_institutional_investors 的既有寫法是網路失敗回 `{}`。
對籌碼評分那是可接受的（少一個評分項）；對安全過濾器那是致命的——
空集合的意思是「今天沒有任何股票被處置」，於是過濾器等於不存在，
而且不會有任何人知道。

本模組因此是三態而非二態：
  ok=True,  codes={...}   確實查到了（可能是空的，代表今天真的沒有）
  ok=True,  stale=True    今天查不到，用最近一次的快取（會告警）
  ok=False               完全沒有可用資料 → 呼叫端必須擋
"""
from datetime import date

import pytest

import twse_restrictions as tr


# ── TWSE rwd 端點的實際封包形狀 ───────────────────────────────────────────────
# 與 chip_data.py 解析 T86 的形狀一致（已在正式機驗證過的那個）：
#   {"stat": "OK", "fields": [...], "data": [[...], ...]}
# 欄位以**名稱**定位而非固定索引——TWSE 調整欄位順序時，固定索引會靜默取到
# 錯的那一欄，而名稱找不到會直接讓整批作廢（可觀測）。

def _payload(rows, code_field="證券代號", name_field="證券名稱", stat="OK"):
    return {
        "stat": stat,
        "fields": [code_field, name_field, "處置原因", "處置期間"],
        "data": rows,
    }


DISPOSED = _payload([
    ["3021", "鴻名", "連續三日達注意標準", "115/09/05~115/09/18"],
    ["6666 ", " 測試股 ", "當沖比過高", "115/09/01~115/09/12"],
])


class TestParsePayload:
    def test_extracts_codes(self):
        s = tr.parse_restricted_payload(DISPOSED, kind="disposition")
        assert set(s.codes) == {"3021", "6666"}

    def test_strips_whitespace_from_code(self):
        """TWSE 的欄位常帶前後空白，沒 strip 就永遠比對不到。"""
        s = tr.parse_restricted_payload(DISPOSED, kind="disposition")
        assert "6666" in s.codes

    def test_reason_includes_kind_label(self):
        s = tr.parse_restricted_payload(DISPOSED, kind="disposition")
        assert "處置" in s.codes["3021"]

    def test_empty_data_is_a_valid_answer(self):
        """今天真的沒有任何處置股 → ok=True 且 codes 為空。

        這和「查不到」是完全不同的兩件事，必須區分得開。
        """
        s = tr.parse_restricted_payload(_payload([]), kind="disposition")
        assert s.ok is True
        assert s.codes == {}

    def test_stat_not_ok_is_a_failure(self):
        s = tr.parse_restricted_payload(_payload([], stat="很抱歉，沒有符合條件的資料!"),
                                        kind="disposition")
        assert s.ok is False

    def test_missing_code_field_fails_loudly(self):
        """★ 欄位名對不上時整批作廢，不能默默回空集合。

        TWSE 改欄位是會發生的。固定索引在那一刻會靜默取到錯的欄位，
        名稱定位則會在這裡直接失敗——寧可擋住交易，也不要假裝有在過濾。
        """
        bad = _payload([["3021", "鴻名", "x", "y"]], code_field="不知道是什麼")
        s = tr.parse_restricted_payload(bad, kind="disposition")
        assert s.ok is False
        assert "欄位" in s.error

    def test_garbage_payload_fails(self):
        for junk in (None, [], "", {"stat": "OK"}, {"data": [[1]]}):
            assert tr.parse_restricted_payload(junk, kind="disposition").ok is False

    def test_rows_with_bad_shape_are_skipped_not_fatal(self):
        p = _payload([["3021", "鴻名", "x", "y"], [], None, ["", "空代號", "x", "y"]])
        s = tr.parse_restricted_payload(p, kind="disposition")
        assert s.ok is True
        assert set(s.codes) == {"3021"}


class TestMergeSources:
    def test_union_of_all_sources(self):
        a = tr.RestrictedSet(codes={"1101": "處置"}, ok=True)
        b = tr.RestrictedSet(codes={"2330": "注意"}, ok=True)
        m = tr.merge([a, b])
        assert set(m.codes) == {"1101", "2330"}

    def test_any_source_failing_fails_the_merge(self):
        """★ 一個來源掛掉就不能宣稱「查到了」——上櫃處置股照樣會鎖死你。"""
        a = tr.RestrictedSet(codes={"1101": "處置"}, ok=True)
        b = tr.RestrictedSet(codes={}, ok=False, error="連線逾時")
        m = tr.merge([a, b])
        assert m.ok is False
        assert "連線逾時" in m.error

    def test_partial_codes_still_reported_on_failure(self):
        """即使整批不可信，已知的那些仍要保留——它們是確定該擋的。"""
        a = tr.RestrictedSet(codes={"1101": "處置"}, ok=True)
        b = tr.RestrictedSet(codes={}, ok=False, error="x")
        assert "1101" in tr.merge([a, b]).codes

    def test_empty_input(self):
        assert tr.merge([]).ok is False


class TestCacheFallback:
    """今天抓不到時退回最近一次成功的結果。

    處置期間通常是連續多個交易日，昨天的清單今天多半仍然成立——
    用舊清單遠好過完全不擋，但必須標記 stale 並告警。
    """

    def _write(self, tmp_path, as_of, codes):
        p = tmp_path / "cache.json"
        tr.cache_save(tr.RestrictedSet(codes=codes, ok=True, as_of=as_of), str(p))
        return str(p)

    def test_uses_fresh_fetch_when_available(self, tmp_path):
        p = self._write(tmp_path, date(2026, 9, 1), {"1101": "舊的"})
        s = tr.load_restricted(
            cache_path=p, today=date(2026, 9, 5),
            fetch=lambda: tr.RestrictedSet(codes={"2330": "新的"}, ok=True),
        )
        assert set(s.codes) == {"2330"}
        assert s.stale is False

    def test_falls_back_to_cache_when_fetch_fails(self, tmp_path):
        p = self._write(tmp_path, date(2026, 9, 4), {"1101": "昨天的處置"})
        s = tr.load_restricted(
            cache_path=p, today=date(2026, 9, 5),
            fetch=lambda: tr.RestrictedSet(codes={}, ok=False, error="逾時"),
        )
        assert set(s.codes) == {"1101"}
        assert s.stale is True
        assert s.ok is True

    def test_fetch_raising_is_not_fatal(self, tmp_path):
        p = self._write(tmp_path, date(2026, 9, 4), {"1101": "x"})

        def boom():
            raise RuntimeError("網路炸了")

        s = tr.load_restricted(cache_path=p, today=date(2026, 9, 5), fetch=boom)
        assert s.stale is True and "1101" in s.codes

    def test_cache_too_old_is_not_used(self, tmp_path):
        """太舊的快取不可信——處置期滿了就該解禁，一直擋著也是錯的。"""
        p = self._write(tmp_path, date(2026, 8, 1), {"1101": "很久以前"})
        s = tr.load_restricted(
            cache_path=p, today=date(2026, 9, 5), max_stale_days=5,
            fetch=lambda: tr.RestrictedSet(codes={}, ok=False, error="逾時"),
        )
        assert s.ok is False

    def test_no_cache_and_failed_fetch_is_not_ok(self, tmp_path):
        s = tr.load_restricted(
            cache_path=str(tmp_path / "nope.json"), today=date(2026, 9, 5),
            fetch=lambda: tr.RestrictedSet(codes={}, ok=False, error="逾時"),
        )
        assert s.ok is False

    def test_successful_fetch_is_cached(self, tmp_path):
        p = str(tmp_path / "c.json")
        tr.load_restricted(cache_path=p, today=date(2026, 9, 5),
                           fetch=lambda: tr.RestrictedSet(codes={"2454": "處置"}, ok=True))
        again = tr.cache_load(p)
        assert "2454" in again.codes

    def test_failed_fetch_does_not_overwrite_cache(self, tmp_path):
        """★ 失敗的結果寫進快取，就等於把「查不到」變成「沒有限制」並永久化。"""
        p = self._write(tmp_path, date(2026, 9, 4), {"1101": "處置"})
        tr.load_restricted(cache_path=p, today=date(2026, 9, 5),
                           fetch=lambda: tr.RestrictedSet(codes={}, ok=False, error="x"))
        assert "1101" in tr.cache_load(p).codes

    def test_cache_save_refuses_a_failed_set_directly(self, tmp_path):
        """★ 直接測 cache_save 這道保護本身。

        load_restricted 的外層有 `if fresh.ok` 把關，所以透過它永遠測不到
        cache_save 內部那道；變異測試（拿掉內部保護）因此全綠。
        未來若有新的呼叫端直接呼叫 cache_save，就會把「查不到」寫成
        「沒有限制」並永久化——過濾器從此形同虛設且無人知曉。
        """
        p = tmp_path / "c.json"
        tr.cache_save(tr.RestrictedSet(codes={"1101": "處置"}, ok=True,
                                       as_of=date(2026, 9, 4)), str(p))
        tr.cache_save(tr.RestrictedSet(codes={}, ok=False, error="逾時"), str(p))
        assert "1101" in tr.cache_load(str(p)).codes

    def test_corrupt_cache_file_is_survivable(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text("{ not json")
        s = tr.load_restricted(cache_path=str(p), today=date(2026, 9, 5),
                               fetch=lambda: tr.RestrictedSet(codes={}, ok=False, error="x"))
        assert s.ok is False


class TestCheckIsFailClosed:
    """★ 整個模組的核心決定：查不到就是不能買。

    漏擋一檔 → 分盤交易出不掉 → 跨日 → 交割義務 → 可能違約。
    誤擋一檔 → 少賺一筆。
    不對稱到這個程度時，預設值只有一個合理選擇。
    """

    def _set(self, **kw):
        base = dict(codes={}, ok=True, as_of=date(2026, 9, 5), stale=False)
        base.update(kw)
        return tr.RestrictedSet(**base)

    def test_clean_code_passes(self):
        r = tr.check("2330", restricted=self._set())
        assert r.blocked is False

    def test_restricted_code_blocked(self):
        r = tr.check("3021", restricted=self._set(codes={"3021": "處置股票"}))
        assert r.blocked is True
        assert "處置" in r.reason

    def test_unknown_blocks_by_default(self):
        r = tr.check("2330", restricted=self._set(ok=False, error="逾時"))
        assert r.blocked is True

    def test_unknown_reason_says_it_could_not_check(self):
        """理由必須說清楚是「查不到」不是「這檔被處置」，否則會誤導排查方向。"""
        r = tr.check("2330", restricted=self._set(ok=False, error="逾時"))
        assert "查不到" in r.reason or "無法確認" in r.reason

    def test_unknown_can_be_opted_out_explicitly(self):
        """要放行必須是明確的選擇，不能是預設。"""
        r = tr.check("2330", restricted=self._set(ok=False, error="逾時"),
                     block_on_unknown=False)
        assert r.blocked is False

    def test_stale_data_still_blocks_listed_code(self):
        r = tr.check("3021", restricted=self._set(codes={"3021": "處置"}, stale=True))
        assert r.blocked is True

    def test_stale_data_passes_unlisted_code_but_flags_stale(self):
        r = tr.check("2330", restricted=self._set(stale=True))
        assert r.blocked is False
        assert r.stale is True

    def test_code_is_normalized(self):
        r = tr.check(" 3021 ", restricted=self._set(codes={"3021": "處置"}))
        assert r.blocked is True

    def test_empty_code_blocks(self):
        assert tr.check("", restricted=self._set()).blocked is True


class TestEnvSwitch:
    def test_block_on_unknown_defaults_true(self, monkeypatch):
        monkeypatch.delenv("DT_BLOCK_ON_UNKNOWN_RESTRICTION", raising=False)
        assert tr.block_on_unknown_default() is True

    def test_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("DT_BLOCK_ON_UNKNOWN_RESTRICTION", "false")
        assert tr.block_on_unknown_default() is False

    def test_garbage_value_stays_safe(self, monkeypatch):
        monkeypatch.setenv("DT_BLOCK_ON_UNKNOWN_RESTRICTION", "maybe")
        assert tr.block_on_unknown_default() is True


class TestSourcesAreDeclared:
    def test_every_source_has_url_and_kind(self):
        assert tr.SOURCES
        for s in tr.SOURCES:
            assert s.url.startswith("https://")
            assert s.kind in ("disposition", "attention")
            assert s.label


class TestTodayMemo:
    """9:10 一次判斷 8 檔，每檔重抓等於打 16 次 TWSE。"""

    def setup_method(self):
        tr.reset_memo()

    def teardown_method(self):
        tr.reset_memo()

    def test_fetches_once_for_many_lookups(self, tmp_path):
        calls = []

        def fetch():
            calls.append(1)
            return tr.RestrictedSet(codes={"3021": "處置"}, ok=True)

        for _ in range(8):
            tr.restricted_for_today(today=date(2026, 9, 5),
                                    cache_path=str(tmp_path / "c.json"),
                                    fetch=fetch)
        assert len(calls) == 1

    def test_failure_is_not_memoized(self, tmp_path):
        """★ 記住失敗，等於一次網路抖動就讓整天都查不到。"""
        calls = []

        def fetch():
            calls.append(1)
            return tr.RestrictedSet(codes={}, ok=False, error="逾時")

        for _ in range(3):
            tr.restricted_for_today(today=date(2026, 9, 5),
                                    cache_path=str(tmp_path / "c.json"),
                                    fetch=fetch)
        assert len(calls) == 3

    def test_new_day_refetches(self, tmp_path):
        calls = []

        def fetch():
            calls.append(1)
            return tr.RestrictedSet(codes={}, ok=True)

        p = str(tmp_path / "c.json")
        tr.restricted_for_today(today=date(2026, 9, 5), cache_path=p, fetch=fetch)
        tr.restricted_for_today(today=date(2026, 9, 8), cache_path=p, fetch=fetch)
        assert len(calls) == 2

    def test_check_uses_the_memo(self, tmp_path):
        """★ check() 若直接呼叫 load_restricted，8 檔就會打 16 次 TWSE。

        這正是加了記憶體快取卻忘了接上去時的樣子——功能看起來有，實際沒生效。
        """
        calls = []

        def fetch():
            calls.append(1)
            return tr.RestrictedSet(codes={}, ok=True)

        for code in ("2330", "2317", "2454", "3021"):
            tr.check(code, today=date(2026, 9, 5),
                     cache_path=str(tmp_path / "c.json"), fetch=fetch)
        assert len(calls) == 1


class TestBlockingVsWarning:
    """★ 處置與注意是兩件不同的事，不能一起擋。

      處置股 → 分盤集合競價（人工管制，約每 2 分鐘撮合一次）＋ 預收款券，
               多半禁止當沖。買了就出不掉 → 必須擋。
      注意股 → 交易方式完全正常，只是「可能轉處置」的提示。當沖做得了，
               而且注意股往往正是波動大的那些。全擋是過度保護。
    """

    def test_blocking_source_goes_to_codes(self):
        s = tr.parse_restricted_payload(DISPOSED, kind="disposition", blocking=True)
        assert set(s.codes) == {"3021", "6666"}
        assert s.warnings == {}

    def test_non_blocking_source_goes_to_warnings(self):
        s = tr.parse_restricted_payload(DISPOSED, kind="attention", blocking=False)
        assert set(s.warnings) == {"3021", "6666"}
        assert s.codes == {}

    def test_check_does_not_block_a_warning_only_code(self):
        r = tr.check("2454", restricted=tr.RestrictedSet(
            warnings={"2454": "注意股票"}, ok=True))
        assert r.blocked is False
        assert "注意" in r.warning

    def test_check_blocks_when_in_both(self):
        r = tr.check("2455", restricted=tr.RestrictedSet(
            codes={"2455": "處置股票"}, warnings={"2455": "注意股票"}, ok=True))
        assert r.blocked is True

    def test_warning_survives_the_unknown_path(self):
        """清單不完整時，已知的注意標記仍要帶出來。"""
        r = tr.check("2454", restricted=tr.RestrictedSet(
            warnings={"2454": "注意股票"}, ok=False, error="逾時"))
        assert "注意" in r.warning

    def test_merge_keeps_the_two_buckets_separate(self):
        a = tr.RestrictedSet(codes={"2455": "處置"}, ok=True)
        b = tr.RestrictedSet(warnings={"2454": "注意"}, ok=True)
        m = tr.merge([a, b])
        assert set(m.codes) == {"2455"}
        assert set(m.warnings) == {"2454"}

    def test_cache_round_trips_warnings(self, tmp_path):
        p = str(tmp_path / "c.json")
        tr.cache_save(tr.RestrictedSet(codes={"1": "a"}, warnings={"2": "b"},
                                       ok=True, as_of=date(2026, 9, 5)), p)
        back = tr.cache_load(p)
        assert back.warnings == {"2": "b"}

    def test_sources_declare_which_ones_block(self):
        kinds = {s.kind: s.blocking for s in tr.SOURCES}
        assert kinds["disposition"] is True
        assert kinds["attention"] is False


class TestNoticeNeedsDateRange:
    """★ 2026-09-05 實機探測發現：notice 端點不帶日期會回 0 列，stat 仍是 OK。

    那看起來就像「今天沒有注意股」——解析成功、集合為空、沒有任何錯誤，
    是最難察覺的一種失敗。punish 的回應裡自帶的連結揭露了正確的參數：
    notice.html?querytype=2&startDate=...&endDate=...
    """

    def test_notice_url_carries_a_date_range(self):
        notice = next(s for s in tr.SOURCES if s.kind == "attention")
        url = notice.resolve_url(date(2026, 9, 5))
        # 回看窗 7 天（原本 30 天會回 142 檔，含權證與 TDR）
        assert "startDate=20260829" in url
        assert "endDate=20260905" in url

    def test_punish_url_needs_no_substitution(self):
        punish = next(s for s in tr.SOURCES if s.kind == "disposition")
        assert punish.resolve_url(date(2026, 9, 5)) == punish.url

    def test_resolve_url_defaults_to_today(self):
        notice = next(s for s in tr.SOURCES if s.kind == "attention")
        assert date.today().strftime("%Y%m%d") in notice.resolve_url()

    def test_lookback_window_is_configurable(self):
        s = tr.Source("x", "u?s={start}&e={end}", "attention",
                      blocking=False, lookback_days=7)
        assert "s=20260829" in s.resolve_url(date(2026, 9, 5))


class TestDispositionWindow:
    """★ 2026-09-05 實機資料：punish 端點回的是**公告**，不是「今天有效的處置」。

    第一列 2455 的處置期間是 115/09/07～115/09/15，而當天是 115/09/05——
    還沒開始。同理，期間已經結束的公告也會留在清單裡。只取代號不看期間，
    等於把一檔股票擋到公告從端點消失為止。

    方向仍然保守：**看不懂期間就照擋**（fail-closed）。誤擋少賺，漏擋可能
    違約交割。但「看得懂而且已經過期」就該放行。
    """

    def _row(self, code, period):
        return {
            "stat": "OK",
            "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計",
                       "處置條件", "處置起迄時間", "處置措施"],
            "data": [[1, "115/09/04", code, "測試", 1, "x", period, "y"]],
        }

    def _parse(self, code, period, today):
        return tr.parse_restricted_payload(
            self._row(code, period), kind="disposition", today=today)

    def test_active_window_blocks(self):
        s = self._parse("2455", "115/09/07～115/09/15", date(2026, 9, 10))
        assert "2455" in s.codes

    def test_future_window_still_blocks(self):
        """尚未開始的處置照擋：兩天內就會生效，先擋沒有壞處。"""
        s = self._parse("2455", "115/09/07～115/09/15", date(2026, 9, 5))
        assert "2455" in s.codes

    def test_expired_window_is_released(self):
        """★ 處置期滿就該解禁，一直擋著是錯的。"""
        s = self._parse("2455", "115/08/07～115/08/15", date(2026, 9, 5))
        assert s.codes == {}
        assert s.ok is True

    def test_last_day_still_blocks(self):
        s = self._parse("2455", "115/09/07～115/09/15", date(2026, 9, 15))
        assert "2455" in s.codes

    def test_day_after_is_released(self):
        s = self._parse("2455", "115/09/07～115/09/15", date(2026, 9, 16))
        assert s.codes == {}

    def test_unparseable_period_blocks(self):
        """看不懂就擋——保守方向。"""
        s = self._parse("2455", "民國某天到某天", date(2026, 9, 5))
        assert "2455" in s.codes

    def test_missing_period_column_blocks_everything(self):
        """沒有期間欄位時退回舊行為（全擋），不能因此放行。"""
        p = {"stat": "OK", "fields": ["證券代號", "證券名稱"],
             "data": [["2455", "全新"]]}
        s = tr.parse_restricted_payload(p, kind="disposition",
                                        today=date(2026, 9, 5))
        assert "2455" in s.codes

    def test_reason_carries_the_period(self):
        s = self._parse("2455", "115/09/07～115/09/15", date(2026, 9, 10))
        assert "09/15" in s.codes["2455"]

    def test_attention_source_ignores_window(self):
        """注意股沒有處置期間欄位，不該因此整批作廢。"""
        p = {"stat": "OK",
             "fields": ["編號", "證券代號", "證券名稱", "日期"],
             "data": [[1, "2454", "聯發科", "115.09.04"]]}
        s = tr.parse_restricted_payload(p, kind="attention", blocking=False,
                                        today=date(2026, 9, 5))
        assert "2454" in s.warnings


class TestRocDate:
    def test_slash_format(self):
        assert tr.parse_roc_date("115/09/07") == date(2026, 9, 7)

    def test_dot_format(self):
        assert tr.parse_roc_date("115.09.04") == date(2026, 9, 4)

    def test_junk_returns_none(self):
        for j in ("", None, "abc", "115/13/99", "9/7"):
            assert tr.parse_roc_date(j) is None

    def test_period_with_fullwidth_tilde(self):
        assert tr.parse_roc_period("115/09/07～115/09/15") == (
            date(2026, 9, 7), date(2026, 9, 15))

    def test_period_with_ascii_tilde(self):
        assert tr.parse_roc_period("115/09/07~115/09/15") == (
            date(2026, 9, 7), date(2026, 9, 15))

    def test_period_junk_returns_none(self):
        assert tr.parse_roc_period("不知道") is None


class TestAttentionLookbackIsShort:
    """142 檔（30 天累積，含權證與 TDR）的警告等於沒有警告。

    注意股的意義是「最近連續達標、可能轉處置」，看一個月前的公告沒有價值，
    只會訓練人忽略警告。
    """

    def test_default_lookback_is_one_trading_week(self):
        notice = next(s for s in tr.SOURCES if s.kind == "attention")
        assert notice.lookback_days <= 7
