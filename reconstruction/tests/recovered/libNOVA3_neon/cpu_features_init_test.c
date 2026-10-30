/*
 * End-to-end behavioural regression for android_cpuInit and the three
 * exported getters of the reconstructed cpu-features TU.
 *
 * Why this file exists
 * --------------------
 * The recovered source reads fixed absolute paths:
 *
 *     /proc/cpuinfo
 *     /sys/devices/system/cpu/present
 *     /sys/devices/system/cpu/possible
 *
 * Those paths, and the `open`/`read`/`close` calls that use them, are part of
 * the recovered behaviour and are deliberately NOT parameterised: injecting a
 * fixture root through a macro would change the translation unit being tested
 * and could hide exactly the path handling under test.  Instead this test
 * keeps the TU byte-for-byte as shipped and redirects the three libc entry
 * points at link time:
 *
 *     -Wl,--wrap=open -Wl,--wrap=read -Wl,--wrap=close
 *
 * The wrapper serves the requested paths out of an in-memory fixture table and
 * returns ENOENT for anything else, so the initializer runs its real code path
 * (including the "file is missing" branch) with no host filesystem access.
 * Nothing here depends on the machine the test runs on.
 *
 * Scope, stated precisely
 * -----------------------
 *   - Establishes behaviour of android_cpuInit end-to-end: which bits it sets
 *     for a given cpuinfo/sysfs fixture, the single-core fallback, the missing
 *     file branches and the (v6l) misreport correction.
 *   - Establishes that the getters run the initializer exactly once through
 *     the recovered pthread_once object and then return the cached values.
 *   - Says nothing about machine-code equivalence.  That is measured
 *     separately by tools/compare/cpufeatures_pilot.py; no match is claimed
 *     here (see cpu_features.c for the recorded statuses).
 *   - The one test-only concession is resetting the file-scope state between
 *     scenarios (`memset(&g_once, 0, ...)` and the three objects), which is
 *     how PTHREAD_ONCE_INIT is spelled and is never done by production code.
 *
 * Build/run: ctest --test-dir build/host -R cpu_features_init_behaviour
 */

#define _GNU_SOURCE 1

#include "../../../recovered/libNOVA3_neon/cpu_features.c"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Fixture filesystem served through the wrapped libc entry points.    */
/* ------------------------------------------------------------------ */

#define FIXTURE_SLOTS 4
#define FIXTURE_FD_BASE 100

typedef struct {
    const char* path;
    const char* contents;   /* NULL means "this path does not exist" */
} FixtureFile;

static FixtureFile g_files[FIXTURE_SLOTS];
static size_t      g_file_count;
static size_t      g_open_count[FIXTURE_SLOTS];

static void
fixture_reset(void)
{
    memset(g_files, 0, sizeof g_files);
    memset(g_open_count, 0, sizeof g_open_count);
    g_file_count = 0;
}

static void
fixture_add(const char* path, const char* contents)
{
    if (g_file_count >= FIXTURE_SLOTS) {
        fprintf(stderr, "FAIL: fixture table overflow\n");
        abort();
    }
    g_files[g_file_count].path = path;
    g_files[g_file_count].contents = contents;
    g_file_count++;
}

static int
fixture_index(const char* path)
{
    size_t i;

    if (path == NULL)
        return -1;
    for (i = 0; i < g_file_count; i++) {
        if (strcmp(g_files[i].path, path) == 0)
            return (int)i;
    }
    return -1;
}

int __wrap_open(const char* path, int flags, ...);

int
__wrap_open(const char* path, int flags, ...)
{
    int index = fixture_index(path);
    va_list ap;

    if (flags & O_CREAT) {
        va_start(ap, flags);
        (void)va_arg(ap, int);
        va_end(ap);
    }

    if (index < 0 || g_files[index].contents == NULL) {
        errno = ENOENT;
        return -1;
    }
    g_open_count[index]++;
    return FIXTURE_FD_BASE + index;
}

ssize_t __wrap_read(int fd, void* buffer, size_t count);

