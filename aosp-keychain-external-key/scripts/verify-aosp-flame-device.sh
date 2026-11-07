#!/usr/bin/env bash
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# Verifies the booted patched Pixel 4 image from an AOSP checkout whose
# packages/apps/RefineID is this directory.

set -euo pipefail
export LC_ALL=C

readonly EXIT_FAILURE=1
readonly EXIT_USAGE=2
readonly EXPECTED_ARGUMENT_COUNT=1
readonly EXPECTED_HOST_KERNEL="Linux"
readonly EXPECTED_HOST_ARCHITECTURE="x86_64"
readonly EXPECTED_DEVICE_CODENAME="flame"
readonly EXPECTED_ANDROID_SDK=33
readonly EXPECTED_BUILD_ID="TP1A.221005.002.B2"
readonly EXPECTED_BUILD_TYPE="userdebug"
readonly EXPECTED_BOOT_COMPLETION="1"
readonly EXPECTED_SELINUX_MODE="Enforcing"
readonly ADB_AUTHORIZED_STATE="device"
readonly ADB_TRANSPORT_ID_LABEL="transport_id"
readonly DEVICE_SERIAL_PROPERTY="ro.serialno"
readonly EXPECTED_APP_SELINUX_CONTEXT_PREFIX="u:r:refineid_app:s0"
readonly LUNCH_TARGET="aosp_flame-userdebug"
readonly MANIFEST_RELATIVE_PATH=".repo/manifests"
readonly EXPECTED_MANIFEST_COMMIT="012e197f31592b82d79ed2d4e03c5fb3ada38b62"
readonly REFINEID_RELATIVE_PATH="packages/apps/RefineID"
readonly HOST_APKSIGNER_RELATIVE_PATH="out/host/linux-x86/bin/apksigner"
readonly REFINEID_PACKAGE="fi.refineid.android"
readonly REFINEID_ACTIVITY="${REFINEID_PACKAGE}/.MainActivity"
readonly REFINEID_PROVIDER_COMPONENT="${REFINEID_PACKAGE}/.keychain.ExternalKeyProviderService"
readonly PROVIDER_INTERFACE_ACTION="com.android.keychain.external.IExternalKeyProviderService"
readonly KEYCHAIN_PACKAGE="com.android.keychain"
readonly DEVICE_REFINEID_APK="/product/priv-app/RefineID/RefineID.apk"
readonly DEVICE_KEYCHAIN_APK="/system/app/KeyChain/KeyChain.apk"
readonly BUILT_REFINEID_APK="product/priv-app/RefineID/RefineID.apk"
readonly BUILT_KEYCHAIN_APK="system/app/KeyChain/KeyChain.apk"
readonly BACKGROUND_ACTIVITY_PERMISSION="android.permission.START_ACTIVITIES_FROM_BACKGROUND"
readonly INTERNET_PERMISSION="android.permission.INTERNET"
readonly CERTIFICATE_DIGEST_LABEL="Signer #1 certificate SHA-256 digest"
readonly ANDROID_UIDS_PER_USER=100000
readonly FIRST_APPLICATION_UID=10000
readonly PROCESS_CONTEXT_ATTEMPTS=15
readonly PROCESS_CONTEXT_INTERVAL_SECONDS=1
readonly DEVICE_AUDIT_DIRECTORY_PREFIX="refineid-aosp-device."
readonly ATEST_RESULT_ROOT="/tmp/atest_result"
readonly ATEST_LATEST_LINK="${ATEST_RESULT_ROOT}/LATEST"
# Atest uses tempfile.mkdtemp() with a YYYYMMDD_HHMMSS_ prefix.
readonly ATEST_RESULT_BASENAME_PATTERN='^[[:digit:]]{8}_[[:digit:]]{6}_[[:alnum:]_.-]+$'
readonly EXPECTED_ATEST_RESULT_DIRECTORY_COUNT=1
readonly -a FRAMEWORK_TESTS=(
  "KeystoreTests:android.security.KeyChainPrivateKeyDescriptorTest"
  "KeystoreTests:android.security.KeyChainExternalSignatureParcelTest"
  "KeystoreTests:android.security.KeyChainExternalKeyProviderTest"
)
readonly -a KEYCHAIN_TESTS=(
  "KeyChainTests:com.android.keychain.BoundExternalKeyProviderTest"
  "KeyChainTests:com.android.keychain.ExternalKeyManagerTest"
  "KeyChainTests:com.android.keychain.external.ExternalKeyProviderParcelTest"
  "KeyChainTests:com.android.keychain.tests.BasicKeyChainServiceTest"
  "KeyChainTests:com.android.keychain.tests.KeyChainActivityTest"
)

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIRECTORY
SERIES_ROOT="$(cd "${SCRIPT_DIRECTORY}/.." && pwd -P)"
readonly SERIES_ROOT
TEMPORARY_PARENT="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
readonly TEMPORARY_PARENT
device_audit_directory=""
atest_started=false
atest_results_collected=false
atest_result_count=0
atest_previous_latest_exists=false
atest_previous_latest_target=""
atest_before_directories=""

