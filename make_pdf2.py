"""
make_pdf2.py — 重製版，使用文泉驛中文字型，大字 + 圖示
"""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1920, 1080

# ── 顏色 ─────────────────────────────────────────────────────────────────────
BG      = (13,  17,  23)
SURFACE = (22,  27,  34)
CARD    = (30,  38,  50)
UP      = (200, 51,  43)
DOWN    = (46,  125, 79)
ACCENT  = (38,  139, 210)
MUTED   = (120, 130, 150)
WHITE   = (255, 255, 255)
YELLOW  = (220, 170, 30)
ORANGE  = (230, 120, 20)
TEAL    = (42,  157, 143)
PURPLE  = (139, 92,  246)
LIGHT   = (200, 210, 230)

# ── 字型（文泉驛支援中文）────────────────────────────────────────────────────
CJK_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

def F(size):
    return ImageFont.truetype(CJK_PATH, size)

# ── 基礎繪圖工具 ─────────────────────────────────────────────────────────────
def new_img():
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)

def R(d, x1, y1, x2, y2, fill, outline=None, lw=2):
    d.rectangle([x1, y1, x2, y2], fill=fill,
                outline=outline, width=lw)

def T(d, text, x, y, color=WHITE, size=24, anchor="lt", cx=None):
    """anchor: lt=左上  mm=置中  rt=右上"""
    fn = F(size)
    if cx is not None:  # cx = 置中的容器寬度
        bbox = d.textbbox((0,0), text, font=fn)
        tw = bbox[2] - bbox[0]
        x = x + (cx - tw) // 2
    d.text((x, y), text, fill=color, font=fn)

def dot(d, cx, cy, r, color):
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color)

def hline(d, x1, x2, y, color, h=4):
    R(d, x1, y, x2, y+h, color)

