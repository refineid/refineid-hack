#!/bin/sh

set -eu

usage() {
  cat <<'USAGE' >&2
Usage: fetch_suomi_occupation.sh <PIN1> [lang=en|fi|sv] [ssh_host=kali]

The script logs in to suomi.fi with a local card reader, through `ssh kali`
by default, and prints:
Use `local` as ssh_host to execute directly on this host.
If `kali` is not reachable, check SSH config and run with `local`.

  Your occupation: <profession>

Requirements:
  - openssl (for card mTLS request on kortti.tunnistautuminen.suomi.fi)
  - reader stack (pcsc_scan is optional but helpful)
  - for environments without openssl pkcs11/provider support: export OPENSSL_CLIENT_CERT/OPENSSL_CLIENT_KEY as PEM files

Optional:
  USB_RAW_APDU=1  (attempt direct raw USB APDU path)
                    (requires usb_ioctl_helper and USB_IOCTL_ARGS)
 
Raw USB/APDU mode:
  - This script can inspect /dev/bus/usb and reader devices.
  - Full APDU transport over /dev/bus/usb requires USBFS ioctls and cannot be done with plain read/write.
  - Set USB_IOCTL_ARGS to pass arguments to `usb_ioctl_helper`.
  - Example:
    USB_IOCTL_ARGS='control-in 0xA1 0x00 0x0000 0x0000 18'
    USB_RAW_APDU=1 ./fetch_suomi_occupation.sh <PIN1> <lang> <host>
  - Helper binary default: ./usb_ioctl_helper (set USB_IOCTL_HELPER)
USAGE
}

PIN1="${1:-${REFINEID_PIN1:-}}"
LANGUAGE="${2:-en}"
SSH_HOST="${3:-kali}"
ALLOW_PKCS11="${ALLOW_PKCS11:-1}"

USB_IOCTL_ARGS_B64="$(printf '%s' "${USB_IOCTL_ARGS:-}" | base64 | tr -d '\n')"

if [ -z "$PIN1" ]; then
  usage
  exit 64
fi

case "$LANGUAGE" in
  en|fi|sv) ;;
  *)
    echo "error: language must be en|fi|sv" >&2
    usage
    exit 64
    ;;
esac

if ! command -v ssh >/dev/null 2>&1; then
  if [ "$SSH_HOST" != "local" ] && [ "$SSH_HOST" != "localhost" ] && [ "$SSH_HOST" != "127.0.0.1" ] && [ "$SSH_HOST" != "::1" ]; then
    echo "error: ssh not found in PATH" >&2
    exit 1
  fi
fi

if [ "$SSH_HOST" = "local" ] || [ "$SSH_HOST" = "localhost" ] || [ "$SSH_HOST" = "127.0.0.1" ] || [ "$SSH_HOST" = "::1" ]; then
  export USB_RAW_APDU USB_RAW_NODE USB_IOCTL_ARGS USB_IOCTL_ARGS_B64 USB_IOCTL_HELPER ALLOW_PKCS11 OPENSSL_CLIENT_CERT OPENSSL_CLIENT_KEY PKCS11_CERT_URI PKCS11_KEY_URI OPENSSL_PKCS11_ENGINE REFINEID_OPENSSL
  awk '
    BEGIN { in_block = 0 }
    /^# __SUOMI_WORKER_START__$/ { in_block = 1; next }
    /^# __SUOMI_WORKER_END__$/ { in_block = 0; next }
    in_block { print }
  ' "$0" | sh -s -- "$PIN1" "$LANGUAGE"
  exit $?
fi

ssh "$SSH_HOST" env \
  USB_RAW_APDU="${USB_RAW_APDU:-}" \
  USB_RAW_NODE="${USB_RAW_NODE:-}" \
  USB_IOCTL_ARGS_B64="${USB_IOCTL_ARGS_B64:-}" \
  USB_IOCTL_HELPER="${USB_IOCTL_HELPER:-}" \
  ALLOW_PKCS11="${ALLOW_PKCS11}" \
  OPENSSL_CLIENT_CERT="${OPENSSL_CLIENT_CERT:-}" \
  OPENSSL_CLIENT_KEY="${OPENSSL_CLIENT_KEY:-}" \
  PKCS11_CERT_URI="${PKCS11_CERT_URI:-}" \
  PKCS11_KEY_URI="${PKCS11_KEY_URI:-}" \
  OPENSSL_PKCS11_ENGINE="${OPENSSL_PKCS11_ENGINE:-}" \
  REFINEID_OPENSSL="${REFINEID_OPENSSL:-}" \
  sh -s -- "$PIN1" "$LANGUAGE" <<'REMOTE_SH'
# __SUOMI_WORKER_START__
set -eu

PIN1=$1
LANGUAGE=$2
if [ -n "${USB_IOCTL_ARGS_B64:-}" ]; then
  USB_IOCTL_ARGS="$(printf '%s' "$USB_IOCTL_ARGS_B64" | base64 -d)"
else
  USB_IOCTL_ARGS=
fi
unset USB_IOCTL_ARGS_B64

USAGE_OPENSSL="${REFINEID_OPENSSL:-openssl}"
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

if ! command -v "$USAGE_OPENSSL" >/dev/null 2>&1; then
  echo "error: openssl not found on remote host" >&2
  exit 1
fi
TMPDIR="$(mktemp -d)"
COOKIE_JAR="$TMPDIR/cookies.txt"
trap 'rm -rf "$TMPDIR"' EXIT HUP INT TERM

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
  echo "error: $*" >&2
  exit 1
}

read_cookie() {
  field="$1"
  awk -v field="$field" '$6 == field { print $7; exit }' "$COOKIE_JAR"
}

cookie_header() {
  if [ ! -f "$COOKIE_JAR" ]; then
    return
  fi
  awk 'NF >= 7 && $1 !~ /^#/ { printf "%s=%s; ", $6, $7 }' "$COOKIE_JAR" | sed 's/; $//'
}

append_set_cookie_headers() {
  header_file=$1
  host=$2

  [ -f "$header_file" ] || return 0

  while IFS= read -r line; do
    case "$line" in
      [sS][eE][tT]-[cC][oO][oO][kK][iI][eE]:*)
        cookie_value="${line#*:}"
        cookie_value="$(printf '%s' "$cookie_value" | sed 's/^[[:space:]]*//')"
        cookie_pair="${cookie_value%%;*}"
        case "$cookie_pair" in
          *=*)
            cookie_name="${cookie_pair%%=*}"
            cookie_value_body="${cookie_pair#*=}"
            if [ -n "$cookie_name" ]; then
              printf '%s\tTRUE\t/\tFALSE\t0\t%s\t%s\n' "$host" "$cookie_name" "$cookie_value_body" >> "$COOKIE_JAR"
            fi
            ;;
          *)
            :
            ;;
        esac
        ;;
      *)
        ;;
    esac
  done < "$header_file"
}

url_host() {
  url=$1
  host="${url#*://}"
  host="${host%%/*}"
  host="${host%%:*}"
  echo "$host"
}

url_path() {
  url=$1
  rest="${url#*://}"
  if [ "$rest" = "$url" ]; then
    echo "/"
    return
  fi
  rest="${rest%%#*}"
  case "$rest" in
    */*)
      path="/${rest#*/}"
      ;;
    *\?*)
      path="/?${rest#*?}"
      ;;
    *)
      path="/"
      ;;
  esac
  if [ -z "$path" ]; then
    path="/"
  fi
  echo "$path"
}

url_target() {
  url_path "$1"
}