fail() {
  echo "$1" >&2
  exit "$EXIT_FAILURE"
}

cleanup() {
  local audit_basename
  local audit_parent

  if [[ "$atest_started" == true && "$atest_results_collected" == false ]]; then
    collect_atest_results >/dev/null 2>&1 || true
  fi
  [[ -n "$device_audit_directory" && -d "$device_audit_directory" ]] || return
  audit_parent="$(cd "$(dirname "$device_audit_directory")" && pwd -P)"
  audit_basename="$(basename "$device_audit_directory")"
  if [[ "$audit_parent" == "$TEMPORARY_PARENT" &&
    "$audit_basename" == "$DEVICE_AUDIT_DIRECTORY_PREFIX"* ]]; then
    rm -rf -- "$device_audit_directory"
  fi
}

prepare_atest_results() {
  if [[ -e "$ATEST_RESULT_ROOT" || -L "$ATEST_RESULT_ROOT" ]]; then
    [[ -d "$ATEST_RESULT_ROOT" && ! -L "$ATEST_RESULT_ROOT" ]] ||
      fail "Atest result root is not a directory"
  fi
  if [[ -e "$ATEST_LATEST_LINK" || -L "$ATEST_LATEST_LINK" ]]; then
    [[ -L "$ATEST_LATEST_LINK" ]] || fail "Atest LATEST is not a symbolic link"
    atest_previous_latest_exists=true
    atest_previous_latest_target="$(readlink "$ATEST_LATEST_LINK")"
  fi

  atest_before_directories="${device_audit_directory}/atest-directories-before"
  if [[ -d "$ATEST_RESULT_ROOT" ]]; then
    find "$ATEST_RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d -print |
      sort >"$atest_before_directories"
  else
    : >"$atest_before_directories"
  fi
  atest_started=true
}

