"""
make_pdf.py — 把 QUANT·AI 流程圖重新用 Pillow 畫成 PDF
執行：python3 make_pdf.py
輸出：QUANT_AI_流程圖.pdf
"""
from PIL import Image, ImageDraw, ImageFont
import os

# ── 尺寸 & 顏色 ───────────────────────────────────────────────────────────────
W, H = 1920, 1080
BLACK   = (13,  17,  23)
SURFACE = (22,  27,  34)
UP      = (200, 51,  43)
DOWN    = (46,  125, 79)
ACCENT  = (38,  139, 210)
MUTED   = (136, 136, 153)
WHITE   = (255, 255, 255)
YELLOW  = (212, 160, 23)
ORANGE  = (240, 140, 0)
TEAL    = (42,  157, 143)
PURPLE  = (139, 92,  246)

# ── 字型 ─────────────────────────────────────────────────────────────────────
def get_font(size):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def get_bold(size):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return get_font(size)


def new_img():
    img = Image.new("RGB", (W, H), BLACK)
    return img, ImageDraw.Draw(img)


def rect(d, x1, y1, x2, y2, fill, outline=None, width=2):
    d.rectangle([x1, y1, x2, y2], fill=fill, outline=outline, width=width)


def text(d, s, x, y, color=WHITE, size=20, bold=False, center_w=None):
    fn = get_bold(size) if bold else get_font(size)
    if center_w:
        bbox = d.textbbox((0, 0), s, font=fn)
        tw = bbox[2] - bbox[0]
        x = x + (center_w - tw) // 2
    d.text((x, y), s, fill=color, font=fn)


def multiline(d, lines_colors, x, y, size=18, line_h=None):
    fn = get_font(size)
    lh = line_h or (size + 8)
    for i, (line, color) in enumerate(lines_colors):
        d.text((x, y + i * lh), line, fill=color, font=fn)


def header_bar(d, title, eyebrow, bar_color=UP):
    rect(d, 0, 0, W, 90, SURFACE)
    text(d, eyebrow, 40, 20, color=bar_color, size=32, bold=True)
    text(d, title,   300, 22, color=WHITE,    size=28, bold=True)
    rect(d, 0, 90, W, 94, bar_color)


# ════════════════════════════════════════════════════════════════════════════════
slides = []

# ── Slide 1 封面 ──────────────────────────────────────────────────────────────
img, d = new_img()
rect(d, 0, 320, W, 328, UP)
rect(d, 0, 330, W, 336, ACCENT)
text(d, "QUANT  AI", 0, 120, color=UP, size=96, bold=True, center_w=W)
text(d, "台股 AI 量化交易系統", 0, 235, color=WHITE, size=44, center_w=W)
text(d, "完整運作流程  ·  時間節點  ·  Telegram 推播", 0, 355, color=MUTED, size=26, center_w=W)

icons = [
    ("AI 選股",   "Gemini + Claude"),
    ("當沖監控",  "TP / SL / Trailing"),
    ("Telegram",  "即時推播確認"),
    ("學習反饋",  "越來越準"),
    ("Notion 報告","每日推送"),
]
bw, bh = 340, 160
gap = 20
total = len(icons) * bw + (len(icons)-1) * gap
sx = (W - total) // 2
for i, (title, sub) in enumerate(icons):
    bx = sx + i * (bw + gap)
    by = 430
    rect(d, bx, by, bx+bw, by+bh, SURFACE)
    text(d, title, bx, by+20,  color=ACCENT, size=24, bold=True, center_w=bw)
    text(d, sub,   bx, by+65, color=MUTED,  size=18, center_w=bw)
text(d, "QUANT · AI System  ·  2026", 0, H-50, color=MUTED, size=18, center_w=W)
slides.append(img)

# ── Slide 2 每日時間軸 ────────────────────────────────────────────────────────
img, d = new_img()
header_bar(d, "每日完整時間軸", "TIMELINE", ACCENT)

