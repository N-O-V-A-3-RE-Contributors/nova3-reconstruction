/*
 * Reconstructed translation unit: cpu-features.c (Premium ARMv7 engine).
 *
 * Reference binary
 *   analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
 *   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
 *
 * Recovered TU inventory (readelf -sW, second symbol group of the .symtab,
 * preceded by STT_FILE "cpu-features.c" and followed by STT_FILE "app.cpp"):
 *
 *   android_getCpuCount     VA 0x000d2f10   44 B  A32  GLOBAL
 *   android_getCpuFeatures  VA 0x000d2f3c   48 B  A32  GLOBAL
 *   android_getCpuFamily    VA 0x000d2f6c   44 B  A32  GLOBAL
 *   android_cpuInit         VA 0x000d2f98 2632 B  A32  LOCAL
 *
 *   g_once       0x00d96598   4 B  LOCAL  OBJECT
 *   g_cpuCount   0x00d9659c   4 B  LOCAL  OBJECT
 *   g_cpuFeatures 0x00d965a0  8 B  LOCAL  OBJECT
 *   g_cpuFamily  0x00d965a8   4 B  LOCAL  OBJECT
 *
 * That symbol group is stronger evidence than any decompiler view: it names
 * four *separate file-scope objects*, not one aggregate.  Earlier drafts of
 * this reconstruction modelled them as a single struct reached through one
 * anchor; that hypothesis is retracted here, because the symbol table and
 * the per-object sizes settle the question.
 *
 * Provenance: the strings and control flow of this TU match the AOSP
 * "cpufeatures" library that ships with the Android NDK (BSD-3-Clause,
 * Copyright (C) 2010 The Android Open Source Project).  The reference reads
 * /proc/cpuinfo, /sys/devices/system/cpu/present and
 * /sys/devices/system/cpu/possible, and inspects the cpuinfo fields
 * "CPU architecture", "Processor", "Features" plus the feature tokens
 * "vfpv3", "vfpv3d16" and "neon"; the Processor field is additionally
 * searched for the token "(v6l)" to correct kernels that misreport an ARMv6
 * part as architecture 7.  Those strings are all present, contiguously, in
 * .rodata at 0x00be6580..0x00be661f, and the reference contains no
 * /proc/self/auxv access, no android_setCpu and no g_inited object, which
 * places the source at the pre-r8c revision of that library (the r7c
 * generation: cpulist-based counting, "Features"-based feature bits).
 *
 * This file is therefore an independent C reconstruction of that revision:
 * written from the recovered symbol/object layout plus the observed
 * instruction structure, using the public AOSP origin text as the reference
 * for naming and for structure that the binary confirms.  It is not copied
 * object code and it is not presented as the original source text.
 *
 * Matching status, measured by tools/compare/cpufeatures_pilot.py with the
 * local NDK r8e GCC 4.4.3 (nothing here asserts a match):
 *
 *   optimum           android_cpuInit  getCpuCount  getCpuFeatures  getCpuFamily
 *   -O3 -fstack-protector   2600/2632    44/44 DIFF   48/48 EQUAL    44/44 DIFF
 *   -O2 -fstack-protector    740/2632    44/44 DIFF   48/48 EQUAL    44/44 DIFF
 *   -Os -fstack-protector    760/2632    44/44 DIFF   48/48 EQUAL    44/44 DIFF
 *
 * `android_getCpuFeatures` reproduces the reference instruction stream word
 * for word (all non-relocation, non-literal words equal).  The other two
 * getters have the correct size and shape but each contains one differing
 * word: the `[base, #offset]` immediate of the final load.  That is a .bss
 * ordering artifact, not a source-level difference - this compiler lays the
 * two int-sized objects out as g_cpuFamily@+4, g_cpuCount@+16, while the
 * reference has g_cpuCount@+4, g_cpuFamily@+16.  Declaration order, object
 * type and symbol names were all varied and do not change this compiler's
 * choice, so the reference layout was produced by a toolchain configuration
 * not yet reproduced; it stays unexplained here rather than fudged.
 *
 * `android_cpuInit` is 32 bytes (8 instructions) short of the reference and
 * still diverges structurally: the inlined field extraction is very close
 * (the reference sign-extends the byte comparisons with LDRSB where this
 * compile uses LDRB, and the Features block is ordered differently), but it
 * is NOT a match.  No byte-identity claim is made for this TU.
 */

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cpu_features.h"

