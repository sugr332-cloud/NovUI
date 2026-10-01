#!/usr/bin/env bash
# ==============================================================================
# Spike-01: AGY非対話実行 (spike01-agy-noninteractive.sh)
# 
# 目的: ホスト上でAGY CLIを非対話形式で実行し、標準入力からの指示受渡し、
#       stdout/stderr分離、終了コード、タイムアウト（全体・無出力）制御、
#       指定作業ディレクトリへの隔離、プロセス終了後の残存有無を実測する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       Spike-00を実行し、AGY CLIの存在と--helpを確認済みであること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike01-agy-noninteractive.sh
# 想定所要時間: 約1〜3分
# 変更するパス:
#   - spike/phase0/.work/spike01/ (使い捨て作業ディレクトリ)
#   - spike/phase0/.logs/spike01_*.log
#   - docs/spike/results/spike01_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
# タイムアウト値（手順書5章）
TIMEOUT_AGY_TOTAL=900
TIMEOUT_AGY_NO_OUTPUT=180

# CLIバイナリおよびフラグ設定（Spike-00 で確定）
AGY_BIN="agy"
AGY_NONINTERACTIVE_FLAGS="--mode accept-edits"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK_DIR="${WORK_DIR}/spike01"
rm -rf "${TEST_WORK_DIR}"
# 使い捨て作業ディレクトリの安全性検証と作成
assert_safe_work_path "${TEST_WORK_DIR}"

LOG_STDOUT="${LOGS_DIR}/spike01_${RUN_ID}_stdout.log"
LOG_STDERR="${LOGS_DIR}/spike01_${RUN_ID}_stderr.log"
RESULT_YAML="${RESULTS_DIR}/spike01_${RUN_ID}.yaml"

# 非対話フラグ未設定時のエラー終了
if [[ -z "${AGY_NONINTERACTIVE_FLAGS}" ]]; then
    echo "ERROR: AGY_NONINTERACTIVE_FLAGS is not set. Please inspect Spike-00 --help output and configure non-interactive flags." >&2
    exit 1
fi

# 追加条件2: ホスト上での隔離なし実行のため、固定の無害なプロンプトを使用
SAFE_PROMPT="Create a file named hello.txt in the current directory with content 'Hello NovUI Spike-01'."
SAFE_PROMPT_FILE="${TEST_WORK_DIR}/prompt.txt"
echo "${SAFE_PROMPT}" > "${SAFE_PROMPT_FILE}"

echo "=== Spike-01: AGY Non-interactive Verification Started ==="
echo "Work directory: ${TEST_WORK_DIR}"

# ------------------------------------------------------------------------------
# テスト1: 正常実行（--print=<プロンプト> 形式での非対話実行）
# ------------------------------------------------------------------------------
echo "--- Test 1: Non-interactive normal execution via --print=<prompt> ---"

# CLI実行前の git status 差分検査
GIT_SAFETY_BEFORE="$(check_git_status_safety)"
if [[ "${GIT_SAFETY_BEFORE}" != "CLEAN" ]]; then
    echo "WARNING: Pre-existing git status modifications detected." >&2
fi

PROMPT_TEXT="$(cat "${SAFE_PROMPT_FILE}")"

# プロンプトを --print="${PROMPT_TEXT}" として渡し、stdin_file は空で監視付き実行
run_monitored_command \
    "${TIMEOUT_AGY_TOTAL}" \
    "${TIMEOUT_AGY_NO_OUTPUT}" \
    "${LOG_STDOUT}" \
    "${LOG_STDERR}" \
    "" \
    env -C "${TEST_WORK_DIR}" "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS} "--print=${PROMPT_TEXT}"

EXIT_CODE="${LAST_CMD_EXIT_CODE}"
DURATION="${LAST_CMD_DURATION}"
TIMED_OUT="${LAST_CMD_TIMED_OUT}"
TERM_SIGNAL="${LAST_CMD_SIGNAL}"
PGID="${LAST_CMD_PGID}"

echo "Execution finished in ${DURATION}s with exit code: ${EXIT_CODE} (timed_out: ${TIMED_OUT}, signal: ${TERM_SIGNAL}, PGID: ${PGID})"

# CLI実行後の git status 差分検査
GIT_SAFETY_AFTER="$(check_git_status_safety)"
GIT_DIFF_PASS="true"
if [[ "${GIT_SAFETY_AFTER}" != "CLEAN" ]]; then
    echo "FAIL: Unintended modifications detected outside allowable areas after AGY CLI execution!" >&2
    GIT_DIFF_PASS="false"
fi

