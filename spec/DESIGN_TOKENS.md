# Design Tokens

> 完整的設計 token 定義。可直接複製到 CSS / Tailwind / Python `themes.py`。

---

## 1. Colors

### 1.1 底色 / 表面

| Token | Hex | OKLCH | 用途 |
|---|---|---|---|
| `--bg`        | `#f3efe6` | oklch(95% 0.012 85) | 畫布 canvas 底色（暖灰白） |
| `--surface`   | `#fbf8f1` | oklch(97% 0.010 85) | 卡片、面板背景 |
| `--surface-2` | `#f7f3ea` | oklch(96% 0.012 85) | 次層背景（list row 交替） |
| `--panel`     | `#ffffff` |          | 表格 / form input 內框 |
| `--ink-bg`    | `#14171f` |          | 反相區塊（dark code block / 倒數 bar） |

### 1.2 文字

| Token | Hex | 用途 |
|---|---|---|
| `--ink`      | `#14171f` | 主要文字 |
| `--ink-2`    | `#2a2e3a` | 內文 |
| `--muted`    | `#6b6a63` | 次要文字 |
| `--muted-2`  | `#8c8a82` | 標籤、輔助文字 |
| `--faint`    | `#b9b5a8` | 微弱（disabled / placeholder） |

### 1.3 邊框

| Token | Hex | 用途 |
|---|---|---|
| `--hair`  | `#e3ddcd` | 細線 1px（卡片邊、divider） |
| `--hair-2`| `#d2cab6` | 深細線（focused input） |

### 1.4 台股漲跌色 ★

| Token | Hex | 用途 |
|---|---|---|
| `--up`        | `#c8332b` | **漲紅**（台股慣例） |
| `--up-soft`   | `#fce8e6` | 漲紅 fill / pill bg |
| `--down`      | `#2e7d4f` | **跌綠** |
| `--down-soft` | `#e2efe6` | 跌綠 fill / pill bg |
| `--flat`      | `#8c8a82` | 無漲跌 |

**⚠ 警告**：台股用 紅漲綠跌，與美股相反。任何漲跌色都必須來自這組 token。

### 1.5 品牌 / AI 強調

| Token | Hex | 用途 |
|---|---|---|
| `--brand`     | `#14171f` | logo / primary button |
| `--gold`      | `#a87a2c` | AI 提示、模擬模式警示、二次強調 |
| `--gold-soft` | `#f3e9d2` | AI 卡片 fill |

### 1.6 暗版面（用於倒數 bar、code block）

| Token | Hex |
|---|---|
| `--dark-ink`     | `#f3efe6` |
| `--dark-hair`    | `#2b2f3a` |
| `--dark-muted`   | `#8c8e98` |

---

## 2. Typography

### 2.1 字型堆疊

```css
--font-ui: 'Helvetica Neue', 'Helvetica', Arial,
           'PingFang TC', 'Microsoft JhengHei', sans-serif;

--font-mono: 'IBM Plex Mono', 'JetBrains Mono', ui-monospace,
             Menlo, monospace;
```

- **UI 文字** 一律用 `--font-ui`
- **所有數字**（價格、百分比、量、ID、tokens、時間戳）一律用 `--font-mono`
- **mono 字體必須開啟 `font-feature-settings: "tnum" 1, "zero" 1`** 讓數字等寬 + 斜線零

### 2.2 字級

| Token | Size | Line-height | 用途 |
|---|---|---|---|
| `--text-xs`   | 10px | 1.4  | 標籤、metadata、key shortcuts |
| `--text-sm`   | 11px | 1.5  | 表格內容、輔助說明 |
| `--text-base` | 12px | 1.55 | row default |
| `--text-md`   | 13px | 1.6  | 內文、nav item |
| `--text-lg`   | 14px | 1.55 | 區塊標題 |
| `--text-xl`   | 16px | 1.4  | 重要數字 |
| `--text-2xl`  | 18px | 1.35 | sub heading |
| `--text-3xl`  | 22px | 1.25 | KPI 數字 |
| `--text-4xl`  | 28px | 1.2  | page H1 |
| `--text-5xl`  | 36px | 1.2  | hero P&L |
| `--text-hero` | 44px–56px | 1.1 | 登入頁 / 週報 |

