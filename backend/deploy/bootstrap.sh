#!/usr/bin/env bash
# Raspberry Pi entry point: install base tools and uv, optionally store a GitHub token
# (not needed for the public repository), then fetch install-release.sh from the given tag and run it.
#
#   sudo bash bootstrap.sh v0.1.0
set -euo pipefail

# Keep identical to VERSION_PATTERN in build_release.py and install-release.sh.
VERSION_PATTERN='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z][0-9A-Za-z.]*)?$'
REPOSITORY="${MOON_OBSERVE_REPOSITORY:-whyixd/tph_art_museum}"
INSTALL_ROOT="${MOON_OBSERVE_INSTALL_ROOT:-/opt/moon-observe}"
CONFIG_ROOT="${MOON_OBSERVE_CONFIG_ROOT:-/etc/moon-observe}"
TOKEN_FILE="${MOON_OBSERVE_GITHUB_TOKEN_FILE:-$CONFIG_ROOT/github-token}"

die() { echo "bootstrap: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0 VERSION"
[[ $# -eq 1 ]] || die "usage: sudo bash $0 VERSION (e.g. v0.1.0)"
VERSION="$1"
[[ $VERSION =~ $VERSION_PATTERN ]] || die "invalid version: $VERSION"
command -v apt-get >/dev/null || die "apt-get is required"

echo "==> Installing OS packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates coreutils curl python3 tar util-linux

if [[ ! -x /usr/local/bin/uv ]]; then
  echo "==> Installing uv to /usr/local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

install -d -m 0755 -o root -g root "$INSTALL_ROOT"
if [[ ! -d $CONFIG_ROOT ]]; then
  install -d -m 0700 -o root -g root "$CONFIG_ROOT"
fi

# 公開 repo 不需要 token；私有 repo 時輸入 fine-grained token（Contents: Read），直接 Enter 略過。
# The public repository needs no token; for a private one enter a fine-grained token (Contents: Read), or Enter to skip.
if [[ ! -s $TOKEN_FILE && -r /dev/tty && -z ${MOON_OBSERVE_NO_TOKEN:-} ]]; then
  read -r -s -p "GitHub token for $REPOSITORY (Enter to skip for a public repository): " token </dev/tty
  echo
  if [[ -n $token ]]; then
    (umask 077 && printf '%s\n' "$token" >"$TOKEN_FILE")
  fi
  unset token
fi
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
auth=()
if [[ -s $TOKEN_FILE ]]; then
  chown root:root "$TOKEN_FILE"
  chmod 0600 "$TOKEN_FILE"
  (umask 077 && printf 'Authorization: Bearer %s\n' "$(tr -d '[:space:]' <"$TOKEN_FILE")" >"$workdir/auth")
  auth=(-H @"$workdir/auth")
fi

echo "==> Fetching install-release.sh at $VERSION"
curl -fsSL "${auth[@]}" \
  -H 'Accept: application/vnd.github.raw+json' \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  -o "$workdir/install-release.sh" \
  "https://api.github.com/repos/$REPOSITORY/contents/backend/deploy/install-release.sh?ref=$VERSION"

MOON_OBSERVE_REPOSITORY="$REPOSITORY" \
MOON_OBSERVE_INSTALL_ROOT="$INSTALL_ROOT" \
MOON_OBSERVE_CONFIG_ROOT="$CONFIG_ROOT" \
MOON_OBSERVE_GITHUB_TOKEN_FILE="$TOKEN_FILE" \
  bash "$workdir/install-release.sh" "$VERSION"
