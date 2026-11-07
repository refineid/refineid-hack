# App-side KeyChain external-key provider

This is the application half of the AOSP KeyChain external-key experiment:
the service that the patched system KeyChain (`patches/`) binds to, the
holder's PIN prompt it raises, the replay lease, and the USB and NFC views
over the app's card sessions that it signs through. No stock Android build
ever binds this service, so it left the RefineID app on 2026-11-07, taken
from refineid-android at commit `f0e6243c63ba0c537f272ab42f987536cbd3f91d`
(the state before the removal, Android PR #220 having already moved the
patch series here).

It is unmaintained, like the rest of this directory. It compiles only
inside a RefineID app built for the patched image: the sources depend on the
app's `fi.refineid.android.core`, `usb`, `nfc`, `ui` and `diagnostics`
packages, on Android's AIDL support in the app module, and on a platform
image whose KeyChain carries the `com.android.keychain.external` contract.
Nothing here builds on its own, and nothing here has been built since the
move.

## Layout

The files keep their paths inside the app module, so the tree grafts onto a
refineid-android checkout with `cp -R app-provider/app <checkout>/`:

- `app/src/main/java/fi/refineid/android/keychain/`: the provider
  (`ExternalKeyProviderService`, `ExternalKeyProviderBinder`,
  `RefineIdExternalKeyProviderBackend` with `ExternalKeyProviderRuntime`,
  `ExternalKeySigningCoordinator`, `ExternalSignatureReplayLease`,
  `ScheduledExecutorReplayLeaseScheduler`, `ExternalKeyPinPromptBroker`,
  `ExternalKeyPinActivity`, `TransportSelectingCardSession`,
  `ExternalKeyCardSession`, `ExternalKeyProviderBackend`,
  `ExternalKeySignRequest`).
- `app/src/main/java/fi/refineid/android/usb/UsbExternalKeyCardSession.kt`
  and `app/src/main/java/fi/refineid/android/nfc/NfcExternalKeyCardSession.kt`:
  the blocking provider views over the worker-confined USB and contactless
  sessions (ADR 0035 of refineid-android).
- `app/src/main/aidl/com/android/keychain/external/` and
  `app/src/main/java/com/android/keychain/external/`: the app's copy of the
  private AIDL contract and its parcelables. `scripts/verify-aosp-provider-contract.sh`
  compares this copy with the KeyChain patch's copy.
- `app/src/test/java/fi/refineid/android/keychain/`: JVM unit tests of the
  coordinator, the replay lease, the backend and the transport selector.
- `app/src/androidTest/java/fi/refineid/android/keychain/`: instrumented
  tests of the Binder, the manifest, the PIN screen, and a live identity
  check that needs a card.
- `AndroidManifest-fragment.xml`: the manifest entries the app declared for
  the provider.
- `app-wiring.patch`: the hunks that wired the provider into the app, as a
  reverse of the removal diff; `git apply app-wiring.patch` from the app
  checkout's root restores them (expect offsets to drift as the app moves on).

## How it was wired

`RefineIdApplication.onCreate` built one `TransportSelectingCardSession` over
`readerController.externalKeyCardSession` and
`nfcReaderController.externalKeyCardSession`, then an
`ExternalKeyPinPromptBroker` (with `AndroidExternalKeyCallerLabelResolver`,
the app's `AuthenticationPinCache`, and a `cardFor` lambda mapping a provider
generation to the `AuthenticationCardIdentity` of that card's certificate),
and an `ExternalKeyProviderRuntime` over the session, the broker and the
app's `CaCertificateStore` as issuer source. Both were `internal lateinit`
properties (`pinPromptBroker`, `externalKeyProviderRuntime`), and
`onTerminate` closed the runtime. `ExternalKeyProviderService` reached the
runtime through the application object.

`UsbReaderController` and `NfcReaderController` each exposed an
`externalKeyCardSession` and minted a fresh provider generation
(`SecureRandom.nextProviderGeneration()`, from `ExternalKeySignRequest.kt`)
whenever they took a card session, clearing it when the session closed.
The NFC view's `onCardLost` callback closed the controller's session and
published `WAITING_FOR_CARD`.

`AppTrace` (debug and release variants) had nine `externalKey*` trace
points; `UiAutomationIds` had the five `EXTERNAL_KEY_*` test tags of the
PIN screen, and the string resource `authenticate` (every locale) existed
only for that screen's button; the other strings it used are shared. The app module enabled `buildFeatures.aidl`, and `.gitattributes`
kept `*.aidl` and the `com/android/keychain/external/*.java` copies at LF.

The manifest declared the unexported PIN activity, the exported provider
service behind `com.android.keychain.permission.BIND_EXTERNAL_KEY_PROVIDER`
with the `IExternalKeyProviderService` intent filter, and
`android.permission.START_ACTIVITIES_FROM_BACKGROUND` for the prompt
(ADR 0027); see `AndroidManifest-fragment.xml`.

`scripts/stage-aosp-prebuilt.sh`, `scripts/verify-aosp-provider-contract.sh`
and `scripts/verify-aosp-flame-device.sh` still expect all of this in the
app they stage and verify, so grafting these files back is the first step
of any attempt to build the image again.

## Design records

ADR 0012 (the integration), ADR 0027 (the background-launch permission) and
ADR 0035 (the transport-selecting session) in refineid-android's
`doc/architecture/` describe this code and carry status notes pointing here.
