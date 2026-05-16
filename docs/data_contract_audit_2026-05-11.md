# Data Contract Audit — 2026-05-11

> 審計範圍：trade record、prior order、current position、force close 四條資料流  
> 限制：純文件，不涉及業務邏輯異動  
> 審計人：Claude（資深後端視角）

---

## 1. 現況 Schema（Current State）

### 1-A. `daily_trades` 表（`research_db.py`）

```sql
CREATE TABLE IF NOT EXISTS daily_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT    NOT NULL,
    code        TEXT    NOT NULL,
    name        TEXT,
    action      TEXT    NOT NULL,   -- "Buy" / "Sell"
    quantity    INTEGER,
    price       REAL,
    amount      REAL,
    pnl         REAL,
    note        TEXT                -- ⚠️ 自由文字：儲存 "id=xxx lot=yyy"
);
```

### 1-B. 寫入端：`main.py` → `save_daily_trade()`

```python
# executor.py: ExecutionResult dataclass
@dataclass
class ExecutionResult:
    code: str; name: str; action: str
    quantity: int; price: float; amount: float
    order_id: str; lot_type: str   # "common" | "intraday_odd"

# main.py: 存入 DB 時 lot_type 被 stringify 進 note
save_daily_trade({
    "trade_date": date.today(),
    "code":       result.code,
    "action":     result.action,
    "quantity":   result.quantity,
    "price":      result.price,
    "amount":     result.amount,
    "pnl":        None,
    "note":       f"id={result.order_id} lot={result.lot_type}",  # ← 字串序列化
})
```

### 1-C. 讀取端 A：`main.py → load_prior_orders()`

```python
# 來源：Shioaji API（非 DB）
# 回傳每筆 order：
{
    "code":     t.contract.code,
    "action":   str(t.order.action),
    "quantity": t.order.quantity,
    "price":    float(t.order.price),
    # ⚠️ 缺少 "date" 欄位
}
```

`is_duplicate_order()` 的判斷邏輯（`executor.py`）：
```python
def is_duplicate_order(order, prior_orders):
    for o in prior_orders:
        if (o.get("code") == order["code"]
                and o.get("action") == order["action"]
                and o.get("date") == str(date.today())):  # ← o["date"] 永遠 KeyError → False
            return True
    return False
```

### 1-D. 讀取端 B：`main.py → load_current_positions()`

```python
# 來源：daily_trades 表，過濾今日 "Buy" 成交紀錄
# 回傳每筆：
{
    "code":     row["code"],
    "name":     row["name"],
    "action":   row["action"],
    "quantity": row["quantity"],
    "price":    row["price"],
    "amount":   row["amount"],
    "pnl":      row["pnl"],
    "note":     row["note"],   # e.g. "id=xxx lot=intraday_odd"
    # ⚠️ 缺少 "sector"、"value"
}
```

### 1-E. 消費端 C：`risk_guard.py → validate_plan()`

```python
def validate_plan(picks, capital, current_positions):
    # current_positions 預期形狀：
    # [{"code": str, "sector": str, "value": float}, ...]
    for pos in current_positions:
        sector = pos["sector"]   # ← KeyError（實際沒有此欄位）
        value  = pos["value"]    # ← KeyError（實際是 "amount"）
```

### 1-F. 讀取端 D：`main.py → ForceCloseJob`

```python
positions = load_current_positions(date.today(), self._db_path)
for t in positions:
    # ⚠️ 從 note 字串 parse lot_type
    lot_type = (
        "intraday_odd"
        if "lot=intraday_odd" in t.get("note", "")
        else "common"
    )
    # 用 lot_type 決定如何送出強制平倉委託
```

---

## 2. 目標 Schema（Target State）

### 2-A. `daily_trades` 表（新增 3 欄）

```sql
CREATE TABLE IF NOT EXISTS daily_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT    NOT NULL,
    code        TEXT    NOT NULL,
    name        TEXT,
    action      TEXT    NOT NULL,
    quantity    INTEGER,
    price       REAL,
    amount      REAL,
    pnl         REAL,
    order_id    TEXT,               -- 新增（原存於 note）
    lot_type    TEXT DEFAULT 'common', -- 新增（"common" | "intraday_odd"）
    sector      TEXT,               -- 新增（供 risk_guard 使用）
    note        TEXT                -- 保留，改為純人工備註
);
```

