package fi.refineid.android.keychain

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import fi.refineid.android.ui.RefineIdTheme
import fi.refineid.android.ui.UiAutomationIds
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ExternalKeyPinScreenInstrumentedTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun securePinEntryEnablesOneTerseAuthenticationAction() {
        var wasSubmitted = false
        var wasCancelled = false
        composeRule.setContent {
            RefineIdTheme {
                ExternalKeyPinScreen(
                    callerLabel = SYNTHETIC_CALLER_LABEL,
                    onSubmit = { pin1 ->
                        pin1.consume { bytes ->
                            assertArrayEquals(SYNTHETIC_PIN1_BYTES, bytes)
                        }
                        wasSubmitted = true
                    },
                    onCancel = { wasCancelled = true },
                )
            }
        }

        composeRule
            .onNodeWithTag(UiAutomationIds.EXTERNAL_KEY_PIN_SCREEN)
            .assertIsDisplayed()
        composeRule
            .onNodeWithTag(UiAutomationIds.EXTERNAL_KEY_CALLER)
            .assertIsDisplayed()
        composeRule
            .onNodeWithTag(UiAutomationIds.EXTERNAL_KEY_AUTHENTICATE_ACTION)
            .assertIsNotEnabled()
        composeRule
            .onNodeWithTag(UiAutomationIds.EXTERNAL_KEY_PIN1_FIELD)
            .performTextInput(SYNTHETIC_PIN1)
        composeRule
            .onNodeWithTag(UiAutomationIds.EXTERNAL_KEY_AUTHENTICATE_ACTION)
            .assertIsEnabled()
            .performClick()

        composeRule.runOnIdle {
            assertTrue(wasSubmitted)
            assertFalse(wasCancelled)
        }
    }

    @Test
    fun cancelActionDoesNotCreateAPinSubmission() {
        var wasSubmitted = false
        var wasCancelled = false
        composeRule.setContent {
            RefineIdTheme {
                ExternalKeyPinScreen(
                    callerLabel = SYNTHETIC_CALLER_LABEL,
                    onSubmit = { pin1 ->
                        pin1.close()
                        wasSubmitted = true
                    },
                    onCancel = { wasCancelled = true },
                )
            }
        }

        composeRule
            .onNodeWithTag(UiAutomationIds.EXTERNAL_KEY_CANCEL_ACTION)
            .assertIsDisplayed()
            .performClick()

        composeRule.runOnIdle {
            assertFalse(wasSubmitted)
            assertTrue(wasCancelled)
        }
    }

    private companion object {
        const val SYNTHETIC_CALLER_LABEL = "Example Browser\ncom.example.browser"
        const val SYNTHETIC_PIN1 = "1357"
        val SYNTHETIC_PIN1_BYTES = SYNTHETIC_PIN1.encodeToByteArray()
    }
}
