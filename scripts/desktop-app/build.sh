#!/bin/bash
# Builds "JARVIS Desktop.app" (default: on the Desktop) from launcher.sh.
#   bash scripts/desktop-app/build.sh [destination-folder]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEST="${1:-$HOME/Desktop}"
APP="$DEST/JARVIS Desktop.app"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed "s|__REPO__|$REPO|" "$HERE/launcher.sh" >"$APP/Contents/MacOS/launcher"
chmod +x "$APP/Contents/MacOS/launcher"

cat >"$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>JARVIS Desktop</string>
  <key>CFBundleDisplayName</key><string>JARVIS Desktop</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>local.jarvis.desktop</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSRequiresNativeExecution</key><true/>
</dict>
</plist>
PLIST

# Icon: render the SVG with headless Chrome, then build an .icns.
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --default-background-color=00000000 --user-data-dir="$WORK/chrome" \
  --window-size=1024,1024 --screenshot="$WORK/icon.png" "file://$HERE/icon.html" >/dev/null 2>&1
ICONSET="$WORK/AppIcon.iconset"
mkdir "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$WORK/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z $d $d "$WORK/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

touch "$APP"
echo "Built: $APP"

# Native helper apps: real compiled apps with their own usage descriptions,
# because macOS only grants (or even asks for) Calendar, Reminders, Messages
# and Automation access to one of those. Each is rebuilt only when its source
# changes — a new binary has a new signature, and macOS would ask for every
# permission all over again.
SUPPORT="$HOME/Library/Application Support/JARVIS Desktop"

build_helper() { # <name> <executable> <bundle id> <source dir> <plist extras> [swiftc flags...]
  local name="$1" exe="$2" id="$3" src="$4" extras="$5"; shift 5
  local helper="$SUPPORT/$name.app"
  if [ -x "$helper/Contents/MacOS/$exe" ] && [ "$helper/Contents/MacOS/$exe" -nt "$src/main.swift" ]; then
    echo "Up to date: $helper"
    return
  fi
  rm -rf "$helper"
  mkdir -p "$helper/Contents/MacOS" "$helper/Contents/Resources"
  xcrun swiftc -O -o "$helper/Contents/MacOS/$exe" "$src/main.swift" "$@"
  cp "$APP/Contents/Resources/AppIcon.icns" "$helper/Contents/Resources/AppIcon.icns"
  cat >"$helper/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$name</string>
  <key>CFBundleDisplayName</key><string>$name</string>
  <key>CFBundleExecutable</key><string>$exe</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>$id</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
$extras
</dict>
</plist>
PLIST
  codesign --force --sign - "$helper" >/dev/null 2>&1
  echo "Built: $helper"
}

build_helper "JARVIS Calendar" JarvisCalendar local.jarvis.calendar "$HERE/calendar-helper" '
  <key>NSCalendarsFullAccessUsageDescription</key><string>JARVIS reads your schedule to you and adds, changes or removes events when you ask.</string>
  <key>NSCalendarsUsageDescription</key><string>JARVIS reads your schedule to you and adds, changes or removes events when you ask.</string>
  <key>NSRemindersFullAccessUsageDescription</key><string>JARVIS creates, changes and deletes reminders when you ask.</string>
  <key>NSRemindersUsageDescription</key><string>JARVIS creates, changes and deletes reminders when you ask.</string>'

build_helper "JARVIS Messages" JarvisMessages local.jarvis.messages "$HERE/messages-helper" '
  <key>NSAppleEventsUsageDescription</key><string>JARVIS sends his replies to you through Messages.</string>' -lsqlite3

build_helper "JARVIS Messages Cleaner" JarvisCleaner local.jarvis.cleaner "$HERE/cleaner-helper" ''
