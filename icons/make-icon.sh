#!/bin/bash
# 生成 AppIcon.icns —— 先画 1024 母版，再切全套尺寸，最后合成 icns
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$DIR/../AppIcon.icns}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "── 1/3 绘制 1024 母版 ──"
swiftc -O -o "$WORK/make-icon" "$DIR/make-icon.swift"
"$WORK/make-icon" "$DIR/preview-1024.png"

echo
echo "── 2/3 切全套尺寸 ──"
ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
            "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
    set -- $spec
    sips -z "$1" "$1" "$DIR/preview-1024.png" --out "$ICONSET/$2.png" >/dev/null 2>&1
    printf "  %-18s %sx%s\n" "$2" "$1" "$1"
done
cp "$ICONSET/icon_256x256.png" "$DIR/preview-256.png"

echo
echo "── 3/3 合成 icns ──"
iconutil -c icns "$ICONSET" -o "$OUT"

echo
echo "✅ 图标已生成：$OUT（$(du -h "$OUT" | cut -f1)）"
echo "   母版预览：$DIR/preview-1024.png"
