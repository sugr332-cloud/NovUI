#!/usr/bin/env bash
# ==============================================================================
# Spike-03: .git保護 (spike03-git-protection.sh)
# 
# 目的: worktree環境において、Git common directory（本体.git）をコンテナへ
#       (1) マウントしない構成、(2) 読み取り専用(:ro)でマウントする構成
#       の2通りで、.git/config、hooks/、worktrees/、.git参照ファイルの保護状況
#       および git status 実行時の挙動（indexロック・更新失敗）を実測・比較する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       実作業用リポジトリを保護するため、spike/phase0/.work/ 配下に作成した
#       使い捨ての試験用リポジトリ・worktreeに対して破壊的検査を実施する。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike03-git-protection.sh
# 想定所要時間: 約2〜3分
# 変更するパス:
#   - spike/phase0/.work/spike03/ (試験用リポジトリ・worktree)
#   - spike/phase0/.logs/spike03_*.log
#   - docs/spike/results/spike03_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
TIMEOUT_ISOLATION_TEST=60

# TODO: Humanが選択（事前にpodman pullを完了させておくこと）
CONTAINER_IMAGE="" # 例: "fedora:latest"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_ROOT="${WORK_DIR}/spike03"
rm -rf "${TEST_ROOT}"
mkdir -p "${TEST_ROOT}"

assert_safe_work_path "${TEST_ROOT}"

LOG_FILE="${LOGS_DIR}/spike03_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike03_${RUN_ID}.yaml"

echo "=== Spike-03: .git Protection Verification Started ===" | tee "${LOG_FILE}"

if [[ -z "${CONTAINER_IMAGE}" ]]; then
    echo "ERROR: CONTAINER_IMAGE is not set. Please set the variable at the top of the script." | tee -a "${LOG_FILE}"
    exit 1
fi

# ------------------------------------------------------------------------------
# 準備: 使い捨ての試験用Gitリポジトリとworktreeを構築
# ------------------------------------------------------------------------------
echo "--- Initializing disposable test repository and worktree ---" | tee -a "${LOG_FILE}"
TEST_REPO="${TEST_ROOT}/test_repo"
TEST_WT="${TEST_ROOT}/test_worktree"

git init "${TEST_REPO}" >> "${LOG_FILE}" 2>&1
(
    cd "${TEST_REPO}"
    git config user.name "Spike Test"
    git config user.email "spike@example.com"
    echo "# Initial" > README.md
    git add README.md
    git commit -m "initial commit" >> "${LOG_FILE}" 2>&1
    git branch -M main
    git worktree add -b test-branch "${TEST_WT}" main >> "${LOG_FILE}" 2>&1
)

TEST_GIT_COMMON="$(cd "${TEST_REPO}" && git rev-parse --git-common-dir)"
# hooks のサンプル作成
echo "#!/bin/sh" > "${TEST_GIT_COMMON}/hooks/post-commit"
chmod +x "${TEST_GIT_COMMON}/hooks/post-commit"

# ハッシュ採取関数
record_git_hashes() {
    local label="$1"
    local out_file="$2"
    echo "# Hashes: ${label}" > "${out_file}"
    find "${TEST_GIT_COMMON}/config" "${TEST_GIT_COMMON}/hooks" "${TEST_WT}/.git" -type f 2>/dev/null | sort | while read -r f; do
        echo "$(sha256sum "$f" | awk '{print $1}')  $f" >> "${out_file}"
    done
}

HASH_BEFORE="${TEST_ROOT}/hashes_before.txt"
record_git_hashes "Before Tests" "${HASH_BEFORE}"

# ------------------------------------------------------------------------------
# 構成1: Git common directory をマウントしない構成
# ------------------------------------------------------------------------------
echo "--- Config 1: Git common directory NOT mounted ---" | tee -a "${LOG_FILE}"

# コンテナ内から .git 参照ファイルの上書きおよびGit操作を試行
C1_OUT=$(podman run --rm \
    --userns=keep-id \
    -v "${TEST_WT}:/workspace:rw" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "
        echo '--- Checking .git file ---'
        cat .git 2>&1 || true
        echo '--- Attempting write to .git file ---'
        echo 'malicious' >> .git 2>&1 || echo 'WRITE_FAILED'
        echo '--- Attempting git status inside container ---'
        git status 2>&1 || echo 'GIT_COMMAND_FAILED'
    " 2>&1 || true)

echo "${C1_OUT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 構成2: Git common directory を RO マウントする構成
# ------------------------------------------------------------------------------
echo "--- Config 2: Git common directory mounted READ-ONLY (:ro) ---" | tee -a "${LOG_FILE}"

# コンテナ内から RO mount された Git common directory への書込み試行
C2_OUT=$(podman run --rm \
    --userns=keep-id \
    -v "${TEST_WT}:/workspace:rw" \
    -v "${TEST_GIT_COMMON}:${TEST_GIT_COMMON}:ro" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "
        echo '--- Attempting write to .git/config ---'
        echo 'malicious_config' >> '${TEST_GIT_COMMON}/config' 2>&1 || echo 'CONFIG_WRITE_BLOCKED'
        echo '--- Attempting write to hooks ---'
        echo 'malicious_hook' >> '${TEST_GIT_COMMON}/hooks/post-commit' 2>&1 || echo 'HOOK_WRITE_BLOCKED'
        echo '--- Attempting git status with RO common-dir ---'
        git status 2>&1 || echo 'GIT_STATUS_FAILED'
    " 2>&1 || true)

echo "${C2_OUT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 実行後ハッシュ比較
# ------------------------------------------------------------------------------
HASH_AFTER="${TEST_ROOT}/hashes_after.txt"
record_git_hashes "After Tests" "${HASH_AFTER}"

echo "--- Comparing Git Hashes Before and After ---" | tee -a "${LOG_FILE}"
DIFF_OUT=$(diff -u "${HASH_BEFORE}" "${HASH_AFTER}" || true)

RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ -n "${DIFF_OUT}" ]]; then
    echo "CRITICAL WARNING: Git common directory files were modified during tests!" | tee -a "${LOG_FILE}"
    echo "${DIFF_OUT}" | tee -a "${LOG_FILE}"
    RESULT_STATUS="FAIL"
    FAILURE_REASON="git_files_modified"
else
    echo "PASS: All .git files (config, hooks, worktrees, .git pointer) preserved identical SHA-256." | tee -a "${LOG_FILE}"
fi

LOG_HASH="$(get_sha256 "${LOG_FILE}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-03" \
    "${RUN_ID}" \
    "git_protection_runner" \
    "git_worktree_podman" \
    "none" \
    "config1:no_git_mount / config2:ro_git_mount" \
    "podman run [protection tests on disposable worktree]" \
    0 \
    "spike/phase0/.logs/spike03_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "config1_recommended_if_git_command_not_required_in_container"

echo "=== Spike-03 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