def pill(d, x, y, w, h, fill, text_str, tcolor=WHITE, size=22):
    """圓角矩形色塊（模擬）"""
    R(d, x, y, x+w, y+h, fill)
    bbox = d.textbbox((0,0), text_str, font=F(size))
    tw = bbox[2]-bbox[0]
    th = bbox[3]-bbox[1]
    d.text((x+(w-tw)//2, y+(h-th)//2-2), text_str, fill=tcolor, font=F(size))

def card(d, x, y, w, h, accent=None, fill=CARD):
    R(d, x, y, x+w, y+h, fill)
    if accent:
        R(d, x, y, x+6, y+h, accent)

def title_bar(d, time_str, title, color=UP):
    R(d, 0, 0, W, 110, SURFACE)
    hline(d, 0, W, 110, color, 6)
    pill(d, 30, 18, 180, 72, color, time_str, WHITE, 32)
    T(d, title, 230, 22, WHITE, 36)

# ════════════════════════════════════════════════════════════════════════════════
slides = []

# ══════════════════════════════════════════════════
# Slide 1 — 封面
# ══════════════════════════════════════════════════
img, d = new_img()

# 背景裝飾條
R(d, 0, 340, W, 348, UP)
R(d, 0, 350, W, 356, ACCENT)

T(d, "QUANT · AI", 0, 80, UP,    90, cx=W)
T(d, "台股 AI 量化交易系統", 0, 200, WHITE, 52, cx=W)
T(d, "完整運作流程  ·  時間節點  ·  Telegram 推播 · 學習機制", 0, 275, MUTED, 28, cx=W)

feature_data = [
    ("🤖", "AI 選股",    "Gemini + Claude"),
    ("📊", "當沖監控",   "TP / SL / 追蹤停損"),
    ("📱", "Telegram",   "12 個推播節點"),
    ("🧠", "學習反饋",   "越來越準"),
    ("📋", "Notion 報告","每日自動推送"),
]
bw, bh = 320, 180
total = len(feature_data)*bw + (len(feature_data)-1)*24
sx = (W - total) // 2
for i, (icon, t1, t2) in enumerate(feature_data):
    bx = sx + i*(bw+24)
    card(d, bx, 395, bw, bh, ACCENT)
    T(d, icon, bx, 415, WHITE, 40, cx=bw)
    T(d, t1,   bx, 465, ACCENT, 28, cx=bw)
    T(d, t2,   bx, 505, MUTED,  22, cx=bw)

T(d, "HotiChen / ai_stock  ·  2026", 0, H-55, MUTED, 22, cx=W)
slides.append(img)

# ══════════════════════════════════════════════════
# Slide 2 — 每日時間軸
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "時間軸", "每日完整時間軸  08:30 → 13:35", ACCENT)

events = [
    ("08:30", "盤前 AI 分析",  ACCENT,  "選股＋當沖預測\nTelegram 推播三套策略"),
    ("09:00", "開盤下單",      DOWN,    "波段下單執行"),
    ("09:05", "開盤確認",      TEAL,    "AI 再確認進場\nTelegram 開盤報告"),
    ("09:10", "當沖進場",      UP,      "買入 Keyboard\nShioaji 下買單"),
    ("09:15", "Tick 監控",     YELLOW,  "MonitorAgent\n開始訂閱 tick"),
    ("10:00", "盤中檢查",      ACCENT,  "持倉損益報告\nTelegram 推播"),
    ("13:00", "出場警報",      ORANGE,  "損益預覽\n⚠ 25分後強制平倉"),
    ("13:25", "強制平倉",      UP,      "ForceCloseJob\n市價清空所有部位"),
    ("13:35", "收盤任務",      PURPLE,  "複盤＋學習更新\nNotion 推送"),
]

LINE_Y = 650
hline(d, 60, W-60, LINE_Y, ACCENT, 5)

ew = (W - 120) // len(events)
for i, (t, title, color, detail) in enumerate(events):
    cx2 = 60 + i*ew + ew//2
    dot(d, cx2, LINE_Y+2, 18, color)

    if i % 2 == 0:
        # 上方：時間，下方：卡片
        T(d, t, cx2-55, LINE_Y-85, color, 28)
        card(d, cx2-100, LINE_Y+35, 200, 160, color)
        T(d, title, cx2-90, LINE_Y+50,  color, 22)
        for j, line in enumerate(detail.split("\n")):
            T(d, line, cx2-90, LINE_Y+90+j*38, LIGHT, 20)
    else:
        card(d, cx2-100, LINE_Y-215, 200, 160, color)
        T(d, title, cx2-90, LINE_Y-200, color, 22)
        for j, line in enumerate(detail.split("\n")):
            T(d, line, cx2-90, LINE_Y-160+j*38, LIGHT, 20)
        T(d, t, cx2-55, LINE_Y+20, color, 28)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 3 — 08:30 盤前 AI 分析
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "08:30", "盤前 AI 分析 — PremarketJob + DaytradingReport", UP)

steps = [
    ("🌅", "① Gemini 早晨簡報", ACCENT, [
        "抓取台股加權指數 / 外資 / 投信",
        "美股 S&P500 / SOX / VIX",
        "Gemini AI 生成市場分析",
        "輸出 MorningBriefing 物件",
    ]),
    ("🛡", "② risk_guard 風控", YELLOW, [
        "每筆預算上限 ≤ 30%",
        "同板塊持倉上限控制",
        "黑名單股票過濾",
        "拒絕不符合風控的選股",
    ]),
    ("📋", "③ 三套策略生成", DOWN, [
        "🚀 衝刺版（高風高報）",
        "⚖ 均衡版（平衡）",
        "🛡 保守版（防守）",
        "各含選股清單 + 預期報酬",
    ]),
    ("🎯", "④ 當沖預測報告", PURPLE, [
        "掃描全市場股票",
        "dt_score 技術評分 0–10",
        "Haiku AI 分析評分 ≥ 4",
        "存入 daytrading_review.db",
    ]),
    ("📱", "⑤ Telegram 推播", ORANGE, [
        "📊 早晨市場簡報",
        "三套策略各一則訊息",
        "Inline Keyboard 讓你選：",
        "[🚀衝刺] [⚖均衡] [🛡保守]",
    ]),
]