events = [
    ("08:30", "盤前 AI 分析",  ACCENT,  "選股+當沖預測\nTelegram 推播"),
    ("09:00", "開盤下單",      DOWN,    "波段下單執行"),
    ("09:05", "開盤確認",      TEAL,    "AI 再確認\nTelegram 報告"),
    ("09:10", "當沖進場",      UP,      "買入 Keyboard\nShioaji 下單"),
    ("09:15", "Tick 監控",     YELLOW,  "MonitorAgent\n訂閱 tick"),
    ("10:00", "盤中檢查",      ACCENT,  "持倉損益\nTelegram 報告"),
    ("13:00", "出場警報",      ORANGE,  "損益預覽\n25分後強制"),
    ("13:15", "停止監控",      MUTED,   "MonitorAgent\n停止"),
    ("13:25", "強制平倉",      UP,      "市價出清\n所有部位"),
    ("13:35", "收盤任務",      PURPLE,  "複盤+學習\nNotion 推送"),
]

# 橫線
line_y = 600
rect(d, 80, line_y, W-80, line_y+6, ACCENT)

ew = (W - 160) // len(events)
for i, (t, title, color, detail) in enumerate(events):
    cx = 80 + i * ew + ew // 2
    # 圓點
    r = 14
    d.ellipse([cx-r, line_y-r+3, cx+r, line_y+r+3], fill=color)
    # 時間
    fn_b = get_bold(22)
    fn_s = get_font(18)
    if i % 2 == 0:
        text(d, t, cx - 40, line_y - 80, color=color, size=22, bold=True)
        rect(d, cx-85, line_y+30, cx+85, line_y+180, SURFACE)
        text(d, title,  cx - 75, line_y + 40,  color=color, size=18, bold=True)
        for j, line in enumerate(detail.split("\n")):
            text(d, line, cx - 75, line_y + 75 + j*30, color=MUTED, size=16)
    else:
        rect(d, cx-85, line_y-185, cx+85, line_y-35, SURFACE)
        text(d, title,  cx - 75, line_y - 178, color=color, size=18, bold=True)
        for j, line in enumerate(detail.split("\n")):
            text(d, line, cx - 75, line_y - 143 + j*30, color=MUTED, size=16)
        text(d, t, cx - 40, line_y + 20, color=color, size=22, bold=True)

slides.append(img)

# ── Slide 3 08:30 盤前 AI 分析 ───────────────────────────────────────────────
img, d = new_img()
header_bar(d, "08:30  盤前 AI 分析 — PremarketJob + DaytradingReport", "08:30", UP)

steps = [
    ("① Gemini 早晨簡報", ACCENT, [
        "· 抓取台股加權 / 外資 / 投信",
        "· SOX / VIX / 美股數據",
        "· Gemini 生成市場分析",
        "· 輸出 MorningBriefing",
    ]),
    ("② risk_guard 風控", YELLOW, [
        "· 每筆預算 ≤ 30%",
        "· 同板塊持倉上限",
        "· 黑名單過濾",
        "· 拒絕不符合的 picks",
    ]),
    ("③ 三套策略生成", DOWN, [
        "· 衝刺版（高風高報）",
        "· 均衡版（平衡）",
        "· 保守版（防守）",
        "· 各含選股 + 預期報酬",
    ]),
    ("④ 當沖預測報告", PURPLE, [
        "· 掃描全市場股票",
        "· dt_score 技術評分 0-10",
        "· Haiku AI 分析評分≥4",
        "· 存入 daytrading_review.db",
    ]),
    ("⑤ Telegram 推播", ORANGE, [
        "· 早晨市場簡報",
        "· 三套策略各一則",
        "· Inline Keyboard：",
        "  [🚀衝刺] [⚖️均衡] [🛡️保守]",
    ]),
]

bw = 340
gap = 14
total_w = len(steps)*bw + (len(steps)-1)*gap
sx2 = (W - total_w) // 2
for i, (title, color, items) in enumerate(steps):
    bx = sx2 + i*(bw+gap)
    rect(d, bx, 110, bx+bw, H-60, SURFACE)
    rect(d, bx, 110, bx+bw, 118, color)
    text(d, title, bx+14, 128, color=color, size=20, bold=True)
    for j, item in enumerate(items):
        text(d, item, bx+14, 180+j*48, color=WHITE, size=17)

text(d, "使用者點選策略 → 09:00 執行 · 60 秒 timeout 後自動略過", 0, H-45, color=MUTED, size=18, center_w=W)
slides.append(img)

# ── Slide 4 09:00–09:10 開盤三步驟 ──────────────────────────────────────────
img, d = new_img()
header_bar(d, "09:00 – 09:10  開盤進場三步驟", "OPEN", DOWN)