url_host_simple() {
  url_host "$1"
}

url_path_simple() {
  url_path "$1"
}

url_join_simple() {
  base_url="$1"
  suffix="$2"
  case "$base_url" in
    https://*) scheme_host="https://" ;;
    http://*) scheme_host="http://" ;;
    *) scheme_host="" ;;
  esac
  base_host="${base_url#http://}"
  base_host="${base_host#https://}"
  base_host="${base_host%%/*}"

  if [ -z "$base_host" ]; then
    echo "$suffix"
    return
  fi

  case "$suffix" in
    http://*|https://*)
      echo "$suffix"
      return
      ;;
    /*)
      echo "${scheme_host}${base_host}${suffix}"
      return
      ;;
    *)
      base_rest="${base_url#*://}"
      base_rest="${base_rest#*/}"
      base_path="/${base_rest}"
      base_path="${base_path%/*}/"
      if [ "$base_path" = "//" ] || [ -z "$base_path" ] || [ "$base_path" = "/" ]; then
        base_path="/"
      fi
      echo "${scheme_host}${base_host}${base_path}${suffix}"
      ;;
  esac
}

url_join() {
  url_join_simple "$1" "$2"
}

find_usb_reader_node() {
  hint_node=$1
  if [ -n "$hint_node" ] && [ -e "$hint_node" ]; then
    printf '%s\n' "$hint_node"
    return 0
  fi

  if ! command -v lsusb >/dev/null 2>&1; then
    return 1
  fi

  reader_line=""
  reader_line="$(lsusb | sed -n '/[Ee][Mm][Vv] Smartcard Reader/p;/[Ff][Ii][Nn][Ee][Ii][Dd]/p;/[Ss][Mm][Aa][Rr][Tt][Cc][Aa][Rr][Dd]/p' | head -n 1)"
  if [ -z "$reader_line" ]; then
    reader_line="$(lsusb | sed -n '1p')"
  fi

  bus="$(printf '%s' "$reader_line" | sed -n 's/^Bus[[:space:]]\([0-9][0-9]*\)[[:space:]].*/\1/p')"
  dev="$(printf '%s' "$reader_line" | sed -n 's/.*Device[[:space:]]\([0-9][0-9]*\):.*/\1/p')"
  if [ -z "$bus" ] || [ -z "$dev" ]; then
    return 1
  fi

  bus_node="$(printf '%03d' "$bus")"
  dev_node="$(printf '%03d' "$dev")"
  candidate="/dev/bus/usb/$bus_node/$dev_node"
  if [ -e "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
  }

resolve_usb_apdu_mode() {
  USB_IOCTL_HELPER="${USB_IOCTL_HELPER:-./usb_ioctl_helper}"

  if [ -n "${USB_RAW_NODE:-}" ]; then
    usb_node="${USB_RAW_NODE}"
  else
    usb_node="$(find_usb_reader_node "" || true)"
  fi

  if [ -n "$usb_node" ]; then
    log "raw USB APDU node candidate: $usb_node"
  else
    fail "could not auto-detect raw USB node under /dev/bus/usb (set USB_RAW_NODE)"
  fi

  if [ ! -r "$usb_node" ] || [ ! -w "$usb_node" ]; then
    fail "USB node is not readable/writable by this shell: $usb_node (run as root or adjust permissions)"
  fi

  if ! command -v "$USB_IOCTL_HELPER" >/dev/null 2>&1; then
    fail "USB_IOCTL_HELPER not found or not executable: $USB_IOCTL_HELPER"
  fi

  if [ -z "${USB_IOCTL_ARGS:-}" ]; then
    fail "USB_RAW_APDU requires USB_IOCTL_ARGS (helper op string for usb_ioctl_helper)."
  fi

  old_IFS=$IFS
  IFS=' '
  set -- $USB_IOCTL_ARGS
  IFS=$old_IFS
  if [ "$1" = "--path" ]; then
    shift 2
  fi
  if [ "$#" -eq 0 ]; then
    fail "USB_IOCTL_ARGS parsed to an empty command"
  fi

  log "invoking helper: $USB_IOCTL_HELPER --path $usb_node $*"
  if ! "$USB_IOCTL_HELPER" --path "$usb_node" "$@"; then
    fail "usb_ioctl_helper execution failed"
  fi
  log "usb_ioctl_helper completed with exit status 0"
}

run_openssl() {
  if [ "${OPENSSL_PKCS11_USE_SUDO:-0}" = "1" ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo -n env OPENSSL_MODULES="$OPENSSL_PKCS11_MODULES" PKCS11_MODULE_PATH="${OPENSSL_PKCS11_MODULE_PATH-}" \
        "$USAGE_OPENSSL" "$@"
      return $?
    fi
  fi
  env OPENSSL_MODULES="$OPENSSL_PKCS11_MODULES" PKCS11_MODULE_PATH="${OPENSSL_PKCS11_MODULE_PATH-}" \
    "$USAGE_OPENSSL" "$@"
}

resolve_pkcs11_provider_backend() {
  if [ -n "${OPENSSL_PKCS11_PROVIDER_DIR:-}" ] && [ -d "$OPENSSL_PKCS11_PROVIDER_DIR" ]; then
    module_dir="$OPENSSL_PKCS11_PROVIDER_DIR"
  fi

  if [ -z "${module_dir:-}" ]; then
    module_dir=""
  fi

  if [ -z "$module_dir" ]; then
    for dir in \
      /usr/lib/x86_64-linux-gnu/ossl-modules \
      /usr/lib/aarch64-linux-gnu/ossl-modules \
      /usr/lib/ossl-modules \
      /usr/lib/ssl/ossl-modules \
      /usr/lib/aarch64-linux-gnu/engines-3 \
      /usr/lib/x86_64-linux-gnu/engines-3 \
      /usr/local/lib/ossl-modules; do
      if [ -d "$dir" ]; then
        module_dir="$dir"
        break
      fi
    done
  fi

  if [ -z "$module_dir" ]; then
    fail "could not locate OpenSSL providers directory; set OPENSSL_PKCS11_PROVIDER_DIR"
  fi

  if [ -f "$module_dir/pkcs11.so" ]; then
    OPENSSL_PKCS11_MODULES="$module_dir"
    return 0
  fi

  if [ -f "$module_dir/libpkcs11.so" ]; then
    OPENSSL_PKCS11_MODULES="$TMPDIR/ossl-modules"
    mkdir -p "$OPENSSL_PKCS11_MODULES"
    ln -sf "$module_dir/libpkcs11.so" "$OPENSSL_PKCS11_MODULES/pkcs11.so"
    if [ -f "$module_dir/legacy.so" ]; then
      ln -sf "$module_dir/legacy.so" "$OPENSSL_PKCS11_MODULES/legacy.so"
    fi
    return 0
  fi

  fail "OpenSSL pkcs11 provider module not found under $module_dir (expected pkcs11.so or libpkcs11.so)"
}

probe_open_ssl_pkcs11_uri() {
  cmd_op="$1"
  shift
  if run_openssl "$cmd_op" -provider-path "$OPENSSL_PKCS11_MODULES" -provider default -provider pkcs11 "$@"; then
    return 0
  fi

  if [ "${OPENSSL_PKCS11_USE_SUDO:-0}" = "1" ]; then
    return 1
  fi

  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    OPENSSL_PKCS11_USE_SUDO=1
    export OPENSSL_PKCS11_USE_SUDO
    if run_openssl "$cmd_op" -provider-path "$OPENSSL_PKCS11_MODULES" -provider default -provider pkcs11 "$@"; then
      return 0
    fi
  fi

  return 1
}

resolve_client_auth() {
  if [ "${USB_RAW_APDU:-0}" = "1" ]; then
    resolve_usb_apdu_mode
  fi

  CLIENT_AUTH_MODE="pkcs11"
  CLIENT_CERT_FILE="${OPENSSL_CLIENT_CERT:-}"
  CLIENT_KEY_FILE="${OPENSSL_CLIENT_KEY:-}"
  PKCS11_CERT_URI="${PKCS11_CERT_URI:-}"
  PKCS11_KEY_URI="${PKCS11_KEY_URI:-}"

  if [ -n "$CLIENT_CERT_FILE" ] || [ -n "$CLIENT_KEY_FILE" ]; then
    if [ -z "$CLIENT_CERT_FILE" ] || [ -z "$CLIENT_KEY_FILE" ]; then
      fail "set both OPENSSL_CLIENT_CERT and OPENSSL_CLIENT_KEY for PEM mode"
    fi
    if [ ! -r "$CLIENT_CERT_FILE" ] || [ ! -r "$CLIENT_KEY_FILE" ]; then
      fail "OPENSSL_CLIENT_CERT/OPENSSL_CLIENT_KEY are not readable"
    fi
    CLIENT_AUTH_MODE="pem"
    export CLIENT_AUTH_MODE CLIENT_CERT_FILE CLIENT_KEY_FILE
    log "using explicit PEM client cert/key files"
    return 0
  fi

  if [ "${ALLOW_PKCS11:-0}" != "1" ]; then
    fail "set OPENSSL_CLIENT_CERT and OPENSSL_CLIENT_KEY (or export ALLOW_PKCS11=1 to opt in to openssl PKCS#11 provider mode)"
  fi

  if [ -z "$PKCS11_CERT_URI" ] || [ -z "$PKCS11_KEY_URI" ]; then
    if ! resolve_pkcs11_uris_auto; then
      fail "ALLOW_PKCS11=1 requires PKCS11_CERT_URI and PKCS11_KEY_URI (or pkcs11-tool discovery). Set both URIs explicitly."
    fi
  fi

  case "$PKCS11_CERT_URI" in
    *"pin-value="*) ;;
    *) PKCS11_CERT_URI="${PKCS11_CERT_URI};pin-value=${PIN1}" ;;
  esac
  case "$PKCS11_KEY_URI" in
    *"pin-value="*) ;;
    *) PKCS11_KEY_URI="${PKCS11_KEY_URI};pin-value=${PIN1}" ;;
  esac

  export PKCS11_CERT_URI PKCS11_KEY_URI
  if [ -n "${PKCS11_DISCOVERED_MODULE_PATH:-}" ]; then
    export PKCS11_MODULE_PATH="$PKCS11_DISCOVERED_MODULE_PATH"
    export PKCS11_PIN="$PIN1"
  else
    if [ -n "${PKCS11_MODULE_PATH:-}" ]; then
      export PKCS11_PIN="$PIN1"
    fi
  fi
  if [ -z "${PKCS11_MODULE_PATH:-}" ]; then
    PKCS11_MODULE_PATH="$(resolve_pkcs11_tool_module || true)"
    if [ -n "$PKCS11_MODULE_PATH" ]; then
      export PKCS11_MODULE_PATH
      export PKCS11_PIN="$PIN1"
    fi
  fi
  if [ -n "${PKCS11_MODULE_PATH:-}" ]; then
    OPENSSL_PKCS11_MODULE_PATH="$PKCS11_MODULE_PATH"
    export OPENSSL_PKCS11_MODULE_PATH
  else
    fail "PKCS11 module path unavailable. set PKCS11_MODULE_PATH to opensc-pkcs11 library path."
  fi

  if [ -z "${OPENSSL_PKCS11_USE_SUDO:-}" ]; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      OPENSSL_PKCS11_USE_SUDO=1
      export OPENSSL_PKCS11_USE_SUDO
    fi
  fi

  export CLIENT_AUTH_MODE
  resolve_pkcs11_provider_backend
  if [ "${PKCS11_PROBED:-0}" != "1" ]; then
    PKCS11_PROBED=1
    log "resolved PKCS#11 cert URI: ${PKCS11_CERT_URI}"
    log "resolved PKCS11 key URI:  ${PKCS11_KEY_URI}"
    log "using OpenSSL PKCS#11 provider dir: ${OPENSSL_PKCS11_MODULES}"
    if ! probe_open_ssl_pkcs11_uri x509 -in "$PKCS11_CERT_URI" -noout; then
      fail "OpenSSL cannot load PKCS#11 cert URI on this host:
  ${USAGE_OPENSSL} x509 -provider default -provider pkcs11 -provider-path ${OPENSSL_PKCS11_MODULES} -in \"$PKCS11_CERT_URI\" -noout