bw2 = 346
gap3 = 10
total2 = len(steps)*bw2 + (len(steps)-1)*gap3
sx2 = (W-total2)//2
for i, (icon, title, color, items) in enumerate(steps):
    bx = sx2 + i*(bw2+gap3)
    card(d, bx, 125, bw2, H-170, color)
    T(d, icon,  bx+10, 135, WHITE,  48)
    T(d, title, bx+10, 195, color,  26)
    hline(d, bx+10, bx+bw2-10, 235, color, 2)
    for j, item in enumerate(items):
        T(d, item, bx+14, 255+j*66, LIGHT, 22)

T(d, "使用者點選策略 → 系統記錄 → 09:00 執行下單", 0, H-45, MUTED, 24, cx=W)
slides.append(img)

# ══════════════════════════════════════════════════
# Slide 4 — 09:00–09:10 開盤進場
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "09:00", "09:00 – 09:10  開盤進場三步驟", DOWN)

col_data = [
    ("🟢", "09:00  波段下單", DOWN, "MarketOpenJob", [
        "使用者已選好策略",
        "執行 picks：",
        "  open / add / close",
        "place_stock_order()",
        "via Shioaji API",
        "寫入 research.db",
        "",
        "⚠ 每筆下單前：",
        "send_confirmation()",
        "Telegram 二次確認",
        "60 秒 timeout → 略過",
    ]),
    ("🔵", "09:05  開盤確認", TEAL, "run_opening_reconfirm()", [
        "AI 再次確認進場條件：",
        "  ✅ 量能比 ≥ 0.8？",
        "  ✅ 大盤方向吻合？",
        "  ✅ 台指期溢貼水正常？",
        "",
        "→ confirmed：繼續",
        "→ skipped：放棄",
        "",
        "📱 Telegram 推播：",
        "  每支股 ✅/❌ 狀態",
        "  現價 + 進場區間",
    ]),
    ("🔴", "09:10  當沖進場", UP, "send_dt_buy_confirmation()", [
        "📱 Telegram 推播：",
        "",
        "[✅ 2330 台積電] [❌ 跳過]",
        "[✅ 2382 廣達]   [❌ 跳過]",
        "[✅ 全部買入] [❌ 全部跳過]",
        "",
        "使用者點選 → callback",
        "→ _handle_dt_buy(code)",
        "→ Shioaji 下買單",
        "→ MonitorAgent 開始監控",
        "→ 持倉 watching → active",
    ]),
]

pw = 590
pgap = 22
psx = (W - (3*pw+2*pgap))//2
for i, (icon, title, color, func, items) in enumerate(col_data):
    bx = psx + i*(pw+pgap)
    card(d, bx, 125, pw, H-165, color)
    T(d, icon,  bx+14, 132, WHITE, 42)
    T(d, title, bx+70, 138, color,  28)
    T(d, func,  bx+14, 190, MUTED,  20)
    hline(d, bx+14, bx+pw-14, 222, color, 2)
    for j, item in enumerate(items):
        c = ACCENT if item.startswith("[") else (MUTED if item.startswith("  ") else LIGHT)
        if item.startswith("→"): c = DOWN
        if item.startswith("⚠") or item.startswith("📱"): c = YELLOW
        T(d, item, bx+14, 238+j*56, c, 22)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 5 — Tick 監控 + 四種出場
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "監控", "09:15 – 13:15  Tick 監控 + 四種即時出場", YELLOW)

# 左側：監控說明
card(d, 20, 125, 500, H-160, YELLOW)
T(d, "📡", 30, 132, WHITE, 48)
T(d, "MonitorAgent", 90, 140, YELLOW, 30)
T(d, "Shioaji Tick 即時訂閱", 30, 200, LIGHT, 22)
hline(d, 30, 510, 232, YELLOW, 2)

