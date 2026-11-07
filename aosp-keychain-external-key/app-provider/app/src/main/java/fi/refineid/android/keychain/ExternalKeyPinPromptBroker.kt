package fi.refineid.android.keychain

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import fi.refineid.android.core.AuthenticationCardIdentity
import fi.refineid.android.core.AuthenticationPinCache
import fi.refineid.android.core.Pin1Submission
import fi.refineid.android.diagnostics.AppTrace
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

internal fun interface ExternalKeyCallerLabelResolver {
    fun resolve(caller: ExternalKeyCaller): String?
}

internal class AndroidExternalKeyCallerLabelResolver(
    private val packageManager: PackageManager,
) : ExternalKeyCallerLabelResolver {
    override fun resolve(caller: ExternalKeyCaller): String? {
        val expectedPackages = caller.copyPackageNames()
        val installedPackages =
            packageManager.getPackagesForUid(caller.uid.value)?.sorted()
                ?: return null
        if (installedPackages != expectedPackages) {
            return null
        }
        val primaryPackage = expectedPackages.first()
        val applicationInfo =
            try {
                packageManager.getApplicationInfo(
                    primaryPackage,
                    PackageManager.ApplicationInfoFlags.of(
                        PackageManager.GET_META_DATA.toLong(),
                    ),
                )
            } catch (_: PackageManager.NameNotFoundException) {
                return null
            }
        val label =
            packageManager
                .getApplicationLabel(applicationInfo)
                .toString()
                .filterNot(Char::isISOControl)
                .trim()
                .take(MAXIMUM_APPLICATION_LABEL_LENGTH)
                .ifEmpty { primaryPackage }
        return if (expectedPackages.size == SINGLE_PACKAGE_COUNT) {
            "$label\n$primaryPackage"
        } else {
            "$label\n" + expectedPackages.joinToString(separator = PACKAGE_SEPARATOR)
        }
    }

    private companion object {
        const val SINGLE_PACKAGE_COUNT = 1
        const val MAXIMUM_APPLICATION_LABEL_LENGTH = 80
        const val PACKAGE_SEPARATOR = ", "
    }
}

internal data class ExternalKeyPinPromptView(
    val id: Long,
    val callerLabel: String,
)

/**
 * Process-memory rendezvous between a synchronous KeyChain call and secure
 * UI. [cardFor] names the card that publishes a provider generation.
 */