ssize_t
__wrap_read(int fd, void* buffer, size_t count)
{
    int    index = fd - FIXTURE_FD_BASE;
    size_t length;

    if (index < 0 || (size_t)index >= g_file_count || g_files[index].contents == NULL) {
        errno = EBADF;
        return -1;
    }
    length = strlen(g_files[index].contents);
    if (length > count)
        length = count;
    memcpy(buffer, g_files[index].contents, length);
    return (ssize_t)length;
}

int __wrap_close(int fd);

int
__wrap_close(int fd)
{
    (void)fd;
    return 0;
}

/* ------------------------------------------------------------------ */
/* Scenario driver                                                     */
/* ------------------------------------------------------------------ */

static void
require(int condition, const char* what)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", what);
        abort();
    }
}

/* Put the TU back into its post-load state.  g_once is zeroed because
 * PTHREAD_ONCE_INIT is the all-zero initialiser on both bionic and glibc;
 * production code never does this. */
static void
reset_tu_state(void)
{
    memset(&g_once, 0, sizeof g_once);
    g_cpuCount = 0;
    g_cpuFeatures = 0;
    g_cpuFamily = 0;
}

static void
run_init(void)
{
    reset_tu_state();
    android_cpuInit();
}

#define ARMv7   ANDROID_CPU_ARM_FEATURE_ARMv7
#define VFPv3   ANDROID_CPU_ARM_FEATURE_VFPv3
#define NEON    ANDROID_CPU_ARM_FEATURE_NEON
#define LDREX   ANDROID_CPU_ARM_FEATURE_LDREX_STREX

static const char CPUINFO_ARMV7_NEON[] =
    "Processor\t: ARMv7 Processor rev 4 (v7l)\n"
    "processor\t: 0\n"
    "Features\t: swp half thumb fastmult vfp edsp neon vfpv3 tls\n"
    "CPU architecture: 7\n"
    "CPU part\t: 0xc09\n";

static const char CPUINFO_ARMV6_MISREPORT[] =
    "Processor\t: ARMv6-compatible processor rev 5 (v6l)\n"
    "Features\t: swp half thumb fastmult vfp edsp\n"
    "CPU architecture: 7\n";

static const char CPUINFO_ARCH6_VFPV3D16[] =
    "Processor\t: ARMv6-compatible processor rev 1 (v6l)\n"
    "Features\t: swp half thumb vfp vfpv3d16\n"
    "CPU architecture: 6\n";

static void
scenario_armv7_neon(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARMV7_NEON);
    fixture_add("/sys/devices/system/cpu/present", "0-3\n");
    fixture_add("/sys/devices/system/cpu/possible", "0-3\n");

    run_init();
    require(g_cpuFamily == ANDROID_CPU_FAMILY_ARM, "family is ARM");
    require(g_cpuCount == 4, "four cores from present/possible 0-3");
    require(g_cpuFeatures == (ARMv7 | VFPv3 | NEON | LDREX),
            "v7 NEON features: ARMv7|VFPv3|NEON|LDREX_STREX");
}

static void
scenario_v6l_correction(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARMV6_MISREPORT);
    fixture_add("/sys/devices/system/cpu/present", "0-1\n");
    fixture_add("/sys/devices/system/cpu/possible", "0-1\n");

    run_init();
    require(g_cpuCount == 2, "two cores");
    require((g_cpuFeatures & ARMv7) == 0,
            "(v6l) Processor clears the reported ARMv7 bit");
    require((g_cpuFeatures & LDREX) == LDREX,
            "architecture 7 still sets LDREX/STREX");
    require((g_cpuFeatures & (VFPv3 | NEON)) == 0,
            "no VFPv3/NEON without the tokens");
}

static void
scenario_vfpv3d16_only(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARCH6_VFPV3D16);
    fixture_add("/sys/devices/system/cpu/present", "0\n");
    fixture_add("/sys/devices/system/cpu/possible", "0\n");

    run_init();
    require(g_cpuCount == 1, "single core");
    require((g_cpuFeatures & VFPv3) == VFPv3, "vfpv3d16 alone implies VFPv3");
    require((g_cpuFeatures & NEON) == 0, "vfpv3d16 does not imply NEON");
    require((g_cpuFeatures & ARMv7) == 0, "architecture 6 is not ARMv7");
    require((g_cpuFeatures & LDREX) == LDREX, "architecture 6 sets LDREX/STREX");
}