Set OPENSSL_CLIENT_CERT and OPENSSL_CLIENT_KEY for PEM mode, or verify p11-kit/pkcs11 provider setup."
    fi
    if ! probe_open_ssl_pkcs11_uri pkey -in "$PKCS11_KEY_URI" -pubout; then
      fail "OpenSSL cannot load PKCS#11 private-key URI on this host:
  ${USAGE_OPENSSL} pkey -provider default -provider pkcs11 -provider-path ${OPENSSL_PKCS11_MODULES} -in \"$PKCS11_KEY_URI\" -pubout
Set OPENSSL_CLIENT_CERT and OPENSSL_CLIENT_KEY for PEM mode, or verify p11-kit/pkcs11 provider setup."
    fi
  fi
  return 0
}

resolve_pkcs11_tool_module() {
  for module in \
    /usr/lib/aarch64-linux-gnu/pkcs11/opensc-pkcs11.so \
    /usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so \
    /usr/lib/pkcs11/opensc-pkcs11.so \
    /usr/lib/opensc-pkcs11.so \
    /usr/lib/ssl/engines/engine_pkcs11.so \
    /usr/lib/aarch64-linux-gnu/engines-3/pkcs11.so; do
    [ -e "$module" ] && printf '%s\n' "$module" && return 0
  done
  return 1
}

resolve_pkcs11_uris_auto() {
  cert_uri="${PKCS11_CERT_URI:-}"
  key_uri="${PKCS11_KEY_URI:-}"
  if [ -n "$cert_uri" ] && [ -n "$key_uri" ]; then
    return 0
  fi
  if ! command -v pkcs11-tool >/dev/null 2>&1; then
    return 1
  fi

  pkcs11_module="$(resolve_pkcs11_tool_module || true)"
  if [ -z "$pkcs11_module" ]; then
    return 1
  fi

  cmd="pkcs11-tool --module $pkcs11_module --list-objects"
  if [ -n "$PIN1" ]; then
    cmd="$cmd --login --pin $PIN1"
  fi

  if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      cmd="sudo -n ${cmd}"
    else
      return 1
    fi
  fi

  if ! objs="$(sh -c "$cmd" 2>/dev/null)"; then
    return 1
  fi

  if [ -z "$cert_uri" ]; then
    cert_uri="$(printf '%s\n' "$objs" | awk '/^Certificate Object; type = X.509 cert/{flag=1;next} flag&&/^  uri:/{print $2; exit}')"
  fi
  if [ -z "$key_uri" ]; then
    key_uri="$(printf '%s\n' "$objs" | awk '/^Private Key Object;/{flag=1;next} flag&&/^  uri:/{print $2; exit}')"
  fi

  if [ -n "$cert_uri" ] && [ -n "$key_uri" ]; then
    PKCS11_CERT_URI="$cert_uri"
    PKCS11_KEY_URI="$key_uri"
    PKCS11_DISCOVERED_MODULE_PATH="$pkcs11_module"
    export PKCS11_CERT_URI PKCS11_KEY_URI PKCS11_DISCOVERED_MODULE_PATH
    log "auto-discovered PKCS#11 cert URI: $PKCS11_CERT_URI"
    log "auto-discovered PKCS#11 key URI:  $PKCS11_KEY_URI"
    return 0
  fi
  return 1
}