internal class ExternalKeyPinPromptBroker(
    context: Context,
    private val callerLabelResolver: ExternalKeyCallerLabelResolver,
    private val pinCache: AuthenticationPinCache,
    private val cardFor: (ExternalKeyProviderGeneration) -> AuthenticationCardIdentity?,
    private val mainHandler: Handler = Handler(Looper.getMainLooper()),
    private val timeoutMilliseconds: Long = DEFAULT_TIMEOUT_MILLISECONDS,
) : ExternalKeyPinAuthorizer {
    private val applicationContext = context.applicationContext
    private val lock = Any()
    private var isClosed = false
    private var nextPromptId = FIRST_PROMPT_ID
    private var pendingPrompt: Prompt? = null

    init {
        require(timeoutMilliseconds >= MINIMUM_TIMEOUT_MILLISECONDS) {
            "external-key PIN prompt timeout must be positive"
        }
    }

    override fun authorize(request: ExternalKeySignRequest): ExternalKeyPinAuthorization {
        if (Looper.myLooper() == Looper.getMainLooper() || request.cancellation.isCancelled) {
            return ExternalKeyPinAuthorization.Interrupted
        }
        val callerLabel =
            callerLabelResolver.resolve(request.caller)
                ?: return ExternalKeyPinAuthorization.Unavailable
        // A PIN1 the holder already proved on the card that serves this
        // request authorizes the signature silently, so repeated signatures
        // stay silent while that card is held, as in the in-app browser path.
        // A PIN1 held for another card is not offered; the holder is asked.
        cardFor(request.providerGeneration)?.let(pinCache::take)?.let { storedPin ->
            return ExternalKeyPinAuthorization.Approved(storedPin)
        }
        val prompt =
            synchronized(lock) {
                if (isClosed || pendingPrompt != null) {
                    return ExternalKeyPinAuthorization.Unavailable
                }
                Prompt(
                    id = nextPromptId.also { nextPromptId += PROMPT_ID_INCREMENT },
                    callerLabel = callerLabel,
                ).also { createdPrompt -> pendingPrompt = createdPrompt }
            }
        val cancellationRegistration =
            request.cancellation.register {
                complete(prompt.id, ExternalKeyPinAuthorization.Interrupted)
            }
        dispatchPrompt(prompt)
        return try {
            prompt.completion.get(timeoutMilliseconds, TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) {
            complete(prompt.id, ExternalKeyPinAuthorization.TimedOut)
            prompt.completion.getNow(ExternalKeyPinAuthorization.TimedOut)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            complete(prompt.id, ExternalKeyPinAuthorization.Interrupted)
            prompt.completion
                .getNow(ExternalKeyPinAuthorization.Interrupted)
                .closeRejectedPin()
            ExternalKeyPinAuthorization.Interrupted
        } catch (_: ExecutionException) {
            complete(prompt.id, ExternalKeyPinAuthorization.Unavailable)
            ExternalKeyPinAuthorization.Unavailable
        } finally {
            cancellationRegistration.close()
            detach(prompt)
            finishPrompt(prompt)
        }
    }

    fun attachActivity(
        promptId: Long,
        finish: () -> Unit,
    ): ExternalKeyPinPromptView? =
        synchronized(lock) {
            pendingPrompt
                ?.takeIf { prompt -> prompt.id == promptId && !prompt.completion.isDone }
                ?.also { prompt -> prompt.finishActivity = finish }
                ?.toView()
        }

    fun detachActivity(promptId: Long) {
        synchronized(lock) {
            pendingPrompt
                ?.takeIf { prompt -> prompt.id == promptId }
                ?.finishActivity = null
        }
    }

    fun submit(
        promptId: Long,
        pin1: Pin1Submission,
    ) {
        complete(promptId, ExternalKeyPinAuthorization.Approved(pin1))
    }

    fun cancel(promptId: Long) {
        complete(promptId, ExternalKeyPinAuthorization.Cancelled)
    }

    override fun cancelPending() {
        val prompt = synchronized(lock) { pendingPrompt }
        if (prompt != null) {
            complete(prompt.id, ExternalKeyPinAuthorization.Interrupted)
        }
    }

    override fun close() {
        val prompt =
            synchronized(lock) {
                if (isClosed) {
                    return
                }
                isClosed = true
                pendingPrompt
            }
        if (prompt != null) {
            complete(prompt.id, ExternalKeyPinAuthorization.Unavailable)
        }
    }

    override fun toString(): String =
        synchronized(lock) {
            "ExternalKeyPinPromptBroker(" +
                "pending=" + (pendingPrompt != null) +
                ", closed=" + isClosed +
                ")"
        }

    private fun dispatchPrompt(prompt: Prompt) {
        mainHandler.post {
            val shouldStart =
                synchronized(lock) {
                    pendingPrompt === prompt && !prompt.completion.isDone && !isClosed
                }
            if (!shouldStart) {
                return@post
            }
            try {
                AppTrace.externalKeyPinPromptDispatched()
                applicationContext.startActivity(
                    ExternalKeyPinActivity.intent(applicationContext, prompt.id),
                )
            } catch (_: RuntimeException) {
                complete(prompt.id, ExternalKeyPinAuthorization.Unavailable)
            }
        }
    }

    private fun complete(
        promptId: Long,
        authorization: ExternalKeyPinAuthorization,
    ) {
        val prompt =
            synchronized(lock) {
                pendingPrompt?.takeIf { candidate -> candidate.id == promptId }
            }
        if (prompt == null || !prompt.completion.complete(authorization)) {
            authorization.closeRejectedPin()
            return
        }
        AppTrace.externalKeyPinPromptCompleted(authorization)
        finishPrompt(prompt)
    }

    private fun detach(prompt: Prompt) {
        synchronized(lock) {
            if (pendingPrompt === prompt) {
                pendingPrompt = null
            }
        }
    }

    private fun finishPrompt(prompt: Prompt) {
        val finish = synchronized(lock) { prompt.finishActivity }
        if (finish != null) {
            mainHandler.post(finish)
        }
    }

    private class Prompt(
        val id: Long,
        val callerLabel: String,
    ) {
        val completion = CompletableFuture<ExternalKeyPinAuthorization>()
        var finishActivity: (() -> Unit)? = null

        fun toView(): ExternalKeyPinPromptView =
            ExternalKeyPinPromptView(
                id = id,
                callerLabel = callerLabel,
            )
    }

    private companion object {
        const val MILLISECONDS_PER_SECOND = 1_000L
        const val MINIMUM_TIMEOUT_MILLISECONDS = 1L
        const val DEFAULT_TIMEOUT_SECONDS = 120L
        const val DEFAULT_TIMEOUT_MILLISECONDS =
            DEFAULT_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND
        const val FIRST_PROMPT_ID = 1L
        const val PROMPT_ID_INCREMENT = 1L
    }
}

private fun ExternalKeyPinAuthorization.closeRejectedPin() {
    if (this is ExternalKeyPinAuthorization.Approved) {
        pin1.close()
    }
}
