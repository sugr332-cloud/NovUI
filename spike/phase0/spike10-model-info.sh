#!/usr/bin/env bash
# ==============================================================================
# Spike-10: モデル情報 (spike10-model-info.sh)
# 
# 目的: AGY CLI および Claude CLI から、Job単位でモデル情報（provider、
#       CLIバージョン、報告されるモデル名、モデルバージョン、取得方法）を
#       取得可能か実測し、取得できる項目と取得できない項目を確定・記録する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       Spike-00を実行し、各CLIが利用可能であること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike10-model-info.sh
# 想定所要時間: 約30秒
# 変更するパス:
#   - spike/phase0/.logs/spike10_*.log
#   - docs/spike/results/spike10_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
TIMEOUT_AGY_TOTAL=900
TIMEOUT_AGY_NO_OUTPUT=180
TIMEOUT_CLAUDE_TOTAL=300
TIMEOUT_CLAUDE_NO_OUTPUT=120

# CLIバイナリ（Spike-00 で確定）
AGY_BIN="agy"
AGY_FLAGS="--print --mode accept-edits"
CLAUDE_BIN="claude"
CLAUDE_READONLY_FLAGS="-p --tools Read --permission-prompts none --no-session-persistence"

# モデル情報取得用フラグ（もしCLIに専用フラグが存在する場合）
# TODO: Spike-00の--help出力を参照
AGY_MODEL_FLAG="" # 例: --model-info または --version
CLAUDE_MODEL_FLAG="" # 例: --model-info または --version

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK_DIR="${WORK_DIR}/spike10"
rm -rf "${TEST_WORK_DIR}"
# 使い捨て作業領域の安全性検証と作成
assert_safe_work_path "${TEST_WORK_DIR}"

LOG_FILE="${LOGS_DIR}/spike10_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike10_${RUN_ID}.yaml"

echo "=== Spike-10: Model Information Inspection Started ===" | tee "${LOG_FILE}"

# フラグ変数空チェック（修正E）
if [[ -z "${AGY_FLAGS}" ]]; then
    echo "ERROR: AGY_FLAGS is not set. Please inspect Spike-00 --help output and configure non-interactive flags." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${CLAUDE_READONLY_FLAGS}" ]]; then
    echo "ERROR: CLAUDE_READONLY_FLAGS is not set. Please inspect Spike-00 --help output and configure read-only flags." | tee -a "${LOG_FILE}"
    exit 1
fi

# CLI実行前の git status 差分検査
GIT_SAFETY_BEFORE="$(check_git_status_safety)"
if [[ "${GIT_SAFETY_BEFORE}" != "CLEAN" ]]; then
    echo "WARNING: Pre-existing git status modifications detected." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 1. AGY CLI モデル情報確認
# ------------------------------------------------------------------------------
echo "--- 1. AGY CLI Model Information ---" | tee -a "${LOG_FILE}"
AGY_RAW_INFO=""
AGY_REPORTED_MODEL="not_available"

if command -v "${AGY_BIN}" >/dev/null 2>&1; then
    AGY_RAW_INFO="$("${AGY_BIN}" ${AGY_MODEL_FLAG:-"--version"} 2>&1 || true)"
    # 構造化メタデータフラグが指定されている場合のみ機械的に抽出を試みる
    if [[ -n "${AGY_MODEL_FLAG}" ]]; then
        AGY_REPORTED_MODEL="${AGY_RAW_INFO}"
    fi
else
    AGY_RAW_INFO="cli_not_found"
fi
echo "AGY Raw Output: ${AGY_RAW_INFO}" | tee -a "${LOG_FILE}"
echo "AGY Reported Model (CLI Metadata): ${AGY_REPORTED_MODEL}" | tee -a "${LOG_FILE}"

# 実行時プロンプトによるモデル名自己申告テスト（使い捨て作業ディレクトリで run_monitored_command を通して実行）
echo "Testing AGY model self-report query (separated from CLI metadata)..." | tee -a "${LOG_FILE}"
AGY_SELF_REPORT="not_available"
if command -v "${AGY_BIN}" >/dev/null 2>&1; then
    AGY_PROMPT_FILE="${TEST_WORK_DIR}/prompt_agy_model.txt"
    echo "Reply with your exact model name and version only." > "${AGY_PROMPT_FILE}"
    AGY_SELF_OUT="${TEST_WORK_DIR}/agy_self_report_out.log"
    AGY_SELF_ERR="${TEST_WORK_DIR}/agy_self_report_err.log"

    run_monitored_command \
        "${TIMEOUT_AGY_TOTAL}" \
        "${TIMEOUT_AGY_NO_OUTPUT}" \
        "${AGY_SELF_OUT}" \
        "${AGY_SELF_ERR}" \
        "${AGY_PROMPT_FILE}" \
        env -C "${TEST_WORK_DIR}" "${AGY_BIN}" ${AGY_FLAGS}

    if [[ -f "${AGY_SELF_OUT}" ]]; then
        AGY_SELF_REPORT="$(cat "${AGY_SELF_OUT}")"
    fi