phases = [
    ("09:00  波段下單", DOWN, "MarketOpenJob", [
        "・使用者已選好策略",
        "・執行 picks",
        "  open / add / close",
        "・place_stock_order()",
        "  via Shioaji API",
        "・寫入 research.db",
        "",
        "⚠ 每筆下單前：",
        "send_confirmation()",
        "二次確認 / 60s timeout",
    ]),
    ("09:05  開盤確認", TEAL, "run_opening_reconfirm()", [
        "AI 再次確認進場條件：",
        "  ・量能比 ≥ 0.8？",
        "  ・大盤方向吻合？",
        "  ・台指期溢貼水正常？",
        "",
        "→ confirmed：繼續",
        "→ skipped：放棄",
        "",
        "📱 Telegram 推播：",
        "  ✅/❌ 每支股狀態",
        "  現價 + 進場區間",
    ]),
    ("09:10  當沖進場", UP, "send_dt_buy_confirmation()", [
        "📱 Telegram Inline Keyboard：",
        "",
        "[✅ 2330 台積電] [❌ 跳過]",
        "[✅ 2382 廣達]   [❌ 跳過]",
        "[✅ 全部買入]    [❌ 全部跳過]",
        "",
        "使用者點選 → callback",
        "→ _handle_dt_buy(code)",
        "→ Shioaji 下買單",
        "→ MonitorAgent 開始監控",
        "→ watching → active",
    ]),
]

pw = 590
gap2 = 20
px = (W - (3*pw + 2*gap2)) // 2
for i, (title, color, func, items) in enumerate(phases):
    bx = px + i*(pw+gap2)
    rect(d, bx, 110, bx+pw, H-60, SURFACE)
    rect(d, bx, 110, bx+pw, 118, color)
    text(d, title, bx+15, 128, color=color, size=22, bold=True)
    text(d, func,  bx+15, 162, color=MUTED, size=15)
    for j, item in enumerate(items):
        c = ACCENT if item.startswith("[") else (MUTED if item.startswith("  ") else WHITE)
        text(d, item, bx+15, 205+j*47, color=c, size=16)

slides.append(img)

# ── Slide 5 Tick 監控 + 四種出場 ─────────────────────────────────────────────
img, d = new_img()
header_bar(d, "09:15 – 13:15  Tick 監控 + 即時出場決策", "MONITOR", YELLOW)

# 左側說明
rect(d, 30, 110, 550, H-60, SURFACE)
text(d, "MonitorAgent", 50, 125, color=YELLOW, size=24, bold=True)
mon_lines = [
    ("訂閱 Shioaji Tick 串流", WHITE),
    ("每個 tick → check_trailing_stop()", WHITE),
    ("", WHITE),
    ("判斷優先順序：", MUTED),
    ("", WHITE),
    ("① gain ≤ -3.0%  →  停損出場", UP),
    ("② gain ≥ +9.0%  →  達目標出場", DOWN),
    ("③ 時間 ≥ 13:00  →  強制出場", ORANGE),
    ("④ peak ≥ +3% 且", YELLOW),
    ("   回落 ≥ 2%  →  追蹤停損", YELLOW),
    ("", WHITE),
    ("peak_price 每 tick 更新", MUTED),
    ("（只漲不跌）", MUTED),
]
multiline(d, mon_lines, 50, 175, size=18, line_h=35)

# 右側四個出場
triggers = [
    ("停損出場",    UP,     "3.0%",    "gain ≤ -stop_loss_pct", "立即市價賣出"),
    ("達目標出場",  DOWN,   "9.0%",    "gain ≥ take_profit_pct","鎖住最大獲利"),
    ("追蹤停損",    YELLOW, "3% / 2%", "peak≥trailing_start\n回落≥trailing_gap","從高點保護利潤"),
    ("強制平倉",    ORANGE, "13:00",   "當前時間 ≥ 13:00",      "確保不留倉過夜"),
]
tw2 = 320
tx2 = 580
gap3 = 14
for i, (title, color, threshold, cond, action) in enumerate(triggers):
    bx = tx2 + i*(tw2+gap3)
    rect(d, bx, 110, bx+tw2, H-60, SURFACE)
    rect(d, bx, 110, bx+tw2, 118, color)
    text(d, title,          bx+12, 128, color=color, size=21, bold=True)
    text(d, "閾值："+threshold, bx+12, 168, color=ACCENT, size=17)
    text(d, "條件：",        bx+12, 210, color=MUTED,  size=15)
    for j, line in enumerate(cond.split("\n")):
        text(d, line,        bx+12, 235+j*30, color=WHITE, size=16)
    text(d, "動作：",        bx+12, 310, color=MUTED,  size=15)
    text(d, action,          bx+12, 335, color=DOWN,   size=16)
    text(d, "📱 Telegram 即時通知", bx+12, 400, color=YELLOW, size=15)
    text(d, "✅ 出場已送出 + 原因 + 價格", bx+12, 430, color=MUTED, size=15)

