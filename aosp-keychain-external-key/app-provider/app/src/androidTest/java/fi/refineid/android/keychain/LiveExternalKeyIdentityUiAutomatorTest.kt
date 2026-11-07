package fi.refineid.android.keychain

import android.content.ComponentName
import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.uiAutomator
import fi.refineid.android.RefineIdApplication
import fi.refineid.android.ui.UiAutomationIds
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

@RunWith(AndroidJUnit4::class)
internal class LiveExternalKeyIdentityUiAutomatorTest {
    @Test
    fun liveCardPublishesACompleteVerifiedBrowserChainWithoutASecret() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(
            "enable the opt-in live external-key identity check",
            InstrumentationRegistry.getArguments().getString(LIVE_TEST_ARGUMENT) ==
                LIVE_TEST_ENABLED_VALUE,
        )
        val launchIntent =
            Intent
                .makeMainActivity(
                    ComponentName(
                        instrumentation.targetContext,
                        TARGET_ACTIVITY_CLASS,
                    ),
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        instrumentation.targetContext.startActivity(launchIntent)

        uiAutomator {
            waitForAppToBeVisible(TARGET_PACKAGE)
            val readerState =
                onElement(CARD_READY_TIMEOUT_MILLISECONDS) {
                    viewIdResourceName == UiAutomationIds.AUTHENTICATION_CARD ||
                        viewIdResourceName == UiAutomationIds.READER_ACTION
                }
            if (readerState.resourceName == UiAutomationIds.READER_ACTION) {
                readerState.click()
                onElement(USB_PERMISSION_TIMEOUT_MILLISECONDS) {
                    packageName?.toString() == SYSTEM_UI_PACKAGE &&
                        viewIdResourceName == ANDROID_CONFIRM_BUTTON_RESOURCE
                }.click()
            }
            onElement(CARD_READY_TIMEOUT_MILLISECONDS) {
                viewIdResourceName == UiAutomationIds.AUTHENTICATION_CARD
            }
        }

        val application =
            instrumentation.targetContext.applicationContext as RefineIdApplication
        val identity = application.externalKeyProviderRuntime.backend.copyActiveIdentity()
        assertNotNull("live card did not publish an external-key identity", identity)
        identity ?: return
        identity.use { ownedIdentity ->
            val leafEncoding = ownedIdentity.copyLeafCertificate()
            val issuerEncoding = checkNotNull(ownedIdentity.copyCaCertificates())
            try {
                val leaf = parseCertificate(leafEncoding)
                val issuers =
                    CertificateFactory
                        .getInstance(X509_CERTIFICATE_TYPE)
                        .generateCertificates(issuerEncoding.inputStream())
                assertEquals(EXPECTED_ISSUER_COUNT, issuers.size)
                val issuer = issuers.single() as X509Certificate
                assertTrue(
                    "external-key issuer must be a certificate authority",
                    issuer.basicConstraints >= CERTIFICATE_AUTHORITY_BASIC_CONSTRAINTS_MINIMUM,
                )
                assertTrue(
                    "external-key issuer name must match the card leaf",
                    issuer.subjectX500Principal == leaf.issuerX500Principal,
                )
                leaf.verify(issuer.publicKey)
            } finally {
                leafEncoding.fill(CLEARED_BYTE)
                issuerEncoding.fill(CLEARED_BYTE)
            }
        }
    }

    private fun parseCertificate(encoded: ByteArray): X509Certificate =
        CertificateFactory
            .getInstance(X509_CERTIFICATE_TYPE)
            .generateCertificate(encoded.inputStream()) as X509Certificate

    private companion object {
        const val TARGET_PACKAGE = "fi.refineid.android"
        const val TARGET_ACTIVITY_CLASS = "$TARGET_PACKAGE.MainActivity"
        const val SYSTEM_UI_PACKAGE = "com.android.systemui"
        const val ANDROID_CONFIRM_BUTTON_RESOURCE = "android:id/button1"
        const val LIVE_TEST_ARGUMENT = "refineidLiveExternalKeyIdentity"
        const val LIVE_TEST_ENABLED_VALUE = "true"
        const val X509_CERTIFICATE_TYPE = "X.509"
        const val CARD_READY_TIMEOUT_MILLISECONDS = 15_000L
        const val USB_PERMISSION_TIMEOUT_MILLISECONDS = 10_000L
        const val EXPECTED_ISSUER_COUNT = 1
        const val CERTIFICATE_AUTHORITY_BASIC_CONSTRAINTS_MINIMUM = 0
        const val CLEARED_BYTE: Byte = 0
    }
}
