set -euo pipefail
bash inventory.sh before
test ! -s "$RUNNER_TEMP/kora616-evidence/before/roots.txt"
for experiment in quota sync; do
  script=run_linux.py
  if [ "$experiment" = sync ]; then
    script=run_blocked_sync.py
  fi
  set +e
  sudo -n /usr/bin/python3 "$PWD/$script" > "$RUNNER_TEMP/kora616-evidence/$experiment-result.jsonl" 2> "$RUNNER_TEMP/kora616-evidence/$experiment-error.log"
  result=$?
  set -e
  printf '%s\n' "$result" > "$RUNNER_TEMP/kora616-evidence/$experiment-status.txt"
  bash inventory.sh "after-$experiment"
  for resource in loops mounts mappers roots; do
    diff -u "$RUNNER_TEMP/kora616-evidence/before/$resource.txt" "$RUNNER_TEMP/kora616-evidence/after-$experiment/$resource.txt"
  done
  test "$result" -eq 0
done
