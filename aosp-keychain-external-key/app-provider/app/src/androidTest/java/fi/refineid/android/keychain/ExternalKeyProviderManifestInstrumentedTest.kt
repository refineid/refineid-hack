package fi.refineid.android.keychain

import android.content.ComponentName
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import fi.refineid.android.MainActivity
import fi.refineid.android.RefineIdApplication
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ExternalKeyProviderManifestInstrumentedTest {
    @Test
    fun providerIsExactlyExportedBehindTheKeyChainSignaturePermission() {
        val context = ApplicationProvider.getApplicationContext<RefineIdApplication>()
        val packageManager = context.packageManager
        val component = ComponentName(context, ExternalKeyProviderService::class.java)
        val serviceInfo =
            packageManager.getServiceInfo(
                component,
                PackageManager.ComponentInfoFlags.of(NO_PACKAGE_MANAGER_FLAGS),
            )

        assertTrue(serviceInfo.exported)
        assertTrue(serviceInfo.enabled)
        assertEquals(KEYCHAIN_BIND_PERMISSION, serviceInfo.permission)

        val matchingServices =
            packageManager.queryIntentServices(
                Intent(PROVIDER_INTERFACE_ACTION).setPackage(context.packageName),
                PackageManager.ResolveInfoFlags.of(NO_PACKAGE_MANAGER_FLAGS),
            )
        assertEquals(
            listOf(component),
            matchingServices.map { result ->
                ComponentName(result.serviceInfo.packageName, result.serviceInfo.name)
            },
        )
    }

    @Test
    fun pinPromptIsPrivateExcludedFromRecentsAndDeclaresItsPlatformLaunchPermission() {
        val context = ApplicationProvider.getApplicationContext<RefineIdApplication>()
        val activityInfo =
            context.packageManager.getActivityInfo(
                ComponentName(context, ExternalKeyPinActivity::class.java),
                PackageManager.ComponentInfoFlags.of(NO_PACKAGE_MANAGER_FLAGS),
            )

        assertFalse(activityInfo.exported)
        assertTrue(activityInfo.flags and ActivityInfo.FLAG_EXCLUDE_FROM_RECENTS != NO_ACTIVITY_FLAGS)
        assertTrue(activityInfo.flags and ActivityInfo.FLAG_FINISH_ON_TASK_LAUNCH != NO_ACTIVITY_FLAGS)
        val packageInfo =
            context.packageManager.getPackageInfo(
                context.packageName,
                PackageManager.PackageInfoFlags.of(PackageManager.GET_PERMISSIONS.toLong()),
            )
        val permissions = requireNotNull(packageInfo.requestedPermissions).toSet()
        assertTrue(permissions.contains(HIDE_OVERLAY_WINDOWS_PERMISSION))
        assertTrue(permissions.contains(START_BACKGROUND_ACTIVITIES_PERMISSION))
    }

    @Test
    fun activitiesSurviveARotationWithTheirTypedEntry() {
        val context = ApplicationProvider.getApplicationContext<RefineIdApplication>()
        for (activity in listOf(MainActivity::class.java, ExternalKeyPinActivity::class.java)) {
            val activityInfo =
                context.packageManager.getActivityInfo(
                    ComponentName(context, activity),
                    PackageManager.ComponentInfoFlags.of(NO_PACKAGE_MANAGER_FLAGS),
                )
            assertEquals(activity.simpleName, ROTATION_CONFIG_CHANGES, activityInfo.configChanges)
        }
    }

    @Test
    fun theMainScreenRequestsPortraitAndThePinPromptFollowsTheActivityBeneath() {
        val context = ApplicationProvider.getApplicationContext<RefineIdApplication>()
        val requested =
            listOf(
                MainActivity::class.java to ActivityInfo.SCREEN_ORIENTATION_PORTRAIT,
                ExternalKeyPinActivity::class.java to ActivityInfo.SCREEN_ORIENTATION_BEHIND,
            )
        for ((activity, orientation) in requested) {
            val activityInfo =
                context.packageManager.getActivityInfo(
                    ComponentName(context, activity),
                    PackageManager.ComponentInfoFlags.of(NO_PACKAGE_MANAGER_FLAGS),
                )
            assertEquals(activity.simpleName, orientation, activityInfo.screenOrientation)
        }
    }

    private companion object {
        const val ROTATION_CONFIG_CHANGES =
            ActivityInfo.CONFIG_ORIENTATION or
                ActivityInfo.CONFIG_SCREEN_SIZE or
                ActivityInfo.CONFIG_SCREEN_LAYOUT or
                ActivityInfo.CONFIG_SMALLEST_SCREEN_SIZE or
                ActivityInfo.CONFIG_DENSITY or
                ActivityInfo.CONFIG_KEYBOARD_HIDDEN
        const val KEYCHAIN_BIND_PERMISSION =
            "com.android.keychain.permission.BIND_EXTERNAL_KEY_PROVIDER"
        const val PROVIDER_INTERFACE_ACTION =
            "com.android.keychain.external.IExternalKeyProviderService"
        const val HIDE_OVERLAY_WINDOWS_PERMISSION = "android.permission.HIDE_OVERLAY_WINDOWS"
        const val START_BACKGROUND_ACTIVITIES_PERMISSION =
            "android.permission.START_ACTIVITIES_FROM_BACKGROUND"
        const val NO_PACKAGE_MANAGER_FLAGS = 0L
        const val NO_ACTIVITY_FLAGS = 0
    }
}