/* These four objects land at .bss offsets 0, 4, 8 and 16 in the reference
 * and the getters read g_once+4, g_once+8/+12 and g_once+16 respectively:
 *   g_once 0x00d96598 (4)  g_cpuCount 0x00d9659c (4)
 *   g_cpuFeatures 0x00d965a0 (8)  g_cpuFamily 0x00d965a8 (4)
 *
 * Measured layout dependence (tools/compare/cpufeatures_pilot.py, the measured candidates in
 * .cache/cpufeat/order/): GCC 4.4.3 emits the exported functions of a TU in
 * the reverse of their definition order and places the TU's .bss objects in
 * the order in which the emitted functions first reference them.  With the
 * getters defined in reverse (see the definition-order note before
 * android_getCpuFamily below) the compiler produces exactly the reference
 * layout: g_cpuCount +4, g_cpuFeatures +8, g_cpuFamily +16.  The declaration
 * order below is a second, independent wart: it is only preserved when the
 * compiler emits these objects in declaration order, which it does here, but
 * the offsets above are what the pilot measures, not this comment. */
static pthread_once_t g_once;
static int            g_cpuCount;
static uint64_t       g_cpuFeatures;
static int            g_cpuFamily;

/* Read up to 'buffer_size' bytes of a file. Returns the byte count, or -1.
 * Interrupted reads are retried; the descriptor is always closed. */
static int
read_file(const char* filename, char* buffer, int buffer_size)
{
    int fd, ret;

    fd = open(filename, O_RDONLY);
    if (fd < 0)
        return -1;

    do {
        ret = read(fd, buffer, buffer_size);
    } while (ret < 0 && errno == EINTR);

    close(fd);
    return ret;
}

/* Extract the content of the first occurrence of a field of /proc/cpuinfo
 * that starts a line, as a heap-allocated NUL-terminated string.
 * Returns NULL if the field is absent or malformed.
 */
static char*
extract_cpuinfo_field(const char* buffer, int buflen, const char* field)
{
    int  fieldlen = strlen(field);
    const char* bufend = buffer + buflen;
    char* result = NULL;
    int len;
    const char *p, *q;

    p = buffer;
    for (;;) {
        p = memmem(p, bufend - p, field, fieldlen);
        if (p == NULL)
            goto EXIT;

        if (p == buffer || p[-1] == '\n')
            break;

        p += fieldlen;
    }

    /* Skip to the first column followed by a space */
    p += fieldlen;
    p  = memchr(p, ':', bufend - p);
    if (p == NULL || p[1] != ' ')
        goto EXIT;

    /* Find the end of the line */
    p += 2;
    q = memchr(p, '\n', bufend - p);
    if (q == NULL)
        q = bufend;

    len = q - p;
    result = malloc(len + 1);
    if (result == NULL)
        goto EXIT;

    memcpy(result, p, len);
    result[len] = '\0';

EXIT:
    return result;
}

/* True if the space/tab separated list contains one given item. */
static int
has_list_item(const char* list, const char* item)
{
    const char*  p = list;
    int itemlen = strlen(item);

    if (list == NULL)
        return 0;

    while (*p) {
        const char*  q;

        while (*p == ' ' || *p == '\t')
            p++;

        q = p;
        while (*q && *q != ' ' && *q != '\t')
            q++;

        if (itemlen == q - p && !memcmp(p, item, itemlen))
            return 1;

        p = q;
    }
    return 0;
}