mon = [
    ("每個 tick 執行：", MUTED),
    ("check_trailing_stop()", ACCENT),
    ("", WHITE),
    ("判斷順序（優先級↓）", MUTED),
    ("", WHITE),
    ("① 停損  gain ≤ -3.0%", UP),
    ("② 達目標 gain ≥ +9.0%", DOWN),
    ("③ 強制  時間 ≥ 13:00", ORANGE),
    ("④ 追蹤  peak↓ ≥ 2%", YELLOW),
    ("", WHITE),
    ("peak_price 每 tick 更新", MUTED),
    ("只會往高點移動", MUTED),
]
for j, (txt2, c) in enumerate(mon):
    T(d, txt2, 30, 250+j*50, c, 24)

# 右側四個出場卡片
tw2 = 340
tgap2 = 12
tsx = 540
trigger_data = [
    ("🛑", "停損出場",   UP,     "-3.0%",    "gain_pct ≤ -stop_loss_pct",       "立即市價賣出\n損失最小化"),
    ("🎯", "達目標出場", DOWN,   "+9.0%",    "gain_pct ≥ take_profit_pct",      "鎖住最大獲利\n送出市價單"),
    ("📈", "追蹤停損",   YELLOW, "+3% / 2%", "peak≥trailing_start\n回落≥gap",  "從高點保護利潤\n不急著出場"),
    ("⏰", "強制平倉",   ORANGE, "13:00",    "當前時間 ≥ force_close_time",      "確保不留倉\n過夜風險歸零"),
]
for i, (icon, title, color, thr, cond, action) in enumerate(trigger_data):
    bx = tsx + i*(tw2+tgap2)
    card(d, bx, 125, tw2, H-160, color)
    T(d, icon,  bx+10, 133, WHITE, 46)
    T(d, title, bx+10, 192, color, 28)
    pill(d, bx+10, 238, tw2-20, 44, color, thr, WHITE, 24)
    T(d, "條件：", bx+10, 295, MUTED, 20)
    for j, line in enumerate(cond.split("\n")):
        T(d, line, bx+10, 320+j*36, LIGHT, 22)
    T(d, "動作：", bx+10, 415, MUTED, 20)
    for j, line in enumerate(action.split("\n")):
        T(d, line, bx+10, 440+j*38, DOWN,  24)
    hline(d, bx+10, bx+tw2-10, 500, MUTED, 1)
    T(d, "📱 Telegram 即時通知", bx+10, 515, YELLOW, 22)
    T(d, "✅ 出場已送出  +  觸發原因 + 價格", bx+10, 550, MUTED, 20)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 6 — 13:00–13:35 收盤三連發
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "13:00", "13:00 – 13:35  收盤三連發", ORANGE)

col3 = [
    ("⚠", "13:00  出場警報", ORANGE, [
        ("📱 Telegram 推播內容：", YELLOW),
        ("", WHITE),
        ("📊 當沖出場警報 13:00", WHITE),
        ("🔴 2330 台積電", LIGHT),
        ("   進場 1068 → 現 1082", MUTED),
        ("   損益 +14,000 (+1.3%)", DOWN),
        ("🔴 2382 廣達", LIGHT),
        ("   進場 305 → 現 312", MUTED),
        ("   損益 +14,000 (+2.3%)", DOWN),
        ("", WHITE),
        ("💰 合計：+28,000 元", DOWN),
        ("⚠ 13:25 將自動強制平倉", UP),
    ]),
    ("💥", "13:25  強制平倉", UP, [
        ("ForceCloseJob", ACCENT),
        ("", WHITE),
        ("Real 模式：", YELLOW),
        ("→ place_order(price=0)", LIGHT),
        ("→ 市價單確保成交", LIGHT),
        ("→ 等待 fill 回報", LIGHT),
        ("", WHITE),
        ("Sim 模式：", YELLOW),
        ("→ 即時報價計算損益", LIGHT),
        ("→ 直接寫入已平倉", LIGHT),
        ("", WHITE),
        ("100% 確保不留倉過夜 ✅", DOWN),
    ]),
    ("🎓", "13:35  收盤四任務", PURPLE, [
        ("① PostMarketJob", YELLOW),
        ("   停監控 + 存每日摘要", MUTED),
        ("   notify_market_close()", ACCENT),
        ("", WHITE),
        ("② DaytradingReview", YELLOW),
        ("   回填 OHLC + 判斷對錯", MUTED),
        ("   → Telegram 複盤報告", ACCENT),
        ("", WHITE),
        ("③ adaptive_scorer.py", YELLOW),
        ("   更新評分區間勝率", MUTED),
        ("   → adaptive_config.json", ACCENT),
        ("", WHITE),
        ("④ notion_reporter.py", YELLOW),
        ("   推送今日報告到 Notion", MUTED),
    ]),
]

