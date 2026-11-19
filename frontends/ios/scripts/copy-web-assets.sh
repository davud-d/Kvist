#!/usr/bin/env bash
# The connect screen shares its look with the page the computer serves: the same stylesheet, sprites, fonts and
# small modules, taken from the web client instead of copied into git, so the two cannot drift apart. The screen
# itself (connect.html/connect.js) is the Android app's file: one screen for both phones. This is the list the
# Android build copies (frontends/android/app/build.gradle.kts), laid out as /assets/... inside the app bundle.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
client="$here/../web-local/kvist_web/client"
connect="$here/../android/app/src/main/assets"
out="${TARGET_BUILD_DIR:?run from Xcode}/${UNLOCALIZED_RESOURCES_FOLDER_PATH:?run from Xcode}/web"

rm -rf "$out"
mkdir -p "$out/mobile" "$out/fonts"
cp "$connect/connect.html" "$connect/connect.js" "$out/"
for file in pixel.css tablet.css season.js i18n.js mascot.js snow.js; do
  cp "$client/mobile/$file" "$out/mobile/"
done
cp -R "$client/mobile/sprites" "$client/mobile/fonts" "$out/mobile/"
cp "$client/fonts/JetBrainsMono.woff2" "$out/fonts/"
cp "$client/kvist-mark.svg" "$out/"
