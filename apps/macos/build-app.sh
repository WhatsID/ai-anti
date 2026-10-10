#!/bin/bash
set -euo pipefail
# No install step; all build products/caches are outside the source tree.
source_dir=$(cd "$(dirname "$0")" && pwd)
output=${1:?用法: build-app.sh /绝对路径/输出目录}
case "$output" in /*) ;; *) echo '输出目录必须为绝对路径' >&2; exit 1 ;; esac
mkdir -p "$output/cache" "$output/build"
export CLANG_MODULE_CACHE_PATH="$output/cache/clang"
export SWIFT_MODULECACHE_PATH="$output/cache/swift"
swift build --cache-path "$output/cache/swiftpm" --config-path "$output/cache/config" --security-path "$output/cache/security" --package-path "$source_dir" --scratch-path "$output/build" -c release
bin=$(swift build --cache-path "$output/cache/swiftpm" --config-path "$output/cache/config" --security-path "$output/cache/security" --package-path "$source_dir" --scratch-path "$output/build" -c release --show-bin-path)
app="$output/AIReviewBar.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/backend"
cp "$bin/AIReviewBar" "$app/Contents/MacOS/AIReviewBar"
cp "$source_dir/backend/control.py" "$source_dir/backend/config_store.py" "$source_dir/backend/checks.py" "$app/Contents/Resources/backend/"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>AIReviewBar</string>
<key>CFBundleIdentifier</key><string>org.ai-anti.reviewbar</string>
<key>CFBundleName</key><string>ai-anti</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$app"
printf '%s\n' "$app"