collect_atest_results() {
  local after_directories
  local collection_failed=0
  local destination_directory
  local new_directories
  local result_basename
  local result_directory
  local result_parent
  local resolved_result_root

  [[ "$atest_started" == true ]] || return 0
  [[ "$atest_results_collected" == false ]] || return 0
  atest_results_collected=true
  after_directories="${device_audit_directory}/atest-directories-after"
  new_directories="${device_audit_directory}/atest-directories-new"
  destination_directory="${device_audit_directory}/atest-results"
  atest_result_count=0

  if [[ -d "$ATEST_RESULT_ROOT" && ! -L "$ATEST_RESULT_ROOT" ]]; then
    resolved_result_root="$(cd "$ATEST_RESULT_ROOT" && pwd -P)"
    if ! find "$ATEST_RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d -print |
      sort >"$after_directories"; then
      collection_failed=1
    fi
  else
    resolved_result_root=""
    : >"$after_directories"
  fi
  if ! comm -13 "$atest_before_directories" "$after_directories" >"$new_directories"; then
    collection_failed=1
  fi

  while IFS= read -r result_directory; do
    [[ -n "$result_directory" ]] || continue
    result_basename="$(basename "$result_directory")"
    result_parent="$(cd "$(dirname "$result_directory")" && pwd -P)"
    if [[ -z "$resolved_result_root" ||
      "$result_parent" != "$resolved_result_root" ||
      ! "$result_basename" =~ $ATEST_RESULT_BASENAME_PATTERN ||
      ! -d "$result_directory" ||
      -L "$result_directory" ]]; then
      collection_failed=1
      continue
    fi
    mkdir -p "$destination_directory"
    if mv -- "$result_directory" "${destination_directory}/${result_basename}"; then
      ((atest_result_count += 1))
    else
      collection_failed=1
    fi
  done <"$new_directories"

  if [[ -L "$ATEST_LATEST_LINK" ]]; then
    unlink "$ATEST_LATEST_LINK" || collection_failed=1
  elif [[ -e "$ATEST_LATEST_LINK" ]]; then
    collection_failed=1
  fi
  if [[ "$atest_previous_latest_exists" == true ]]; then
    if [[ -e "$ATEST_LATEST_LINK" || -L "$ATEST_LATEST_LINK" ]] ||
      ! ln -s -- "$atest_previous_latest_target" "$ATEST_LATEST_LINK"; then
      collection_failed=1
    fi
  fi

  [[ "$collection_failed" -eq 0 ]]
}

device_shell() {
  "${ADB_DEVICE[@]}" shell "$@" 2>/dev/null | tr -d '\r'
}

require_device_value() {
  local label="$1"
  local expected="$2"
  shift 2

  [[ "$(device_shell "$@")" == "$expected" ]] || fail "$label does not match"
}

device_package_path() {
  local package_name="$1"
  local paths

  paths="$(device_shell pm path "$package_name" | sed -n 's/^package://p')"
  [[ -n "$paths" && "$paths" != *$'\n'* ]] ||
    fail "${package_name} does not have one base APK"
  printf '%s' "$paths"
}

signer_digest() {
  local apk="$1"

  "$APKSIGNER" verify --print-certs "$apk" 2>/dev/null |
    awk -F': ' -v label="$CERTIFICATE_DIGEST_LABEL" '$1 == label {print $2; exit}'
}

package_requests_permission() {
  local permission="$1"

  printf '%s\n' "$package_dump" |
    sed -n '/^[[:space:]]*requested permissions:/,/^[[:space:]]*install permissions:/p' |
    sed -e '1d' -e '$d' -e 's/^[[:space:]]*//' |
    grep -Fxq "$permission"
}

package_has_install_permission() {
  local permission="$1"

  printf '%s\n' "$package_dump" |
    sed -n '/^[[:space:]]*install permissions:/,/^[[:space:]]*User [0-9][0-9]*:/p' |
    sed -e '1d' -e '$d' -e 's/^[[:space:]]*//' |
    grep -Fxq "${permission}: granted=true"
}

run_atest_suite() {
  local label="$1"
  local test_status
  shift

  prepare_atest_results
  set +e
  ANDROID_SERIAL="$selected_adb_serial" \
    atest "$@" >"${device_audit_directory}/${label}.log" 2>&1
  test_status=$?
  set -e
  collect_atest_results || fail "Atest result cleanup failed"
  [[ "$atest_result_count" -eq "$EXPECTED_ATEST_RESULT_DIRECTORY_COUNT" ]] ||
    fail "Atest result capture failed"
  if [[ "$test_status" -ne 0 ]]; then
    fail "${label} failed"
  fi
}

if [[ "$#" -ne "$EXPECTED_ARGUMENT_COUNT" ]]; then
  echo "usage: $0 AOSP_ROOT" >&2
  exit "$EXIT_USAGE"
