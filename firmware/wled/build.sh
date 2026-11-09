#!/usr/bin/env bash
# Build WLED with the moon motor usermod: clone the pinned tag, apply the patch,
# copy the usermod and run PlatformIO. Needs git, PlatformIO (pio) and Node.js.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
version="${WLED_VERSION:-$(tr -d '[:space:]' < "$here/WLED_VERSION")}"
work="${WLED_WORK_DIR:-$here/.build/WLED-$version}"
pio="${PIO:-pio}"

if [ ! -d "$work/.git" ]; then
  # Keep LF on Windows too, otherwise the LF patch does not apply.
  git clone -c core.autocrlf=false --depth 1 --branch "$version" https://github.com/wled/WLED.git "$work"
fi
# GL-C-616WL uses Ethernet type 13 (Gledopto), added in v0.15.4; older WLED would boot without network.
if ! grep -q "WLED_ETH_GLEDOPTO" "$work/wled00/const.h"; then
  echo "WLED $version has no Gledopto Ethernet type; use the version running on the GL-C-616WL (>= v0.15.4)." >&2
  exit 1
fi
# Start from the pristine tag every time so the patch always applies cleanly.
git -C "$work" checkout -- wled00
rm -rf "$work/usermods/moon_motor"
git -C "$work" apply "$here/patches/0001-moon-motor-hook.patch"
cp -R "$here/usermod_moon_motor" "$work/usermods/moon_motor"
cp "$here/platformio_override.ini" "$work/platformio_override.ini"

(cd "$work" && "$pio" run -e moon_eth)

mkdir -p "$here/dist"
out="$here/dist/WLED_${version}_moon_eth.bin"
cp "$work/.pio/build/moon_eth/firmware.bin" "$out"
# Stock WLED 4MB layout; compare with the unit before a USB flash (see README).
cp "$work/.pio/build/moon_eth/partitions.bin" "$here/dist/partitions_reference.bin"
echo "Firmware: $out"