/* Parse a decimal integer starting from 'input', not going further than
 * 'limit'. Returns the new position, or NULL on bad format. */
static const char*
parse_decimal(const char* input, const char* limit, int* result)
{
    const char* p = input;
    int val = 0;

    while (p < limit) {
        int d = (*p - '0');
        if ((unsigned)d >= 10U)
            break;
        val = val*10 + d;
        p++;
    }
    if (p == input)
        return NULL;

    *result = val;
    return p;
}

/* CPU list / mask as read from sysfs. Mobile devices have far fewer than
 * 32 cores, so a single word is enough. */
typedef struct {
    uint32_t mask;
} CpuList;

static __inline__ void
cpulist_init(CpuList* list) {
    list->mask = 0;
}

static __inline__ void
cpulist_and(CpuList* list1, CpuList* list2) {
    list1->mask &= list2->mask;
}

static __inline__ void
cpulist_set(CpuList* list, int index) {
    if ((unsigned)index < 32) {
        list->mask |= (uint32_t)(1U << index);
    }
}

static __inline__ int
cpulist_count(CpuList* list) {
    return __builtin_popcount(list->mask);
}

/* Parse a textual cpu list ("0", "0-3", "2,4-127,128-143"). */
static void
cpulist_parse(CpuList* list, const char* line, int line_len)
{
    const char* p = line;
    const char* end = p + line_len;
    const char* q;

    while (p < end && *p != '\n')
    {
        int val, start_value, end_value;

        q = memchr(p, ',', end - p);
        if (q == NULL) {
            q = end;
        }

        p = parse_decimal(p, q, &start_value);
        if (p == NULL)
            goto BAD_FORMAT;

        end_value = start_value;

        if (p < q && *p == '-') {
            p = parse_decimal(p + 1, q, &end_value);
            if (p == NULL)
                goto BAD_FORMAT;
        }

        for (val = start_value; val <= end_value; val++) {
            cpulist_set(list, val);
        }

        p = q;
        if (p < end)
            p++;
    }

BAD_FORMAT:
    ;
}

static void
cpulist_read_from(CpuList* list, const char* filename)
{
    char   file[64];
    int    filelen;

    cpulist_init(list);

    filelen = read_file(filename, file, sizeof file);
    if (filelen < 0)
        return;

    cpulist_parse(list, file, filelen);
}

/* Number of CPUs present: the intersection of the 'present' and 'possible'
 * sysfs lists, i.e. every core the kernel can use. */
static int
get_cpu_count(void)
{
    CpuList cpus_present[1];
    CpuList cpus_possible[1];

    cpulist_read_from(cpus_present, "/sys/devices/system/cpu/present");
    cpulist_read_from(cpus_possible, "/sys/devices/system/cpu/possible");

    cpulist_and(cpus_present, cpus_possible);

    return cpulist_count(cpus_present);
}

