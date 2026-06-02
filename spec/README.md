# QUANT·AI · 設計實作規格

> 給 AI coding agent / 工程師：把 `index.html` 的設計套入既有 `ai_stock/` 後端的完整規格。

---

## 📂 文件導讀

依序閱讀：

1. **[SPEC.md](./SPEC.md)** — 總則 + 17 畫面索引 + 設計 DO/DON'T
2. **[DESIGN_TOKENS.md](./DESIGN_TOKENS.md)** — 顏色、字型、間距、CSS 變數、Tailwind config、Python `themes.py`
3. **[DATA_SHAPES.md](./DATA_SHAPES.md)** — TypeScript types + Pydantic models（API 契約）
4. **[SCREENS.md](./SCREENS.md)** — 每個畫面的版面與互動細節
5. **[BACKEND_MAPPING.md](./BACKEND_MAPPING.md)** — Python 模組 → FastAPI router 對應
6. **[IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)** — 8 個 milestone + 預估工時

---

## 🚀 給 AI agent 的起手 prompt

```
我有一個台股 AI 量化交易系統，後端在 ai_stock/（Python，詳見 SYSTEM.md），
設計檔在 index.html（React JSX 預覽，共 17 個畫面）。

請依照 spec/ 資料夾的規格：
1. 讀完 spec/SPEC.md 與 spec/IMPLEMENTATION_PLAN.md 了解架構
2. 從 Milestone M1 開始實作（建 FastAPI 骨架 + React 骨架 + Login + Settings）
3. 每完成一個畫面，與 index.html 對應 artboard pixel-level 比對
4. 不要改動 ai_stock/* 既有 Python 邏輯，只在 backend/ 中薄包裝
5. 嚴格遵守 spec/DESIGN_TOKENS.md 的顏色、字型、間距 token
6. 所有 API 回傳的資料結構必須符合 spec/DATA_SHAPES.md
```

---

## 🎯 重點原則

### 必須做到
- ✅ **台股漲跌色**：紅漲 `#c8332b` / 綠跌 `#2e7d4f`，絕不能反
- ✅ **數字一律 mono 字型**（IBM Plex Mono），開啟 tabular numerals
- ✅ **13:25 強制平倉倒數**在任何持倉相關畫面常駐
- ✅ **每筆下單必經 Telegram 二次確認**（沿用既有 `user_confirm.py`）
- ✅ **不改動 `ai_stock/*` 既有邏輯**，只做薄包裝
- ✅ **資訊密度優先**，機構級 Bloomberg 風格

### 絕對禁止
- ❌ 不要用美股漲跌色（綠漲紅跌）
- ❌ 不要用陰影、漸層、emoji 當主視覺
- ❌ 不要用 Plotly / Chart.js / ECharts（用自製 SVG，與設計檔一致）
- ❌ 不要把 AI 信心只顯示為單一數字（要附高/中/低分級）
- ❌ 不要重寫既有 Python 模組

---

## 📊 17 畫面快速對照

| 區塊 | 畫面 | 優先級 |
|---|---|---|
| 認證 | 01 Login | P0 |
| 總覽 | 02 Dashboard | P0 |
| **AI 預測 ★** | 03.1 Top N · 03.2 深度 · 03.3 推理過程 | **P0** |
| **AI 當沖 ★** | 04.1 駕駛艙 · 04.2 K 線 · 04.3 下單 | **P0** |
| 工作區 | 05.1 持倉 · 05.2 日誌 · 05.3 回測 · 05.4 週報 · 05.5 掃描 | P1–P2 |
| 設定 | 06 Settings | P0 |
| 手機 | 07.1 預測 · 07.2 當沖 · 07.3 Telegram | P1 |

---

## 🔗 變更請求格式

如果你（使用者）想調整任何畫面，這樣告訴 AI：

```
請依照 spec/SCREENS.md 的 §<a id="screen-04-1"></a> 駕駛艙，
新增一個「靜音 30 分鐘」按鈕在警報串流區的右上角，
靜音時所有 high 警報只寫入 alerts 表不推送 Telegram。
```

AI 會自動在 SCREENS.md 中找到該錨點 + 對應 BACKEND_MAPPING.md 中的服務 + DATA_SHAPES.md 中的資料結構。

---

製作日期：2026-05-24
規格版本：v0.1
對應設計版本：index.html（17 artboards）
對應後端版本：ai_stock/ SYSTEM.md
