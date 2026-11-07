package fi.refineid.android.keychain

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.foundation.text.input.TextObfuscationMode
import androidx.compose.foundation.text.input.clearText
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SecureTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import fi.refineid.android.R
import fi.refineid.android.RefineIdApplication
import fi.refineid.android.core.Pin1Submission
import fi.refineid.android.ui.Pin1InputTransformation
import fi.refineid.android.ui.RefineIdTheme
import fi.refineid.android.ui.UiAutomationIds

class ExternalKeyPinActivity : ComponentActivity() {
    private var promptId = NO_PROMPT_ID
    private var didComplete = false
    private lateinit var broker: ExternalKeyPinPromptBroker

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(
            WindowManager.LayoutParams.FLAG_SECURE or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
        )
        window.setHideOverlayWindows(true)
        setRecentsScreenshotEnabled(false)
        window.decorView.filterTouchesWhenObscured = true
        window.decorView.importantForAutofill =
            View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        window.decorView.importantForContentCapture =
            View.IMPORTANT_FOR_CONTENT_CAPTURE_NO_EXCLUDE_DESCENDANTS

        broker = (application as RefineIdApplication).pinPromptBroker
        promptId = intent.getLongExtra(EXTRA_PROMPT_ID, NO_PROMPT_ID)
        val prompt =
            broker.attachActivity(promptId) {
                if (!isFinishing) {
                    finish()
                }
            }
        if (prompt == null) {
            finish()
            return
        }

        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    cancelPrompt()
                }
            },
        )
        setContent {
            RefineIdTheme {
                ExternalKeyPinScreen(
                    callerLabel = prompt.callerLabel,
                    onSubmit = ::submitPin,
                    onCancel = ::cancelPrompt,
                )
            }
        }
    }

    override fun onStop() {
        if (!isChangingConfigurations && !didComplete) {
            cancelPrompt()
        }
        super.onStop()
    }

    override fun onDestroy() {
        if (::broker.isInitialized) {
            broker.detachActivity(promptId)
        }
        super.onDestroy()
    }

    private fun submitPin(pin1: Pin1Submission) {
        if (didComplete) {
            pin1.close()
            return
        }
        didComplete = true
        broker.submit(promptId, pin1)
        finish()
    }

    private fun cancelPrompt() {
        if (didComplete) {
            return
        }
        didComplete = true
        broker.cancel(promptId)
        finish()
    }

    companion object {
        internal fun intent(
            context: Context,
            promptId: Long,
        ): Intent =
            Intent(context, ExternalKeyPinActivity::class.java)
                .putExtra(EXTRA_PROMPT_ID, promptId)
                .addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP,
                )

        private const val EXTRA_PROMPT_ID =
            "fi.refineid.android.keychain.extra.PROMPT_ID"
        private const val NO_PROMPT_ID = 0L
    }
}

@Suppress("FunctionName", "ktlint:standard:function-naming")
@Composable
internal fun ExternalKeyPinScreen(
    callerLabel: String,
    onSubmit: (Pin1Submission) -> Unit,
    onCancel: () -> Unit,
) {
    val pinState = remember { TextFieldState() }
    DisposableEffect(pinState) {
        onDispose(pinState::clearText)
    }
    val submit = {
        val pin1 =
            if (Pin1Submission.isComplete(pinState.text)) {
                Pin1Submission.from(pinState.text)
            } else {
                null
            }
        pinState.clearText()
        pin1?.let(onSubmit)
        Unit
    }

    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .semantics { testTagsAsResourceId = true }
                .testTag(UiAutomationIds.EXTERNAL_KEY_PIN_SCREEN)
                .padding(
                    horizontal = SCREEN_HORIZONTAL_PADDING,
                    vertical = SCREEN_VERTICAL_PADDING,
                ),
        verticalArrangement = Arrangement.spacedBy(SCREEN_ITEM_SPACING),
    ) {
        Text(
            text = stringResource(R.string.authentication),
            style = MaterialTheme.typography.headlineLarge,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = callerLabel,
            modifier = Modifier.testTag(UiAutomationIds.EXTERNAL_KEY_CALLER),
            style = MaterialTheme.typography.bodyLarge,
        )
        SecureTextField(
            state = pinState,
            modifier =
                Modifier
                    .fillMaxWidth()
                    .testTag(UiAutomationIds.EXTERNAL_KEY_PIN1_FIELD),
            label = { Text(stringResource(R.string.pin1)) },
            inputTransformation = Pin1InputTransformation,
            textObfuscationMode = TextObfuscationMode.Hidden,
            keyboardOptions =
                KeyboardOptions(
                    autoCorrectEnabled = false,
                    keyboardType = KeyboardType.NumberPassword,
                    imeAction = ImeAction.Done,
                ),
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(ACTION_SPACING),
        ) {
            TextButton(
                onClick = onCancel,
                modifier =
                    Modifier
                        .weight(ACTION_WEIGHT)
                        .testTag(UiAutomationIds.EXTERNAL_KEY_CANCEL_ACTION),
            ) {
                Text(stringResource(R.string.cancel))
            }
            Button(
                onClick = submit,
                modifier =
                    Modifier
                        .weight(ACTION_WEIGHT)
                        .testTag(UiAutomationIds.EXTERNAL_KEY_AUTHENTICATE_ACTION),
                enabled = Pin1Submission.isComplete(pinState.text),
            ) {
                Text(stringResource(R.string.authenticate))
            }
        }
    }
}

private val SCREEN_HORIZONTAL_PADDING = 24.dp
private val SCREEN_VERTICAL_PADDING = 28.dp
private val SCREEN_ITEM_SPACING = 24.dp
private val ACTION_SPACING = 12.dp
private const val ACTION_WEIGHT = 1F
