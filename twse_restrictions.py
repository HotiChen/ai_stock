"""
twse_restrictions.py — 處置股／注意股過濾（買進前的硬性守衛）

為什麼這是最該補的一個缺口
--------------------------
其他缺口最多讓你少賺或多賠一點；買到處置股會讓你**出不掉**：

  處置股改為分盤集合競價（每 5 或 20 分鐘撮合一次），且多半禁止當沖。
  買進當下就註定要留到隔天 → T+2 交割義務 → 戶頭錢不夠就是違約交割。
  違約交割不是罰錢了事：券商註記、聯合徵信通報、可能的法律責任。

三態，不是二態
--------------
既有的 chip_data.fetch_institutional_investors 網路失敗時回 `{}`。對籌碼
評分那是可接受的（少一個評分項）；對安全過濾器那是致命的——空集合的意思是
「今天沒有任何股票被處置」，於是過濾器等於不存在，而且沒有人會知道。

所以本模組區分三種狀態：

  ok=True,  stale=False   確實查到了（codes 可能是空的，代表今天真的沒有）
  ok=True,  stale=True    今天查不到，改用最近一次的快取（會告警）
  ok=False                完全沒有可用資料 → **買進必須擋下**

fail-closed 的理由
------------------
漏擋一檔 → 分盤交易出不掉 → 跨日 → 交割義務 → 可能違約。
誤擋一檔 → 少賺一筆。
不對稱到這個程度，預設值只有一個合理選擇。要放行必須明確設
DT_BLOCK_ON_UNKNOWN_RESTRICTION=false，是一個有意識的決定。

欄位以名稱定位
--------------
TWSE 調整欄位順序是會發生的。固定索引在那一刻會**靜默**取到錯的那一欄；
名稱找不到則整批作廢（ok=False）→ 買進被擋 → 有人會發現。

⚠ 端點形狀待實機確認
--------------------
撰寫當下開發環境的網路政策擋掉了 www.twse.com.tw，無法實地驗證 punish /
notice 端點回傳的 fields 名稱。封包外層（stat / fields / data）與
chip_data.py 解析 T86 的形狀相同，那個已在正式機驗證過；欄位名則是依
TWSE 慣例（證券代號 / 證券名稱）並保留多個別名。

**上線前請先跑 `python3 tools/probe_twse_restrictions.py`**，它會把真實
payload 存到 data/ 並印出實際欄位名。名稱對不上時本模組會回 ok=False，
表現為「所有買單都被擋」而不是「靜默不過濾」——會被立刻發現，不會被誤信。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = "data/twse_restrictions.json"

#: 快取最多能舊幾個日曆日。處置期通常是連續多個交易日，昨天的清單今天多半
#: 仍成立；但太舊就不可信了——處置期滿本該解禁，一直擋著也是錯的。
DEFAULT_MAX_STALE_DAYS = 5

REQUEST_TIMEOUT = 10

#: 代號欄可能的名稱。TWSE 各端點用字不完全一致，多列幾個別名。
_CODE_FIELDS = ("證券代號", "股票代號", "有價證券代號", "代號")
_NAME_FIELDS = ("證券名稱", "股票名稱", "有價證券名稱", "名稱")

_KIND_LABEL = {
    "disposition": "處置股票（分盤交易，多半禁止當沖）",
    "attention": "注意股票（交易正常，但可能轉處置）",
}


@dataclass(frozen=True)
class Source:
    label: str
    url: str          # 可含 {start} / {end}（YYYYMMDD），fetch 時代入
    kind: str         # "disposition" | "attention"
    blocking: bool    # True = 買不到，擋單；False = 只是風險提示
    lookback_days: int = 30

    def resolve_url(self, today: Optional[date] = None) -> str:
        if "{start}" not in self.url and "{end}" not in self.url:
            return self.url
        today = today or date.today()
        start = today - timedelta(days=self.lookback_days)
        return self.url.format(start=start.strftime("%Y%m%d"),
                               end=today.strftime("%Y%m%d"))


#: 資料來源。
#:
#: 處置 vs 注意是**兩件不同的事**，不能一起擋：
#:   處置股 → 分盤集合競價（人工管制，約每 2 分鐘撮合一次）＋ 預收款券，
#:            多半禁止當沖。買了就出不掉 → 必須擋。
#:   注意股 → 交易方式完全正常，只是「連續達注意標準、可能轉處置」的提示。
#:            當沖是可以做的，而且注意股往往正是波動大的那些。
#:            全擋等於每天砍掉一批合格標的，屬於過度保護。
#:
#: notice 端點**必須帶日期區間**，不帶會回 0 列且 stat 仍是 OK——
#: 那看起來就像「今天沒有注意股」，是最難察覺的一種錯。
#:
#: 上櫃（TPEx）的處置股同樣會鎖死你，端點確認後再補進來——在那之前這份
#: 清單只涵蓋上市，這件事必須寫在這裡而不是靠人記得。
SOURCES: tuple[Source, ...] = (
    Source("TWSE 處置有價證券",
           "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json",
           "disposition", blocking=True),
    Source("TWSE 注意有價證券",
           "https://www.twse.com.tw/rwd/zh/announcement/notice"
           "?response=json&querytype=1&startDate={start}&endDate={end}",
           # 30 天累積會回 142 檔（含權證與 TDR），對 142 檔開火的警告等於
           # 沒有警告。注意股的意義是「最近連續達標、可能轉處置」，看一個月
           # 前的公告沒有價值。一個交易週足夠。
           "attention", blocking=False, lookback_days=7),
)


@dataclass
class RestrictedSet:
    """一批「不可買」的代號。

    codes  代號 → 原因
    ok     False = **查不到**，不是「沒有限制」。呼叫端必須據此擋單
    stale  True  = 用的是舊快取
    """
    codes: dict = field(default_factory=dict)
    #: 不擋單、只提示的標記（注意股）。代號 → 原因
    warnings: dict = field(default_factory=dict)
    ok: bool = False
    error: str = ""
    as_of: Optional[date] = None
    stale: bool = False


@dataclass
class Restriction:
    """單一代號的查詢結果。

    blocked  不能買
    warning  可以買，但有風險標記（注意股）
    """
    code: str
    blocked: bool
    reason: str = ""
    warning: str = ""
    as_of: Optional[date] = None
    stale: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# 解析
# ══════════════════════════════════════════════════════════════════════════════

#: 處置期間欄位可能的名稱
_PERIOD_FIELDS = ("處置起迄時間", "處置期間", "起迄")

_PERIOD_SEP = ("～", "~", "－", "-", "至")


def parse_roc_date(s) -> Optional[date]:
    """民國日期 → 西元。接受 115/09/07 與 115.09.04 兩種寫法。"""
    if not s:
        return None
    t = str(s).strip().replace(".", "/")
    parts = t.split("/")
    if len(parts) != 3:
        return None
    try:
        y, m, d = (int(x) for x in parts)
    except ValueError:
        return None
    if y < 1 or not (1 <= m <= 12) or not (1 <= d <= 31):
        return None
    try:
        return date(y + 1911, m, d)
    except ValueError:
        return None


def parse_roc_period(s) -> Optional[tuple]:
    """"115/09/07～115/09/15" → (date, date)。看不懂回 None。"""
    if not s:
        return None
    t = str(s).strip()
    for sep in _PERIOD_SEP:
        if sep in t:
            a, _, b = t.partition(sep)
            start, end = parse_roc_date(a), parse_roc_date(b)
            if start and end:
                return start, end
    return None


def _find_field(fields: list, candidates: Iterable[str]) -> Optional[int]:
    for i, f in enumerate(fields):
        t = str(f).strip()
        for c in candidates:
            if c in t:
                return i
    return None


def parse_restricted_payload(payload, kind: str, blocking: bool = True,
                             today: Optional[date] = None) -> RestrictedSet:
    """把 TWSE rwd 封包轉成 RestrictedSet。任何形狀不符都回 ok=False。

    punish 端點回的是**公告**不是「今天有效的處置」：期間可能還沒開始
    （2026-09-05 實測，2455 的期間是 115/09/07～115/09/15），也可能已經結束。
    有處置期間欄位時據此過濾——期滿就該解禁，一直擋著是錯的。

    **看不懂期間就照擋**：誤擋少賺，漏擋可能違約交割。
    """
    if not isinstance(payload, dict):
        return RestrictedSet(ok=False, error="回應不是 JSON 物件")

    stat = str(payload.get("stat", ""))
    if stat.upper() != "OK":
        return RestrictedSet(ok=False, error=f"stat={stat or '缺'}")

    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        return RestrictedSet(ok=False, error="缺少 fields 欄位定義")

    idx = _find_field(fields, _CODE_FIELDS)
    if idx is None:
        return RestrictedSet(
            ok=False,
            error=f"找不到代號欄位（實際欄位：{'、'.join(map(str, fields))}）",
        )

    label = _KIND_LABEL.get(kind, kind)
    today = today or date.today()
    p_idx = _find_field(fields, _PERIOD_FIELDS)

    codes: dict[str, str] = {}
    for row in payload.get("data") or []:
        try:
            code = str(row[idx]).strip()
        except (TypeError, IndexError, KeyError):
            continue          # 單列壞掉跳過；整批形狀壞掉才作廢
        if not code:
            continue

        reason = label
        if p_idx is not None:
            try:
                raw = row[p_idx]
            except (TypeError, IndexError, KeyError):
                raw = None
            period = parse_roc_period(raw)
            if period is not None:
                start, end = period
                if today > end:
                    continue          # 期滿解禁
                reason = f"{label}　期間 {start:%m/%d}–{end:%m/%d}"
            # 解析不出來 → 不 continue，照擋（fail-closed）

        # 同一檔可能有多列公告（第一次／第二次處置）。保留期間最長的那個
        # 描述，避免顯示成已經過期的那一次。
        if code not in codes or len(reason) > len(codes[code]):
            codes[code] = reason

    if blocking:
        return RestrictedSet(codes=codes, ok=True)
    return RestrictedSet(warnings=codes, ok=True)


def merge(sets: Iterable[RestrictedSet]) -> RestrictedSet:
    """聯集。**任一來源失敗就整體 ok=False**。

    已知的代號仍然保留——它們是確定該擋的，只是清單不完整。
    """
    sets = list(sets)
    if not sets:
        return RestrictedSet(ok=False, error="沒有任何資料來源")

    codes: dict[str, str] = {}
    warnings: dict[str, str] = {}
    errors = []
    for s in sets:
        codes.update(s.codes)
        warnings.update(s.warnings)
        if not s.ok:
            errors.append(s.error or "未知錯誤")
    return RestrictedSet(
        codes=codes, warnings=warnings, ok=not errors, error="；".join(errors),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 抓取與快取
# ══════════════════════════════════════════════════════════════════════════════

def fetch_source(src: Source, timeout: int = REQUEST_TIMEOUT,
                 today: Optional[date] = None) -> RestrictedSet:
    try:
        import requests
        resp = requests.get(src.resolve_url(today), timeout=timeout)
        payload = resp.json()
    except Exception as e:
        return RestrictedSet(ok=False, error=f"{src.label}：{e}")
    s = parse_restricted_payload(payload, src.kind, blocking=src.blocking,
                                 today=today)
    if not s.ok:
        s.error = f"{src.label}：{s.error}"
    return s


def fetch_restricted(sources: Iterable[Source] = SOURCES) -> RestrictedSet:
    return merge(fetch_source(s) for s in sources)


def cache_save(s: RestrictedSet, path: str = DEFAULT_CACHE_PATH) -> None:
    """只存成功的結果。

    把失敗寫進快取等於把「查不到」永久化成「沒有限制」——過濾器從此形同虛設。
    """
    if not s.ok:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "as_of": (s.as_of or date.today()).isoformat(),
        "codes": s.codes,
        "warnings": s.warnings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def cache_load(path: str = DEFAULT_CACHE_PATH) -> RestrictedSet:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        as_of = datetime.strptime(raw["as_of"], "%Y-%m-%d").date()
        codes = dict(raw["codes"])
        warnings = dict(raw.get("warnings") or {})
    except Exception as e:
        return RestrictedSet(ok=False, error=f"快取不可用：{e}")
    return RestrictedSet(codes=codes, warnings=warnings, ok=True, as_of=as_of)


def load_restricted(
    cache_path: str = DEFAULT_CACHE_PATH,
    fetch: Optional[Callable[[], RestrictedSet]] = None,
    today: Optional[date] = None,
    max_stale_days: int = DEFAULT_MAX_STALE_DAYS,
) -> RestrictedSet:
    """抓今天的；失敗則退回夠新的快取；再沒有就是 ok=False。"""
    today = today or date.today()
    fetch = fetch or fetch_restricted

    try:
        fresh = fetch()
    except Exception as e:
        fresh = RestrictedSet(ok=False, error=f"抓取失敗：{e}")

    if fresh.ok:
        fresh.as_of = today
        try:
            cache_save(fresh, cache_path)
        except Exception as e:
            log.warning("交易限制清單快取寫入失敗（不影響本次判斷）: %s", e)
        return fresh

    cached = cache_load(cache_path)
    if cached.ok and cached.as_of is not None:
        age = (today - cached.as_of).days
        if 0 <= age <= max_stale_days:
            log.warning(
                "交易限制清單今日抓取失敗（%s），改用 %s 的快取（%d 天前）",
                fresh.error, cached.as_of, age,
            )
            cached.stale = age > 0
            # 已知的代號合併進來：新抓到一半也算數
            cached.codes.update(fresh.codes)
            cached.warnings.update(fresh.warnings)
            return cached

    return RestrictedSet(
        codes=fresh.codes, warnings=fresh.warnings, ok=False,
        error=fresh.error or "無可用資料", as_of=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 判斷
# ══════════════════════════════════════════════════════════════════════════════

def block_on_unknown_default() -> bool:
    """查不到時是否擋單。預設 True，只有明確設成 false 才放行。"""
    return os.getenv("DT_BLOCK_ON_UNKNOWN_RESTRICTION", "true").strip().lower() != "false"


def check(
    code: str,
    restricted: Optional[RestrictedSet] = None,
    block_on_unknown: Optional[bool] = None,
    **load_kw,
) -> Restriction:
    """這一檔現在能不能買。blocked=True 代表不能。"""
    if block_on_unknown is None:
        block_on_unknown = block_on_unknown_default()
    if restricted is None:
        # 走當日快取而不是 load_restricted：9:10 一次判斷 8 檔，直接抓等於
        # 打 16 次 TWSE。ok=False 不會被記憶，所以下一檔仍會重試。
        restricted = restricted_for_today(**load_kw)

    c = (code or "").strip()
    if not c:
        return Restriction(code=c, blocked=True, reason="代號為空，無法確認交易限制")

    warning = restricted.warnings.get(c, "")

    if c in restricted.codes:
        return Restriction(
            code=c, blocked=True, reason=restricted.codes[c], warning=warning,
            as_of=restricted.as_of, stale=restricted.stale,
        )

    if not restricted.ok:
        return Restriction(
            code=c, blocked=bool(block_on_unknown),
            reason=(f"查不到處置股清單（{restricted.error}），"
                    f"無法確認 {c} 是否可當沖"),
            warning=warning,
            as_of=restricted.as_of, stale=restricted.stale,
        )

    return Restriction(code=c, blocked=False, warning=warning,
                       as_of=restricted.as_of, stale=restricted.stale)


# ── 當日記憶體快取 ────────────────────────────────────────────────────────────
#
# 9:10 一次判斷 8 檔，每檔都重抓等於打 16 次 TWSE。磁碟快取只在「今天抓失敗」
# 時才會被用到，擋不住這種同日重複請求。

_MEMO: dict = {}


def restricted_for_today(today: Optional[date] = None, **load_kw) -> RestrictedSet:
    """當日只實際抓一次。ok=False 不記憶——下一檔還要再試一次。"""
    today = today or date.today()
    hit = _MEMO.get(today)
    if hit is not None:
        return hit
    s = load_restricted(today=today, **load_kw)
    if s.ok:
        _MEMO.clear()
        _MEMO[today] = s
    return s


def reset_memo() -> None:
    """測試與長時間執行的行程（跨日）用。"""
    _MEMO.clear()
