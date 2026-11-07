# Agent handoff: Pixel 4 platform image

Date: 2026-08-16

Layout note: this series moved from refineid-android to refineid-hack on
2026-11-07. The commands below use the new layout, in which
`packages/apps/RefineID` is this directory and `REFINEID_ANDROID` names a
refineid-android checkout outside the AOSP tree; see [`BUILD.md`](BUILD.md).

This is the continuation record for another AI agent working on the Android
port. Treat the current repository, Linux builder, and physical device as the
authoritative state. Do not infer that the platform image or browser result is
complete from the successful module builds below.

## Public repository state

The public `main` branch contains the Android application, native card bridge,
document-signing work, AOSP patch series, build tooling, and physical-device
verifier. Before this handoff, the complete OpenJDK 26 `./gradlew check` gate
passed 98 tasks. That includes Android lint, Detekt, ShellCheck, release
network isolation, release no-logging verification, the provider wire-contract
comparison, Rust formatting and Clippy, and 49 Rust tests. A full-history
Gitleaks scan passed without a finding.

The device verifier now handles simultaneous USB and wireless ADB transports
for the same physical Pixel. It checks transport codenames, compares the
physical identity only in memory, chooses an ephemeral transport ID, and does
not print or persist an ADB serial or network endpoint. Both the build and
device verifier select AOSP's installed host `apksigner` explicitly instead of
an unusable intermediate wrapper.

## Linux builder state

The preserved AOSP tree is `/srv/refineid-aosp` on the x86_64 Linux builder.
It uses exact tag `android-13.0.0_r31`; the manifest checkout is commit
`012e197f31592b82d79ed2d4e03c5fb3ada38b62`. The framework, KeyChain, and
Pixel device patch series all pass `scripts/apply-aosp-patches.sh
--check-applied`.

The matching Google and Qualcomm Pixel 4 vendor archives were obtained. The
project owner explicitly authorized entering `I ACCEPT` for both vendor
agreements, both extraction programs completed, and 37 vendor files were
extracted. These required makefiles are present:

    vendor/google_devices/coral/proprietary/device-vendor.mk
    vendor/google_devices/flame/device-partial.mk
    vendor/qcom/flame/device-partial.mk

The vendor files remain only in the private AOSP worktree. Do not copy them
into this public repository.

The application release gate and provider-contract comparison pass on the
builder. The changed RefineID, KeyChain, framework, `KeystoreTests`,
`KeyChainTests`, and host `apksigner` modules built successfully. The product
RefineID APK, system KeyChain APK, and system framework JAR are present.
AOSP's installed host signer validates both APKs, and their signer digests
match; the digest was compared only in memory and was not recorded.

## Full image checkpoint

The full `aosp_flame-userdebug` image build reached 36,656 of 132,481 Ninja
actions (27%). It ran for 1 hour 42 minutes, reached a 23.7 GiB memory peak and
a 5.8 GiB swap peak, and had no compiler or packaging failure. It was stopped
cleanly with `systemctl stop` solely because the current agent was instructed
to preserve weekly execution credits and hand the work over. Ninja reported
an intentional user interruption. The transient service is inactive.

All Soong, Ninja, Gradle, Rust, and ccache outputs remain under the existing
tree and caches. The builder has approximately 593 GiB free. Do not delete or
resync the output tree; the next build is incremental.

The builder previously became temporarily unreachable because wired Ethernet
and Wi-Fi shared one LAN and the default weak-host ARP behavior caused a false
address-conflict response. Persistent interface-specific ARP settings were
installed and Wi-Fi power saving was disabled. The host did not reboot and the
build continued throughout that network interruption.

## Host compatibility fixes already made

Android 13's `build/envsetup.sh` and `lunch` are not nounset-safe. Both
`build-aosp-flame.sh` and `verify-aosp-flame-device.sh` now use `set +u` only
around AOSP environment evaluation and restore `set -u` immediately afterward.
Do not remove that transition.

Ubuntu 26.04 lacks the ncurses 5 ABI required by Android 13's legacy
RenderScript Clang. `scripts/configure-aosp-host-compat.sh` installs exact
AOSP-provided ncurses and tinfo ABI-5 libraries as root-owned mode-0644 files
under `/usr/local/lib`, refreshes the linker cache, and verifies the legacy
compiler. The installer is guarded and idempotent. Do not replace these with
symlinks into the user-writable source tree.

Eight direct Ninja jobs were stable. Setting `CCACHE_EXEC=/usr/bin/ccache`
regenerated the command graph and made the cold cache substantially slower, so
the stable configuration retains `USE_CCACHE=1` and `CCACHE_DIR` but does not
set `CCACHE_EXEC`.

## Exact resume step

First replace the builder's `packages/apps/RefineID` clone with this directory
as described in [`BUILD.md`](BUILD.md), fast-forward both checkouts, verify host
compatibility, then run the same build script against the preserved output
tree:

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

Do not start another clean build. The immediate milestone is the script's
`aosp_flame_image=ready` result. If it fails, diagnose the first terminal
error, make only the targeted correction, and rerun the same incremental
command.

## Still unverified

The following are not complete and must not be reported as passed:

- the complete boot, system, product, system-ext, vbmeta, dynamic-super, and
  related Pixel image artifact set;
- a successful uninterrupted full Soong image build;
- the installed-image, SELinux, privilege, signer, and on-device
  `KeystoreTests`/`KeyChainTests` checks in
  `scripts/verify-aosp-flame-device.sh`;
- booting the custom image on the Pixel 4;
- independent Chrome and Firefox client-certificate handshakes through the
  patched system KeyChain path; and
- authentication to Suomi.fi or another production relying party with a
  valid, non-revoked card.

After `aosp_flame_image=ready`, inspect every required image for nonzero size
and a current timestamp, rerun patch and manifest checks, verify both APKs with
the installed AOSP signer, and compare their signer digests without printing
them. Then update the documentation that still says the full image is pending.

Bootloader unlock and the first flash both erase device data. Neither has been
performed. Obtain a new, explicit owner decision before unlocking, wiping, or
flashing. After a permitted flash, pair the Linux builder with the new ADB
installation, run `verify-aosp-flame-device.sh`, and only then proceed to the
independent-browser matrix in `BROWSER-ACCEPTANCE.md`.

Never record, print, automate, or commit a PIN, PUK, CAN, private key, card or
device serial, personal certificate content, network endpoint, APDU trace, or
platform signing key. PIN entry remains a manual holder action. Do not consume
a card retry unless the recovery path is known.
