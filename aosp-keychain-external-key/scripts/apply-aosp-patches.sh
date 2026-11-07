#!/usr/bin/env bash
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# Preflights and applies the patch series to an AOSP checkout whose
# packages/apps/RefineID is this directory. Apply mode stages the application
# first and needs REFINEID_ANDROID, a refineid-android checkout.

set -euo pipefail
export LC_ALL=C

readonly EXIT_FAILURE=1
readonly EXIT_USAGE=2
readonly MODE_APPLY="apply"
readonly MODE_CHECK_BASE="check-base"
readonly MODE_CHECK_APPLIED="check-applied"
readonly CHECK_BASE_ARGUMENT="--check-base"
readonly CHECK_APPLIED_ARGUMENT="--check-applied"
readonly EXPECTED_POSITIONAL_ARGUMENT_COUNT=1

readonly FRAMEWORKS_BASE_RELATIVE_PATH="frameworks/base"
readonly KEYCHAIN_RELATIVE_PATH="packages/apps/KeyChain"
readonly PIXEL_DEVICE_RELATIVE_PATH="device/google/coral"
readonly REFINEID_RELATIVE_PATH="packages/apps/RefineID"

readonly FRAMEWORKS_BASE_COMMIT="9cc5d58d0254f472ae071b29ccf4fae93ca1cc3d"
readonly KEYCHAIN_BASE_COMMIT="97a7bc2ba75391487ecd3f23153cfb1ce293d6fe" # gitleaks:allow
readonly PIXEL_DEVICE_BASE_COMMIT="64f0b208f174e5d8627c201424c00102ef433adb"

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIRECTORY
SERIES_ROOT="$(cd "${SCRIPT_DIRECTORY}/.." && pwd -P)"
readonly SERIES_ROOT

usage() {
  echo "usage: $0 [${CHECK_BASE_ARGUMENT}|${CHECK_APPLIED_ARGUMENT}] AOSP_ROOT" >&2
}

fail() {
  echo "$1" >&2
  exit "$EXIT_FAILURE"
}

mode="$MODE_APPLY"
case "${1:-}" in
  "$CHECK_BASE_ARGUMENT")
    mode="$MODE_CHECK_BASE"
    shift
    ;;
  "$CHECK_APPLIED_ARGUMENT")
    mode="$MODE_CHECK_APPLIED"
    shift
    ;;
esac

if [[ "$#" -ne "$EXPECTED_POSITIONAL_ARGUMENT_COUNT" ]]; then
  usage
  exit "$EXIT_USAGE"
fi

[[ -d "$1" ]] || fail "AOSP root is not a directory"
AOSP_ROOT="$(cd "$1" && pwd -P)"
readonly AOSP_ROOT
readonly EXPECTED_SERIES_ROOT="${AOSP_ROOT}/${REFINEID_RELATIVE_PATH}"
[[ -d "$EXPECTED_SERIES_ROOT" ]] ||
  fail "the patch series must be placed at ${REFINEID_RELATIVE_PATH}"
RESOLVED_EXPECTED_SERIES_ROOT="$(cd "$EXPECTED_SERIES_ROOT" && pwd -P)"
readonly RESOLVED_EXPECTED_SERIES_ROOT
[[ "$RESOLVED_EXPECTED_SERIES_ROOT" == "$SERIES_ROOT" ]] ||
  fail "this directory must be the AOSP packages/apps/RefineID directory"

