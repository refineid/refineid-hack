#!/usr/bin/env bash
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# Builds the patched aosp_flame-userdebug image in an AOSP checkout whose
# packages/apps/RefineID is this directory. Staging the application needs
# REFINEID_ANDROID, a refineid-android checkout.

set -euo pipefail
export LC_ALL=C

readonly EXIT_FAILURE=1
readonly EXIT_USAGE=2
readonly EXPECTED_ARGUMENT_COUNT=1
readonly EXPECTED_HOST_KERNEL="Linux"
readonly EXPECTED_HOST_ARCHITECTURE="x86_64"
readonly REQUIRED_BUILD_FREE_SPACE_GIB=150
readonly KIB_PER_GIB=1048576
readonly REQUIRED_BUILD_FREE_SPACE_KIB=$((REQUIRED_BUILD_FREE_SPACE_GIB * KIB_PER_GIB))
readonly REQUIRED_GRADLE_JAVA_FEATURE=26
readonly LUNCH_TARGET="aosp_flame-userdebug"
readonly MANIFEST_RELATIVE_PATH=".repo/manifests"
readonly EXPECTED_MANIFEST_COMMIT="012e197f31592b82d79ed2d4e03c5fb3ada38b62"
readonly REFINEID_RELATIVE_PATH="packages/apps/RefineID"
readonly LEGACY_CLANG_RELATIVE_PATH="prebuilts/clang/host/linux-x86/clang-3289846/bin/clang.real"
readonly SHARED_VENDOR_MAKEFILE="vendor/google_devices/coral/proprietary/device-vendor.mk"
readonly GOOGLE_VENDOR_MAKEFILE="vendor/google_devices/flame/device-partial.mk"
readonly QUALCOMM_VENDOR_MAKEFILE="vendor/qcom/flame/device-partial.mk"
readonly HOST_APKSIGNER_RELATIVE_PATH="out/host/linux-x86/bin/apksigner"
readonly BUILT_REFINEID_APK="product/priv-app/RefineID/RefineID.apk"
readonly BUILT_KEYCHAIN_APK="system/app/KeyChain/KeyChain.apk"
readonly CERTIFICATE_DIGEST_LABEL="Signer #1 certificate SHA-256 digest"
readonly -a CHANGED_BUILD_MODULES=(
  "RefineID"
  "KeyChain"
  "framework-minus-apex"
  "KeystoreTests"
  "KeyChainTests"
  "apksigner"
)

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIRECTORY
SERIES_ROOT="$(cd "${SCRIPT_DIRECTORY}/.." && pwd -P)"
readonly SERIES_ROOT

fail() {
  echo "$1" >&2
  exit "$EXIT_FAILURE"
}

signer_digest() {
  local apk="$1"
  "$APKSIGNER" verify --print-certs "$apk" 2>/dev/null |
    awk -F': ' -v label="$CERTIFICATE_DIGEST_LABEL" '$1 == label {print $2; exit}'
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
  fail "the Android 13 platform build requires Linux"
[[ "$(uname -m)" == "$EXPECTED_HOST_ARCHITECTURE" ]] ||
  fail "the Android 13 platform build requires x86_64"
[[ -f "${AOSP_ROOT}/build/envsetup.sh" ]] || fail "AOSP build/envsetup.sh is missing"
readonly LEGACY_CLANG="${AOSP_ROOT}/${LEGACY_CLANG_RELATIVE_PATH}"
[[ -x "$LEGACY_CLANG" ]] || fail "AOSP legacy Clang is missing"
"$LEGACY_CLANG" --version >/dev/null 2>&1 ||
  fail "AOSP legacy Clang host libraries are missing; run scripts/configure-aosp-host-compat.sh with sudo"
readonly MANIFEST_PROJECT="${AOSP_ROOT}/${MANIFEST_RELATIVE_PATH}"
git -C "$MANIFEST_PROJECT" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "AOSP manifest checkout is missing"
[[ "$(git -C "$MANIFEST_PROJECT" rev-parse HEAD)" == "$EXPECTED_MANIFEST_COMMIT" ]] ||
  fail "AOSP manifest is not android-13.0.0_r31"
[[ -z "$(git -C "$MANIFEST_PROJECT" status --porcelain --untracked-files=normal)" ]] ||
  fail "AOSP manifest checkout has local changes"
[[ -d "$EXPECTED_SERIES_ROOT" ]] ||
  fail "the patch series must be placed at ${REFINEID_RELATIVE_PATH}"
RESOLVED_EXPECTED_SERIES_ROOT="$(cd "$EXPECTED_SERIES_ROOT" && pwd -P)"
readonly RESOLVED_EXPECTED_SERIES_ROOT
[[ "$RESOLVED_EXPECTED_SERIES_ROOT" == "$SERIES_ROOT" ]] ||
  fail "this directory must be the AOSP packages/apps/RefineID directory"

FREE_SPACE_KIB="$(df -Pk "$AOSP_ROOT" | awk 'NR == 2 {print $4}')"
readonly FREE_SPACE_KIB
[[ "$FREE_SPACE_KIB" =~ ^[0-9]+$ ]] || fail "could not measure AOSP build storage"
[[ "$FREE_SPACE_KIB" -ge "$REQUIRED_BUILD_FREE_SPACE_KIB" ]] ||
  fail "the synced tree needs at least ${REQUIRED_BUILD_FREE_SPACE_GIB} GiB free to build"

[[ -n "${JAVA_HOME:-}" ]] || fail "JAVA_HOME must select OpenJDK 26 for the app build"
[[ -x "${JAVA_HOME}/bin/java" ]] || fail "JAVA_HOME does not contain java"
JAVA_FEATURE="$(
  "${JAVA_HOME}/bin/java" -XshowSettings:properties -version 2>&1 |
    awk -F= '/java.specification.version/ {gsub(/[[:space:]]/, "", $2); print $2}'
)"
readonly JAVA_FEATURE
[[ "$JAVA_FEATURE" == "$REQUIRED_GRADLE_JAVA_FEATURE" ]] ||
  fail "JAVA_HOME must select OpenJDK ${REQUIRED_GRADLE_JAVA_FEATURE}"

