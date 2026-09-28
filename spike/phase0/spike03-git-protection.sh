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
# 準備: 使い捨ての試験用Gitリポジトリと2つの独立したworktreeを構築
# ------------------------------------------------------------------------------
echo "--- Initializing disposable test repository and 2 independent worktrees ---" | tee -a "${LOG_FILE}"
TEST_REPO="${TEST_ROOT}/test_repo"
TEST_WT_1="${TEST_ROOT}/test_worktree_1"
TEST_WT_2="${TEST_ROOT}/test_worktree_2"

assert_safe_work_path "${TEST_REPO}"
assert_safe_work_path "${TEST_WT_1}"
assert_safe_work_path "${TEST_WT_2}"

git init "${TEST_REPO}" >> "${LOG_FILE}" 2>&1
(
    cd "${TEST_REPO}"
    git config user.name "Spike Test"
    git config user.email "spike@example.com"
    echo "# Initial" > README.md
    git add README.md
    git commit -m "initial commit" >> "${LOG_FILE}" 2>&1
    git branch -M main
    git worktree add -b test-branch-1 "${TEST_WT_1}" main >> "${LOG_FILE}" 2>&1
    git worktree add -b test-branch-2 "${TEST_WT_2}" main >> "${LOG_FILE}" 2>&1
)

TEST_GIT_COMMON="$(cd "${TEST_REPO}" && git rev-parse --git-common-dir)"
# hooks のサンプル作成
echo "#!/bin/sh" > "${TEST_GIT_COMMON}/hooks/post-commit"
chmod +x "${TEST_GIT_COMMON}/hooks/post-commit"

# 1. Git Common Directory ハッシュ採取関数（config, hooks, worktrees/<name> 配下）
record_common_git_hashes() {
    local label="$1"
    local out_file="$2"
    echo "# Common Git Hashes: ${label}" > "${out_file}"
    find "${TEST_GIT_COMMON}/config" "${TEST_GIT_COMMON}/hooks" "${TEST_GIT_COMMON}/worktrees" -type f 2>/dev/null | sort | while read -r f; do
        echo "$(sha256sum "$f" | awk '{print $1}')  $f" >> "${out_file}"
    done
}

# 2. worktree 内 .git 参照ファイルハッシュ採取関数
record_wt_pointer_hashes() {
    local label="$1"
    local out_file="$2"
    echo "# Worktree Pointer Hashes: ${label}" > "${out_file}"
    find "${TEST_WT_1}/.git" "${TEST_WT_2}/.git" -type f 2>/dev/null | sort | while read -r f; do
        echo "$(sha256sum "$f" | awk '{print $1}')  $f" >> "${out_file}"
    done
}

COMMON_HASH_BEFORE="${TEST_ROOT}/common_hashes_before.txt"
WT_HASH_BEFORE="${TEST_ROOT}/wt_hashes_before.txt"
record_common_git_hashes "Before Tests" "${COMMON_HASH_BEFORE}"
record_wt_pointer_hashes "Before Tests" "${WT_HASH_BEFORE}"

# ------------------------------------------------------------------------------
# 構成1: Git common directory をマウントしない構成 (TEST_WT_1 を使用)
# ------------------------------------------------------------------------------
echo "--- Config 1: Git common directory NOT mounted (using worktree 1) ---" | tee -a "${LOG_FILE}"

C1_OUT=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --userns=keep-id \
    -v "${TEST_WT_1}:/workspace:rw" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "
        echo '--- Checking .git pointer file ---'
        cat .git 2>&1 || true
        echo '--- Attempting write to .git pointer file ---'
        echo 'tampered_pointer' >> .git 2>&1 && echo 'POINTER_WRITE_SUCCEEDED' || echo 'POINTER_WRITE_FAILED'
        echo '--- Attempting git status inside container ---'
        if command -v git >/dev/null 2>&1; then
            git status 2>&1 || echo 'GIT_COMMAND_FAILED_AS_EXPECTED'
        else
            echo 'git_not_available'
        fi
    " 2>&1 || true)