query_param() {
  url=$1
  key=$2
  query="${url#*\?}"
  if [ "$query" = "$url" ]; then
    echo ""
    return
  fi
  query="${query%%#*}"
  echo "$query" | awk -v key="$key" -F '&' '
    {
      for (i = 1; i <= NF; i++) {
        pair = $i
        eq = index(pair, "=")
        if (eq == 0) {
          k = pair
          v = ""
        } else {
          k = substr(pair, 1, eq - 1)
          v = substr(pair, eq + 1)
        }
        if (k == key) {
          print v
          exit
        }
      }
    }'
}

urlencode() {
  text=$1
  encoded=""
  i=1
  len="$(printf '%s' "$text" | wc -c | tr -dc '0-9')"
  while [ "$i" -le "$len" ]; do
    ch="$(printf '%s' "$text" | cut -c "$i")"
    case "$ch" in
      [A-Za-z0-9._~\-])
        encoded="${encoded}${ch}"
        ;;
      " ")
        encoded="${encoded}+"
        ;;
      *)
        hex="$(printf '%s' "$ch" | od -An -tx1 | tr -d ' \n' | tr '[:lower:]' '[:upper:]')"
        [ -n "$hex" ] && encoded="${encoded}%${hex}"
        ;;
    esac
    i=$((i + 1))
  done
  printf '%s' "$encoded"
}

urlencode_pairs() {
  out=$1
  shift
  : > "$out"
  if [ "$#" -ne 0 ] && [ "$(( $# % 2 ))" -ne 0 ]; then
    fail "urlencode_pairs expected even number of arguments"
  fi
  sep=""
  while [ "$#" -gt 0 ]; do
    key="$1"
    value="$2"
    shift 2
    enc_key="$(urlencode "$key")"
    enc_value="$(urlencode "$value")"
    [ -z "$enc_key" ] && continue
    printf '%s%s=%s' "$sep" "$enc_key" "$enc_value" >> "$out"
    sep="&"
  done
}

extract_form() {
  html_file=$1
  mode=$2
  base_url=$3
  out_file=$4

  form_tmp="$TMPDIR/forms.$$"
  selected=0
  in_form=0
  form_action=
  form_fields=
  has_saml=0
  has_delegate=0
  selected_action=
  selected_fields=

  tr '\n' ' ' < "$html_file" | \
    sed 's#<\([Ff][Oo][Rr][Mm]\)#\n<form#g; s#</[Ff][Oo][Rr][Mm]>#\n</form>\n#g; s#<[Ii][Nn][Pp][Uu][Tt]#\n<input#g; s#<[Bb][Uu][Tt][Tt][Oo][Nn]#\n<button#g; s#>[[:space:]]*#>\n#g' > "$form_tmp"

  while IFS= read -r tag; do
    [ -n "$tag" ] || continue
    case "$tag" in
      "<form"*)
        in_form=1
        form_action=
        form_fields=
        has_saml=0
        has_delegate=0
        form_action="$(printf '%s\n' "$tag" | sed -n 's/.*[Aa][Cc][Tt][Ii][Oo][Nn]=\"\([^"]*\)\".*/\1/p')"
        if [ -z "$form_action" ]; then
          form_action="$(printf '%s\n' "$tag" | sed -n "s/.*[Aa][Cc][Tt][Ii][Oo][Nn]='\([^']*\)'.*/\1/p")"
        fi
        if [ -z "$form_action" ]; then
          form_action="$(printf '%s\n' "$tag" | sed -n 's/.*[Aa][Cc][Tt][Ii][Oo][Nn]=\([^ >]*\).*/\1/p')"
        fi
        ;;
      "</form>")
        if [ "$in_form" -eq 1 ] && [ -n "$form_action" ]; then
          select_form=0
          case "$mode" in
            saml)
              [ "$has_saml" -eq 1 ] && select_form=1
              ;;
            role)
              if [ "$has_delegate" -eq 1 ]; then
                select_form=1
              elif printf '%s\n' "$form_action" | grep -q 'select-role'; then
                select_form=1
              fi
            ;;
          *)
            select_form=1
            ;;
          esac

          if [ "$select_form" -eq 1 ] && [ "$selected" -eq 0 ]; then
            selected=1
            if [ "$form_action" != "#" ]; then
              selected_action="$form_action"
              selected_fields="$form_fields"
            fi
          fi
        fi
        in_form=0
        ;;
      "<input"*)
        [ "$in_form" -eq 1 ] || continue
        tag_name="$(printf '%s\n' "$tag" | sed -n 's/.*[Nn][Aa][Mm][Ee]=\"\([^"]*\)\".*/\1/p')"
        if [ -z "$tag_name" ]; then
          tag_name="$(printf '%s\n' "$tag" | sed -n "s/.*[Nn][Aa][Mm][Ee]='\([^']*\)'.*/\1/p")"
        fi
        [ -n "$tag_name" ] || continue
        tag_value="$(printf '%s\n' "$tag" | sed -n 's/.*[Vv][Aa][Ll][Uu][Ee]=\"\([^"]*\)\".*/\1/p')"
        if [ -z "$tag_value" ]; then
          tag_value="$(printf '%s\n' "$tag" | sed -n "s/.*[Vv][Aa][Ll][Uu][Ee]='\([^']*\)'.*/\1/p")"
        fi
        form_fields="${form_fields}${form_fields:+&}$(urlencode "$tag_name")=$(urlencode "$tag_value")"
        [ "$tag_name" = "SAMLResponse" ] && has_saml=1
        [ "$tag_name" = "delegateId" ] && has_delegate=1
        ;;
      "<button"*)
        [ "$in_form" -eq 1 ] || continue
        tag_type="$(printf '%s\n' "$tag" | sed -n 's/.*[Tt][Yy][Pp][Ee]=\"\([^\"]*\)\".*/\1/p')"
        if [ -z "$tag_type" ]; then
          tag_type="$(printf '%s\n' "$tag" | sed -n "s/.*[Tt][Yy][Pp][Ee]='\([^']*\)'.*/\1/p")"
        fi
        [ -z "$tag_type" ] && tag_type=submit
        case "$tag_type" in
          submit|Submit|SUBMIT)
            tag_name="$(printf '%s\n' "$tag" | sed -n 's/.*[Nn][Aa][Mm][Ee]=\"\([^"]*\)\".*/\1/p')"
            if [ -z "$tag_name" ]; then
              tag_name="$(printf '%s\n' "$tag" | sed -n "s/.*[Nn][Aa][Mm][Ee]='\([^']*\)'.*/\1/p")"
            fi
            [ -z "$tag_name" ] && continue
            tag_value="$(printf '%s\n' "$tag" | sed -n 's/.*[Vv][Aa][Ll][Uu][Ee]=\"\([^"]*\)\".*/\1/p')"
            if [ -z "$tag_value" ]; then
              tag_value="$(printf '%s\n' "$tag" | sed -n "s/.*[Vv][Aa][Ll][Uu][Ee]='\([^']*\)'.*/\1/p")"
            fi
            form_fields="${form_fields}${form_fields:+&}$(urlencode "$tag_name")=$(urlencode "$tag_value")"
            ;;
          *) ;;
        esac
        ;;
      *)
        ;;
    esac
  done < "$form_tmp"
  rm -f "$form_tmp"

  if [ "$selected" -eq 1 ] && [ -n "$selected_action" ]; then
    case "$selected_action" in
      http://*|https://*)
        ;;
      *)
        selected_action="$(url_join_simple "$base_url" "$selected_action")"
        ;;
    esac
    printf '%s\n%s\n' "$selected_action" "$selected_fields" > "$out_file"
    return 0
  fi

  return 1
}