pw3 = 590
pgap3 = 22
psx3 = (W-(3*pw3+2*pgap3))//2
for i, (icon, title, color, items) in enumerate(col3):
    bx = psx3 + i*(pw3+pgap3)
    card(d, bx, 125, pw3, H-160, color)
    T(d, icon,  bx+14, 130, WHITE, 48)
    T(d, title, bx+72, 140, color, 28)
    hline(d, bx+14, bx+pw3-14, 194, color, 2)
    for j, (item, c) in enumerate(items):
        T(d, item, bx+14, 208+j*52, c, 23)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 7 — Telegram 推播完整清單
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "📱", "Telegram 推播完整清單  ( 12 個節點 )", YELLOW)

rows = [
    ("🔵 系統啟動",  MUTED,   "notify_system_start()",          "系統已啟動 · 模擬/真實模式"),
    ("🟢 08:30",    ACCENT,  "send_morning_push()",            "早晨簡報 + 三套策略 + 選擇按鈕"),
    ("🟣 08:30",    PURPLE,  "DaytradingReport",               "今日當沖候選股 · 評分 · 信心度"),
    ("🩵 09:05",    TEAL,    "開盤確認通知",                     "✅/❌ 每支股狀態 + 現價 + 大盤方向"),
    ("🔴 09:10",    UP,      "send_dt_buy_confirmation()",     "[✅ CODE] [❌跳過] Inline Keyboard"),
    ("🟡 盤中即時",  YELLOW,  "_run_dt_sell_alerts()",          "✅出場送出 / ❌失敗 + 觸發原因 + 價格"),
    ("🔵 10:00",    ACCENT,  "_mid_session_check()",           "持倉損益報告 + 剩餘候選提示"),
    ("🟠 13:00",    ORANGE,  "出場警報",                         "所有持倉損益 + ⚠ 13:25 強制平倉"),
    ("🔴 13:25",    UP,      "ForceCloseJob",                  "強制平倉執行結果通知"),
    ("🟢 13:35",    DOWN,    "notify_market_close()",          "今日總損益 + 交易筆數"),
    ("🟣 13:35",    PURPLE,  "DaytradingReview",               "預測複盤：達目標/觸停損 · 近30日勝率"),
    ("⚫ 系統停止",  MUTED,   "notify_system_stop()",           "系統已停止"),
]

# 表頭
hx4 = [30, 280, 760, 1350]
heads = ["時間 / 節點", "函式 / 模組", "推播內容", ""]
for h2, hbx in zip(heads, hx4):
    T(d, h2, hbx, 118, MUTED, 22)
hline(d, 20, W-20, 148, MUTED, 2)

for i, (ts, color, func, content) in enumerate(rows):
    ry = 158 + i*74
    if i % 2 == 0:
        R(d, 20, ry, W-20, ry+72, SURFACE)
    T(d, ts,      hx4[0], ry+16, color, 24)
    T(d, func,    hx4[1], ry+16, WHITE, 22)
    T(d, content, hx4[2], ry+16, MUTED, 21)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 8 — AI 學習反饋回路
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "🧠", "AI 學習反饋回路 — 越來越準", PURPLE)

