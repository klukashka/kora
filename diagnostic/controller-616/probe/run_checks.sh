set -euo pipefail
bash inventory.sh before
test ! -s "$RUNNER_TEMP/kora616-evidence/before/roots.txt"
experiment_evidence="/tmp/kora616-evidence-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
test ! -e "$experiment_evidence"
set +e
sudo -n /usr/bin/python3 "$PWD/run_owned_service.py" "$GITHUB_WORKSPACE/subject" "$experiment_evidence" > "$RUNNER_TEMP/kora616-evidence/controller-result.jsonl" 2> "$RUNNER_TEMP/kora616-evidence/controller-error.log"
result=$?
set -e
printf '%s\n' "$result" > "$RUNNER_TEMP/kora616-evidence/controller-status.txt"
if [ -d "$experiment_evidence" ]; then
  sudo -n cp -a "$experiment_evidence" "$RUNNER_TEMP/kora616-evidence/native"
  sudo -n chown -R "$(id -u):$(id -g)" "$RUNNER_TEMP/kora616-evidence/native"
fi
if [ -d "${experiment_evidence}-service" ]; then
  sudo -n cp -a "${experiment_evidence}-service" "$RUNNER_TEMP/kora616-evidence/service"
  sudo -n chown -R "$(id -u):$(id -g)" "$RUNNER_TEMP/kora616-evidence/service"
fi
bash inventory.sh after
for resource in loops mounts mappers roots; do
  diff -u "$RUNNER_TEMP/kora616-evidence/before/$resource.txt" "$RUNNER_TEMP/kora616-evidence/after/$resource.txt"
done
test "$result" -eq 0
