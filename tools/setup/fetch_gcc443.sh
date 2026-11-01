#!/usr/bin/env bash
# Fetch the historical ARM toolchain that built the original engine.
#
# The Premium engine's .comment says "GCC: (GNU) 4.4.3"; its ELF attributes say
# ARM v7-A / VFPv3 / NEON optional / softfp.  Android NDK r8e is the last NDK
# that ships arm-linux-androideabi-gcc 4.4.3, and it runs on current x86_64
# Linux hosts without an emulator.
#
# Everything lands in the git-ignored .cache/toolchains directory.  Nothing is
# installed system-wide, nothing needs root, and the download is verified
# against a pinned SHA-1 before it is unpacked.
set -euo pipefail

VERSION=android-ndk-r8e
ARCHIVE=${VERSION}-linux-x86_64.tar.bz2
URL="https://dl.google.com/android/ndk/${ARCHIVE}"
# SHA-1 published alongside the r8e release; verified on fetch 2026-11-02.
EXPECTED_SHA1=5a2f85ac665e34e3d27199ed25b7585f3f2f488b

PROJECT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
CACHE="$PROJECT/.cache/toolchains"
GXX="$CACHE/$VERSION/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++"

mkdir -p "$CACHE"

if [ -x "$GXX" ]; then
    echo "already present: $("$GXX" --version | head -1)"
    echo "$GXX"
    exit 0
fi

echo "downloading $URL (about 450 MB)"
curl -fsSL --retry 3 -o "$CACHE/$ARCHIVE.part" "$URL"
echo "verifying SHA-1"
actual=$(sha1sum "$CACHE/$ARCHIVE.part" | cut -d' ' -f1)
if [ "$actual" != "$EXPECTED_SHA1" ]; then
    echo "checksum mismatch: got $actual, expected $EXPECTED_SHA1" >&2
    rm -f "$CACHE/$ARCHIVE.part"
    exit 1
fi
mv "$CACHE/$ARCHIVE.part" "$CACHE/$ARCHIVE"

echo "extracting into $CACHE"
tar xjf "$CACHE/$ARCHIVE" -C "$CACHE"

if ! [ -x "$GXX" ]; then
    echo "extraction did not produce the expected compiler" >&2
    exit 1
fi
echo "$("$GXX" --version | head -1)"
echo "$GXX"
