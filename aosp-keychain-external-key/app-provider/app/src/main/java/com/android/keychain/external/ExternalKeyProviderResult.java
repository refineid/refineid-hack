/*
 * Copyright (C) 2026 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.android.keychain.external;

import android.os.Parcel;
import android.os.Parcelable;

import androidx.annotation.IntDef;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.util.Arrays;
import java.util.Objects;

/** Versioned coarse result returned across the private provider boundary. */
public final class ExternalKeyProviderResult implements Parcelable, AutoCloseable {
    public static final int FAILURE_NONE = 0;
    public static final int FAILURE_INVALID_REQUEST = 1;
    public static final int FAILURE_PROVIDER_UNAVAILABLE = 2;
    public static final int FAILURE_PROVIDER_GENERATION_CHANGED = 3;
    public static final int FAILURE_USER_CANCELLED = 4;
    public static final int FAILURE_USER_TIMED_OUT = 5;
    public static final int FAILURE_CALLER_INTERRUPTED = 6;
    public static final int FAILURE_SIGNING_FAILED = 7;
    public static final int FAILURE_INTERNAL_ERROR = 8;

    @IntDef(
            value = {
                FAILURE_NONE,
                FAILURE_INVALID_REQUEST,
                FAILURE_PROVIDER_UNAVAILABLE,
                FAILURE_PROVIDER_GENERATION_CHANGED,
                FAILURE_USER_CANCELLED,
                FAILURE_USER_TIMED_OUT,
                FAILURE_CALLER_INTERRUPTED,
                FAILURE_SIGNING_FAILED,
                FAILURE_INTERNAL_ERROR
            })
    @Retention(RetentionPolicy.SOURCE)
    public @interface Failure {}

    private static final int PARCEL_WIRE_VERSION = 1;
    private static final int NO_SPECIAL_CONTENTS = 0;
    private static final int NO_SIGNATURE_LENGTH_BYTES = 0;
    private static final int MINIMUM_SIGNATURE_LENGTH_BYTES = 1;
    private static final int RETURN_VALUE_WRITE_FLAG = Parcelable.PARCELABLE_WRITE_RETURN_VALUE;
    private static final byte CLEARED_BYTE = 0;

    private final @Failure int mFailure;
    private @Nullable byte[] mOwnedSignature;

    private ExternalKeyProviderResult(@Failure int failure, @Nullable byte[] ownedSignature) {
        try {
            validate(failure, ownedSignature);
        } catch (RuntimeException exception) {
            clear(ownedSignature);
            throw exception;
        }
        mFailure = failure;
        mOwnedSignature = ownedSignature;
    }

    /** Creates a successful result with a defensive copy of the signature. */
    @NonNull
    public static ExternalKeyProviderResult success(@NonNull byte[] signature) {
        return new ExternalKeyProviderResult(
                FAILURE_NONE, Objects.requireNonNull(signature).clone());
    }

    /** Creates a failed result without a signature payload. */
    @NonNull
    public static ExternalKeyProviderResult failure(@Failure int failure) {
        return new ExternalKeyProviderResult(failure, null);
    }

    public boolean isSuccess() {
        return mFailure == FAILURE_NONE;
    }

    public @Failure int getFailure() {
        return mFailure;
    }

    /** Returns a defensive copy of a successful signature. */
    @NonNull
    public synchronized byte[] copySignature() {
        if (!isSuccess()) {
            throw new IllegalStateException("External provider result is a failure");
        }
        byte[] signature = mOwnedSignature;
        if (signature == null) {
            throw new IllegalStateException("External provider result is closed");
        }
        return signature.clone();
    }

    @Override
    public int describeContents() {
        return NO_SPECIAL_CONTENTS;
    }

    @Override
    public synchronized void writeToParcel(@NonNull Parcel destination, int flags) {
        try {
            destination.writeInt(PARCEL_WIRE_VERSION);
            destination.writeInt(mFailure);
            if (isSuccess() && mOwnedSignature == null) {
                throw new IllegalStateException("External provider result is closed");
            }
            destination.writeByteArray(mOwnedSignature);
        } finally {
            if ((flags & RETURN_VALUE_WRITE_FLAG) != NO_SPECIAL_CONTENTS) {
                close();
            }
        }
    }

    @Override
    public synchronized void close() {
        clear(mOwnedSignature);
        mOwnedSignature = null;
    }

    @Override
    public synchronized String toString() {
        return "ExternalKeyProviderResult{"
                + "failure="
                + failureName(mFailure)
                + ", signatureLength="
                + (mOwnedSignature == null ? NO_SIGNATURE_LENGTH_BYTES : mOwnedSignature.length)
                + ", closed="
                + (isSuccess() && mOwnedSignature == null)
                + '}';
    }

    @NonNull
    public static final Parcelable.Creator<ExternalKeyProviderResult> CREATOR =
            new Parcelable.Creator<ExternalKeyProviderResult>() {
                @Override
                public ExternalKeyProviderResult createFromParcel(Parcel source) {
                    return readFromParcel(source);
                }

                @Override
                public ExternalKeyProviderResult[] newArray(int size) {
                    return new ExternalKeyProviderResult[size];
                }
            };

    @NonNull
    private static ExternalKeyProviderResult readFromParcel(@NonNull Parcel source) {
        int wireVersion = source.readInt();
        if (wireVersion != PARCEL_WIRE_VERSION) {
            throw new IllegalArgumentException("Unknown external result wire version");
        }
        return new ExternalKeyProviderResult(source.readInt(), source.createByteArray());
    }

    private static void validate(@Failure int failure, @Nullable byte[] signature) {
        switch (failure) {
            case FAILURE_NONE:
                if (signature == null || signature.length < MINIMUM_SIGNATURE_LENGTH_BYTES) {
                    throw new IllegalArgumentException("Successful provider result is empty");
                }
                return;
            case FAILURE_INVALID_REQUEST:
            case FAILURE_PROVIDER_UNAVAILABLE:
            case FAILURE_PROVIDER_GENERATION_CHANGED:
            case FAILURE_USER_CANCELLED:
            case FAILURE_USER_TIMED_OUT:
            case FAILURE_CALLER_INTERRUPTED:
            case FAILURE_SIGNING_FAILED:
            case FAILURE_INTERNAL_ERROR:
                if (signature != null) {
                    throw new IllegalArgumentException("Failed provider result has a signature");
                }
                return;
            default:
                throw new IllegalArgumentException("Unknown external provider failure");
        }
    }

    @NonNull
    private static String failureName(@Failure int failure) {
        switch (failure) {
            case FAILURE_NONE:
                return "NONE";
            case FAILURE_INVALID_REQUEST:
                return "INVALID_REQUEST";
            case FAILURE_PROVIDER_UNAVAILABLE:
                return "PROVIDER_UNAVAILABLE";
            case FAILURE_PROVIDER_GENERATION_CHANGED:
                return "PROVIDER_GENERATION_CHANGED";
            case FAILURE_USER_CANCELLED:
                return "USER_CANCELLED";
            case FAILURE_USER_TIMED_OUT:
                return "USER_TIMED_OUT";
            case FAILURE_CALLER_INTERRUPTED:
                return "CALLER_INTERRUPTED";
            case FAILURE_SIGNING_FAILED:
                return "SIGNING_FAILED";
            case FAILURE_INTERNAL_ERROR:
                return "INTERNAL_ERROR";
            default:
                return "UNKNOWN";
        }
    }

    private static void clear(@Nullable byte[] value) {
        if (value != null) {
            Arrays.fill(value, CLEARED_BYTE);
        }
    }
}