fi
[[ -d "$1" ]] || fail "AOSP root is not a directory"
AOSP_ROOT="$(cd "$1" && pwd -P)"
readonly AOSP_ROOT
readonly EXPECTED_SERIES_ROOT="${AOSP_ROOT}/${REFINEID_RELATIVE_PATH}"

[[ "$(uname -s)" == "$EXPECTED_HOST_KERNEL" ]] ||
  fail "the patched Pixel verifier requires Linux"
[[ "$(uname -m)" == "$EXPECTED_HOST_ARCHITECTURE" ]] ||
  fail "the patched Pixel verifier requires x86_64"
[[ -f "${AOSP_ROOT}/build/envsetup.sh" ]] || fail "AOSP build/envsetup.sh is missing"
[[ -d "$EXPECTED_SERIES_ROOT" ]] ||
  fail "the patch series must be placed at ${REFINEID_RELATIVE_PATH}"
RESOLVED_EXPECTED_SERIES_ROOT="$(cd "$EXPECTED_SERIES_ROOT" && pwd -P)"
readonly RESOLVED_EXPECTED_SERIES_ROOT
[[ "$RESOLVED_EXPECTED_SERIES_ROOT" == "$SERIES_ROOT" ]] ||
  fail "this directory must be the AOSP packages/apps/RefineID directory"
[[ -z "$(git -C "$SERIES_ROOT" status --porcelain --untracked-files=normal -- .)" ]] ||
  fail "the patch series checkout has local changes"

readonly MANIFEST_PROJECT="${AOSP_ROOT}/${MANIFEST_RELATIVE_PATH}"
git -C "$MANIFEST_PROJECT" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "AOSP manifest checkout is missing"
[[ "$(git -C "$MANIFEST_PROJECT" rev-parse HEAD)" == "$EXPECTED_MANIFEST_COMMIT" ]] ||
  fail "AOSP manifest is not android-13.0.0_r31"
[[ -z "$(git -C "$MANIFEST_PROJECT" status --porcelain --untracked-files=normal)" ]] ||
  fail "AOSP manifest checkout has local changes"
"${SCRIPT_DIRECTORY}/apply-aosp-patches.sh" --check-applied "$AOSP_ROOT"

unset JAVA_HOME
cd "$AOSP_ROOT"
# Android 13 environment setup is not nounset-safe.
set +u
# shellcheck source=/dev/null
source build/envsetup.sh >/dev/null
lunch "$LUNCH_TARGET" >/dev/null
set -u
command -v adb >/dev/null 2>&1 || fail "adb is unavailable"
command -v atest >/dev/null 2>&1 || fail "atest is unavailable"
ADB="$(command -v adb)"
readonly ADB
adb_device_listing="$("$ADB" devices -l 2>/dev/null)" ||
  fail "ADB device discovery failed"
readonly adb_device_listing
candidate_adb_transports="$(
  printf '%s\n' "$adb_device_listing" |
    awk \
      -v state="$ADB_AUTHORIZED_STATE" \
      -v label="${ADB_TRANSPORT_ID_LABEL}:" \
      '$2 == state {
        for (field = 3; field <= NF; field++) {
          if (index($field, label) == 1) {
            print $1 "\t" substr($field, length(label) + 1)
          }
        }
      }'
)"
readonly candidate_adb_transports
selected_adb_serial=""
selected_adb_transport_id=""
selected_physical_serial=""
while IFS=$'\t' read -r candidate_adb_serial candidate_adb_transport_id; do
  [[ -n "$candidate_adb_serial" && "$candidate_adb_transport_id" =~ ^[0-9]+$ ]] ||
    continue
  candidate_codename="$(
    "$ADB" -t "$candidate_adb_transport_id" shell getprop ro.product.device \
      2>/dev/null | tr -d '\r'
  )"
  [[ "$candidate_codename" == "$EXPECTED_DEVICE_CODENAME" ]] || continue
  candidate_physical_serial="$(
    "$ADB" -t "$candidate_adb_transport_id" shell getprop "$DEVICE_SERIAL_PROPERTY" \
      2>/dev/null | tr -d '\r'
  )"
  [[ -n "$candidate_physical_serial" ]] || fail "Pixel 4 identity is unavailable"
  if [[ -n "$selected_physical_serial" &&
    "$candidate_physical_serial" != "$selected_physical_serial" ]]; then
    fail "multiple Pixel 4 devices are connected"
  fi
  selected_physical_serial="$candidate_physical_serial"
  if [[ -z "$selected_adb_transport_id" ]]; then
    selected_adb_serial="$candidate_adb_serial"
    selected_adb_transport_id="$candidate_adb_transport_id"
  fi