else
    AGY_SELF_REPORT="skipped(cli_not_found)"
fi
echo "AGY Self Report (Human/Prompt response): ${AGY_SELF_REPORT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 2. Claude CLI モデル情報確認
# ------------------------------------------------------------------------------
echo "--- 2. Claude CLI Model Information ---" | tee -a "${LOG_FILE}"
CLAUDE_RAW_INFO=""
CLAUDE_REPORTED_MODEL="not_available"

if command -v "${CLAUDE_BIN}" >/dev/null 2>&1; then
    CLAUDE_RAW_INFO="$("${CLAUDE_BIN}" ${CLAUDE_MODEL_FLAG:-"--version"} 2>&1 || true)"
    if [[ -n "${CLAUDE_MODEL_FLAG}" ]]; then
        CLAUDE_REPORTED_MODEL="${CLAUDE_RAW_INFO}"
    fi
else
    CLAUDE_RAW_INFO="cli_not_found"
fi
echo "Claude Raw Output: ${CLAUDE_RAW_INFO}" | tee -a "${LOG_FILE}"
echo "Claude Reported Model (CLI Metadata): ${CLAUDE_REPORTED_MODEL}" | tee -a "${LOG_FILE}"

# 実行時プロンプトによるモデル名自己申告テスト（使い捨て作業ディレクトリで run_monitored_command を通して実行）
echo "Testing Claude model self-report query (separated from CLI metadata)..." | tee -a "${LOG_FILE}"
CLAUDE_SELF_REPORT="not_available"
if command -v "${CLAUDE_BIN}" >/dev/null 2>&1; then
    CLAUDE_PROMPT_FILE="${TEST_WORK_DIR}/prompt_claude_model.txt"
    echo "Reply with your exact model name and version only." > "${CLAUDE_PROMPT_FILE}"
    CLAUDE_SELF_OUT="${TEST_WORK_DIR}/claude_self_report_out.log"
    CLAUDE_SELF_ERR="${TEST_WORK_DIR}/claude_self_report_err.log"

    run_monitored_command \
        "${TIMEOUT_CLAUDE_TOTAL}" \
        "${TIMEOUT_CLAUDE_NO_OUTPUT}" \
        "${CLAUDE_SELF_OUT}" \
        "${CLAUDE_SELF_ERR}" \
        "${CLAUDE_PROMPT_FILE}" \
        env -C "${TEST_WORK_DIR}" "${CLAUDE_BIN}" ${CLAUDE_READONLY_FLAGS}

    if [[ -f "${CLAUDE_SELF_OUT}" ]]; then
        CLAUDE_SELF_REPORT="$(cat "${CLAUDE_SELF_OUT}")"
    fi
else
    CLAUDE_SELF_REPORT="skipped(cli_not_found)"
fi
echo "Claude Self Report (Human/Prompt response): ${CLAUDE_SELF_REPORT}" | tee -a "${LOG_FILE}"

# CLI実行後の git status 差分検査
GIT_SAFETY_AFTER="$(check_git_status_safety)"
GIT_DIFF_PASS="true"
if [[ "${GIT_SAFETY_AFTER}" != "CLEAN" ]]; then
    echo "FAIL: Unintended modifications detected outside allowable areas after CLI execution!" | tee -a "${LOG_FILE}"
    GIT_DIFF_PASS="false"
fi

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
LOG_HASH="$(get_sha256 "${LOG_FILE}")"
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${GIT_DIFF_PASS}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="unintended_git_diff"
fi

cat << EOF >> "${LOG_FILE}"

--- Model Metadata Capability Summary ---
AGY:
  CLI Version Obtainable: $(command -v "${AGY_BIN}" >/dev/null 2>&1 && echo "YES" || echo "NO")
  Reported Model (Metadata): ${AGY_REPORTED_MODEL}
  Self Report (Prompt): ${AGY_SELF_REPORT}
Claude:
  CLI Version Obtainable: $(command -v "${CLAUDE_BIN}" >/dev/null 2>&1 && echo "YES" || echo "NO")
  Reported Model (Metadata): ${CLAUDE_REPORTED_MODEL}
  Self Report (Prompt): ${CLAUDE_SELF_REPORT}
EOF

# reported_model は CLI構造化メタデータから取得できた値のみ（なければ not_available）
REPORTED_MODEL_VAL="AGY: ${AGY_REPORTED_MODEL} | Claude: ${CLAUDE_REPORTED_MODEL}"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-10" \
    "${RUN_ID}" \
    "agy / claude" \
    "version_check" \
    "${REPORTED_MODEL_VAL}" \
    "host_cli_query" \
    "${AGY_BIN} / ${CLAUDE_BIN} --version / prompt" \
    0 \
    "spike/phase0/.logs/spike10_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "self_report_recorded_separately_in_log"

echo "=== Spike-10 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
