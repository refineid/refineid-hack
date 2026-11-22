#!/bin/sh
# Copies the host and the extension into a user directory and registers the
# native messaging manifest with Firefox.
# Usage: install.sh [DESTINATION]
set -eu

here=$(cd "$(dirname "$0")" && pwd)
destination=${1:-"$HOME/refineid-webeid"}
registry="$HOME/.mozilla/native-messaging-hosts"

mkdir -p "$destination" "$registry"
cp "$here/host.py" "$here/host.sh" "$destination/"
rm -rf "$destination/ext"
cp -R "$here/ext" "$destination/ext"
chmod +x "$destination/host.sh" "$destination/host.py"

sed "s#__ENTRY__#$destination/host.sh#" "$here/native-manifest.json" \
  > "$registry/refineid_webeid.json"

echo "host installed in $destination"
echo "extension directory: $destination/ext"
