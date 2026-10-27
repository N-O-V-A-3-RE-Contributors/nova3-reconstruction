#pragma once

#include <cstdint>
#include <jni.h>

// Only the two observed static dependencies are declared here. Their bodies
// are outside this recovery; no billing implementation or Java service is
// supplied. int preserves the return register forwarded by both A32 veneers;
// the original source-level return type of clearItemList remains uncertain.
class InAppBilling {
public:
    static int clearItemList();
    static int getState();
};

extern "C" int InAppBilling_ClearItemList();
extern "C" int InAppBilling_GetState();

// Recovered output-parameter interface (source-level void return INFERRED).
// output must be writable/aligned; input contains length readable bytes when
// length > 0. Bytes are signed, length-delimited, and need not be decimal.
// Null input is valid for length <= 0. Overlap with output is supported.
extern "C" void charToUI64(std::uint64_t* output, std::int32_t length,
                           const unsigned char* input);

// Valid JNI environment and JNI string arguments are required. Deletes the
// class local reference even when FindClass returns null; ignores ThrowNew's
// result and does not clear pending exceptions.
extern "C" void JNU_ThrowByName(JNIEnv* environment, const char* class_name,
                                const char* message);
