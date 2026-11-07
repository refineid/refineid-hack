# Android 13 Pixel 4 platform patches

This is RefineID's experimental AOSP patch series: it lets the Android system
KeyChain sign TLS client authentication with the card, per
[ADR 0012](https://github.com/refineid/refineid-android/blob/main/doc/architecture/0012-platform-keychain-external-key.md)
of refineid-android. It moved here from refineid-android on 2026-11-07, taken
from its commit `e41ecd4a3d9371e9be206a868519f838fbcd5de9`. It works only on a
custom Pixel 4 image built from these patches and has nothing to do with the
RefineID app in the store. It is unmaintained unless someone picks it up.

This directory carries the RefineID AOSP patch series for the Pixel 4 build
`TP1A.221005.002.B2`, which maps to the AOSP tag `android-13.0.0_r31`.

The series currently contains the descriptor, exact-digest contract, full
SHA-2 RSA browser-process JCA vocabulary, KeyChain service-policy, native
chooser-discovery, caller-liveness, and trusted-provider binding steps from
ADR 0012. It preserves
the existing Keystore2 grant path and adds a non-exportable external key plus a
narrowly routed signature provider. KeyChain now owns a stable non-personal
alias, grant checks, caller-package resolution, active-generation checks,
validated certificate snapshots, signature-shape checks, and an exact-component
Binder connection guarded by a dedicated signature permission and install-time
trust checks. The native chooser unions active external aliases with
AndroidKeyStore aliases before applying its existing user-selectability,
key-type, and issuer filters. The application now supplies the privileged
provider service, generation-bound USB backend, secure PIN prompt with the
platform-only background launch permission, product priv-app import, and a
dedicated non-network SELinux domain.

## Upstream bases

| AOSP project | Base commit | Patch directory |
| --- | --- | --- |
| `platform/frameworks/base` | `9cc5d58d0254f472ae071b29ccf4fae93ca1cc3d` | `patches/frameworks-base` |
| `platform/packages/apps/KeyChain` | `97a7bc2ba75391487ecd3f23153cfb1ce293d6fe` | `patches/packages-apps-KeyChain` |
| `device/google/coral` | `64f0b208f174e5d8627c201424c00102ef433adb` | `patches/device-google-coral` |

Apply each directory's patches to its corresponding project with `git am`, in
filename order. New AIDL methods are appended to the interface so all existing
Binder transaction numbers remain unchanged.

For a guarded end-to-end checkout, vendor, patch, and build procedure, use
[`BUILD.md`](BUILD.md). `scripts/apply-aosp-patches.sh` verifies each pinned
base and cumulatively preflights every series before changing a checkout.
`scripts/audit-aosp-patches.sh` performs the same check against fresh sparse
upstream repositories without requiring a complete AOSP checkout; its
`--replay` mode also proves release staging, ordered `git am`, and exact final
trees.

After boot, `scripts/verify-aosp-flame-device.sh` proves the installed-image
and platform-test boundary without printing raw device test output. Independent
browser acceptance remains a separate physical gate described in
[`BROWSER-ACCEPTANCE.md`](BROWSER-ACCEPTANCE.md).

Place this directory at `packages/apps/RefineID` in the AOSP tree, point
`REFINEID_ANDROID` at a refineid-android checkout, and run
`scripts/stage-aosp-prebuilt.sh` before starting the platform build. The script
builds the application there and stages its minimized unsigned release APK at
the path consumed by `Android.bp`. Soong installs it under the product priv-app
directory and signs it with the build-local platform certificate. Never copy a
platform private key into this directory.

## Current verification

- All three project patch sequences pass sequential application checks and full
  `git am` replay in filename order from their exact upstream bases. The
  reproducible sparse audit uses about 20 MiB for preflight and 1.1 GiB for a
  release-staging replay with warm dependency caches.
- The framework AIDL interface generates with Build Tools 36; the only
  excluded warning is its pre-existing Android 13 interface-wide
  missing-permission annotation. The private provider AIDL generates with
  every warning treated as an error.
- New framework and KeyChain boundary sources compile as Java 11 with warnings
  treated as errors. The complete Android 13 KeyChain service compiles against
  an Android 13 hidden-API framework, excluding only warnings already present
  in the upstream service.
- Descriptor and signature-contract parcel round trips, defensive copies,
  close behavior, algorithm mappings, coarse failures, and sanitized string
  forms pass on the physical Android 13 Pixel 4 using synthetic values only.
- Framework-provider tests cover streaming SHA-256/SHA-384/SHA-512, all three
  fixed RSA-PSS profiles, strict Firefox-style `NoneWithRSA` and
  `NoneWithECDSA`, software-key provider
  fall-through, one-shot requests, generic failures, and non-exportability.
  The same JCA routing paths pass in Android's runtime on the physical Pixel 4
  with a synthetic signing operation; no reader or card command is involved.
- Thirteen service-manager tests cover certificate validation, defensive
  copies, alias publication, credential-policy ownership, generation and
  algorithm rejection, interrupted and closed requests, caller attribution,
  provider failures, output shape, removal, and sanitized string forms. An
  isolated Android-runtime harness on
  the physical Pixel accepts synthetic RSA and P-384 identities, rejects
  P-256, and exercises descriptor/signing/generation paths without a reader or
  card command.
- The modified native chooser and its tests compile against the Android 13
  hidden-API framework. A host semantic harness verifies external-certificate
  issuer and key-type filtering plus ordered, deduplicated alias merging.
- Seven host tests cover the bound-provider protocol, caller and liveness-token
  forwarding, coarse failure mapping, defensive copies, close behavior, and
  exact component, permission, privilege, UID, and signature trust checks.
  Android-runtime validation on the physical Pixel covers the opaque Binder
  token and provider parcelables, including sender-side byte clearing for
  returned values, using synthetic data only.
- Release staging proves that the independently compiled application and
  KeyChain copies of the private AIDL and parcelable wire contract remain
  identical, allowing only their required SDK annotation dialect difference.
- The app-side service and backend pass Kotlin unit tests and nine synthetic
  Binder, Compose UI, and manifest tests on the physical Pixel. The tests cover
  stale generations, card-session replacement, removal suppression, caller
  interruption, exact service permission, and private prompt exposure without
  submitting a card credential.
- The provider publishes the card leaf only with a fingerprint-pinned public
  issuing certificate whose name, CA profile, key usage, and signature match.
  A credential-free physical Pixel test verified that complete chain on the
  attached card, and the minimized release APK retains all four issuer
  resources.
- On the Linux builder, the patched RefineID, KeyChain, framework, test, and
  host signer modules build. The staged RefineID and KeyChain APKs validate
  with AOSP's installed host signer and have the same platform certificate.
- The app, framework, KeyChain, and product-integration diffs pass Gitleaks.
- The sparse audit has independently replayed the complete series from the
  pinned upstream tag.

The first complete Soong image build was intentionally stopped at 27%, with
all caches and outputs preserved. It had not reported a compile failure, but a
complete image and the on-device platform tests remain unverified. The exact
resume state and next command are recorded in [`BUILD.md`](BUILD.md). Android
11 and newer platform builds are unsupported on macOS; use the preserved
64-bit Linux builder for the remaining image and device-test stages.

Never add platform signing keys, device identifiers, network addresses, real
card material, credentials, or card traces to this directory.
