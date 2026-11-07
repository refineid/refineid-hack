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

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.util.Arrays;
import java.util.Objects;

/** One versioned public-identity snapshot returned by the trusted provider. */
public final class ExternalKeyProviderIdentity implements Parcelable, AutoCloseable {
    private static final int PARCEL_WIRE_VERSION = 1;
    private static final int NO_SPECIAL_CONTENTS = 0;
    private static final int NO_CERTIFICATE_LENGTH_BYTES = 0;
    private static final int RETURN_VALUE_WRITE_FLAG = Parcelable.PARCELABLE_WRITE_RETURN_VALUE;
    private static final int MINIMUM_CERTIFICATE_LENGTH_BYTES = 1;
    private static final long FIRST_PROVIDER_GENERATION = 1L;
    private static final byte CLEARED_BYTE = 0;

    private final long mProviderGeneration;
    private @Nullable byte[] mOwnedLeafCertificate;
    private @Nullable byte[] mOwnedCaCertificates;

    private ExternalKeyProviderIdentity(
            @NonNull byte[] ownedLeafCertificate,
            @Nullable byte[] ownedCaCertificates,
            long providerGeneration) {
        try {
            validate(ownedLeafCertificate, ownedCaCertificates, providerGeneration);
        } catch (RuntimeException exception) {
            clear(ownedLeafCertificate);
            clear(ownedCaCertificates);
            throw exception;
        }
        mOwnedLeafCertificate = ownedLeafCertificate;
        mOwnedCaCertificates = ownedCaCertificates;
        mProviderGeneration = providerGeneration;
    }

    /** Creates a snapshot with defensive copies of all certificate bytes. */
    @NonNull
    public static ExternalKeyProviderIdentity create(
            @NonNull byte[] leafCertificate,
            @Nullable byte[] caCertificates,
            long providerGeneration) {
        return new ExternalKeyProviderIdentity(
                Objects.requireNonNull(leafCertificate).clone(),
                caCertificates == null ? null : caCertificates.clone(),
                providerGeneration);
    }

    /** Returns a defensive copy of the encoded leaf certificate. */
    @NonNull
    public synchronized byte[] copyLeafCertificate() {
        return requireLeafCertificate().clone();
    }

    /** Returns a defensive copy of the concatenated CA certificates, if supplied. */
    @Nullable
    public synchronized byte[] copyCaCertificates() {
        requireOpen();
        return mOwnedCaCertificates == null ? null : mOwnedCaCertificates.clone();
    }

    public long getProviderGeneration() {
        return mProviderGeneration;
    }

    @Override
    public int describeContents() {
        return NO_SPECIAL_CONTENTS;
    }

    @Override
    public synchronized void writeToParcel(@NonNull Parcel destination, int flags) {
        try {
            destination.writeInt(PARCEL_WIRE_VERSION);
            destination.writeByteArray(requireLeafCertificate());
            destination.writeByteArray(mOwnedCaCertificates);
            destination.writeLong(mProviderGeneration);
        } finally {
            if ((flags & RETURN_VALUE_WRITE_FLAG) != NO_SPECIAL_CONTENTS) {
                close();
            }
        }
    }

    @Override
    public synchronized void close() {
        clear(mOwnedLeafCertificate);
        clear(mOwnedCaCertificates);
        mOwnedLeafCertificate = null;
        mOwnedCaCertificates = null;
    }

    @Override
    public synchronized String toString() {
        return "ExternalKeyProviderIdentity{"
                + "leafLength="
                + (mOwnedLeafCertificate == null
                        ? NO_CERTIFICATE_LENGTH_BYTES
                        : mOwnedLeafCertificate.length)
                + ", chainLength="
                + (mOwnedCaCertificates == null
                        ? NO_CERTIFICATE_LENGTH_BYTES
                        : mOwnedCaCertificates.length)
                + ", providerGeneration="
                + mProviderGeneration
                + ", closed="
                + (mOwnedLeafCertificate == null)
                + '}';
    }

    @NonNull
    public static final Parcelable.Creator<ExternalKeyProviderIdentity> CREATOR =
            new Parcelable.Creator<ExternalKeyProviderIdentity>() {
                @Override
                public ExternalKeyProviderIdentity createFromParcel(Parcel source) {
                    return readFromParcel(source);
                }

                @Override
                public ExternalKeyProviderIdentity[] newArray(int size) {
                    return new ExternalKeyProviderIdentity[size];
                }
            };

    @NonNull
    private static ExternalKeyProviderIdentity readFromParcel(@NonNull Parcel source) {
        int wireVersion = source.readInt();
        if (wireVersion != PARCEL_WIRE_VERSION) {
            throw new IllegalArgumentException("Unknown external identity wire version");
        }

        byte[] leafCertificate = null;
        byte[] caCertificates = null;
        boolean ownershipTransferred = false;
        try {
            leafCertificate = source.createByteArray();
            if (leafCertificate == null) {
                throw new IllegalArgumentException("External leaf certificate is absent");
            }
            caCertificates = source.createByteArray();
            ExternalKeyProviderIdentity identity =
                    new ExternalKeyProviderIdentity(
                            leafCertificate, caCertificates, source.readLong());
            ownershipTransferred = true;
            return identity;
        } finally {
            if (!ownershipTransferred) {
                clear(leafCertificate);
                clear(caCertificates);
            }
        }
    }

    private static void validate(
            @NonNull byte[] leafCertificate,
            @Nullable byte[] caCertificates,
            long providerGeneration) {
        if (leafCertificate.length < MINIMUM_CERTIFICATE_LENGTH_BYTES) {
            throw new IllegalArgumentException("External leaf certificate is empty");
        }
        if (caCertificates != null && caCertificates.length < MINIMUM_CERTIFICATE_LENGTH_BYTES) {
            throw new IllegalArgumentException("External CA certificate chain is empty");
        }
        if (providerGeneration < FIRST_PROVIDER_GENERATION) {
            throw new IllegalArgumentException("External provider generation must be positive");
        }
    }

    @NonNull
    private byte[] requireLeafCertificate() {
        byte[] leafCertificate = mOwnedLeafCertificate;
        if (leafCertificate == null) {
            throw new IllegalStateException("External provider identity is closed");
        }
        return leafCertificate;
    }

    private void requireOpen() {
        requireLeafCertificate();
    }

    private static void clear(@Nullable byte[] value) {
        if (value != null) {
            Arrays.fill(value, CLEARED_BYTE);
        }
    }
}