preflight_series() {
  local label="$1"
  local project_directory="$2"
  local base_commit="$3"
  local patch_directory="$4"
  local patch
  local project_status
  local temporary_index
  local patches=("${patch_directory}"/*.patch)

  git -C "$project_directory" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    fail "${label} checkout is missing"
  [[ "$(git -C "$project_directory" rev-parse HEAD)" == "$base_commit" ]] ||
    fail "${label} is not at its pinned base commit"
  project_status="$(git -C "$project_directory" status --porcelain --untracked-files=normal)"
  [[ -z "$project_status" ]] || fail "${label} has local changes"
  git -C "$project_directory" var GIT_COMMITTER_IDENT >/dev/null 2>&1 ||
    fail "Git committer identity is not configured for ${label}"
  [[ -f "${patches[0]}" ]] || fail "${label} patch series is empty"

  temporary_index="$(mktemp "${TMPDIR:-/tmp}/refineid-${label}-index.XXXXXX")"
  rm -f "$temporary_index"
  GIT_INDEX_FILE="$temporary_index" git -C "$project_directory" read-tree "$base_commit"
  for patch in "${patches[@]}"; do
    if ! GIT_INDEX_FILE="$temporary_index" git -C "$project_directory" apply --cached "$patch"; then
      rm -f "$temporary_index"
      fail "${label} patch preflight failed"
    fi
  done
  GIT_INDEX_FILE="$temporary_index" git -C "$project_directory" diff --cached --check
  rm -f "$temporary_index"
  echo "${label}=ready"
}

expected_tree() {
  local label="$1"
  local project_directory="$2"
  local base_commit="$3"
  local patch_directory="$4"
  local patch
  local tree
  local temporary_index
  local patches=("${patch_directory}"/*.patch)

  [[ -f "${patches[0]}" ]] || fail "${label} patch series is empty"
  temporary_index="$(mktemp "${TMPDIR:-/tmp}/refineid-${label}-index.XXXXXX")"
  rm -f "$temporary_index"
  GIT_INDEX_FILE="$temporary_index" git -C "$project_directory" read-tree "$base_commit"
  for patch in "${patches[@]}"; do
    if ! GIT_INDEX_FILE="$temporary_index" git -C "$project_directory" apply --cached "$patch"; then
      rm -f "$temporary_index"
      fail "${label} patch verification failed"
    fi
  done
  tree="$(GIT_INDEX_FILE="$temporary_index" git -C "$project_directory" write-tree)"
  rm -f "$temporary_index"
  printf '%s' "$tree"
}

verify_applied_series() {
  local label="$1"
  local project_directory="$2"
  local base_commit="$3"
  local patch_directory="$4"
  local actual_tree
  local project_status
  local wanted_tree

  git -C "$project_directory" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    fail "${label} checkout is missing"
  git -C "$project_directory" merge-base --is-ancestor "$base_commit" HEAD ||
    fail "${label} does not descend from its pinned base commit"
  project_status="$(git -C "$project_directory" status --porcelain --untracked-files=normal)"
  [[ -z "$project_status" ]] || fail "${label} has local changes"
  actual_tree="$(git -C "$project_directory" rev-parse 'HEAD^{tree}')"
  wanted_tree="$(expected_tree "$label" "$project_directory" "$base_commit" "$patch_directory")"
  [[ "$actual_tree" == "$wanted_tree" ]] ||
    fail "${label} does not exactly match the RefineID patch series"
  echo "${label}=applied"
}

apply_series() {
  local label="$1"
  local project_directory="$2"
  local patch_directory="$3"
  local patches=("${patch_directory}"/*.patch)

  if ! git -C "$project_directory" am "${patches[@]}"; then
    echo "${label} patching stopped; inspect it and use git am --abort to roll back" >&2
    exit "$EXIT_FAILURE"
  fi
  echo "${label}=applied"
}

readonly FRAMEWORKS_PROJECT="${AOSP_ROOT}/${FRAMEWORKS_BASE_RELATIVE_PATH}"
readonly KEYCHAIN_PROJECT="${AOSP_ROOT}/${KEYCHAIN_RELATIVE_PATH}"
readonly PIXEL_DEVICE_PROJECT="${AOSP_ROOT}/${PIXEL_DEVICE_RELATIVE_PATH}"
readonly FRAMEWORKS_PATCH_DIRECTORY="${SERIES_ROOT}/patches/frameworks-base"
readonly KEYCHAIN_PATCH_DIRECTORY="${SERIES_ROOT}/patches/packages-apps-KeyChain"
readonly PIXEL_DEVICE_PATCH_DIRECTORY="${SERIES_ROOT}/patches/device-google-coral"

if [[ "$mode" == "$MODE_CHECK_APPLIED" ]]; then
  verify_applied_series \
    "frameworks-base" "$FRAMEWORKS_PROJECT" "$FRAMEWORKS_BASE_COMMIT" \
    "$FRAMEWORKS_PATCH_DIRECTORY"
  verify_applied_series \
    "keychain" "$KEYCHAIN_PROJECT" "$KEYCHAIN_BASE_COMMIT" "$KEYCHAIN_PATCH_DIRECTORY"
  verify_applied_series \
    "pixel-device" "$PIXEL_DEVICE_PROJECT" "$PIXEL_DEVICE_BASE_COMMIT" \
    "$PIXEL_DEVICE_PATCH_DIRECTORY"
  exit
fi

preflight_series \
  "frameworks-base" "$FRAMEWORKS_PROJECT" "$FRAMEWORKS_BASE_COMMIT" \
  "$FRAMEWORKS_PATCH_DIRECTORY"
preflight_series \
  "keychain" "$KEYCHAIN_PROJECT" "$KEYCHAIN_BASE_COMMIT" "$KEYCHAIN_PATCH_DIRECTORY"
preflight_series \
  "pixel-device" "$PIXEL_DEVICE_PROJECT" "$PIXEL_DEVICE_BASE_COMMIT" \
  "$PIXEL_DEVICE_PATCH_DIRECTORY"

if [[ "$mode" == "$MODE_CHECK_BASE" ]]; then
  exit
fi

"${SCRIPT_DIRECTORY}/stage-aosp-prebuilt.sh"
apply_series "frameworks-base" "$FRAMEWORKS_PROJECT" "$FRAMEWORKS_PATCH_DIRECTORY"
apply_series "keychain" "$KEYCHAIN_PROJECT" "$KEYCHAIN_PATCH_DIRECTORY"
apply_series "pixel-device" "$PIXEL_DEVICE_PROJECT" "$PIXEL_DEVICE_PATCH_DIRECTORY"