### 2.3 標籤（eyebrow）規範
- font-size 10px
- letter-spacing 0.16em
- text-transform uppercase
- font-family mono
- color `--muted`

### 2.4 字重
- 300 light（hero title only）
- 400 regular（default）
- 500 medium（數字、KPI、強調）
- 600 semibold（heading、code 標的）
- 不要用 700+

---

## 3. Spacing & Sizes

### 3.1 Padding / Gap 階梯

```
--space-1:  2px
--space-2:  4px
--space-3:  6px
--space-4:  8px
--space-5: 10px
--space-6: 12px
--space-7: 14px
--space-8: 16px
--space-10: 20px
--space-12: 24px
--space-14: 28px
--space-16: 32px
```

### 3.2 元件高度

| 元件 | 高度 |
|---|---|
| TopBar | 44px |
| StatusBar | 22px |
| 表格 row | 38–44px |
| List row | 52px |
| Sub-tab | 38px |
| Button (default) | 30px |
| Button (small) | 24px |
| Input | 38px |
| Pill | 18–22px |
| Toggle | 20px |
| Sidebar width | 200px |

### 3.3 圓角
- **大多數元件 `border-radius: 0`**（機構級工具偏好直角）
- pill `border-radius: 2px`
- toggle `border-radius: 20px`
- Telegram message bubble `border-radius: 4–8px`（僅 Telegram 鏡像）

---

## 4. 框線

```
border: 1px solid var(--hair);          /* 預設細線 */
border-left: 2px solid var(--up);       /* 強調左邊（卡片標示） */
border-left: 3px solid var(--up);       /* 警報串流左色條 */
```

**不要**：陰影、漸層邊、glow、blur

---

## 5. 元件規範

### 5.1 Pill

```jsx
<Pill color={T.up} border={T.up} bg={T.upSoft} size={10|11|12}>標籤</Pill>
```

- padding `2px 7px`
- border-radius `2px`
- font-family mono
- font-size 10–11px
- letter-spacing `0.04em`

### 5.2 Button

```css
.btn {
  padding: 7px 14px;
  font-size: 13px;
  font-weight: 500;
  border: 1px solid var(--hair-2);
  border-radius: 2px;
  background: var(--surface);
  color: var(--ink);
}
.btn-primary { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.btn-danger  { background: var(--up); color: #fff; border-color: var(--up); }
.btn-ghost   { background: transparent; border: none; color: var(--muted); }
.btn-small   { padding: 4px 10px; font-size: 12px; }
```

### 5.3 Card

```
背景：surface
邊框：1px solid hair
header：
  padding 10px 14px
  border-bottom 1px solid hair
  display flex
  eyebrow 標籤左對齊
  右側 actions
body：padding 16px（除非另指定 padding=0）
```

### 5.4 Eyebrow

每個區塊上方的小標籤行：

```jsx
<div className="eyebrow">
  <span>標籤文字</span>
  <span className="rule" /> {/* flex: 1, height: 1px, background: hair */}
  <span>{right}</span>
</div>
```

```css
.eyebrow {
  display: flex; align-items: center; gap: 10px;
  font: 500 10px / 1.4 var(--font-mono);
  color: var(--muted);
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
```

### 5.5 Bar / Progress

- 高度 3–6px
- 背景 `--hair`
- fill 用語意色

### 5.6 Sparkline

- 24px 高 / 80px 寬（預設）
- stroke 1.25px
- color：自動依首末值 → up / down
- 可選填充 opacity 0.10

### 5.7 Confidence 顯示

兩種樣式：
- **Tick**（5 段條）：用於表格
- **Bar**（10 段條 + 數字）：用於詳細頁

色階：
- ≥ 0.75 → up（紅）
- 0.60–0.74 → gold
- < 0.60 → muted-2

---

## 6. CSS 變數（完整版，直接複製）

