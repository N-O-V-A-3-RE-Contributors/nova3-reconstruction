/* Source-owned integration check; fixture is raw DEFLATE for ASCII hello. */
#include "zlib.h"
#include <stdio.h>
#include <string.h>

static int run(unsigned char *input, unsigned input_size,
               unsigned output_size, int expected_status) {
    unsigned char output[16] = {0};
    z_stream stream = {0};
    if (inflateInit2(&stream, -MAX_WBITS) != Z_OK) return 1;
    stream.next_in = input;
    stream.avail_in = input_size;
    stream.next_out = output;
    stream.avail_out = output_size;
    int status = inflate(&stream, Z_FINISH);
    int failed = status != expected_status;
    if (expected_status == Z_STREAM_END)
        failed |= stream.total_out != 5 || memcmp(output, "hello", 5) != 0;
    if (output_size == 2) failed |= stream.total_out != 2;
    failed |= inflateEnd(&stream) != Z_OK;
    return failed;
}

int main(void) {
    unsigned char valid[] = {0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x07, 0x00};
    unsigned char invalid[] = {0x07}; /* reserved DEFLATE block type */
    int failed = strcmp(zlibVersion(), "1.3.2") != 0;
    failed |= run(valid, sizeof(valid), 16, Z_STREAM_END);
    failed |= run(valid, 2, 16, Z_BUF_ERROR);
    failed |= run(valid, sizeof(valid), 2, Z_BUF_ERROR);
    failed |= run(invalid, sizeof(invalid), 16, Z_DATA_ERROR);
    if (failed) fputs("zlib source-build integration check failed\n", stderr);
    else puts("zlib1.3.2: raw inflate, truncated/corrupt input, bounded output passed");
    return failed;
}
