package fi.refineid.android.keychain

import android.app.Service
import android.content.Intent
import android.os.IBinder
import com.android.keychain.external.IExternalKeyProviderService
import fi.refineid.android.RefineIdApplication
import fi.refineid.android.diagnostics.AppTrace

/** Exact-component privileged service consumed only by the system KeyChain app. */
class ExternalKeyProviderService : Service() {
    private lateinit var providerBinder: ExternalKeyProviderBinder

    override fun onCreate() {
        super.onCreate()
        AppTrace.externalKeyProviderServiceCreated()
        val refineIdApplication = application as RefineIdApplication
        providerBinder =
            ExternalKeyProviderBinder(
                backend = refineIdApplication.externalKeyProviderRuntime.backend,
            )
    }

    override fun onBind(intent: Intent?): IBinder? {
        val isAccepted = intent?.action == PROVIDER_INTERFACE_ACTION
        AppTrace.externalKeyProviderServiceBound(isAccepted)
        return providerBinder.takeIf { isAccepted }
    }

    override fun onDestroy() {
        AppTrace.externalKeyProviderServiceDestroyed()
        super.onDestroy()
    }

    private companion object {
        val PROVIDER_INTERFACE_ACTION = IExternalKeyProviderService::class.java.name
    }
}
