#include <sys/types.h>
#include <sys/stat.h>
#include <sys/quota.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int number(const char *input, uint64_t *result) {
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(input, &end, 10);
    if (errno || input[0] < '0' || input[0] > '9' || !end || *end) return 0;
    *result = value;
    return 1;
}

int main(int argc, char **argv) {
    uint64_t uid = 0, limit = 0, device_number = 0;
    struct stat metadata;
    struct dqblk quota;
    if (argc != 5 || geteuid() != 0 || !number(argv[3], &uid) || !uid || uid > INT_MAX) return 2;
    if (strncmp(argv[2], "/dev/loop", 9) || !number(argv[2] + 9, &device_number)) return 3;
    if (lstat(argv[2], &metadata) || !S_ISBLK(metadata.st_mode)) return 4;
    memset(&quota, 0, sizeof(quota));
    if (!strcmp(argv[1], "set")) {
        if (!number(argv[4], &limit) || (limit != 8388608 && limit != 16777216)) return 5;
        quota.dqb_bhardlimit = limit / 1024;
        quota.dqb_valid = QIF_BLIMITS;
        if (quotactl(QCMD(Q_SETQUOTA, USRQUOTA), argv[2], (int)uid, (void *)&quota)) {
            fprintf(stderr, "{\"operation\":\"setquota\",\"errno\":%d}\n", errno);
            return 6;
        }
    } else if (strcmp(argv[1], "get") || strcmp(argv[4], "0")) return 7;
    memset(&quota, 0, sizeof(quota));
    if (quotactl(QCMD(Q_GETQUOTA, USRQUOTA), argv[2], (int)uid, (void *)&quota)) {
        fprintf(stderr, "{\"operation\":\"getquota\",\"errno\":%d}\n", errno);
        return 8;
    }
    if (limit && quota.dqb_bhardlimit * 1024 != limit) return 9;
    printf("{\"hard_bytes\":%" PRIu64 ",\"used_bytes\":%" PRIu64 "}\n", (uint64_t)quota.dqb_bhardlimit * 1024, (uint64_t)quota.dqb_curspace);
    return 0;
}