### 2-B. `load_prior_orders()` 目標回傳格式

```python
{
    "code":     str,
    "action":   str,
    "quantity": int,
    "price":    float,
    "date":     str,   # 新增：str(date.today()) — is_duplicate_order 需要
}
```

### 2-C. `load_current_positions()` 目標回傳格式

```python
{
    "code":     str,
    "name":     str,
    "sector":   str,   # 新增（從 config 或 Shioaji contract 取得）
    "value":    float, # 新增（= amount，或 quantity × price）
    "lot_type": str,   # 新增（直接讀 DB 欄位，不 parse note）
    "quantity": int,
    "price":    float,
    "amount":   float,
}
```

### 2-D. Schema 對照摘要表

| 欄位 | `daily_trades` 現況 | `daily_trades` 目標 | `load_prior_orders` 現況 | `load_prior_orders` 目標 |
|------|---|---|---|---|
| `code` | ✅ | ✅ | ✅ | ✅ |
| `action` | ✅ | ✅ | ✅ | ✅ |
| `quantity` | ✅ | ✅ | ✅ | ✅ |
| `price` | ✅ | ✅ | ✅ | ✅ |
| `date` / `trade_date` | ✅ DB | ✅ DB | ❌ 缺 | ✅ 補上 |
| `lot_type` | ❌ 藏在 note | ✅ 獨立欄 | — | — |
| `order_id` | ❌ 藏在 note | ✅ 獨立欄 | — | — |
| `sector` | ❌ 缺 | ✅ 獨立欄 | — | — |
| `value` | ❌ 用 amount 代替 | ✅ 別名 / 計算 | — | — |

---

## 3. 最危險的 3 個不一致點

### 🔴 危險 #1：`is_duplicate_order` 永遠失效 → 重複下單

**位置**：`executor.py: is_duplicate_order()` + `main.py: load_prior_orders()`

**問題**：
```python
# load_prior_orders 回傳的每筆 order 無 "date" 欄位
o.get("date") == str(date.today())
# → None == "2026-05-11" → False → 永遠不重複
```

**後果**：同一檔在同一天的第二次策略執行會再次下單，沒有任何防重複保護。  
**風險等級**：P0 — 真實資金環境下會導致意外加倉。

---

### 🔴 危險 #2：`lot_type` 依賴 note 字串 parsing → 強制平倉用錯交易類型

**位置**：`main.py: ForceCloseJob`

**問題**：
```python
lot_type = "intraday_odd" if "lot=intraday_odd" in t.get("note", "") else "common"
```

`note` 是自由文字欄位。若格式稍有差異（如空格、大小寫、note 含其他說明文字），  
就會 fallback 成 `"common"`。

**後果**：零股盤中交易（intraday_odd）用普通張數下強制平倉委託，  
Shioaji API 會拒絕委託，導致強制平倉失敗、留下裸部位。  
**風險等級**：P0 — 停損失效。

---

### 🟠 危險 #3：`risk_guard.validate_plan` 的 sector cap 從未生效

**位置**：`risk_guard.py: validate_plan()` + `main.py: load_current_positions()`

**問題**：
```python
# load_current_positions 回傳欄位：{code, name, action, quantity, price, amount, pnl, note}
# validate_plan 使用欄位：{code, sector, value}
# → sector / value 兩個欄位都不存在
```

在 production 執行時，若 `validate_plan` 直接用 `pos["sector"]`，  
會 KeyError crash；若有 `.get("sector", "unknown")` 保護，  
則所有部位都落在同一個假 sector，sector 集中度上限從未被真正執行。

**後果**：AI 策略可以在同一類股（如 AI 伺服器）押注超過設定上限的資金，  
風控機制形同虛設。  
**風險等級**：P1 — 風控失效，市場黑天鵝時放大損失。

---

## 4. 建議修正優先順序

| 優先 | 問題 | 最小改動 |
|------|------|------|
| P0 立刻 | `load_prior_orders` 缺 `date` | 在回傳 dict 加 `"date": str(date.today())` |
| P0 立刻 | `lot_type` 存 note | `daily_trades` 加 `lot_type TEXT` 欄，migration script |
| P1 下輪 | `load_current_positions` 缺 `sector`/`value` | 加欄位 + 從 config/Shioaji 取 sector mapping |

> 以上修正不涉及策略邏輯，屬純資料層異動，可安全在測試環境先驗證。
