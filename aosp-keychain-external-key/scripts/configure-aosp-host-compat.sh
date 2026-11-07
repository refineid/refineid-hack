#!/usr/bin/env bash
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# Installs the ncurses 5 host libraries that Android 13's legacy RenderScript
# Clang needs, copied from the AOSP checkout named by the argument.

set -euo pipefail
export LC_ALL=C

readonly EXIT_FAILURE=1
readonly EXIT_USAGE=2
readonly EXPECTED_ARGUMENT_COUNT=1
readonly REQUIRED_ROOT_USER_ID=0
readonly EXPECTED_HOST_KERNEL="Linux"
readonly EXPECTED_HOST_ARCHITECTURE="x86_64"
readonly HOST_LIBRARY_DIRECTORY="/usr/local/lib"
readonly HOST_LIBRARY_DIRECTORY_MODE="0755"
readonly HOST_LIBRARY_FILE_MODE="0644"
readonly NCURSES_LIBRARY_NAME="libncurses.so.5"
readonly TINFO_LIBRARY_NAME="libtinfo.so.5"
readonly NCURSES_SOURCE_FILE_NAME="libncurses.so.5.9"
readonly TINFO_SOURCE_FILE_NAME="libtinfo.so.5.9"
readonly AOSP_HOST_SYSROOT_RELATIVE_PATH="prebuilts/gcc/linux-x86/host/x86_64-linux-glibc2.17-4.8/sysroot/usr/lib"
readonly LEGACY_CLANG_RELATIVE_PATH="prebuilts/clang/host/linux-x86/clang-3289846/bin/clang.real"

fail() {
  echo "$1" >&2
  exit "$EXIT_FAILURE"
}

install_compatibility_library() {
  local source_path="$1"
  local destination_path="$2"

  [[ -f "$source_path" ]] || fail "AOSP host compatibility library is missing: ${source_path}"
  if [[ -e "$destination_path" || -L "$destination_path" ]]; then
    [[ -f "$destination_path" && ! -L "$destination_path" ]] ||
      fail "host compatibility destination is not a regular file: ${destination_path}"
    cmp -s "$source_path" "$destination_path" ||
      fail "host compatibility destination contains different bytes: ${destination_path}"
    return
  fi
  install -m "$HOST_LIBRARY_FILE_MODE" "$source_path" "$destination_path"
}

if [[ "$#" -ne "$EXPECTED_ARGUMENT_COUNT" ]]; then
  echo "usage: sudo $0 AOSP_ROOT" >&2
  exit "$EXIT_USAGE"
fi
[[ "$EUID" -eq "$REQUIRED_ROOT_USER_ID" ]] || fail "run this script with sudo"
[[ "$(uname -s)" == "$EXPECTED_HOST_KERNEL" ]] || fail "AOSP host compatibility requires Linux"
[[ "$(uname -m)" == "$EXPECTED_HOST_ARCHITECTURE" ]] ||
  fail "AOSP host compatibility requires x86_64"
DYNAMIC_LINKER_CACHE_TOOL="$(command -v ldconfig || true)"
readonly DYNAMIC_LINKER_CACHE_TOOL
[[ -x "$DYNAMIC_LINKER_CACHE_TOOL" ]] || fail "ldconfig is unavailable"

[[ -d "$1" ]] || fail "AOSP root is not a directory"
AOSP_ROOT="$(cd "$1" && pwd -P)"
readonly AOSP_ROOT
readonly HOST_SYSROOT="${AOSP_ROOT}/${AOSP_HOST_SYSROOT_RELATIVE_PATH}"
readonly LEGACY_CLANG="${AOSP_ROOT}/${LEGACY_CLANG_RELATIVE_PATH}"
readonly NCURSES_SOURCE="${HOST_SYSROOT}/${NCURSES_SOURCE_FILE_NAME}"
readonly TINFO_SOURCE="${HOST_SYSROOT}/${TINFO_SOURCE_FILE_NAME}"
readonly NCURSES_DESTINATION="${HOST_LIBRARY_DIRECTORY}/${NCURSES_SOURCE_FILE_NAME}"
readonly TINFO_DESTINATION="${HOST_LIBRARY_DIRECTORY}/${TINFO_SOURCE_FILE_NAME}"
readonly NCURSES_SONAME_PATH="${HOST_LIBRARY_DIRECTORY}/${NCURSES_LIBRARY_NAME}"
readonly TINFO_SONAME_PATH="${HOST_LIBRARY_DIRECTORY}/${TINFO_LIBRARY_NAME}"

[[ -x "$LEGACY_CLANG" ]] || fail "AOSP legacy Clang is missing"
install -d -m "$HOST_LIBRARY_DIRECTORY_MODE" "$HOST_LIBRARY_DIRECTORY"
install_compatibility_library "$NCURSES_SOURCE" "$NCURSES_DESTINATION"
install_compatibility_library "$TINFO_SOURCE" "$TINFO_DESTINATION"
"$DYNAMIC_LINKER_CACHE_TOOL"
[[ "$(readlink -f "$NCURSES_SONAME_PATH")" == "$NCURSES_DESTINATION" ]] ||
  fail "ncurses compatibility library was not registered"
[[ "$(readlink -f "$TINFO_SONAME_PATH")" == "$TINFO_DESTINATION" ]] ||
  fail "tinfo compatibility library was not registered"
"$LEGACY_CLANG" --version >/dev/null 2>&1 || fail "AOSP legacy Clang remains unavailable"

echo "aosp_host_compatibility=ready"