extract_first_form_action() {
  html_file=$1
  base_url=$2
  first_form="$(
    tr '\n' ' ' < "$html_file" | awk '
      BEGIN { IGNORECASE = 1 }
      {
        if (match($0, /<form[^>]*>/)) {
          print substr($0, RSTART, RLENGTH)
          exit
        }
      }
    '
  )"
  if [ -z "$first_form" ]; then
    return 1
  fi

  action="$(printf '%s\n' "$first_form" | sed -n 's/.*[Aa][Cc][Tt][Ii][Oo][Nn]=\"\([^"]*\)\".*/\1/p')"
  if [ -z "$action" ]; then
    action="$(printf '%s\n' "$first_form" | sed -n "s/.*[Aa][Cc][Tt][Ii][Oo][Nn]='\([^']*\)'.*/\1/p")"
  fi
  if [ -z "$action" ]; then
    action="$(printf '%s\n' "$first_form" | sed -n 's/.*[Aa][Cc][Tt][Ii][Oo][Nn]=\([^ >]*\).*/\1/p')"
  fi
  if [ -z "$action" ]; then
    return 1
  fi

  case "$action" in
    http://*|https://*)
      printf '%s\n' "$action"
      ;;
    *)
      printf '%s\n' "$(url_join_simple "$base_url" "$action")"
      ;;
  esac
  return 0
}

http_request() {
  method=$1
  url=$2
  data_file=$3
  body_out=$4
  meta_out=$5
  shift 5
  HTTP_REQUEST_STRICT="${HTTP_REQUEST_STRICT:-1}"
  HTTP_STATUS=""
  HTTP_FINAL_URL=""

  FOLLOW_REDIRECTS=0
  MAX_REDIRS=0
  EXTRA_HEADERS="$TMPDIR/step_request.extra_headers"
  : > "$EXTRA_HEADERS"

  while [ "$#" -gt 0 ]; do
    case "$1" in
      -L|--location)
        FOLLOW_REDIRECTS=1
        if [ "$MAX_REDIRS" -eq 0 ]; then
          MAX_REDIRS=20
        fi
        shift
        ;;
      --max-redirs)
        if [ "$#" -lt 2 ]; then
      log "warning: http_request missing --max-redirs value"
          break
        fi
        MAX_REDIRS="$2"
        shift 2
        ;;
      -H|--header)
        if [ "$#" -lt 2 ]; then
      log "warning: http_request missing -H/--header value"
          break
        fi
        printf '%s\n' "$2" >> "$EXTRA_HEADERS"
        shift 2
        ;;
      -A|--user-agent)
        if [ "$#" -lt 2 ]; then
      log "warning: http_request missing --user-agent value"
          break
        fi
        printf 'User-Agent: %s\n' "$2" >> "$EXTRA_HEADERS"
        shift 2
        ;;
      -b|-c|--cookie|--cookie-jar)
        if [ "$#" -lt 2 ]; then
      log "warning: http_request missing cookie argument"
          break
        fi
        shift 2
        ;;
      *)
        log "warning: http_request received unsupported passthrough arg: $1"
        shift
        ;;
    esac
  done

  request_url="$url"
  attempt=0
  while :; do
    attempt=$(expr "$attempt" + 1)
    if [ "$FOLLOW_REDIRECTS" -eq 1 ] && [ "$MAX_REDIRS" -gt 0 ] && [ "$attempt" -gt "$MAX_REDIRS" ]; then
      [ "$HTTP_REQUEST_STRICT" -eq 1 ] && fail "redirect limit reached for $url"
      return 1
    fi

    req_host="$(url_host_simple "$request_url")"
    req_path="$(url_path_simple "$request_url")"
    case "$request_url" in
      https://*) req_url_scheme="https://" ;;
      http://*) req_url_scheme="http://" ;;
      *) req_url_scheme="" ;;
    esac
    if [ -z "$req_host" ] || [ -z "$req_path" ] || [ "$req_url_scheme" != "https://" ]; then
      if [ "$HTTP_REQUEST_STRICT" -eq 1 ]; then
        fail "invalid request URL: $request_url"
      fi
      return 1
    fi

    req_file="$TMPDIR/step_request.req"
    raw_file="$TMPDIR/step_request.raw"
    hdr_file="$TMPDIR/step_request.hdr"
    openssl_err_file="$TMPDIR/step_request.err"
    rm -f "$req_file" "$raw_file" "$hdr_file" "$openssl_err_file"

    if [ "$method" = "POST" ]; then
      if [ -z "$data_file" ] || [ ! -f "$data_file" ]; then
        if [ "$HTTP_REQUEST_STRICT" -eq 1 ]; then
          fail "missing POST body file: $data_file"
        fi
        return 1
      fi
      content_len="$(wc -c < "$data_file" | tr -dc '0-9')"
    else
      content_len=0
    fi

    req_cookies="$(cookie_header)"
    {
      printf '%s %s HTTP/1.1\r\n' "$method" "$req_path"
      printf 'Host: %s\r\n' "$req_host"
      printf 'User-Agent: %s\r\n' "$UA"
      printf 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n'
      if [ -n "$req_cookies" ]; then
        printf 'Cookie: %s\r\n' "$req_cookies"
      fi
      if [ -s "$EXTRA_HEADERS" ]; then
        sed 's/$/\r/' "$EXTRA_HEADERS"
      fi
      if [ "$method" = "POST" ]; then
        printf 'Content-Type: application/x-www-form-urlencoded\r\n'
        printf 'Content-Length: %s\r\n' "$content_len"
      fi
      printf 'Connection: close\r\n'
      printf '\r\n'
      if [ "$method" = "POST" ]; then
        cat "$data_file"
      fi
    } > "$req_file"

    openssl_cmd_failed=0
    if ! "$USAGE_OPENSSL" s_client -quiet -tls1_2 -ign_eof \
      -connect "${req_host}:443" \
      -servername "$req_host" \
      < "$req_file" > "$raw_file" 2>"$openssl_err_file"; then
      openssl_cmd_failed=1
    fi
    if [ "$openssl_cmd_failed" -ne 0 ] && [ -s "$openssl_err_file" ]; then
      log "openssl request returned non-zero status: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
    fi

    : > "$body_out"
    awk -v hdr="$hdr_file" -v body="$body_out" '
      BEGIN { found_http = 0; in_headers = 0; in_body = 0 }
      {
        sub(/\r$/, "", $0)
        if (!found_http) {
          if ($0 ~ /^HTTP\/[0-9]\.[0-9] [0-9]{3} /) {
            found_http = 1
            in_headers = 1
            print $0 > hdr
          }
          next
        }
        if (in_headers) {
          if ($0 == "") {
            in_headers = 0
            in_body = 1
            next
          }
          print $0 > hdr
          next
        }
        if (in_body) {
          print $0 > body
        }
      }
    ' "$raw_file"

    if [ ! -s "$hdr_file" ]; then
      if [ -s "$openssl_err_file" ]; then
        log "openssl response parse failed: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
      fi
      if [ "$HTTP_REQUEST_STRICT" -eq 1 ]; then
        fail "openssl response parse failed: $request_url"
      fi
      return 1
    fi

    append_set_cookie_headers "$hdr_file" "$req_host"

    HTTP_STATUS="$(awk 'NR==1 {print $2}' "$hdr_file" | tr -dc '0-9')"
    HTTP_FINAL_URL="$request_url"
    if [ -n "$HTTP_STATUS" ] && [ "$HTTP_STATUS" -ge 300 ] && [ "$HTTP_STATUS" -lt 400 ]; then
      LOCATION="$(grep -i '^Location:' "$hdr_file" | sed 's/^[Ll]ocation:[[:space:]]*//' | head -n 1 | tr -d '\r')"
      if [ -n "$LOCATION" ]; then
        if [ "${LOCATION#http://}" != "$LOCATION" ] || [ "${LOCATION#https://}" != "$LOCATION" ]; then
          HTTP_FINAL_URL="$LOCATION"
        elif [ "${LOCATION#/}" != "$LOCATION" ]; then
          HTTP_FINAL_URL="${req_url_scheme}${req_host}${LOCATION}"
        else
          HTTP_FINAL_URL="$(url_join_simple "$request_url" "$LOCATION")"
        fi
      fi
    fi

    {
      echo "$HTTP_STATUS"
      echo "$HTTP_FINAL_URL"
    } > "$meta_out"

    if [ -z "$HTTP_FINAL_URL" ]; then
      HTTP_FINAL_URL="$request_url"
    fi
    if [ -z "$HTTP_STATUS" ] && [ -f "$meta_out" ]; then
      HTTP_STATUS="$(sed -n '1p' "$meta_out" | tr -d '\r')"
    fi
    if [ "$HTTP_STATUS" -lt 200 ] || [ "$HTTP_STATUS" -ge 400 ]; then
      if [ -s "$openssl_err_file" ]; then
        log "openssl response failed: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
      fi
      [ "$HTTP_REQUEST_STRICT" -eq 1 ] && fail "HTTP ${HTTP_STATUS} from $request_url"
      return 1
    fi

    if [ "$FOLLOW_REDIRECTS" -eq 1 ] && [ "$HTTP_STATUS" -ge 300 ] && [ "$HTTP_STATUS" -lt 400 ] && [ -n "$HTTP_FINAL_URL" ]; then
      request_url="$HTTP_FINAL_URL"
      continue
    fi

    return 0
  done
}

