/*
 * Public interface and flag values for the reconstructed cpu-features
 * translation unit (reconstruction/recovered/libNOVA3_neon/cpu_features.c).
 *
 * The identifiers and numeric values are the stable, publicly documented
 * Android NDK "cpufeatures" API (see NDK sources/android/cpufeatures/
 * cpu-features.h, BSD-3-Clause). The engine links this API under its
 * exported symbol names, so the values are part of the reconstructed
 * interface, not a free choice:
 *
 *   android_getCpuFamily()   returns ANDROID_CPU_FAMILY_*
 *   android_getCpuFeatures() returns the ANDROID_CPU_ARM_FEATURE_* bitmask
 *   android_getCpuCount()    returns the usable core count (>= 1)
 */
#ifndef NOVA3_CPU_FEATURES_H
#define NOVA3_CPU_FEATURES_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    ANDROID_CPU_FAMILY_UNKNOWN = 0,
    ANDROID_CPU_FAMILY_ARM     = 1,
    ANDROID_CPU_FAMILY_X86     = 2,
    ANDROID_CPU_FAMILY_MIPS    = 3
} AndroidCpuFamily;

typedef enum {
    ANDROID_CPU_ARM_FEATURE_ARMv7       = (1 << 0),
    ANDROID_CPU_ARM_FEATURE_VFPv3       = (1 << 1),
    ANDROID_CPU_ARM_FEATURE_NEON        = (1 << 2),
    ANDROID_CPU_ARM_FEATURE_LDREX_STREX = (1 << 3)
} AndroidCpuArmFeature;

int      android_getCpuCount(void);
uint64_t android_getCpuFeatures(void);
int      android_getCpuFamily(void);

#ifdef __cplusplus
}
#endif

#endif /* NOVA3_CPU_FEATURES_H */
