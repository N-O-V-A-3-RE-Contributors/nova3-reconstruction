/*
 * Behavioural regression for the reconstructed cpu-features TU.
 *
 * The TU is compiled *into* this test (single definition of the four recovered
 * symbols) so the file-scope helpers can be exercised directly.  Nothing here
 * reads the host's /proc/cpuinfo or /sys/devices/system/cpu: every input is a
 * caller-owned fixture buffer, so the results are deterministic on any host.
 *
 * Scope of this test, stated precisely:
 *   - It establishes behaviour of the pure helpers (extract_cpuinfo_field,
 *     has_list_item, parse_decimal, cpulist_parse/cpulist_set/cpulist_and/
 *     cpulist_count) against fixtures.
 *   - It does NOT establish behaviour of android_cpuInit or of the three
 *     exported getters end-to-end: those read fixed absolute paths
 *     (/proc/cpuinfo, /sys/devices/system/cpu/present, .../possible) and the
 *     reconstruction keeps those paths in the source instead of injecting
 *     them, so an integration test needs a fixture root in the process
 *     filesystem.  See docs/progress/NEXT.md for that open task.
 *   - It says nothing about machine-code equivalence; that is measured
 *     separately by tools/compare/cpufeatures_pilot.py.
 *
 * This is a C file on purpose: the TU is C, and compiling a C source inside a
 * C++ translation unit is not portable.
 *
 * Build/run: ctest --test-dir build/host -R cpu_features_behaviour, or
 *   cc -std=c99 -O2 -UNDEBUG -D_GNU_SOURCE -pthread
 *     reconstruction/tests/recovered/libNOVA3_neon/cpu_features_test.c
 *     -o build/host/cpu_features_behaviour_test
 */

#define _GNU_SOURCE 1

#include "../../../recovered/libNOVA3_neon/cpu_features.c"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void
require(int condition, const char* what)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", what);
        abort();
    }
}

/* Copy the extracted value into 'out' (declared by the caller) and free it.
 * Returns 1 when the field was present. */
static int
field_into(const char* cpuinfo, const char* name, char* out, size_t out_size)
{
    int   len = (int)strlen(cpuinfo);
    char* value = extract_cpuinfo_field(cpuinfo, len, name);

    if (value == NULL)
        return 0;
    snprintf(out, out_size, "%s", value);
    free(value);
    return 1;
}

static int
field_missing(const char* cpuinfo, const char* name)
{
    int   len = (int)strlen(cpuinfo);
    char* value = extract_cpuinfo_field(cpuinfo, len, name);

    if (value == NULL)
        return 1;
    free(value);
    return 0;
}

static void
test_has_list_item(void)
{
    const char* features =
        "swp half thumb fastmult vfp edsp neon vfpv3 tls vfpv4 idiva";

    require(has_list_item(features, "neon") == 1, "token present in list");
    require(has_list_item(features, "vfpv3") == 1, "vfpv3 present");
    require(has_list_item(features, "vfpv4") == 1, "vfpv4 present");
    require(has_list_item(features, "swp") == 1, "first token present");
    require(has_list_item(features, "idiva") == 1, "last token present");
    require(has_list_item(features, "vfpv") == 0, "prefix of a token does not match");
    require(has_list_item(features, "neonx") == 0, "token plus suffix does not match");
    require(has_list_item(features, "armv7") == 0, "absent token does not match");

    /* A token that is a substring of a longer token must not match: this is
     * the vfpv3 / vfpv3d16 distinction the initializer depends on. */
    require(has_list_item("vfpv3d16", "vfpv3") == 0,
            "vfpv3 is not matched inside vfpv3d16");
    require(has_list_item("vfpv3d16", "vfpv3d16") == 1, "vfpv3d16 matches itself");
    require(has_list_item("neon vfpv3d16", "vfpv3") == 0,
            "vfpv3 absent when only vfpv3d16 is listed");
    require(has_list_item("vfpv3d16 neon", "neon") == 1, "neon after vfpv3d16 matches");

    require(has_list_item("vfp\tneon", "neon") == 1, "tab-separated list");
    require(has_list_item("  neon  ", "neon") == 1, "leading/trailing blanks");
    require(has_list_item("", "neon") == 0, "empty list");
    require(has_list_item("   ", "neon") == 0, "blank list");
    require(has_list_item(NULL, "neon") == 0, "null list");

    /* The Processor field is also searched for the (v6l) correction token. */
    require(has_list_item("ARMv7 Processor rev 0 (v7l)", "(v6l)") == 0,
            "v7l processor is not reported as v6l");
    require(has_list_item("ARMv6-compatible processor rev 5 (v6l)", "(v6l)") == 1,
            "v6l processor detected");
}

static void
test_parse_decimal(void)
{
    const char* text = "42-7";
    const char* end;
    int         value = -1;

    end = parse_decimal(text, text + 5, &value);
    require(end == text + 2 && value == 42, "parse leading decimal");
    require(parse_decimal(text + 2, text + 5, &value) == NULL, "dash is not a digit");
    end = parse_decimal(text + 3, text + 5, &value);
    require(end != NULL && value == 7, "parse decimal after separator");

    require(parse_decimal("", "", &value) == NULL, "empty input rejected");

    {
        const char* zeros = "007";
        end = parse_decimal(zeros, zeros + 3, &value);
        require(end == zeros + 3 && value == 7, "leading zeros accepted");
    }
    {
        /* A digit run that stops at the limit, not at the first non-digit. */
        const char* digits = "12345";
        end = parse_decimal(digits, digits + 3, &value);
        require(end == digits + 3 && value == 123, "limit truncates the digit run");
    }
}