done <<<"$candidate_adb_transports"
[[ -n "$selected_adb_transport_id" ]] || fail "Pixel 4 ADB transport is unavailable"
readonly selected_adb_serial
readonly selected_adb_transport_id
readonly selected_physical_serial
readonly -a ADB_DEVICE=("$ADB" -t "$selected_adb_transport_id")
[[ -n "${ANDROID_PRODUCT_OUT:-}" ]] || fail "Android product output is unset"
readonly PRODUCT_REFINEID_APK="${ANDROID_PRODUCT_OUT}/${BUILT_REFINEID_APK}"
readonly PRODUCT_KEYCHAIN_APK="${ANDROID_PRODUCT_OUT}/${BUILT_KEYCHAIN_APK}"
[[ -f "$PRODUCT_REFINEID_APK" ]] || fail "built RefineID APK is missing"
[[ -f "$PRODUCT_KEYCHAIN_APK" ]] || fail "built KeyChain APK is missing"
APKSIGNER="${AOSP_ROOT}/${HOST_APKSIGNER_RELATIVE_PATH}"
readonly APKSIGNER
[[ -x "$APKSIGNER" ]] || fail "AOSP host apksigner is missing"

device_audit_directory="$(
  mktemp -d "${TEMPORARY_PARENT}/${DEVICE_AUDIT_DIRECTORY_PREFIX}XXXXXX"
)"
trap cleanup EXIT

[[ "$("${ADB_DEVICE[@]}" get-state 2>/dev/null)" == "$ADB_AUTHORIZED_STATE" ]] ||
  fail "ADB device is unavailable"
require_device_value "device codename" "$EXPECTED_DEVICE_CODENAME" getprop ro.product.device
require_device_value "Android SDK" "$EXPECTED_ANDROID_SDK" getprop ro.build.version.sdk
require_device_value "build ID" "$EXPECTED_BUILD_ID" getprop ro.build.id
require_device_value "build type" "$EXPECTED_BUILD_TYPE" getprop ro.build.type
require_device_value "boot completion" "$EXPECTED_BOOT_COMPLETION" getprop sys.boot_completed
require_device_value "SELinux mode" "$EXPECTED_SELINUX_MODE" getenforce

current_user="$(device_shell am get-current-user)"
readonly current_user
[[ "$current_user" =~ ^[0-9]+$ ]] || fail "current Android user cannot be identified"

installed_refineid_apk="$(device_package_path "$REFINEID_PACKAGE")"
readonly installed_refineid_apk
installed_keychain_apk="$(device_package_path "$KEYCHAIN_PACKAGE")"
readonly installed_keychain_apk
[[ "$installed_refineid_apk" == "$DEVICE_REFINEID_APK" ]] ||
  fail "RefineID is not the product priv-app"
[[ "$installed_keychain_apk" == "$DEVICE_KEYCHAIN_APK" ]] ||
  fail "KeyChain is not the system image app"

package_dump="$(device_shell dumpsys package "$REFINEID_PACKAGE")"
readonly package_dump
printf '%s\n' "$package_dump" | grep -Eq 'flags=\[[^]]*SYSTEM' ||
  fail "RefineID is not a system package"
