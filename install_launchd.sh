#!/usr/bin/env bash
# install_launchd.sh — 用 macOS 原生 launchd 取代失效的 cron
#
# 背景：現代 macOS（Catalina+）把 cron 列為受限程序，需「完全磁碟取用權限」
# 才能執行 crontab。本機 cron daemon 從未被授權 → crontab 寫對也不會跑
# （證據：start_all.log 從不存在、log show process==cron 全空）。
# launchd 跑在使用者登入 session，繼承使用者權限，可正常存取外接 SSD，
# 且睡眠錯過時間後喚醒會補跑一次。
#
# 用法：bash install_launchd.sh
# 移除：launchctl unload ~/Library/LaunchAgents/com.timchen.quantai.start.plist

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
PLIST="$LA_DIR/com.timchen.quantai.start.plist"

mkdir -p "$LA_DIR"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.timchen.quantai.start</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SCRIPT_DIR}/start_all.sh</string>
  </array>
  <key>WorkingDirectory</key><string>${SCRIPT_DIR}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>25</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>25</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>25</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>25</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>25</integer></dict>
  </array>
  <key>StandardOutPath</key><string>${HOME}/quantai_launchd.out</string>
  <key>StandardErrorPath</key><string>${HOME}/quantai_launchd.err</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ launchd 已安裝：$PLIST"
echo "   平日 08:25 自動啟動（取代 cron）。log → ~/quantai_launchd.{out,err}"
echo "   立即測試一次：launchctl start com.timchen.quantai.start"