slides.append(img)

# ── Slide 6 13:00 / 13:25 / 13:35 ───────────────────────────────────────────
img, d = new_img()
header_bar(d, "13:00 – 13:35  收盤三連發", "CLOSE", ORANGE)

phase3 = [
    ("13:00  出場警報", ORANGE, [
        "📱 Telegram 推播：",
        "",
        "📊 當沖出場警報 13:00",
        "🔴 2330 台積電",
        "   買 1068 → 現 1082",
        "   損益 +14,000（+1.3%）",
        "🔴 2382 廣達",
        "   買 305 → 現 312",
        "   損益 +14,000（+2.3%）",
        "",
        "💰 合計損益：+28,000 元",
        "⚠ 13:25 將自動強制平倉",
    ]),
    ("13:25  強制平倉", UP, [
        "ForceCloseJob",
        "",
        "Real 模式：",
        "→ place_order(price=0, MKT)",
        "→ 市價單確保成交",
        "→ 等待 fill 回報",
        "",
        "Sim 模式：",
        "→ 取即時報價",
        "→ 計算損益",
        "→ 寫入已平倉記錄",
        "",
        "100% 確保不留倉過夜",
    ]),
    ("13:35  收盤四任務", PURPLE, [
        "① PostMarketJob",
        "   停監控 + 存每日摘要",
        "   notify_market_close()",
        "",
        "② DaytradingReview",
        "   回填 OHLC（yfinance）",
        "   → Telegram 複盤報告",
        "",
        "③ adaptive_scorer.py",
        "   更新評分區間勝率",
        "   寫入 adaptive_config.json",
        "",
        "④ notion_reporter.py",
        "   推送今日報告到 Notion",
    ]),
]

pw2 = 590
px2 = (W - (3*pw2 + 2*gap2)) // 2
for i, (title, color, items) in enumerate(phase3):
    bx = px2 + i*(pw2+gap2)
    rect(d, bx, 110, bx+pw2, H-60, SURFACE)
    rect(d, bx, 110, bx+pw2, 118, color)
    text(d, title, bx+15, 128, color=color, size=22, bold=True)
    for j, item in enumerate(items):
        c = WHITE
        if item.startswith("→"):   c = DOWN
        if item.startswith("   ") or item.startswith("Real") or item.startswith("Sim"):
            c = MUTED
        if item[0:1] in list("①②③④"):
            c = YELLOW
        text(d, item, bx+15, 185+j*48, color=c, size=16)

slides.append(img)

# ── Slide 7 Telegram 推播完整清單 ────────────────────────────────────────────
img, d = new_img()
header_bar(d, "Telegram 推播完整清單  ( 12 個節點 )", "TELEGRAM", YELLOW)

msgs = [
    ("系統啟動",   MUTED,   "notify_system_start()",           "系統已啟動 · 模擬/真實模式"),
    ("08:30",     ACCENT,  "send_morning_push()",             "早晨市場簡報 + 三套策略 + 選擇 Keyboard"),
    ("08:30",     PURPLE,  "DaytradingReport",                "今日當沖候選股 · 評分 · 信心度"),
    ("09:05",     TEAL,    "開盤確認通知",                      "每支股 ✅確認/❌放棄 + 現價 + 大盤方向"),
    ("09:10",     UP,      "send_dt_buy_confirmation()",      "[✅ CODE NAME] [❌跳過] Inline Keyboard"),
    ("盤中即時",   YELLOW,  "_run_dt_sell_alerts()",           "✅出場已送出 / ❌出場失敗 + 觸發原因 + 價格"),
    ("10:00",     ACCENT,  "_mid_session_check()",            "持倉損益 + 剩餘候選提示"),
    ("13:00",     ORANGE,  "出場警報",                          "所有持倉損益 + ⚠ 13:25 強制平倉"),
    ("13:25",     UP,      "ForceCloseJob",                   "強制平倉執行結果"),
    ("13:35",     DOWN,    "notify_market_close()",           "今日總損益 + 交易筆數"),
    ("13:35",     PURPLE,  "DaytradingReview",                "預測複盤：hit_target / hit_stop · 近30日勝率"),
    ("系統停止",   MUTED,   "notify_system_stop()",           "系統已停止"),
]