printf '%s\n' "$package_dump" | grep -Eq 'privateFlags=\[[^]]*PRIVILEGED' ||
  fail "RefineID is not a privileged package"
application_uid="$(
  printf '%s\n' "$package_dump" |
    awk -F= '/^[[:space:]]*userId=/{gsub(/[[:space:]]/, "", $2); print $2; exit}'
)"
readonly application_uid
[[ "$application_uid" =~ ^[0-9]+$ ]] || fail "RefineID UID cannot be identified"
application_id=$((application_uid % ANDROID_UIDS_PER_USER))
readonly application_id
[[ "$application_id" -ge "$FIRST_APPLICATION_UID" ]] ||
  fail "RefineID does not use a regular application UID"

package_requests_permission "$BACKGROUND_ACTIVITY_PERMISSION" ||
  fail "RefineID does not request the background activity permission"
package_has_install_permission "$BACKGROUND_ACTIVITY_PERMISSION" ||
  fail "RefineID was not granted the background activity permission"
if package_requests_permission "$INTERNET_PERMISSION"; then
  fail "RefineID requests Internet access"
fi
require_device_value \
  "external-key provider service" \
  "$REFINEID_PROVIDER_COMPONENT" \
  cmd package query-services --brief --components --user "$current_user" \
  -a "$PROVIDER_INTERFACE_ACTION"

"${ADB_DEVICE[@]}" pull "$installed_refineid_apk" \
  "${device_audit_directory}/RefineID.apk" >/dev/null 2>&1 ||
  fail "installed RefineID APK cannot be read"
"${ADB_DEVICE[@]}" pull "$installed_keychain_apk" \
  "${device_audit_directory}/KeyChain.apk" >/dev/null 2>&1 ||
  fail "installed KeyChain APK cannot be read"
cmp -s "${device_audit_directory}/RefineID.apk" "$PRODUCT_REFINEID_APK" ||
  fail "installed RefineID APK differs from the built image"
cmp -s "${device_audit_directory}/KeyChain.apk" "$PRODUCT_KEYCHAIN_APK" ||
  fail "installed KeyChain APK differs from the built image"
"$APKSIGNER" verify "${device_audit_directory}/RefineID.apk" >/dev/null 2>&1 ||
  fail "installed RefineID APK signature is invalid"
"$APKSIGNER" verify "${device_audit_directory}/KeyChain.apk" >/dev/null 2>&1 ||
  fail "installed KeyChain APK signature is invalid"
refineid_signer="$(signer_digest "${device_audit_directory}/RefineID.apk")"
readonly refineid_signer
keychain_signer="$(signer_digest "${device_audit_directory}/KeyChain.apk")"
readonly keychain_signer
[[ -n "$refineid_signer" && "$refineid_signer" == "$keychain_signer" ]] ||
  fail "installed RefineID and KeyChain signers differ"

"${ADB_DEVICE[@]}" shell am start -W -n "$REFINEID_ACTIVITY" >/dev/null 2>&1 ||
  fail "RefineID activity cannot start"
process_context=""
for ((attempt = 0; attempt < PROCESS_CONTEXT_ATTEMPTS; attempt++)); do
  process_context="$(
    device_shell ps -AZ |
      awk -v process="$REFINEID_PACKAGE" '$NF == process {print $1; exit}'
  )"
  [[ -n "$process_context" ]] && break
  sleep "$PROCESS_CONTEXT_INTERVAL_SECONDS"
done
[[ "$process_context" == "$EXPECTED_APP_SELINUX_CONTEXT_PREFIX"* ]] ||
  fail "RefineID process is not confined to refineid_app"

run_atest_suite "platform-tests" "${FRAMEWORK_TESTS[@]}" "${KEYCHAIN_TESTS[@]}"

echo "aosp_flame_image=verified"
echo "aosp_platform_tests=verified"