openssl_client_auth_request() {
  method=$1
  url=$2
  data_file=$3
  body_out=$4
  meta_out=$5

  if ! command -v "$USAGE_OPENSSL" >/dev/null 2>&1; then
    return 1
  fi

  req_host="$(url_host_simple "$url")"
  req_path="$(url_path_simple "$url")"
  case "$url" in
    https://*) req_url_scheme="https://" ;;
    http://*) req_url_scheme="http://" ;;
    *) req_url_scheme="" ;;
  esac
  if [ -z "$req_host" ] || [ -z "$req_path" ]; then
    return 1
  fi

  HTTP_STATUS=""
  HTTP_FINAL_URL=""

  req_file="$TMPDIR/step4_openssl.req"
  raw_file="$TMPDIR/step4_openssl.raw"
  hdr_file="$TMPDIR/step4_openssl.hdr"
  rm -f "$req_file" "$raw_file" "$hdr_file"

  if [ "$method" = "POST" ]; then
    content_len="$(wc -c < "$data_file" | tr -dc '0-9')"
  else
    content_len=0
  fi

  req_cookies="$(cookie_header)"
  {
    printf '%s %s HTTP/1.1\r\n' "$method" "$req_path"
    printf 'Host: %s\r\n' "$req_host"
    printf 'User-Agent: %s\r\n' "$UA"
    printf 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n'
    if [ -n "$req_cookies" ]; then
      printf 'Cookie: %s\r\n' "$req_cookies"
    fi
    if [ "$method" = "POST" ]; then
      printf 'Content-Type: application/x-www-form-urlencoded\r\n'
      printf 'Content-Length: %s\r\n' "$content_len"
    fi
    printf 'Connection: close\r\n'
    printf '\r\n'
    if [ "$method" = "POST" ]; then
      cat "$data_file"
    fi
  } > "$req_file"

  openssl_err_file="$TMPDIR/step4_openssl.err"
  rm -f "$openssl_err_file"

  openssl_client_auth_failed=0
  client_mode="${CLIENT_AUTH_MODE:-pkcs11}"
  if [ "$client_mode" = "pem" ]; then
    if [ -z "${CLIENT_CERT_FILE:-}" ] || [ -z "${CLIENT_KEY_FILE:-}" ]; then
      log "missing OPENSSL_CLIENT_CERT/OPENSSL_CLIENT_KEY in PEM mode"
      return 1
    fi
    if ! "$USAGE_OPENSSL" s_client -quiet -tls1_2 -verify 1 \
      -connect "${req_host}:443" \
      -servername "$req_host" \
      -cert "$CLIENT_CERT_FILE" \
      -key "$CLIENT_KEY_FILE" \
      < "$req_file" > "$raw_file" 2>"$openssl_err_file"; then
      openssl_client_auth_failed=1
    fi
 else
    log "using openssl PKCS#11 provider for mTLS"
    if ! run_openssl s_client -quiet -tls1_2 -verify 1 \
      -provider default -provider pkcs11 -provider-path "$OPENSSL_PKCS11_MODULES" \
      -connect "${req_host}:443" \
      -servername "$req_host" \
      -cert "$PKCS11_CERT_URI" \
      -key "$PKCS11_KEY_URI" \
      < "$req_file" > "$raw_file" 2>"$openssl_err_file"; then
      openssl_client_auth_failed=1
    fi
  fi

  if [ "$openssl_client_auth_failed" -ne 0 ] && [ -s "$openssl_err_file" ]; then
    log "openssl request returned non-zero status: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
  fi

  # Continue if response headers parsed successfully, since s_client often exits
  # non-zero even on successful HTTP responses.
  if [ ! -s "$raw_file" ]; then
    if [ -s "$openssl_err_file" ]; then
      log "openssl output missing: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
    fi
    return 1
  fi

  : > "$body_out"
  awk -v hdr="$hdr_file" -v body="$body_out" '
    BEGIN { in_headers = 0; saw_status = 0; body_started = 0 }
    {
      sub(/\r$/, "", $0)
      if (!saw_status) {
        if ($0 ~ /^HTTP\/[0-9]\.[0-9] [0-9]{3} /) {
          saw_status = 1
          in_headers = 1
          print $0 > hdr
        }
        next
      }
      if (in_headers) {
        if ($0 == "") {
          in_headers = 0
          body_started = 1
          next
        }
        print $0 > hdr
        next
      }
      if (body_started) {
        print $0 > body
      }
    }
  ' "$raw_file"

  if [ ! -s "$hdr_file" ]; then
    if [ -s "$openssl_err_file" ]; then
      log "openssl response parse failed: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
    else
      log "openssl response parse failed: no headers parsed"
      if [ -f "$raw_file" ]; then
        log "openssl raw head: $(sed -n '1,3p' "$raw_file" | tr '\n' ' ')"
      fi
    fi
    return 1
  fi

  append_set_cookie_headers "$hdr_file" "$req_host"

  HTTP_STATUS="$(awk 'NR==1 { print $2 }' "$hdr_file" | tr -dc '0-9')"
  HTTP_FINAL_URL="$url"
  if [ -n "$HTTP_STATUS" ] && [ "$HTTP_STATUS" -ge 300 ] && [ "$HTTP_STATUS" -lt 400 ]; then
    LOCATION="$(grep -i '^Location:' "$hdr_file" | sed 's/^[Ll]ocation:[[:space:]]*//' | head -n 1 | tr -d '\r')"
    if [ -n "$LOCATION" ]; then
      if [ "${LOCATION#http://}" != "$LOCATION" ] || [ "${LOCATION#https://}" != "$LOCATION" ]; then
        HTTP_FINAL_URL="$LOCATION"
      elif [ "${LOCATION#/}" != "$LOCATION" ]; then
        HTTP_FINAL_URL="${req_url_scheme}${req_host}${LOCATION}"
      else
        HTTP_FINAL_URL="$(url_join_simple "$url" "$LOCATION")"
      fi
    fi
  fi

  {
    echo "$HTTP_STATUS"
    echo "$HTTP_FINAL_URL"
  } > "$meta_out"

  if [ -z "$HTTP_STATUS" ] || [ "$HTTP_STATUS" -lt 200 ] || [ "$HTTP_STATUS" -ge 400 ]; then
    if [ -s "$openssl_err_file" ]; then
      log "openssl mTLS request error: $(sed -n '1,2p' "$openssl_err_file" | tr '\n' ' ')"
    fi
    return 1
  fi

  return 0
}

