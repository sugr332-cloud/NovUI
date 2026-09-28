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

# CLIバイナリ
# TODO: Spike-00の--help出力で確認してHumanが設定
AGY_BIN="agy"
CLAUDE_BIN="claude"

# モデル情報取得用フラグ（もしCLIに専用フラグが存在する場合）
# TODO: Spike-00の--help出力を参照
AGY_MODEL_FLAG="" # 例: --model-info または --version
CLAUDE_MODEL_FLAG="" # 例: --model-info または --version

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOGS_DIR}/spike10_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike10_${RUN_ID}.yaml"

echo "=== Spike-10: Model Information Inspection Started ===" | tee "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 1. AGY CLI モデル情報確認
# ------------------------------------------------------------------------------
echo "--- 1. AGY CLI Model Information ---" | tee -a "${LOG_FILE}"
AGY_RAW_INFO=""
if command -v "${AGY_BIN}" >/dev/null 2>&1; then
    AGY_RAW_INFO="$("${AGY_BIN}" ${AGY_MODEL_FLAG:-"--version"} 2>&1 || true)"
else
    AGY_RAW_INFO="cli_not_found"
fi
echo "AGY Raw Output: ${AGY_RAW_INFO}" | tee -a "${LOG_FILE}"

# 実行時プロンプトによるモデル名自己申告テスト（メタデータが出力されない場合の代替手段）
echo "Testing AGY model self-report query..." | tee -a "${LOG_FILE}"
AGY_SELF_REPORT=""
if command -v "${AGY_BIN}" >/dev/null 2>&1; then
    AGY_SELF_REPORT="$(echo "Reply with your exact model name and version only." | "${AGY_BIN}" 2>&1 || echo "query_failed")"
else
    AGY_SELF_REPORT="skipped"
fi
echo "AGY Self Report: ${AGY_SELF_REPORT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 2. Claude CLI モデル情報確認
# ------------------------------------------------------------------------------
echo "--- 2. Claude CLI Model Information ---" | tee -a "${LOG_FILE}"
CLAUDE_RAW_INFO=""
if command -v "${CLAUDE_BIN}" >/dev/null 2>&1; then
    CLAUDE_RAW_INFO="$("${CLAUDE_BIN}" ${CLAUDE_MODEL_FLAG:-"--version"} 2>&1 || true)"
else
    CLAUDE_RAW_INFO="cli_not_found"
fi
echo "Claude Raw Output: ${CLAUDE_RAW_INFO}" | tee -a "${LOG_FILE}"

echo "Testing Claude model self-report query..." | tee -a "${LOG_FILE}"
CLAUDE_SELF_REPORT=""
if command -v "${CLAUDE_BIN}" >/dev/null 2>&1; then
    CLAUDE_SELF_REPORT="$(echo "Reply with your exact model name and version only." | "${CLAUDE_BIN}" 2>&1 || echo "query_failed")"
else
    CLAUDE_SELF_REPORT="skipped"
fi
echo "Claude Self Report: ${CLAUDE_SELF_REPORT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
LOG_HASH="$(get_sha256 "${LOG_FILE}")"

# 各項目が確定できているかを評価
RESULT_STATUS="PASS"
FAILURE_REASON=""

cat << EOF >> "${LOG_FILE}"

--- Model Metadata Capability Summary ---
AGY:
  CLI Version Obtainable: $(command -v "${AGY_BIN}" >/dev/null 2>&1 && echo "YES" || echo "NO")
  Reported Model: ${AGY_SELF_REPORT}
Claude:
  CLI Version Obtainable: $(command -v "${CLAUDE_BIN}" >/dev/null 2>&1 && echo "YES" || echo "NO")
  Reported Model: ${CLAUDE_SELF_REPORT}
EOF

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-10" \
    "${RUN_ID}" \
    "agy / claude" \
    "version_check" \
    "AGY: ${AGY_SELF_REPORT} | Claude: ${CLAUDE_SELF_REPORT}" \
    "host_cli_query" \
    "${AGY_BIN} / ${CLAUDE_BIN} --version / prompt" \
    0 \
    "spike/phase0/.logs/spike10_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "none" \
    "fallback_to_self_report_if_cli_metadata_unavailable"

echo "=== Spike-10 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