[[ -f "${AOSP_ROOT}/${SHARED_VENDOR_MAKEFILE}" ]] ||
  fail "the shared Pixel 4 vendor makefile is missing"
[[ -f "${AOSP_ROOT}/${GOOGLE_VENDOR_MAKEFILE}" ]] ||
  fail "the licensed Google Pixel 4 vendor files are missing"
[[ -f "${AOSP_ROOT}/${QUALCOMM_VENDOR_MAKEFILE}" ]] ||
  fail "the licensed Qualcomm Pixel 4 vendor files are missing"

"${SCRIPT_DIRECTORY}/apply-aosp-patches.sh" --check-applied "$AOSP_ROOT"
"${SCRIPT_DIRECTORY}/stage-aosp-prebuilt.sh"

unset JAVA_HOME
cd "$AOSP_ROOT"
# Android 13 build functions are not nounset-safe.
set +u
# shellcheck source=/dev/null
source build/envsetup.sh
lunch "$LUNCH_TARGET"
m "${CHANGED_BUILD_MODULES[@]}"
m
set -u

[[ -n "${ANDROID_PRODUCT_OUT:-}" ]] || fail "Android product output is unset"
readonly PRODUCT_APK="${ANDROID_PRODUCT_OUT}/${BUILT_REFINEID_APK}"
readonly KEYCHAIN_APK="${ANDROID_PRODUCT_OUT}/${BUILT_KEYCHAIN_APK}"
[[ -f "$PRODUCT_APK" ]] || fail "the product image is missing RefineID"
[[ -f "$KEYCHAIN_APK" ]] || fail "the product image is missing KeyChain"
APKSIGNER="${AOSP_ROOT}/${HOST_APKSIGNER_RELATIVE_PATH}"
readonly APKSIGNER
[[ -x "$APKSIGNER" ]] || fail "the AOSP host apksigner is missing"
"$APKSIGNER" verify "$PRODUCT_APK" >/dev/null 2>&1 ||
  fail "the product RefineID APK is not signed"
"$APKSIGNER" verify "$KEYCHAIN_APK" >/dev/null 2>&1 ||
  fail "the product KeyChain APK is not signed"
REFINEID_SIGNER_DIGEST="$(signer_digest "$PRODUCT_APK")"
readonly REFINEID_SIGNER_DIGEST
KEYCHAIN_SIGNER_DIGEST="$(signer_digest "$KEYCHAIN_APK")"
readonly KEYCHAIN_SIGNER_DIGEST
[[ -n "$REFINEID_SIGNER_DIGEST" ]] || fail "the RefineID signer cannot be identified"
[[ "$REFINEID_SIGNER_DIGEST" == "$KEYCHAIN_SIGNER_DIGEST" ]] ||
  fail "RefineID and KeyChain are not signed by the same platform certificate"

echo "aosp_flame_image=ready"