show_reader_state() {
  log "reader/card visibility"
  if command -v lsusb >/dev/null 2>&1; then
    lsusb | sed 's/^/[lsusb] /'
  else
    log "lsusb not installed"
  fi

  if [ -d /dev/bus/usb ] && command -v ls >/dev/null 2>&1; then
    ls /dev/bus/usb/*/* 2>/dev/null | sed 's/^/[dev-usb] /'
  fi
  if [ -e /dev/hidraw0 ] || [ -e /dev/hidraw1 ] || [ -e /dev/hidraw2 ] || [ -e /dev/hidraw3 ] || [ -e /dev/hidraw4 ]; then
    ls /dev/hidraw* 2>/dev/null | sed 's/^/[hidraw] /'
  fi
  if [ -e /dev/sg0 ] || [ -e /dev/sg1 ] || [ -e /dev/sg2 ]; then
    ls /dev/sg* 2>/dev/null | sed 's/^/[sgdev] /'
  fi

  if command -v pcsc_scan >/dev/null 2>&1; then
    if command -v timeout >/dev/null 2>&1; then
      timeout 5 pcsc_scan 2>/dev/null | sed 's/^/[pcsc_scan] /' | sed -n '1,40p' || true
    else
      pcsc_scan 2>/dev/null | sed 's/^/[pcsc_scan] /' | sed -n '1,40p' || true
    fi
  else
    log "pcsc_scan not installed"
  fi
}

resolve_pkcs11_uris() {
  resolve_client_auth
  export CLIENT_AUTH_MODE CLIENT_CERT_FILE CLIENT_KEY_FILE PKCS11_CERT_URI PKCS11_KEY_URI OPENSSL_PKCS11_ENGINE
}

show_reader_state
resolve_pkcs11_uris

if [ "${CLIENT_AUTH_MODE}" = "pem" ]; then
  log "using PEM client cert: ${CLIENT_CERT_FILE}"
  log "using PEM client key:  ${CLIENT_KEY_FILE}"
else
  log "using PKCS#11 cert URI: ${PKCS11_CERT_URI}"
  log "using PKCS#11 key URI:  ${PKCS11_KEY_URI}"
fi

# Step 1: seed cookie jar and csrf-token
step1_body="$TMPDIR/step1.html"
step1_meta="$TMPDIR/step1.meta"
log "step 1: GET https://www.suomi.fi/etusivu"
http_request GET "https://www.suomi.fi/etusivu" "" "$step1_body" "$step1_meta"

CSRF_TOKEN="$(read_cookie csrf-token)"
if [ -z "$CSRF_TOKEN" ]; then
  CSRF_TOKEN="$(read_cookie _csrf)"
fi

if [ -z "$CSRF_TOKEN" ]; then
  fail "csrf-token missing after step 1 (site maintenance or TLS trust issue)"
fi
log "csrf token found"

# Step 2: api/auth -> tunnistautuminen discovery
step2_form="$TMPDIR/step2.form"
step2_body="$TMPDIR/step2.html"
step2_meta="$TMPDIR/step2.meta"
urlencode_pairs "$step2_form" _csrf "$CSRF_TOKEN" redirectUrl /frontpage lang "$LANGUAGE"
log "step 2: POST https://www.suomi.fi/api/auth"
http_request POST "https://www.suomi.fi/api/auth" "$step2_form" "$step2_body" "$step2_meta"
STEP2_URL="$HTTP_FINAL_URL"

STEP2_HOST="$(url_host "$STEP2_URL")"

TID="$(query_param "$STEP2_URL" tid)"
PID="$(query_param "$STEP2_URL" pid)"
TAG="$(query_param "$STEP2_URL" tag)"
CONV="$(query_param "$STEP2_URL" conversation)"
IS_SAML_REDIRECT=0

if [ -n "$STEP2_URL" ] && printf '%s\n' "$STEP2_URL" | grep -Eq 'SAMLRequest='; then
  IS_SAML_REDIRECT=1
fi

if [ -z "$TID" ] || [ -z "$PID" ] || [ -z "$TAG" ] || [ -z "$CONV" ]; then
  DISCOVER_URL=""
  if form_action=$(extract_first_form_action "$step2_body" "$STEP2_URL"); then
    if [ -n "$form_action" ]; then
      DISCOVER_URL="$form_action"
    fi
  fi

  if [ -n "$DISCOVER_URL" ]; then
    STEP2_URL="$DISCOVER_URL"
    TID="$(query_param "$STEP2_URL" tid)"
    PID="$(query_param "$STEP2_URL" pid)"
    TAG="$(query_param "$STEP2_URL" tag)"
    CONV="$(query_param "$STEP2_URL" conversation)"
    STEP2_HOST="$(url_host "$STEP2_URL")"
  fi

  if [ -z "$TID" ] || [ -z "$PID" ] || [ -z "$TAG" ] || [ -z "$CONV" ]; then
    if [ "$IS_SAML_REDIRECT" -eq 1 ] || [ "$STEP2_HOST" = "tunnistautuminen.suomi.fi" ]; then
      log "step 2 has no legacy query params; proceeding with SAML redirect flow"
      :
    else
      fail "conversation params missing from step-2 URL (host=${STEP2_HOST:-unknown} url=${STEP2_URL:-unknown})"
    fi
  fi
fi

if [ "$STEP2_HOST" != "tunnistautuminen.suomi.fi" ]; then
  log "step 2 host is $STEP2_HOST; continuing with discovered auth URL"
fi

if [ -z "$STEP2_HOST" ]; then
  fail "step 2 returned empty host"
fi

if [ -n "$TID" ] && [ -n "$PID" ] && [ -n "$TAG" ] && [ -n "$CONV" ]; then
  STEP3_URL="https://tunnistautuminen.suomi.fi/idp/authn/External?entityId=VARMENNEKORTTI&tid=${TID}&pid=${PID}&tag=${TAG}&conversation=${CONV}"
else
  STEP3_URL="$STEP2_URL"
fi
if printf '%s\n' "$STEP2_URL" | grep -Eq 'tunnistautuminen\.suomi\.fi/.+'; then
  STEP3_URL="$STEP2_URL"
fi

# Step 3: outer discovery redirect to kortti
step3_body="$TMPDIR/step3.html"
step3_meta="$TMPDIR/step3.meta"
log "step 3: GET $STEP3_URL"
http_request GET "$STEP3_URL" "" "$step3_body" "$step3_meta"
STEP3_URL="$HTTP_FINAL_URL"

STEP3_HOST="$(url_host "$STEP3_URL")"
if [ "$STEP3_HOST" = "tunnistautuminen.suomi.fi" ]; then
  if form_action=$(extract_first_form_action "$step3_body" "$STEP3_URL"); then
    if printf '%s\n' "$form_action" | grep -q 'kortti\.tunnistautuminen\.suomi\.fi'; then
      STEP3_URL="$form_action"
      STEP3_HOST="$(url_host "$STEP3_URL")"
    fi
  fi
fi

if [ "$STEP3_HOST" != "kortti.tunnistautuminen.suomi.fi" ]; then
  log "step 3 landed on $STEP3_HOST; continuing with discovered flow"
fi

KORTTI_CONV="$(query_param "$STEP3_URL" conversation)"
if [ -z "$KORTTI_CONV" ]; then
  KORTTI_CONV="e1s1"
fi

# Some runs switch language in spring flow; copy it from cookie when present.
KORTTI_LANG="$LANGUAGE"
if read_cookie E-Identification-Lang >/dev/null 2>&1; then
  TMP_LANG="$(read_cookie E-Identification-Lang)"
  if [ -n "$TMP_LANG" ]; then
    KORTTI_LANG="$TMP_LANG"
  fi
fi

# Step 4: kortti mTLS POST
step4_form="$TMPDIR/step4.form"
step4_body="$TMPDIR/step4.html"
step4_meta="$TMPDIR/step4.meta"
SAML_URL="https://kortti.tunnistautuminen.suomi.fi/hstidp/authn/External?conversation=${KORTTI_CONV}"
urlencode_pairs "$step4_form" lang "$KORTTI_LANG"
log "step 4: POST $SAML_URL (mTLS client auth)"
if ! openssl_client_auth_request POST "$SAML_URL" "$step4_form" "$step4_body" "$step4_meta"; then
  if [ -n "${HTTP_STATUS:-}" ]; then
    fail "step 4 failed with HTTP ${HTTP_STATUS} from $SAML_URL"
  fi
  fail "step 4 failed: openssl client-auth path"
fi

STEP4_URL="$HTTP_FINAL_URL"
STEP4_HOST="$(url_host "$STEP4_URL")"
STEP4_PATH="$(url_path "$STEP4_URL")"
if printf '%s\n' "$STEP4_PATH" | grep -q '/500/'; then
  fail "step 4 ended in suomi.fi 500 path: $STEP4_URL"
fi
if [ "$STEP4_HOST" = "kortti.tunnistautuminen.suomi.fi" ] && printf '%s\n' "$STEP4_URL" | grep -q 'e=3'; then
  fail "kortti reported certificate rejection for this card (often replacement-card cert / revocation): $STEP4_URL"
fi

FORM_OUT="$TMPDIR/saml0.txt"
if ! extract_form "$step4_body" saml "$STEP4_URL" "$FORM_OUT"; then
  fail "step 4 response has no SAMLResponse form"
fi
FORM_ACTION=$(sed -n '1p' "$FORM_OUT")
FORM_BODY=$(sed -n '2p' "$FORM_OUT")
echo "$FORM_BODY" > "$TMPDIR/saml_body"
step5_body="$TMPDIR/saml_step.html"
step5_meta="$TMPDIR/saml_step.meta"

log "step 5: SAML form hop 0 -> $FORM_ACTION"
http_request POST "$FORM_ACTION" "$TMPDIR/saml_body" "$step5_body" "$step5_meta"
SAML_URL="$HTTP_FINAL_URL"

# Replay SAMLResponse chain until we leave tunnistautuminen domain.
hop=0
max_hops=4
while :; do
  HOST="$(url_host "$SAML_URL")"
  if [ "$HOST" = "www.suomi.fi" ]; then
    break
  fi

  case "$HOST" in
    *tunnistautuminen.suomi.fi)
      if ! extract_form "$step5_body" any "$SAML_URL" "$FORM_OUT"; then
        break
      fi
      FORM_ACTION=$(sed -n '1p' "$FORM_OUT")
      FORM_BODY=$(sed -n '2p' "$FORM_OUT")
      echo "$FORM_BODY" > "$TMPDIR/saml_body"
      hop=$((hop + 1))
      if [ "$hop" -gt "$max_hops" ]; then
        fail "SAML chain did not complete after ${max_hops} proxy hops"
      fi
      log "step 5: SAML form hop ${hop} -> $FORM_ACTION"
      http_request POST "$FORM_ACTION" "$TMPDIR/saml_body" "$step5_body" "$step5_meta"
      SAML_URL="$HTTP_FINAL_URL"
      ;;
    *)
      break
      ;;
  esac
done

SAML_LANDING="$SAML_URL"
log "step 5 final URL: $SAML_LANDING"

# Step 6: best effort role-select (React flow is frequently already resolved)
if printf '%s\n' "$SAML_LANDING" | grep -q 'select-role'; then
  if extract_form "$step5_body" role "$SAML_LANDING" "$FORM_OUT"; then
    FORM_ACTION=$(sed -n '1p' "$FORM_OUT")
    FORM_BODY=$(sed -n '2p' "$FORM_OUT")
    echo "$FORM_BODY" > "$TMPDIR/saml_body"
    log "step 6: posting role form -> $FORM_ACTION"
    http_request POST "$FORM_ACTION" "$TMPDIR/saml_body" "$step5_body" "$step5_meta"
    SAML_LANDING="$HTTP_FINAL_URL"
  else
    log "step 6: role selection required but no static form found; continuing with current session"
  fi
fi

# Step 7: fetch profession from authenticated API
PERSONAL_DATA_URL="https://www.suomi.fi/api/public/v1/personal-data/current/$LANGUAGE"
personal_body="$TMPDIR/personal.json"
personal_meta="$TMPDIR/personal.meta"
log "step 7: GET $PERSONAL_DATA_URL"
if ! http_request GET "$PERSONAL_DATA_URL" "" "$personal_body" "$personal_meta" \
  -L --max-redirs 20 \
  -A "$UA" \
  -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  -H "Accept: application/json" \
  -H "Origin: https://www.suomi.fi" \
  -H "Referer: https://www.suomi.fi/your-data/personal-data"; then
  fail "personal-data GET failed"
fi
read -r PERSONAL_CODE PERSONAL_FINAL < "$personal_meta"
if [ "$PERSONAL_CODE" -lt 200 ] || [ "$PERSONAL_CODE" -ge 400 ]; then
  fail "personal-data API returned HTTP ${PERSONAL_CODE}"
fi

OCCUPATION="$(sed -n 's/.*\"profession\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' "$personal_body" | head -n 1)"

if [ -z "$OCCUPATION" ]; then
  fail "cannot locate profession in personal-data response"
fi

echo "Your occupation: $OCCUPATION"
exit 0
# __SUOMI_WORKER_END__
REMOTE_SH