steps2 = [
    ("①", "08:30\n盤前 AI 分析",  ACCENT,  130,  160, "注入 learning_summary\n提升選股準確度"),
    ("②", "09:10\n買入執行",      DOWN,    620,  160, "記錄進場價格\n持倉狀態更新"),
    ("③", "盤中\nTP/SL 觸發",     YELLOW, 1110,  160, "即時出場\nTelegram 通知"),
    ("④", "13:35\n收盤複盤",      ORANGE, 1110,  540, "OHLC 回填\nhit_target/hit_stop"),
    ("⑤", "13:35\nAdaptive 更新", PURPLE,  620,  540, "勝率統計\n閾值優化"),
    ("⑥", "13:35\nNotion 推送",   TEAL,    130,  540, "每日報告\n勝率趨勢圖"),
]
cw3, ch3 = 440, 200
for num, subtitle, color, cx3, cy3, detail in steps2:
    card(d, cx3, cy3, cw3, ch3, color)
    T(d, num,      cx3+14, cy3+12, color, 40)
    T(d, subtitle, cx3+70, cy3+16, color, 28)
    hline(d, cx3+14, cx3+cw3-14, cy3+85, color, 2)
    for j, line in enumerate(detail.split("\n")):
        T(d, line, cx3+14, cy3+100+j*46, LIGHT, 24)

# 箭頭標示
arrow_labels2 = [
    (570, 255,  "→ 評分預測"),
    (1060, 255, "→ 成交記錄"),
    (1280, 490, "↓ OHLC 回填"),
    (1060, 635, "← 勝率統計"),
    (570,  635, "← learning_summary"),
    (240,  490, "↑ 注入隔日提示"),
]
for ax, ay, al in arrow_labels2:
    T(d, al, ax, ay, MUTED, 24)

# 底部說明
R(d, 20, H-145, W-20, H-20, SURFACE)
T(d, "學習效果（隨交易日累積）：", 40, H-135, YELLOW, 26)
effects2 = [
    ("評分 4-5  勝率 ~31%  →  建議跳過", UP),
    ("評分 6-7  勝率 ~58%  →  推薦操作", YELLOW),
    ("評分 8-10 勝率 ~72%  →  強烈推薦", DOWN),
]
for k, (eff, ec) in enumerate(effects2):
    T(d, eff, 50+k*620, H-90, ec, 26)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 9 — 環境設定
# ══════════════════════════════════════════════════
img, d = new_img()
title_bar(d, "⚙", "環境設定 & 快速啟動指令", DOWN)

# 左欄
card(d, 20, 125, 940, H-145, DOWN)
T(d, "📄  .env 必填設定", 44, 138, DOWN, 28)
hline(d, 40, 950, 178, DOWN, 2)
env2 = [
    ("# Telegram Bot", MUTED),
    ("TELEGRAM_BOT_TOKEN = 你的 Bot Token", ACCENT),
    ("TELEGRAM_CHAT_ID  = 你的 Chat ID", ACCENT),
    ("TELEGRAM_USER_ID  = 你的 User ID  # 安全驗證", ACCENT),
    ("", WHITE),
    ("# Notion（每日報告）", MUTED),
    ("NOTION_TOKEN = secret_xxx", PURPLE),
    ("NOTION_DB_ID = 1031a6aff6b3...  # 可選", MUTED),
    ("", WHITE),
    ("# 券商 Shioaji", MUTED),
    ("SHIOAJI_API_KEY    = xxx", YELLOW),
    ("SHIOAJI_SECRET_KEY = xxx", YELLOW),
    ("SHIOAJI_SIMULATION = true  # 改 false 為真實", YELLOW),
    ("", WHITE),
    ("# AI 模型", MUTED),
    ("ANTHROPIC_API_KEY = sk-ant-xxx", TEAL),
    ("GEMINI_API_KEY    = xxx", TEAL),
    ("", WHITE),
    ("# 安全上限（元）", MUTED),
    ("ORDER_HARD_LIMIT = 150000", LIGHT),
    ("BUDGET_PER_STOCK = 100000", LIGHT),
]
for j, (line, c) in enumerate(env2):
    T(d, line, 44, 192+j*38, c, 21)