static void
test_cpulist(void)
{
    static const struct {
        const char* text;
        unsigned    expected;
    } cases[] = {
        {"0\n", 0x1u},
        {"0-3\n", 0xFu},
        {"2,4-7\n", (1u << 2) | (1u << 4) | (1u << 5) | (1u << 6) | (1u << 7)},
        {"0-31\n", 0xFFFFFFFFu},
        {"1,3\n", (1u << 1) | (1u << 3)},
        {"2", 1u << 2}
    };
    size_t i;

    for (i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        CpuList list;
        cpulist_init(&list);
        cpulist_parse(&list, cases[i].text, (int)strlen(cases[i].text));
        if (list.mask != cases[i].expected) {
            fprintf(stderr, "FAIL: cpulist \"%s\" -> 0x%08x, expected 0x%08x\n",
                    cases[i].text, list.mask, cases[i].expected);
            abort();
        }
    }

    {
        /* Indices at or above 32 are outside the model and must be dropped. */
        CpuList wide;
        CpuList mixed;

        cpulist_init(&wide);
        cpulist_parse(&wide, "32-35\n", 6);
        require(wide.mask == 0u, "indices >= 32 are ignored");
        cpulist_init(&mixed);
        cpulist_parse(&mixed, "0,40\n", 5);
        require(mixed.mask == 1u, "only the in-range index of a mixed list is set");
    }

    {
        /* Set/count/and are the operations the core count uses. */
        CpuList a, b;

        cpulist_init(&a);
        cpulist_init(&b);
        cpulist_set(&a, 0);
        cpulist_set(&a, 1);
        cpulist_set(&a, 2);
        cpulist_set(&b, 1);
        cpulist_set(&b, 9);
        require(cpulist_count(&a) == 3, "popcount of three cores");
        cpulist_and(&a, &b);
        require(a.mask == (1u << 1), "intersection keeps the shared core");
        require(cpulist_count(&a) == 1, "count after intersection");
        require(cpulist_count(&b) == 2, "count of untouched operand");
    }

    {
        /* sysfs files carry one line; parsing stops at its newline. */
        CpuList stopped;
        cpulist_init(&stopped);
        cpulist_parse(&stopped, "0-1\n9\n", 6);
        require(stopped.mask == 0x3u, "parse stops at the first newline");
    }

    {
        /* Malformed lists are not half-applied silently: whatever was parsed
         * before the bad token is kept and the rest is abandoned.  This test
         * pins the current behaviour rather than asserting it is optimal. */
        CpuList bad;
        cpulist_init(&bad);
        cpulist_parse(&bad, "x\n", 2);
        require(bad.mask == 0u, "non-numeric list yields no cores");
    }
}

static void
test_extract_cpuinfo_field(void)
{
    static const char* cpuinfo =
        "Processor\t: ARMv7 Processor rev 10 (v7l)\n"
        "processor\t: 0\n"
        "Features\t: swp half thumb fastmult vfp edsp neon vfpv3 tls\n"
        "CPU architecture: 7\n"
        "CPU part\t: 0xc09\n"
        "processor\t: 1\n";
    char value[128];

    require(field_into(cpuinfo, "Features", value, sizeof value) &&
                strcmp(value, "swp half thumb fastmult vfp edsp neon vfpv3 tls") == 0,
            "Features field extracted without its leading blank");
    require(field_into(cpuinfo, "CPU architecture", value, sizeof value) &&
                strcmp(value, "7") == 0,
            "CPU architecture extracted");
    require(field_into(cpuinfo, "Processor", value, sizeof value) &&
                strcmp(value, "ARMv7 Processor rev 10 (v7l)") == 0,
            "Processor field extracted");
    require(field_into(cpuinfo, "CPU part", value, sizeof value) &&
                strcmp(value, "0xc09") == 0,
            "CPU part extracted");

    /* Multi-core cpuinfo repeats Processor; only the first occurrence is used,
     * which is what the initializer relies on for the (v6l) correction. */
    require(field_into(cpuinfo, "processor", value, sizeof value) &&
                strcmp(value, "0") == 0,
            "first processor line wins");

    require(field_missing(cpuinfo, "Hardware"), "absent field yields NULL");
    require(field_missing(cpuinfo, "Features "),
            "field name with a trailing blank is a different name");

    /* A field name that only appears mid-line must not be accepted. */
    require(field_missing("Model\t: see Features: neon\n", "Features"),
            "field must start a line");
    require(field_into("Features: neon\n", "Features", value, sizeof value) &&
                strcmp(value, "neon") == 0,
            "field at line start accepted");

    require(field_missing("Features:neon\n", "Features"),
            "missing space after the colon is rejected");
    require(field_missing("Features\n", "Features"), "field without colon rejected");

    /* Value on the last line without a trailing newline. */
    require(field_into("CPU architecture: 7", "CPU architecture", value, sizeof value) &&
                strcmp(value, "7") == 0,
            "value at end of buffer");

    /* An empty value is a value, not a missing field. */
    require(field_into("Features: \n", "Features", value, sizeof value) &&
                strcmp(value, "") == 0,
            "empty value extracted");
}

int
main(void)
{
    test_has_list_item();
    test_parse_decimal();
    test_cpulist();
    test_extract_cpuinfo_field();
    puts("cpu-features helper behaviour: token matching, decimal/cpulist parsing, "
         "cpuinfo field extraction passed");
    return 0;
}
