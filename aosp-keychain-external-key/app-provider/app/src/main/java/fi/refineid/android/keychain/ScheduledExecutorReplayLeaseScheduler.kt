package fi.refineid.android.keychain

import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/** Production adapter; the owning privileged service controls executor lifetime. */
internal class ScheduledExecutorReplayLeaseScheduler(
    private val executor: ScheduledExecutorService,
) : ReplayLeaseScheduler {
    override fun schedule(
        delayMilliseconds: Long,
        action: () -> Unit,
    ): ReplayLeaseCancellation {
        val future =
            executor.schedule(
                action,
                delayMilliseconds,
                TimeUnit.MILLISECONDS,
            )
        return ReplayLeaseCancellation {
            future.cancel(MAY_INTERRUPT_IF_RUNNING)
        }
    }

    private companion object {
        const val MAY_INTERRUPT_IF_RUNNING = false
    }
}