```css
:root {
  /* surfaces */
  --bg: #f3efe6;
  --surface: #fbf8f1;
  --surface-2: #f7f3ea;
  --panel: #ffffff;
  --ink-bg: #14171f;

  /* ink */
  --ink: #14171f;
  --ink-2: #2a2e3a;
  --muted: #6b6a63;
  --muted-2: #8c8a82;
  --faint: #b9b5a8;

  /* borders */
  --hair: #e3ddcd;
  --hair-2: #d2cab6;

  /* TW market */
  --up: #c8332b;
  --up-soft: #fce8e6;
  --down: #2e7d4f;
  --down-soft: #e2efe6;
  --flat: #8c8a82;

  /* brand / ai */
  --brand: #14171f;
  --gold: #a87a2c;
  --gold-soft: #f3e9d2;

  /* dark */
  --dark-ink: #f3efe6;
  --dark-hair: #2b2f3a;
  --dark-muted: #8c8e98;

  /* fonts */
  --font-ui: 'Helvetica Neue', 'Helvetica', Arial,
             'PingFang TC', 'Microsoft JhengHei', sans-serif;
  --font-mono: 'IBM Plex Mono', 'JetBrains Mono', ui-monospace, Menlo, monospace;
}

html, body { background: var(--bg); color: var(--ink); font-family: var(--font-ui); }

.mono { font-family: var(--font-mono); font-feature-settings: "tnum" 1, "zero" 1; }
.up   { color: var(--up); }
.down { color: var(--down); }
```

---

## 7. Tailwind Config（若使用 Tailwind）

```js
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        bg: '#f3efe6',
        surface: { DEFAULT: '#fbf8f1', 2: '#f7f3ea' },
        panel: '#ffffff',
        ink:    { DEFAULT: '#14171f', 2: '#2a2e3a', bg: '#14171f' },
        muted:  { DEFAULT: '#6b6a63', 2: '#8c8a82' },
        faint:  '#b9b5a8',
        hair:   { DEFAULT: '#e3ddcd', 2: '#d2cab6' },
        up:     { DEFAULT: '#c8332b', soft: '#fce8e6' },
        down:   { DEFAULT: '#2e7d4f', soft: '#e2efe6' },
        flat:   '#8c8a82',
        gold:   { DEFAULT: '#a87a2c', soft: '#f3e9d2' },
      },
      fontFamily: {
        ui:   ['Helvetica Neue', 'Helvetica', 'Arial', 'PingFang TC', 'Microsoft JhengHei', 'sans-serif'],
        mono: ['IBM Plex Mono', 'JetBrains Mono', 'ui-monospace', 'Menlo', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '0px',
        sm: '2px',
        DEFAULT_KEEP: '4px',
      },
    },
  },
};
```

---

## 8. Python `themes.py` 對應

```python
# ai_stock/themes.py
class Tokens:
    # surfaces
    BG          = "#f3efe6"
    SURFACE     = "#fbf8f1"
    SURFACE_2   = "#f7f3ea"
    PANEL       = "#ffffff"
    INK_BG      = "#14171f"
    # ink
    INK         = "#14171f"
    INK_2       = "#2a2e3a"
    MUTED       = "#6b6a63"
    MUTED_2     = "#8c8a82"
    FAINT       = "#b9b5a8"
    # borders
    HAIR        = "#e3ddcd"
    HAIR_2      = "#d2cab6"
    # TW market
    UP          = "#c8332b"
    UP_SOFT     = "#fce8e6"
    DOWN        = "#2e7d4f"
    DOWN_SOFT   = "#e2efe6"
    FLAT        = "#8c8a82"
    # brand / ai
    BRAND       = "#14171f"
    GOLD        = "#a87a2c"
    GOLD_SOFT   = "#f3e9d2"

    @classmethod
    def tone(cls, v: float) -> str:
        """Return TW-convention color for a delta."""
        if v > 0: return cls.UP
        if v < 0: return cls.DOWN
        return cls.FLAT

    @classmethod
    def confidence_color(cls, c: float) -> str:
        if c >= 0.75: return cls.UP
        if c >= 0.60: return cls.GOLD
        return cls.MUTED_2
```

---

下一步：讀 `DATA_SHAPES.md`。
