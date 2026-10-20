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
app="$output/AIAnti.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/backend"
cp "$bin/AIAnti" "$app/Contents/MacOS/AIAnti"
cp "$source_dir/backend/control.py" "$source_dir/backend/config_store.py" "$source_dir/backend/checks.py" "$app/Contents/Resources/backend/"
cp "$source_dir/Resources/MenuBarTemplate.png" "$app/Contents/Resources/"
iconset="$output/build/AppIcon.iconset"
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$source_dir/Resources/AppIcon.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    double_size=$((size * 2))
    sips -z "$double_size" "$double_size" "$source_dir/Resources/AppIcon.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>AIAnti</string>
<key>CFBundleIdentifier</key><string>org.ai-anti.reviewbar</string>
<key>CFBundleName</key><string>AIAnti</string>
<key>CFBundleDisplayName</key><string>AIAnti</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleVersion</key><string>3</string>
<key>CFBundleShortVersionString</key><string>0.2.1</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$app"
printf '%s\n' "$app"