# 成果物 hello.txt の確認
FILE_CREATED="false"
if [[ -f "${TEST_WORK_DIR}/hello.txt" ]]; then
    FILE_CREATED="true"
    echo "PASS: hello.txt successfully created in target work directory."
    echo "hello.txt content: $(head -n 1 "${TEST_WORK_DIR}/hello.txt")"
else
    echo "WARNING: hello.txt was not created in ${TEST_WORK_DIR}."
fi

# ------------------------------------------------------------------------------
# テスト1b: 標準入力の参考試験（合否判定には含めない）
# ------------------------------------------------------------------------------
echo "--- Test 1b: Reference test - execution via stdin with --print (informational only) ---"
STDIN_TEST_WORK="${TEST_WORK_DIR}/stdin_test"
assert_safe_work_path "${STDIN_TEST_WORK}"

STDIN_PROMPT_FILE="${STDIN_TEST_WORK}/prompt.txt"
echo "${SAFE_PROMPT}" > "${STDIN_PROMPT_FILE}"

STDIN_OUT="${LOGS_DIR}/spike01_${RUN_ID}_stdin_stdout.log"
STDIN_ERR="${LOGS_DIR}/spike01_${RUN_ID}_stdin_stderr.log"

run_monitored_command \
    "${TIMEOUT_AGY_TOTAL}" \
    "${TIMEOUT_AGY_NO_OUTPUT}" \
    "${STDIN_OUT}" \
    "${STDIN_ERR}" \
    "${STDIN_PROMPT_FILE}" \
    env -C "${STDIN_TEST_WORK}" "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS} --print

TEST1B_EXIT_CODE="${LAST_CMD_EXIT_CODE}"
TEST1B_HELLO_EXISTS="false"
if [[ -f "${STDIN_TEST_WORK}/hello.txt" ]]; then
    TEST1B_HELLO_EXISTS="true"
fi
TEST1B_ERR_MSG="$(cat "${STDIN_ERR}" 2>/dev/null || echo '')"

echo "Test 1b exit code: ${TEST1B_EXIT_CODE}"
echo "Test 1b hello.txt exists: ${TEST1B_HELLO_EXISTS}"
echo "Test 1b stderr message: ${TEST1B_ERR_MSG}"

# ------------------------------------------------------------------------------
# テスト2: 異常終了時の終了コード検査（不正な引数指定）
# ------------------------------------------------------------------------------
echo "--- Test 2: Error exit code check with invalid arguments ---"
INVALID_EXIT_CODE=0
"${AGY_BIN}" --invalid-flag-for-testing-spike01 >/dev/null 2>&1 || INVALID_EXIT_CODE=$?
echo "Invalid argument exit code: ${INVALID_EXIT_CODE}"

# ------------------------------------------------------------------------------
# テスト3: PGID / 子プロセスの残存確認
# ------------------------------------------------------------------------------
echo "--- Test 3: Residual child process check ---"
RESIDUAL_PROCS="$(ps -o pid= -g "${PGID}" 2>/dev/null || true)"
if [[ -z "${RESIDUAL_PROCS//[[:space:]]/}" ]]; then
    echo "PASS: No residual processes found for PGID ${PGID}."
    PROCESS_CLEAN="true"
else
    echo "WARNING: Possible residual processes detected for PGID ${PGID}: ${RESIDUAL_PROCS}"
    PROCESS_CLEAN="false"
fi

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${GIT_DIFF_PASS}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="unintended_git_diff"
elif [[ "${TIMED_OUT}" != "none" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="timeout_${TIMED_OUT}"
elif [[ ${EXIT_CODE} -ne 0 ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="non_zero_exit_code_${EXIT_CODE}"
elif [[ "${FILE_CREATED}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="target_file_not_created"
elif [[ ${INVALID_EXIT_CODE} -eq 0 ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="invalid_args_did_not_fail"
elif [[ "${PROCESS_CLEAN}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="residual_processes_detected"
fi

COMBINED_LOG="${LOGS_DIR}/spike01_${RUN_ID}_combined.log"
cat "${LOG_STDOUT}" "${LOG_STDERR}" > "${COMBINED_LOG}"
LOG_HASH="$(get_sha256 "${COMBINED_LOG}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-01" \
    "${RUN_ID}" \
    "agy" \
    "$("${AGY_BIN}" --version 2>/dev/null || echo 'unknown')" \
    "unknown" \
    "host_execution_noninteractive" \
    "${AGY_BIN} ${AGY_NONINTERACTIVE_FLAGS} --print=..." \
    "${EXIT_CODE}" \
    "spike/phase0/.logs/spike01_${RUN_ID}_combined.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "none"

echo "=== Spike-01 Completed: ${RESULT_STATUS} ==="
echo "Result recorded at: ${RESULT_YAML}"
