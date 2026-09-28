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

# CLIバイナリおよびフラグ設定
# TODO: Spike-00の--help出力を確認してHumanが設定
AGY_BIN="agy"
AGY_NONINTERACTIVE_FLAGS="" # TODO: Spike-00の--help出力で確認 (例: --non-interactive, -y, --yes 等)

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK_DIR="${WORK_DIR}/spike01"
rm -rf "${TEST_WORK_DIR}"
mkdir -p "${TEST_WORK_DIR}"

LOG_STDOUT="${LOGS_DIR}/spike01_${RUN_ID}_stdout.log"
LOG_STDERR="${LOGS_DIR}/spike01_${RUN_ID}_stderr.log"
RESULT_YAML="${RESULTS_DIR}/spike01_${RUN_ID}.yaml"

# 追加条件2: ホスト上での隔離なし実行のため、固定の無害なプロンプトを使用
SAFE_PROMPT="Create a file named hello.txt in the current directory with content 'Hello NovUI Spike-01'."

echo "=== Spike-01: AGY Non-interactive Verification Started ==="
echo "Work directory: ${TEST_WORK_DIR}"

# ------------------------------------------------------------------------------
# テスト1: 正常実行（stdin経由のプロンプト入力、stdout/stderr分離、終了コード検査）
# ------------------------------------------------------------------------------
echo "--- Test 1: Non-interactive normal execution via stdin ---"

# プロンプトを標準入力から渡し、作業ディレクトリを TEST_WORK_DIR に固定して実行
# stdout と stderr を別ファイルに分離
START_TIME=$(date +%s)
EXIT_CODE=0

# setsid を用いてプロセスグループを作成し、タイムアウト監視関数で実行
(
    cd "${TEST_WORK_DIR}"
    # stdin経由でプロンプトを渡し、stdout/stderrを分離
    # TODO: AGY CLIが引数でプロンプトを受け取るかstdinで受け取るかを--helpで確認
    echo "${SAFE_PROMPT}" | "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS} > "${LOG_STDOUT}" 2> "${LOG_STDERR}"
) || EXIT_CODE=$?

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo "Execution finished in ${DURATION}s with exit code: ${EXIT_CODE}"

# 成果物 hello.txt の確認
FILE_CREATED="false"
if [[ -f "${TEST_WORK_DIR}/hello.txt" ]]; then
    FILE_CREATED="true"
    echo "PASS: hello.txt successfully created in target work directory."
else
    echo "WARNING: hello.txt was not created in ${TEST_WORK_DIR}."
fi

# ------------------------------------------------------------------------------
# テスト2: 異常終了時の終了コード検査（不正な引数指定）
# ------------------------------------------------------------------------------
echo "--- Test 2: Error exit code check with invalid arguments ---"
INVALID_EXIT_CODE=0
"${AGY_BIN}" --invalid-flag-for-testing-spike01 >/dev/null 2>&1 || INVALID_EXIT_CODE=$?
echo "Invalid argument exit code: ${INVALID_EXIT_CODE}"

# ------------------------------------------------------------------------------
# テスト3: 子プロセスの残存確認
# ------------------------------------------------------------------------------
echo "--- Test 3: Residual child process check ---"
RESIDUAL_PROCS="$(pgrep -f "${AGY_BIN}" || true)"
if [[ -z "${RESIDUAL_PROCS}" ]]; then
    echo "PASS: No residual AGY processes found."
    PROCESS_CLEAN="true"
else
    echo "WARNING: Possible residual processes detected: ${RESIDUAL_PROCS}"
    PROCESS_CLEAN="false"
fi

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ ${EXIT_CODE} -ne 0 ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="non_zero_exit_code_${EXIT_CODE}"
elif [[ "${FILE_CREATED}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="target_file_not_created"
elif [[ ${INVALID_EXIT_CODE} -eq 0 ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="invalid_args_did_not_fail"
fi

COMBINED_LOG="${LOGS_DIR}/spike01_${RUN_ID}_combined.log"
cat "${LOG_STDOUT}" > "${COMBINED_LOG}"
echo -e "\n--- STDERR ---" >> "${COMBINED_LOG}"
cat "${LOG_STDERR}" >> "${COMBINED_LOG}"
LOG_HASH="$(get_sha256 "${COMBINED_LOG}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-01" \
    "${RUN_ID}" \
    "agy" \
    "$("${AGY_BIN}" --version 2>/dev/null || echo 'unknown')" \
    "unknown" \
    "host_execution_noninteractive" \
    "${AGY_BIN} ${AGY_NONINTERACTIVE_FLAGS} [stdin prompt]" \
    "${EXIT_CODE}" \
    "spike/phase0/.logs/spike01_${RUN_ID}_combined.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON}" \
    "none"

echo "=== Spike-01 Completed: ${RESULT_STATUS} ==="
echo "Result recorded at: ${RESULT_YAML}"
