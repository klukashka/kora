set -euo pipefail
export LC_ALL=C
phase="$1"
directory="$RUNNER_TEMP/kora616-evidence/$phase"
mkdir -p "$directory"
trap 'result=$?; printf "%s\n" "$result" > "$directory/status.txt"' EXIT
timeout --kill-after=5s 15s sudo -n losetup --list --noheadings --output NAME,BACK-FILE | sort > "$directory/loops.txt"
timeout --kill-after=5s 15s findmnt --raw --noheadings --output TARGET,SOURCE,MAJ:MIN | sort > "$directory/mounts.txt"
timeout --kill-after=5s 15s sudo -n dmsetup info --columns --noheadings --options name,uuid | sort > "$directory/mappers.txt"
find /tmp -maxdepth 1 \( -name 'kora616-quota-*' -o -name 'kora616-sync-*' \) -print | sort > "$directory/roots.txt"