# 右欄
card(d, 975, 125, W-995, H-145, ACCENT)
T(d, "🚀  啟動指令", 995, 138, ACCENT, 28)
hline(d, 995, W-20, 178, ACCENT, 2)
cmds2 = [
    ("# 主排程（每交易日自動執行）", MUTED),
    ("python3 main.py", DOWN),
    ("", WHITE),
    ("# Telegram Bot（另開一個終端）", MUTED),
    ("python3 telegram_bot.py", YELLOW),
    ("", WHITE),
    ("# 每日 13:35 後收盤任務", MUTED),
    ("bash post_market.sh", PURPLE),
    ("", WHITE),
    ("# 加入 crontab 自動執行：", MUTED),
    ("35 13 * * 1-5  bash post_market.sh", PURPLE),
    ("", WHITE),
    ("# 手動查看 AI 學習狀態", MUTED),
    ("python3 adaptive_scorer.py", ACCENT),
    ("", WHITE),
    ("# 手動推送 Notion 報告", MUTED),
    ("python3 notion_reporter.py 2026-05-29", TEAL),
    ("", WHITE),
    ("# Telegram Bot 主選單", MUTED),
    ("發送 /menu 到 Bot 即可操作", WHITE),
]
for j, (line, c) in enumerate(cmds2):
    T(d, line, 995, 192+j*38, c, 21)

slides.append(img)

# ══════════════════════════════════════════════════
# Slide 10 — 總結
# ══════════════════════════════════════════════════
img, d = new_img()
R(d, 0, 260, W, 268, UP)
R(d, 0, 270, W, 276, ACCENT)
T(d, "系統設計六大亮點", 0, 60, WHITE, 62, cx=W)
T(d, "QUANT · AI  台股量化交易工作站", 0, 155, MUTED, 30, cx=W)

hi = [
    ("💡", "買入手動  賣出自動",  UP,     "下單需 Telegram 二次確認\n出場全自動，不等人"),
    ("🛡", "四層出場保護",        YELLOW, "停損3% / 達目標9%\n追蹤停損 / 13:25強制"),
    ("🧠", "AI 每日學習",         PURPLE, "評分區間勝率統計\n自動調整最低門檻"),
    ("📋", "Notion 每日報告",      TEAL,   "每日自動推送\n勝率趨勢可視化"),
    ("📱", "12 個 Telegram 節點", ACCENT, "系統啟動到收盤\n全程追蹤無遺漏"),
    ("🇹🇼", "台股色彩規範",       DOWN,   "漲紅 #C8332B\n跌綠 #2E7D4F"),
]
hw3 = 580
hgap3 = 16
htotal2 = 3*hw3 + 2*hgap3
hsx2 = (W-htotal2)//2
for i, (icon, title, color, desc) in enumerate(hi):
    col = i % 3
    row = i // 3
    bx = hsx2 + col*(hw3+hgap3)
    by = 300 + row*350
    card(d, bx, by, hw3, 320, color)
    T(d, icon,  bx+16, by+14, WHITE,  50)
    T(d, title, bx+86, by+22, color,  30)
    hline(d, bx+16, bx+hw3-16, by+82, color, 2)
    for j, line in enumerate(desc.split("\n")):
        T(d, line, bx+16, by+102+j*68, LIGHT, 26)

slides.append(img)

# ── 輸出 PDF ─────────────────────────────────────────────────────────────────
out = "QUANT_AI_流程圖.pdf"
slides[0].save(out, save_all=True, append_images=slides[1:], format="PDF")
sz = os.path.getsize(out)/1024/1024
print(f"✅ {out}  {len(slides)} 頁  {sz:.1f} MB")