# 表頭
hx3 = [40, 200, 680, 1250]
hl = ["時間", "函式 / 模組", "推播內容", ""]
for hh, hbx in zip(hl, hx3):
    text(d, hh, hbx, 108, color=MUTED, size=17, bold=True)
rect(d, 30, 130, W-30, 134, MUTED)

for i, (ts, color, func, content) in enumerate(msgs):
    ry = 144 + i*74
    if i % 2 == 0:
        rect(d, 30, ry, W-30, ry+70, SURFACE)
    text(d, ts,      hx3[0], ry+16, color=color, size=18, bold=True)
    # 色點
    d.ellipse([hx3[1], ry+22, hx3[1]+20, ry+42], fill=color)
    text(d, func,    hx3[1]+28, ry+16, color=WHITE, size=16)
    text(d, content, hx3[2],    ry+16, color=MUTED, size=15)

slides.append(img)

# ── Slide 8 AI 學習反饋回路 ──────────────────────────────────────────────────
img, d = new_img()
header_bar(d, "AI 學習反饋回路 — 越來越準", "LEARNING", PURPLE)

cycle = [
    ("① 盤前 AI 分析\n08:30", ACCENT,  140,  160),
    ("② 使用者執行\n09:10 買入", DOWN,  700,  160),
    ("③ 盤中監控\nTP/SL 觸發", YELLOW, 1280,  160),
    ("④ 收盤複盤\n13:35", ORANGE,       1280,  530),
    ("⑤ 學習更新\nadaptive_scorer", PURPLE, 700, 530),
    ("⑥ Notion 報告\n每日推送", TEAL,  140,  530),
]
cw2, ch2 = 480, 180
for title, color, cx2, cy2 in cycle:
    rect(d, cx2, cy2, cx2+cw2, cy2+ch2, SURFACE)
    rect(d, cx2, cy2, cx2+cw2, cy2+8, color)
    for j, line in enumerate(title.split("\n")):
        text(d, line, cx2+16, cy2+20+j*55, color=color, size=22, bold=True)

# 箭頭文字
arrows = [
    (620, 245, "→ 評分預測"),
    (1195, 245, "→ 成交記錄"),
    (1450, 470, "↓ OHLC 回填"),
    (1195, 610, "← 勝率統計"),
    (620,  610, "← learning_summary"),
    (220,  470, "↑ 注入隔日提示"),
]
for ax, ay, alabel in arrows:
    text(d, alabel, ax, ay, color=MUTED, size=17)

# 底部效果說明
rect(d, 30, H-140, W-30, H-30, SURFACE)
text(d, "學習效果（隨交易日累積）：", 50, H-130, color=YELLOW, size=20, bold=True)
effects = [
    "評分 4-5 分 → 歷史勝率 31% → 建議跳過",
    "評分 6-7 分 → 歷史勝率 58% → 推薦操作",
    "評分 8-10分 → 歷史勝率 72% → 強烈推薦",
]
for k, eff in enumerate(effects):
    text(d, eff, 50 + k*600, H-90, color=WHITE, size=17)

slides.append(img)

# ── Slide 9 環境設定 & 快速啟動 ──────────────────────────────────────────────
img, d = new_img()
header_bar(d, "環境設定 & 快速啟動", "SETUP", DOWN)