static void
scenario_missing_cpuinfo(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", NULL);
    fixture_add("/sys/devices/system/cpu/present", "0-3\n");
    fixture_add("/sys/devices/system/cpu/possible", "0-3\n");

    run_init();
    require(g_cpuFamily == ANDROID_CPU_FAMILY_ARM, "family default is ARM");
    require(g_cpuFeatures == 0, "no features without cpuinfo");
    require(g_cpuCount == 1,
            "unreadable cpuinfo returns before the cpulist query, so count stays 1");
    require(g_open_count[1] == 0 && g_open_count[2] == 0,
            "sysfs cpulists are not opened when cpuinfo cannot be read");
}

static void
scenario_cpulist_intersection(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARMV7_NEON);
    fixture_add("/sys/devices/system/cpu/present", "0-3\n");
    fixture_add("/sys/devices/system/cpu/possible", "0,1\n");

    run_init();
    require(g_cpuCount == 2,
            "count is the intersection of present and possible (0,1)");
    require(g_cpuFeatures == (ARMv7 | VFPv3 | NEON | LDREX),
            "feature bits unaffected by the cpulist intersection");
}

static void
scenario_zero_count_falls_back_to_one(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARMV7_NEON);
    fixture_add("/sys/devices/system/cpu/present", NULL);
    fixture_add("/sys/devices/system/cpu/possible", NULL);

    run_init();
    require(g_cpuCount == 1, "unreadable cpulists leave count 0, clamped to 1");
}

static void
scenario_missing_features_field(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo",
                "Processor\t: ARMv7 Processor rev 4 (v7l)\n"
                "CPU architecture: 7\n");
    fixture_add("/sys/devices/system/cpu/present", "0\n");
    fixture_add("/sys/devices/system/cpu/possible", "0\n");

    run_init();
    require(g_cpuCount == 1, "one core");
    require(g_cpuFeatures == (ARMv7 | LDREX),
            "absent Features field keeps the architecture-derived bits only");
}

static void
scenario_getters_initialise_once(void)
{
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARMV7_NEON);
    fixture_add("/sys/devices/system/cpu/present", "0-3\n");
    fixture_add("/sys/devices/system/cpu/possible", "0-3\n");

    reset_tu_state();
    require(android_getCpuFamily() == ANDROID_CPU_FAMILY_ARM, "getter family on first call");
    require(android_getCpuCount() == 4, "getter count on first call");
    require(android_getCpuFeatures() == (ARMv7 | VFPv3 | NEON | LDREX),
            "getter features on first call");
    require(g_open_count[0] == 1, "cpuinfo opened exactly once across three getters");

    /* Replace the fixture and call again: pthread_once must keep the cached
     * values, because the initializer is not re-run. */
    fixture_reset();
    fixture_add("/proc/cpuinfo", CPUINFO_ARCH6_VFPV3D16);
    fixture_add("/sys/devices/system/cpu/present", "0\n");
    fixture_add("/sys/devices/system/cpu/possible", "0\n");

    require(android_getCpuCount() == 4, "count stays cached after fixture change");
    require(android_getCpuFeatures() == (ARMv7 | VFPv3 | NEON | LDREX),
            "features stay cached after fixture change");
    require(g_open_count[0] == 0, "cpuinfo is not reopened on later getter calls");
}

int
main(void)
{
    scenario_armv7_neon();
    scenario_v6l_correction();
    scenario_vfpv3d16_only();
    scenario_missing_cpuinfo();
    scenario_cpulist_intersection();
    scenario_zero_count_falls_back_to_one();
    scenario_missing_features_field();
    scenario_getters_initialise_once();

    puts("cpu-features initializer behaviour: fixtures, cpulist intersection, "
         "(v6l) correction, missing-file branches and pthread_once caching passed");
    return 0;
}
