# Build the Pixel 4 image

This runbook builds the pinned Android 13 `aosp_flame-userdebug` image. It does
not download separately licensed vendor files, unlock a bootloader, or flash a
device.

## Builder

Use an x86_64 Linux host. Before checkout, provide at least 400 GB of free
space; after checkout, the build script requires 150 GiB to remain free. Google
recommends 64 GiB of RAM. These are the current
[AOSP workstation requirements](https://source.android.com/docs/setup/start/requirements).

The RefineID Gradle build requires OpenJDK 26 through `JAVA_HOME`. After staging
the app, the build script clears `JAVA_HOME` so AOSP can select its pinned Java
toolchain.

Android 13's legacy RenderScript compiler requires the ncurses 5 host ABI.
Current Linux distributions such as Ubuntu 26.04 may provide only ncurses 6.
After syncing AOSP, install the matching compatibility libraries already
contained in the pinned AOSP tree:

    sudo packages/apps/RefineID/scripts/configure-aosp-host-compat.sh "$PWD"

The script refuses conflicting library files, installs root-owned copies of
only AOSP's matching 64-bit ncurses and tinfo libraries under `/usr/local/lib`,
refreshes the dynamic linker cache, and verifies the legacy compiler. The image
build also checks the compiler before starting the Gradle or platform builds.

Patch applicability can be checked on any current Git host without a full AOSP
checkout:

    scripts/audit-aosp-patches.sh

The audit creates sparse temporary checkouts of only the exact upstream files
touched by the series. `--replay` additionally runs the complete release gate,
stages the unsigned APK, replays every `git am`, and compares the resulting
trees with the expected patch trees. Replay requires the Android SDK, JDK 26
and `REFINEID_ANDROID` naming a refineid-android checkout, but not the
proprietary Pixel archives.

## Source

Create and sync the exact AOSP tag:

    repo init \
      -u https://android.googlesource.com/platform/manifest \
      -b android-13.0.0_r31
    repo sync -c --no-tags

The tag maps to Pixel build `TP1A.221005.002.B2` in Google's
[build-number table](https://source.android.com/docs/setup/reference/build-numbers).

## Pixel binaries

A physical Pixel build needs the matching proprietary binaries. Download both
Pixel 4 (`flame`) archives from Google's
[driver-binary page](https://developers.google.com/android/drivers/), verify
their SHA-256 digests, unpack them at the AOSP root, and run each extraction
script. The extraction scripts present their respective licenses.

| Archive | SHA-256 |
| --- | --- |
| [`google_devices-flame-tp1a.221005.002.b2-22399ead.tgz`](https://dl.google.com/dl/android/aosp/google_devices-flame-tp1a.221005.002.b2-22399ead.tgz) | `fc2bc6cd2fde8d96641d624d68260b5392ad62e3d031d81659fad21d654f57e0` |
| [`qcom-flame-tp1a.221005.002.b2-358af558.tgz`](https://dl.google.com/dl/android/aosp/qcom-flame-tp1a.221005.002.b2-358af558.tgz) | `4793dcccf3c61593110ce35efafa5f60064fcc84f4921ed79be676027b33ee03` |

After extraction, these files must exist:

    vendor/google_devices/coral/proprietary/device-vendor.mk
    vendor/google_devices/flame/device-partial.mk
    vendor/qcom/flame/device-partial.mk

The binaries and generated `vendor/` trees are not part of RefineID and must
not be copied into this public repository.

## RefineID and patches

Place this directory at the fixed product path `packages/apps/RefineID`, for
example by cloning refineid-hack beside the AOSP tree and linking it:

    git clone https://github.com/refineid/refineid-hack ../refineid-hack
    ln -s "$PWD/../refineid-hack/aosp-keychain-external-key" \
      packages/apps/RefineID

Clone the application repository outside the AOSP tree and export its path;
the staging step builds the unsigned APK from it:

    git clone https://github.com/refineid/refineid-android ../refineid-android
    export REFINEID_ANDROID="$PWD/../refineid-android"

From the AOSP root, first verify all pinned bases without changing them:

    packages/apps/RefineID/scripts/apply-aosp-patches.sh \
      --check-base "$PWD"

Configure a Git committer identity before this check; applying the patch series
creates local AOSP commits.

Then build the unsigned, minimized app and apply all patch series:

    packages/apps/RefineID/scripts/apply-aosp-patches.sh "$PWD"

The command preflights every complete series in a temporary Git index before
modifying any AOSP project. It stops on a dirty or mismatched checkout.

## Build

With `JAVA_HOME` still selecting OpenJDK 26, run:

    packages/apps/RefineID/scripts/build-aosp-flame.sh "$PWD"

The script verifies the exact final patch trees and vendor files, stages the
unsigned release APK, builds the changed framework modules plus `KeystoreTests`
and `KeyChainTests`, builds the full image, and checks that the installed
RefineID and KeyChain APKs share the platform signer. It also refuses an AOSP
tree whose manifest checkout is not the exact `android-13.0.0_r31` commit.

## Current Linux-builder handoff

The continuation checklist for another AI agent is
[`AGENT-HANDOFF.md`](AGENT-HANDOFF.md). The operational summary follows.

The 2026-08-16 builder state is preserved under `/srv/refineid-aosp`:

- the manifest is the exact pinned commit, and all framework, KeyChain, and
  Pixel device patches pass `--check-applied`;
- the matching Google and Qualcomm Pixel 4 packages were accepted by the
  project owner and extracted, all three required vendor makefiles are
  present, and no vendor file is in this repository;
- the application release gate, provider-contract comparison, changed runtime
  modules, `KeystoreTests`, and `KeyChainTests` built successfully;
- the product RefineID APK, system KeyChain APK, and system framework JAR are
  present; AOSP's installed host `apksigner` validates both APKs and their
  platform signer digests match without recording the digest; and
- the public application checkout passed its 98-task `check` gate, including
  Android lint, Detekt, ShellCheck, release network and no-logging checks, and
  49 Rust tests.

The complete image build was intentionally stopped, not failed, at
36,656 of 132,481 Ninja actions (27%). It had run for 1 hour 42 minutes with a
23.7 GiB memory peak and a 5.8 GiB swap peak. The builder retains the Soong,
Ninja, Gradle, Rust, and ccache outputs and has approximately 593 GiB free.
No complete flashable image has been claimed or verified.

Two host compatibility problems have already been resolved. Android 13's
`envsetup.sh` and `lunch` are not safe under `set -u`, so the build and device
verification scripts suspend nounset only while evaluating the AOSP
environment and restore it immediately afterward. Ubuntu 26.04 also lacks the
legacy ncurses 5 ABI used by RenderScript Clang; the guarded compatibility
installer copies AOSP's exact libraries as root-owned system files and verifies
that compiler before a build starts.

The preserved tree predates the move of this series to refineid-hack: its
`packages/apps/RefineID` is a refineid-android clone. Before resuming, move
that clone beside the AOSP tree as `../refineid-android`, place this directory
at `packages/apps/RefineID` as described above, fast-forward both checkouts,
run the compatibility installer once to verify the host, then reuse the
preserved output tree with eight direct Ninja jobs. Do not set `CCACHE_EXEC`; forcing the cold compiler
cache into the command graph was slower than the direct build.

    cd /srv/refineid-aosp
    git -C ../refineid-hack pull --ff-only
    git -C ../refineid-android pull --ff-only
    sudo packages/apps/RefineID/scripts/configure-aosp-host-compat.sh "$PWD"

    export REFINEID_ANDROID="$PWD/../refineid-android"
    export JAVA_HOME=/usr/lib/jvm/java-26-openjdk-amd64
    export ANDROID_HOME=/srv/refineid-tools/android-sdk
    export ANDROID_SDK_ROOT="$ANDROID_HOME"
    export CARGO_HOME=/srv/refineid-tools/cargo
    export RUSTUP_HOME=/srv/refineid-tools/rustup
    export GRADLE_USER_HOME=/srv/refineid-cache/gradle
    export CCACHE_DIR=/srv/refineid-cache/ccache
    export USE_CCACHE=1
    export NINJA_ARGS=-j8
    export PATH="$CARGO_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
    packages/apps/RefineID/scripts/build-aosp-flame.sh "$PWD"

The next concrete milestone is `aosp_flame_image=ready`, followed by explicit
inspection of every required Pixel image, `verify-aosp-flame-device.sh`, and
only then a separate owner decision about bootloader unlock, wipe, and flash.
After boot, pair the Linux builder with the device, run the platform tests, and
continue to independent Chrome and Firefox acceptance. No bootloader unlock,
wipe, flash, or production relying-party login has been performed.

After the patched image is running, verify it from the same clean Linux
checkout:

    packages/apps/RefineID/scripts/verify-aosp-flame-device.sh "$PWD"

The verifier checks the exact source and manifest revisions, device build,
installed product paths and bytes, platform signer relationship, privileged
regular-UID package state, permissions, provider discovery, enforcing
`refineid_app` SELinux domain, and every new `KeystoreTests` and
`KeyChainTests` class. It suppresses raw `atest` output so a device identifier
does not enter captured build output.

This is the platform gate, not the browser result. Continue with
[`BROWSER-ACCEPTANCE.md`](BROWSER-ACCEPTANCE.md) for the independent Chrome,
Firefox, and valid-card service checks.

## Flash gate

The bootloader must be unlocked before a custom image can be flashed. Unlocking
requires physical confirmation and erases all user data. The first full flash
also uses a data wipe. See Google's
[Fastboot instructions](https://source.android.com/docs/setup/test/running).

Keep flashing outside the build scripts. Before it is authorized, confirm a
recoverable device backup, the matching factory image, OEM unlocking, and a
direct host USB data connection. Wireless ADB and the card-reader connection
are not available in bootloader mode.