static void
android_cpuInit(void)
{
    char  cpuinfo[4096];
    int   cpuinfo_len;

    g_cpuCount    = 1;
    g_cpuFamily   = ANDROID_CPU_FAMILY_ARM;
    g_cpuFeatures = 0;

    cpuinfo_len = read_file("/proc/cpuinfo", cpuinfo, sizeof cpuinfo);
    if (cpuinfo_len < 0)
        return;

    /* Count the CPU cores; the count may be 0 for single-core CPUs. */
    g_cpuCount = get_cpu_count();
    if (g_cpuCount == 0)
        g_cpuCount = 1;

    {
        char*  cpuFeatures  = NULL;
        char*  cpuArch;

        /* The architecture is a well-known numeric field, unlike the output
         * of 'Processor' which varies greatly. */
        cpuArch = extract_cpuinfo_field(cpuinfo, cpuinfo_len,
                                        "CPU architecture");

        if (cpuArch != NULL) {
            char*  end;
            long   archNumber;
            int    hasARMv7 = 0;

            archNumber = strtol(cpuArch, &end, 10);

            if (end > cpuArch && archNumber >= 7) {
                hasARMv7 = 1;
            }

            /* Some ARMv6 parts report architecture 7. Correct that by
             * looking at the 'Processor' field, which carries "(v6l)" for
             * ARMv6 and "(v7l)" for ARMv7. */
            if (hasARMv7) {
                char* cpuProc = extract_cpuinfo_field(cpuinfo, cpuinfo_len,
                                                      "Processor");
                if (cpuProc != NULL) {
                    if (has_list_item(cpuProc, "(v6l)")) {
                        hasARMv7 = 0;
                    }
                    free(cpuProc);
                }
            }

            if (hasARMv7) {
                g_cpuFeatures |= ANDROID_CPU_ARM_FEATURE_ARMv7;
            }

            /* LDREX/STREX exist from ARMv6 on. */
            if (archNumber >= 6) {
                g_cpuFeatures |= ANDROID_CPU_ARM_FEATURE_LDREX_STREX;
            }

            free(cpuArch);
        }

        cpuFeatures = extract_cpuinfo_field(cpuinfo, cpuinfo_len,
                                           "Features");
        if (cpuFeatures == NULL)
            return;

        /* 'vfpv3' and 'vfpv3d16' both imply VFPv3; only vfpv3d16 is
         * reported by some kernels. 'neon' implies VFPv3 as well. */
        if (has_list_item(cpuFeatures, "vfpv3"))
            g_cpuFeatures |= ANDROID_CPU_ARM_FEATURE_VFPv3;
        if (has_list_item(cpuFeatures, "vfpv3d16"))
            g_cpuFeatures |= ANDROID_CPU_ARM_FEATURE_VFPv3;
        if (has_list_item(cpuFeatures, "neon"))
            g_cpuFeatures |= ANDROID_CPU_ARM_FEATURE_VFPv3 |
                             ANDROID_CPU_ARM_FEATURE_NEON;

        free(cpuFeatures);
    }
}

/* Definition order of the three exported getters is significant, and it is a
 * measured property of this TU rather than a style choice.
 *
 * GCC 4.4.3 emits the exported (GLOBAL) functions of a TU in the reverse of
 * their definition order and lays out the TU's .bss objects in the order in
 * which the emitted functions first reference them.  The reference TU is laid
 * out as
 *
 *     android_getCpuCount 0x000d2f10 (44)   g_cpuCount    +0x4
 *     android_getCpuFeatures 0x000d2f3c (48) g_cpuFeatures +0x8
 *     android_getCpuFamily 0x000d2f6c (44)  g_cpuFamily   +0x10
 *     android_cpuInit    0x000d2f98 (2632)
 *
 * i.e. emitted order Count, Features, Family, cpuInit and .bss order
 * count, features, family.  Both follow from defining the getters in the
 * reverse of that order, after android_cpuInit, as below; with any other
 * definition order the compiler emits Family first and puts g_cpuFamily at
 * +0x4, which changes the load immediate in two of the three getters (measured
 * in .cache/cpufeat/order/, see tools/compare/cpufeatures_pilot.py).
 *
 * -fno-toplevel-reorder also reproduces the .bss offsets, but it disables the
 * address-caching the reference clearly uses (candidate getters grow to 48/52
 * bytes and address .bss through `ldr rX, [pc, rY]`), so the reference was not
 * built that way.  Definition order is the explanation consistent with both
 * the .bss offsets and the 44/48/44-byte instruction shapes.
 */
int
android_getCpuFamily(void)
{
    pthread_once(&g_once, android_cpuInit);
    return g_cpuFamily;
}

uint64_t
android_getCpuFeatures(void)
{
    pthread_once(&g_once, android_cpuInit);
    return g_cpuFeatures;
}

int
android_getCpuCount(void)
{
    pthread_once(&g_once, android_cpuInit);
    return g_cpuCount;
}
