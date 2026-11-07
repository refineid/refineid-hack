#!/usr/bin/env bash
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# Builds the unsigned, minimized RefineID release APK and stages it beside this
# directory's Android.bp. Requires the application repository: REFINEID_ANDROID
# must name a refineid-android checkout.

set -euo pipefail

readonly RELEASE_APK="app/build/outputs/apk/release/app-release-unsigned.apk"
readonly RELEASE_MERGED_MANIFEST="app/build/intermediates/merged_manifest/release/processReleaseMainManifest/AndroidManifest.xml"
readonly AOSP_PREBUILT_DIRECTORY="aosp-prebuilt"
readonly APK_SIGNATURE_ENTRY_PATTERN='^META-INF/[^/]+\.(RSA|DSA|EC|SF)$'
readonly PREBUILT_FILE_MODE="0644"
readonly RELEASE_PACKAGE='package="fi.refineid.android"'
readonly PROVIDER_SERVICE='android:name="fi.refineid.android.keychain.ExternalKeyProviderService"'
readonly KEYCHAIN_BIND_PERMISSION='android:permission="com.android.keychain.permission.BIND_EXTERNAL_KEY_PROVIDER"'
readonly PROVIDER_INTERFACE_ACTION='android:name="com.android.keychain.external.IExternalKeyProviderService"'
readonly BACKGROUND_ACTIVITY_PERMISSION='android:name="android.permission.START_ACTIVITIES_FROM_BACKGROUND"'
readonly INTERNET_PERMISSION="android.permission.INTERNET"
readonly CLEARTEXT_DISABLED='android:usesCleartextTraffic="false"'

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIRECTORY
SERIES_ROOT="$(cd "${SCRIPT_DIRECTORY}/.." && pwd -P)"
readonly SERIES_ROOT
readonly AOSP_PREBUILT_APK="${SERIES_ROOT}/${AOSP_PREBUILT_DIRECTORY}/RefineID.apk"

[[ -n "${REFINEID_ANDROID:-}" ]] || {
  echo "REFINEID_ANDROID must name a refineid-android checkout" >&2
  exit 1
}
[[ -x "${REFINEID_ANDROID}/gradlew" ]] || {
  echo "REFINEID_ANDROID does not contain the RefineID Gradle build" >&2
  exit 1
}
cd "$REFINEID_ANDROID"

# shellcheck source=/dev/null
source Scripts/gradle-environment.sh

./gradlew check

[[ -f "$RELEASE_APK" ]] || {
  echo "release APK was not produced" >&2
  exit 1
}
[[ -f "$RELEASE_MERGED_MANIFEST" ]] || {
  echo "release merged manifest was not produced" >&2
  exit 1
}

for required_manifest_entry in \
  "$RELEASE_PACKAGE" \
  "$PROVIDER_SERVICE" \
  "$KEYCHAIN_BIND_PERMISSION" \
  "$PROVIDER_INTERFACE_ACTION" \
  "$BACKGROUND_ACTIVITY_PERMISSION"; do
  grep -Fq "$required_manifest_entry" "$RELEASE_MERGED_MANIFEST" || {
    echo "release manifest is missing a required provider declaration" >&2
    exit 1
  }
done

if grep -Fq "$INTERNET_PERMISSION" "$RELEASE_MERGED_MANIFEST"; then
  echo "release manifest must not request Internet access" >&2
  exit 1
fi
if ! grep -Fq "$CLEARTEXT_DISABLED" "$RELEASE_MERGED_MANIFEST"; then
  echo "release manifest must explicitly disable cleartext traffic" >&2
  exit 1
fi

if unzip -Z1 "$RELEASE_APK" | grep -Eq "$APK_SIGNATURE_ENTRY_PATTERN"; then
  echo "release APK must remain unsigned for AOSP platform signing" >&2
  exit 1
fi

mkdir -p "${SERIES_ROOT}/${AOSP_PREBUILT_DIRECTORY}"
install -m "$PREBUILT_FILE_MODE" "$RELEASE_APK" "$AOSP_PREBUILT_APK"
echo "staged unsigned AOSP prebuilt at ${AOSP_PREBUILT_APK}"
