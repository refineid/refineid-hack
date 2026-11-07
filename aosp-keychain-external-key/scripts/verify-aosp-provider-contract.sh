#!/usr/bin/env bash
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# Compares the application's copy of the private provider wire contract with
# the copy the KeyChain patch adds. Requires the application repository:
# REFINEID_ANDROID must name a refineid-android checkout.

set -euo pipefail
export LC_ALL=C

readonly EXIT_FAILURE=1
readonly PATCH_SOURCE_DIRECTORY="src/com/android/keychain/external"

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIRECTORY
SERIES_ROOT="$(cd "${SCRIPT_DIRECTORY}/.." && pwd -P)"
readonly SERIES_ROOT
readonly PROVIDER_PATCH_DIRECTORY="${SERIES_ROOT}/patches/packages-apps-KeyChain"
readonly PROVIDER_PATCH="${PROVIDER_PATCH_DIRECTORY}/0005-Bind-statically-trusted-external-key-provider.patch"

fail() {
  echo "$1" >&2
  exit "$EXIT_FAILURE"
}

[[ -n "${REFINEID_ANDROID:-}" ]] || fail "REFINEID_ANDROID must name a refineid-android checkout"
[[ -d "$REFINEID_ANDROID" ]] || fail "REFINEID_ANDROID is not a directory"
APP_ROOT="$(cd "$REFINEID_ANDROID" && pwd -P)"
readonly APP_ROOT
readonly APP_AIDL_DIRECTORY="${APP_ROOT}/app/src/main/aidl/com/android/keychain/external"
readonly APP_JAVA_DIRECTORY="${APP_ROOT}/app/src/main/java/com/android/keychain/external"

extract_added_contract_file() {
  local contract_file="$1"
  local patch_marker
  patch_marker="diff --git a/${PATCH_SOURCE_DIRECTORY}/${contract_file} b/${PATCH_SOURCE_DIRECTORY}/${contract_file}"

  awk -v target="$patch_marker" -v exit_failure="$EXIT_FAILURE" '
    $0 == target {
      found = "yes"
      next
    }
    found && /^diff --git / {
      exit
    }
    found && /^\+\+\+ / {
      next
    }
    found && /^\+/ {
      sub(/^\+/, "")
      print
    }
    END {
      if (!found) {
        exit exit_failure
      }
    }
  ' "$PROVIDER_PATCH"
}

normalize_annotation_dialect() {
  sed \
    -e '/^import android\.annotation\./d' \
    -e '/^import androidx\.annotation\./d' \
    -e '/^[[:space:]]*prefix = {"FAILURE_"},$/d' |
    awk '
      NF == 0 && previousWasEmpty {
        next
      }
      {
        previousWasEmpty = (NF == 0)
        print
      }
    '
}

verify_exact_contract() {
  local contract_file="$1"
  local app_file="${APP_AIDL_DIRECTORY}/${contract_file}"

  [[ -f "$app_file" ]] || fail "application provider contract is missing ${contract_file}"
  if ! extract_added_contract_file "$contract_file" | cmp -s "$app_file" -; then
    fail "application and AOSP provider contracts differ: ${contract_file}"
  fi
}

verify_java_contract() {
  local contract_file="$1"
  local app_file="${APP_JAVA_DIRECTORY}/${contract_file}"
  local patch_contract
  local contracts_match=true

  [[ -f "$app_file" ]] || fail "application provider contract is missing ${contract_file}"
  patch_contract="$(mktemp "${TMPDIR:-/tmp}/refineid-provider-contract.XXXXXX")"
  extract_added_contract_file "$contract_file" | normalize_annotation_dialect >"$patch_contract"
  normalize_annotation_dialect <"$app_file" | cmp -s - "$patch_contract" || contracts_match=false
  rm -f "$patch_contract"
  [[ "$contracts_match" == true ]] ||
    fail "application and AOSP provider contracts differ: ${contract_file}"
}

[[ -f "$PROVIDER_PATCH" ]] || fail "AOSP provider patch is missing"

verify_exact_contract "ExternalKeyProviderIdentity.aidl"
verify_exact_contract "ExternalKeyProviderResult.aidl"
verify_exact_contract "IExternalKeyProviderService.aidl"
verify_java_contract "ExternalKeyProviderIdentity.java"
verify_java_contract "ExternalKeyProviderResult.java"

echo "aosp_provider_contract=verified"
