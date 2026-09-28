#!/usr/bin/env bash
# ==============================================================================
# Spike-00: 事前確認・環境固定 (spike00-env.sh)
# 
# 目的: 実験開始前のホストOS、SELinux、Podman、Git、各CLIのバージョンおよび
#       Gitリポジトリ・worktree状態を記録・固定し、AGYおよびClaudeの--help出力を保存する。
# 前提: ホストOS（Bazzite）上で実行すること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike00-env.sh
# 想定所要時間: 約10秒
# 変更するパス:
#   - spike/phase0/.logs/spike00-env.log
#   - spike/phase0/.logs/agy_help.log
#   - spike/phase0/.logs/claude_help.log
#   - docs/spike/results/spike00_result.yaml
# ==============================================================================

set -euo pipefail

# 共通スクリプトの読み込み
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
# タイムアウト値（手順書5章）
TIMEOUT_ISOLATION_TEST=60

# CLIバイナリ名
# TODO: Humanがパスまたはエイリアスを確認
AGY_BIN="agy"
CLAUDE_BIN="claude"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
ENV_LOG="${LOGS_DIR}/spike00-env.log"
AGY_HELP_LOG="${LOGS_DIR}/agy_help.log"
CLAUDE_HELP_LOG="${LOGS_DIR}/claude_help.log"
RESULT_YAML="${RESULTS_DIR}/spike00_${RUN_ID}.yaml"

echo "=== Spike-00: Environment Verification Started ===" | tee "${ENV_LOG}"

# 1. OS / Kernel
echo "--- OS / Kernel ---" | tee -a "${ENV_LOG}"
uname -a | tee -a "${ENV_LOG}"
if [[ -f /etc/os-release ]]; then
    cat /etc/os-release | tee -a "${ENV_LOG}"
fi

# 2. SELinux
echo "--- SELinux Status ---" | tee -a "${ENV_LOG}"
SELINUX_STATUS="unknown"
if command -v getenforce >/dev/null 2>&1; then
    SELINUX_STATUS="$(getenforce)"
elif [[ -f /sys/fs/selinux/enforce ]]; then
    if [[ "$(cat /sys/fs/selinux/enforce)" == "1" ]]; then
        SELINUX_STATUS="Enforcing"
    else
        SELINUX_STATUS="Permissive"
    fi
fi
echo "SELinux: ${SELINUX_STATUS}" | tee -a "${ENV_LOG}"

# 3. Podman
echo "--- Podman Version and Rootless Check ---" | tee -a "${ENV_LOG}"
PODMAN_VER="not_installed"
IS_ROOTLESS="unknown"
if command -v podman >/dev/null 2>&1; then
    PODMAN_VER="$(podman --version)"
    IS_ROOTLESS="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo 'unknown')"
fi
echo "Podman: ${PODMAN_VER} (Rootless: ${IS_ROOTLESS})" | tee -a "${ENV_LOG}"

# 4. Git
echo "--- Git Version and Repository Status ---" | tee -a "${ENV_LOG}"
GIT_VER="$(git --version)"
REPO_HEAD="$(git rev-parse HEAD)"
CURRENT_BRANCH="$(git branch --show-current)"
CURRENT_WORKTREE="$(pwd -P)"
COMMON_DIR="$(git rev-parse --git-common-dir)"
echo "Git: ${GIT_VER}" | tee -a "${ENV_LOG}"
echo "HEAD: ${REPO_HEAD} (branch: ${CURRENT_BRANCH})" | tee -a "${ENV_LOG}"
echo "Worktree Root: ${CURRENT_WORKTREE}" | tee -a "${ENV_LOG}"
echo "Git Common Dir: ${COMMON_DIR}" | tee -a "${ENV_LOG}"

# 5. AGY CLI
echo "--- AGY CLI Info and --help ---" | tee -a "${ENV_LOG}"
AGY_VER="not_found"
if command -v "${AGY_BIN}" >/dev/null 2>&1; then
    AGY_VER="$("${AGY_BIN}" --version 2>/dev/null || echo "version_flag_unsupported")"
    "${AGY_BIN}" --help > "${AGY_HELP_LOG}" 2>&1 || true
    echo "AGY CLI Help saved to: ${AGY_HELP_LOG}" | tee -a "${ENV_LOG}"
else
    echo "WARNING: AGY CLI '${AGY_BIN}' not found in PATH." | tee -a "${ENV_LOG}"
    echo "AGY CLI not found" > "${AGY_HELP_LOG}"
fi
echo "AGY Version: ${AGY_VER}" | tee -a "${ENV_LOG}"

# 6. Claude CLI
echo "--- Claude CLI Info and --help ---" | tee -a "${ENV_LOG}"
CLAUDE_VER="not_found"
if command -v "${CLAUDE_BIN}" >/dev/null 2>&1; then
    CLAUDE_VER="$("${CLAUDE_BIN}" --version 2>/dev/null || echo "version_flag_unsupported")"
    "${CLAUDE_BIN}" --help > "${CLAUDE_HELP_LOG}" 2>&1 || true
    echo "Claude CLI Help saved to: ${CLAUDE_HELP_LOG}" | tee -a "${ENV_LOG}"
else
    echo "WARNING: Claude CLI '${CLAUDE_BIN}' not found in PATH." | tee -a "${ENV_LOG}"
    echo "Claude CLI not found" > "${CLAUDE_HELP_LOG}"
fi
echo "Claude Version: ${CLAUDE_VER}" | tee -a "${ENV_LOG}"

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
LOG_HASH="$(get_sha256 "${ENV_LOG}")"
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${PODMAN_VER}" == "not_installed" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="podman_not_installed"
elif [[ "${IS_ROOTLESS}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="podman_not_rootless"
fi

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-00" \
    "${RUN_ID}" \
    "system" \
    "${AGY_VER} / ${CLAUDE_VER}" \
    "none" \
    "host_execution" \
    "./spike/phase0/spike00-env.sh" \
    0 \
    "spike/phase0/.logs/spike00-env.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON}" \
    "none"

echo "=== Spike-00 Completed: ${RESULT_STATUS} ===" | tee -a "${ENV_LOG}"
echo "Result recorded at: ${RESULT_YAML}"