# 左欄 .env
rect(d, 30, 110, 920, H-40, SURFACE)
text(d, ".env 必填設定", 55, 125, color=DOWN, size=22, bold=True)
env = [
    ("# Telegram", MUTED),
    ("TELEGRAM_BOT_TOKEN=你的 Bot Token", ACCENT),
    ("TELEGRAM_CHAT_ID=你的 Chat ID", ACCENT),
    ("TELEGRAM_USER_ID=你的 User ID", ACCENT),
    ("", WHITE),
    ("# Notion（每日推送報告）", MUTED),
    ("NOTION_TOKEN=secret_xxx", PURPLE),
    ("NOTION_DB_ID=1031a6aff6b3...  # 可選", MUTED),
    ("", WHITE),
    ("# 券商 API（Shioaji）", MUTED),
    ("SHIOAJI_API_KEY=xxx", YELLOW),
    ("SHIOAJI_SECRET_KEY=xxx", YELLOW),
    ("SHIOAJI_SIMULATION=true", YELLOW),
    ("", WHITE),
    ("# AI 模型", MUTED),
    ("ANTHROPIC_API_KEY=sk-ant-xxx", TEAL),
    ("GEMINI_API_KEY=xxx", TEAL),
    ("", WHITE),
    ("# 安全上限", MUTED),
    ("ORDER_HARD_LIMIT=150000", WHITE),
    ("BUDGET_PER_STOCK=100000", WHITE),
]
multiline(d, env, 55, 172, size=17, line_h=38)

# 右欄指令
rect(d, 950, 110, W-30, H-40, SURFACE)
text(d, "啟動指令", 975, 125, color=ACCENT, size=22, bold=True)
cmds = [
    ("# 主排程（08:30–13:35 自動執行）", MUTED),
    ("python3 main.py", DOWN),
    ("", WHITE),
    ("# Telegram Bot（另開終端）", MUTED),
    ("python3 telegram_bot.py", YELLOW),
    ("", WHITE),
    ("# 每日 13:35 後收盤任務", MUTED),
    ("bash post_market.sh", PURPLE),
    ("", WHITE),
    ("# 或加入 crontab：", MUTED),
    ("35 13 * * 1-5  bash post_market.sh", PURPLE),
    ("", WHITE),
    ("# 手動查看學習狀態", MUTED),
    ("python3 adaptive_scorer.py", ACCENT),
    ("", WHITE),
    ("# 手動推送 Notion 報告", MUTED),
    ("python3 notion_reporter.py", TEAL),
    ("", WHITE),
    ("# Telegram Bot 主選單", MUTED),
    ("發送：/menu  到 Bot", WHITE),
]
multiline(d, cmds, 975, 172, size=17, line_h=38)

slides.append(img)

# ── Slide 10 總結 ─────────────────────────────────────────────────────────────
img, d = new_img()
rect(d, 0, 290, W, 296, UP)
rect(d, 0, 298, W, 302, ACCENT)
text(d, "系統設計亮點", 0, 60, color=WHITE, size=52, bold=True, center_w=W)
text(d, "QUANT · AI 台股量化交易系統", 0, 145, color=MUTED, size=26, center_w=W)

highlights2 = [
    ("買入手動確認  賣出全自動", UP,     "下單需 Telegram 二次確認\n出場不等人，即時執行"),
    ("四層出場保護",              YELLOW, "停損3% / 達目標9%\n追蹤停損 / 13:25強制"),
    ("AI 每日學習",               PURPLE, "評分區間勝率統計\n自動調整最低門檻"),
    ("Notion 每日報告",            TEAL,   "每日自動推送\n勝率趨勢可視化"),
    ("12 個 Telegram 節點",       ACCENT, "系統啟動到收盤\n全程追蹤無遺漏"),
    ("台股色彩規範",               DOWN,   "漲紅 #C8332B\n跌綠 #2E7D4F"),
]
hw2 = 580
hgap2 = 20
htotal = 3*hw2 + 2*hgap2
hsx = (W - htotal) // 2
for i, (title, color, desc) in enumerate(highlights2):
    col = i % 3
    row = i // 3
    bx = hsx + col*(hw2+hgap2)
    by = 330 + row*290
    rect(d, bx, by, bx+hw2, by+265, SURFACE)
    rect(d, bx, by, bx+hw2, by+7, color)
    text(d, title, bx+16, by+22, color=color, size=20, bold=True)
    for j, line in enumerate(desc.split("\n")):
        text(d, line, bx+16, by+75+j*50, color=MUTED, size=18)

slides.append(img)


# ── 合成 PDF ──────────────────────────────────────────────────────────────────
out = "QUANT_AI_流程圖.pdf"
slides[0].save(
    out,
    save_all=True,
    append_images=slides[1:],
    format="PDF",
)
print(f"✅ 已生成：{out}  ({len(slides)} 頁)")
import os
size_mb = os.path.getsize(out) / 1024 / 1024
print(f"   檔案大小：{size_mb:.1f} MB")
