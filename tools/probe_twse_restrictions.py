#!/usr/bin/env python3
"""
tools/probe_twse_restrictions.py — 實機驗證處置股／注意股端點

為什麼需要這支
--------------
twse_restrictions.py 是在無法連到 www.twse.com.tw 的環境寫的（開發環境的
網路政策擋掉了）。封包外層形狀（stat / fields / data）與 chip_data.py 解析
T86 的相同，那個已在正式機驗證過；但 **punish / notice 端點的欄位名稱沒有
被實地確認過**。

欄位名對不上時模組會回 ok=False → 所有買單被擋 → 你會立刻發現（而不是
默默不過濾）。但那也表示明天整天不會有任何交易，所以上線前先跑這支。

用法
----
    python3 tools/probe_twse_restrictions.py

它會：
  1. 逐一打每個來源，印出 HTTP 狀態與實際的 fields 名稱
  2. 把原始 JSON 存到 data/probe_twse_*.json 供事後比對
  3. 用 twse_restrictions 的解析器實跑一次，報告會不會通過
  4. 最後給出「今天總共會擋幾檔」

退出碼：全部來源可解析回 0，否則回 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import twse_restrictions as tr  # noqa: E402

OUT_DIR = Path("data")


def probe(src: tr.Source) -> tuple[bool, dict]:
    print(f"\n── {src.label}")
    print(f"   {src.url}")
    try:
        import requests
        resp = requests.get(src.url, timeout=tr.REQUEST_TIMEOUT)
        print(f"   HTTP {resp.status_code}  {len(resp.content):,} bytes")
        payload = resp.json()
    except Exception as e:
        print(f"   ❌ 抓取失敗：{e}")
        return False, {}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"probe_twse_{src.kind}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"   原始回應已存：{out}")

    if not isinstance(payload, dict):
        print(f"   ❌ 回應不是 JSON 物件，而是 {type(payload).__name__}")
        return False, {}

    print(f"   stat   = {payload.get('stat')!r}")
    fields = payload.get("fields")
    print(f"   fields = {fields}")
    print(f"   data   = {len(payload.get('data') or [])} 列")

    rows = payload.get("data") or []
    if rows:
        print(f"   第一列 = {rows[0]}")

    parsed = tr.parse_restricted_payload(payload, kind=src.kind)
    if parsed.ok:
        print(f"   ✅ 解析成功，取得 {len(parsed.codes)} 檔")
        for c in list(parsed.codes)[:5]:
            print(f"      {c}")
        if len(parsed.codes) > 5:
            print(f"      …共 {len(parsed.codes)} 檔")
    else:
        print(f"   ❌ 解析失敗：{parsed.error}")
        print(f"   → 模組目前認得的代號欄位名：{tr._CODE_FIELDS}")
        print("   → 把上面 fields 裡真正的代號欄位名加進 _CODE_FIELDS 即可")
    return parsed.ok, parsed.codes


def main() -> int:
    print("處置股／注意股端點探測")
    print("=" * 60)

    all_ok = True
    merged: dict = {}
    for src in tr.SOURCES:
        ok, codes = probe(src)
        all_ok = all_ok and ok
        merged.update(codes)

    print("\n" + "=" * 60)
    if all_ok:
        print(f"✅ 所有來源可解析。今天總共會擋 {len(merged)} 檔。")
        if merged:
            print("   " + "、".join(sorted(merged)))
        print("\n可以放心讓 Guard 0b 生效。")
    else:
        print("❌ 有來源無法解析。")
        print("   目前的行為是 fail-closed：**所有買單都會被擋**。")
        print("   修正 twse_restrictions._CODE_FIELDS 或 SOURCES 後重跑本腳本。")
        print("   若要暫時放行（沒有處置股保護），設：")
        print("       DT_BLOCK_ON_UNKNOWN_RESTRICTION=false")

    print("\n注意：SOURCES 目前只涵蓋上市（TWSE）。"
          "上櫃（TPEx）的處置股同樣會鎖死你，端點確認後要補進 SOURCES。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