echo "${C1_OUT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 構成2: Git common directory を RO マウントする構成 (TEST_WT_2 を使用)
# ------------------------------------------------------------------------------
echo "--- Config 2: Git common directory mounted READ-ONLY (:ro) (using worktree 2) ---" | tee -a "${LOG_FILE}"

C2_OUT=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --userns=keep-id \
    -v "${TEST_WT_2}:/workspace:rw" \
    -v "${TEST_GIT_COMMON}:${TEST_GIT_COMMON}:ro" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "
        echo '--- Attempting write to .git/config ---'
        echo 'malicious_config' >> '${TEST_GIT_COMMON}/config' 2>&1 || echo 'CONFIG_WRITE_BLOCKED'
        echo '--- Attempting write to hooks ---'
        echo 'malicious_hook' >> '${TEST_GIT_COMMON}/hooks/post-commit' 2>&1 || echo 'HOOK_WRITE_BLOCKED'
        echo '--- Attempting write to worktrees dir ---'
        touch '${TEST_GIT_COMMON}/worktrees/test-tamper' 2>&1 || echo 'WORKTREES_DIR_WRITE_BLOCKED'
        echo '--- Attempting git status with RO common-dir ---'
        if command -v git >/dev/null 2>&1; then
            git status 2>&1 || echo 'GIT_STATUS_FAILED_OR_WARNED'
        else
            echo 'git_not_available'
        fi
    " 2>&1 || true)

echo "${C2_OUT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 実行後ハッシュ比較（保護された Common Dir と 書換え可能な Pointer を分離判定）
# ------------------------------------------------------------------------------
COMMON_HASH_AFTER="${TEST_ROOT}/common_hashes_after.txt"
WT_HASH_AFTER="${TEST_ROOT}/wt_hashes_after.txt"
record_common_git_hashes "After Tests" "${COMMON_HASH_AFTER}"
record_wt_pointer_hashes "After Tests" "${WT_HASH_AFTER}"

echo "--- Comparing Git Common Directory Hashes (Must be identical) ---" | tee -a "${LOG_FILE}"
COMMON_DIFF_OUT=$(diff -u "${COMMON_HASH_BEFORE}" "${COMMON_HASH_AFTER}" || true)

echo "--- Comparing Worktree Pointer Hashes (Reference files inside worktree) ---" | tee -a "${LOG_FILE}"
WT_DIFF_OUT=$(diff -u "${WT_HASH_BEFORE}" "${WT_HASH_AFTER}" || true)

RESULT_STATUS="PASS"
FAILURE_REASON=""

# 1. Common Directory 保護判定（最重要防御ライン）
if [[ -n "${COMMON_DIFF_OUT}" ]]; then
    echo "CRITICAL ERROR: Git common directory files (.git/config, hooks, worktrees) were modified!" | tee -a "${LOG_FILE}"
    echo "${COMMON_DIFF_OUT}" | tee -a "${LOG_FILE}"
    RESULT_STATUS="FAIL"
    FAILURE_REASON="git_common_dir_modified"
else
    echo "PASS: Git common directory files (.git/config, hooks, worktrees/<name>) strictly preserved identical SHA-256." | tee -a "${LOG_FILE}"
fi

# 2. Worktree Pointer 挙動記録（worktree内rwマウントによる書き換え可能性の観察記録）
if [[ -n "${WT_DIFF_OUT}" ]]; then
    echo "OBSERVATION: Worktree .git reference pointer was writable within worktree as expected when worktree is rw mounted." | tee -a "${LOG_FILE}"
else
    echo "OBSERVATION: Worktree .git reference pointer was not modified." | tee -a "${LOG_FILE}"
fi

LOG_HASH="$(get_sha256 "${LOG_FILE}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-03" \
    "${RUN_ID}" \
    "git_protection_runner" \
    "git_worktree_podman" \
    "none" \
    "config1:wt1_no_git_mount / config2:wt2_ro_git_mount" \
    "podman run [protection tests on separate disposable worktrees]" \
    0 \
    "spike/phase0/.logs/spike03_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "common_dir_protected_pointer_isolated_per_worktree"

echo "=== Spike-03 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
