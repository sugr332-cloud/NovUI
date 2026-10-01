#!/usr/bin/env bash
# ==============================================================================
# Spike-02: Podman実行隔離 (spike02-podman-isolation.sh)
# 
# 目的: コンテナ環境において、worktree内の許可対象以外の領域（親ディレクトリ、Home、
#       ホスト/tmp、Git common dir、設定ファイル等）への書込みが確実に拒否されるか、
#       mountinfoに不要なホストパスがないか、SELinuxラベル（なし/:z/:Z）と
#       --userns=keep-id によるホスト権限への影響を実測する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       AGY自身ではなく、同一コンテナ設定で通常のshell scriptから書込みを試行する。
#       使用するコンテナイメージが事前にpullされていること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike02-podman-isolation.sh
# 想定所要時間: 約3〜5分
# 変更するパス:
#   - spike/phase0/.work/spike02/ (使い捨て作業ディレクトリ)
#   - spike/phase0/.logs/spike02_*.log
#   - docs/spike/results/spike02_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

TIMEOUT_ISOLATION_TEST=60
TIMEOUT_AGY_TOTAL=900
TIMEOUT_NO_OUTPUT=180

# TODO: build-image.sh の出力したタグを指定
CONTAINER_IMAGE="novui-spike:agy-<バージョン>"

# SELinuxラベル設定（既定値は "Z"、"z" または "Z" または ""）
# TODO: Spike-02の結果で確定
MOUNT_LABEL="Z"

# AGY CLI バイナリ設定（Git未マウント時の挙動観察用）
AGY_BIN="/usr/local/bin/agy"
AGY_NONINTERACTIVE_FLAGS="" # TODO: Spike-00の--help出力で確認 (例: --non-interactive 等)

# 認証設定（Spike-11 方式Aのトークンファイルコピー方式）
AGY_HOST_TOKEN_FILE="${AGY_HOST_TOKEN_FILE:-$HOME/.gemini/antigravity-cli/antigravity-oauth-token}"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_ROOT="${WORK_DIR}/spike02"
rm -rf "${TEST_ROOT}"
# 使い捨てルートの安全性検証と作成
assert_safe_work_path "${TEST_ROOT}"

AGY_JOB_HOME="${TEST_ROOT}/agy_home"
AGY_OBSERVE_CONTAINER="spike02_${RUN_ID}_agy_observe"

# trap 設定: 異常終了時でも使い捨てコンテナとジョブ用HOMEを必ず削除
cleanup_spike02_resources() {
    podman rm -f "${AGY_OBSERVE_CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${AGY_JOB_HOME}" >/dev/null 2>&1 || true
}
trap cleanup_spike02_resources EXIT INT TERM

LOG_FILE="${LOGS_DIR}/spike02_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike02_${RUN_ID}.yaml"

echo "=== Spike-02: Podman Isolation Verification Started ===" | tee "${LOG_FILE}"

if [[ -z "${CONTAINER_IMAGE}" ]] || [[ "${CONTAINER_IMAGE}" == *"<バージョン>"* ]]; then
    echo "ERROR: CONTAINER_IMAGE is not set correctly. Please run build-image.sh and specify the image tag (novui-spike:agy-<VERSION>)." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${AGY_NONINTERACTIVE_FLAGS}" ]]; then
    echo "ERROR: AGY_NONINTERACTIVE_FLAGS is not set. Please inspect Spike-00 --help output and configure non-interactive flags." | tee -a "${LOG_FILE}"
    exit 1
fi

# 判定記録用連想配列
declare -A ISOLATION_RESULTS

# ------------------------------------------------------------------------------
# 1. Bazzite固有: SELinuxラベル（なし / :z / :Z）試験（冒頭に配置）
# ------------------------------------------------------------------------------
echo "--- 1. SELinux Label Option Test (none vs :z vs :Z) ---" | tee -a "${LOG_FILE}"

for label in "" "z" "Z"; do
    label_name="${label:-none}"
    echo "Testing SELinux label mode: ${label_name}" | tee -a "${LOG_FILE}"
    SE_WORK="${TEST_ROOT}/selinux_${label_name}"
    assert_safe_work_path "${SE_WORK}"

    se_opt="$(mount_opts rw "${label}")"
    se_out=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
        --userns=keep-id \
        -v "${SE_WORK}:/workspace:${se_opt}" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        sh -c "echo 'selinux_test' > /workspace/test.txt && echo SUCCESS || echo FAIL" 2>&1 || true)
    
    echo "SELinux label [${label_name}] output: ${se_out}" | tee -a "${LOG_FILE}"
    if [[ "${se_out}" == *"SUCCESS"* ]] && [[ -f "${SE_WORK}/test.txt" ]]; then
        ISOLATION_RESULTS["selinux_${label_name}"]="PASS(Writable)"
    else
        ISOLATION_RESULTS["selinux_${label_name}"]="FAIL(Blocked_or_Error)"
    fi
done

# ------------------------------------------------------------------------------
# 2. 模擬テスト環境の構築（ホスト側）
# ------------------------------------------------------------------------------
MOCK_PARENT="${TEST_ROOT}/mock_parent"
MOCK_WORKTREE="${MOCK_PARENT}/mock_worktree"
MOCK_GIT_COMMON="${TEST_ROOT}/mock_git_common"

assert_safe_work_path "${MOCK_PARENT}"
assert_safe_work_path "${MOCK_WORKTREE}"
assert_safe_work_path "${MOCK_GIT_COMMON}/hooks"

echo "[core]" > "${MOCK_GIT_COMMON}/config"
echo "#!/bin/sh" > "${MOCK_GIT_COMMON}/hooks/pre-commit"
chmod +x "${MOCK_GIT_COMMON}/hooks/pre-commit"
echo "gitdir: ${MOCK_GIT_COMMON}" > "${MOCK_WORKTREE}/.git"

# 設定ファイルおよびディレクトリ群の作成（修正G）
mkdir -p "${MOCK_WORKTREE}/world" "${MOCK_WORKTREE}/characters" "${MOCK_WORKTREE}/plot" "${MOCK_WORKTREE}/foreshadowing" "${MOCK_WORKTREE}/rules" "${MOCK_WORKTREE}/chapters/ch-001"
echo "world_setting" > "${MOCK_WORKTREE}/world/world.md"
echo "character_setting" > "${MOCK_WORKTREE}/characters/chara.md"
echo "plot_setting" > "${MOCK_WORKTREE}/plot/plot.md"
echo "foreshadowing_setting" > "${MOCK_WORKTREE}/foreshadowing/fs.md"
echo "rules_setting" > "${MOCK_WORKTREE}/rules/rules.md"
echo "format_version: 0.4" > "${MOCK_WORKTREE}/project.md"
echo "order_info" > "${MOCK_WORKTREE}/chapters-order.md"
echo "initial draft text" > "${MOCK_WORKTREE}/chapters/ch-001/draft.md"

# ------------------------------------------------------------------------------
# 3. /proc/self/mountinfo の記録と意図外マウント検査
# ------------------------------------------------------------------------------
echo "--- 2. Recording and Inspecting /proc/self/mountinfo ---" | tee -a "${LOG_FILE}"
MOUNTINFO_LOG="${LOGS_DIR}/spike02_${RUN_ID}_mountinfo.log"

assert_safe_work_path "${MOCK_WORKTREE}"
timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --userns=keep-id \
    -v "${MOCK_WORKTREE}:/workspace:$(mount_opts rw)" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    cat /proc/self/mountinfo > "${MOUNTINFO_LOG}" 2>&1 || true

echo "mountinfo recorded to: ${MOUNTINFO_LOG}" | tee -a "${LOG_FILE}"

# 意図しないホストパス（Homeやホストルート等）が露出していないか検査
# 許可されるホストマウントは MOCK_WORKTREE のみ
UNINTENDED_MOUNTS=""
while IFS= read -r line; do
    # mountinfo の形式: 5番目フィールドがコンテナ内マウントポイント
    # 例: ... /workspace ...
    if [[ "${line}" == *"${HOME}"* ]] && [[ "${line}" != *"${MOCK_WORKTREE}"* ]]; then
        UNINTENDED_MOUNTS="${UNINTENDED_MOUNTS} ${line}"
    fi
done < "${MOUNTINFO_LOG}"

if [[ -z "${UNINTENDED_MOUNTS}" ]]; then
    echo "PASS: No unintended host paths detected in mountinfo." | tee -a "${LOG_FILE}"
    ISOLATION_RESULTS["mountinfo_inspection"]="PASS"
else
    echo "WARNING: Unintended host mounts detected: ${UNINTENDED_MOUNTS}" | tee -a "${LOG_FILE}"
    ISOLATION_RESULTS["mountinfo_inspection"]="FAIL(UnintendedMountsFound)"
fi

# ------------------------------------------------------------------------------
# 4. 書込み隔離試験（同一コンテナ設定で shell script から書込み試行）
# ------------------------------------------------------------------------------
echo "--- 3. Write Isolation Tests ---" | tee -a "${LOG_FILE}"

run_write_test() {
    local test_name="$1"
    local container_cmd="$2"
    local extra_mounts="${3:-}"

    assert_safe_work_path "${MOCK_WORKTREE}"
    local mount_opt="-v ${MOCK_WORKTREE}:/workspace:$(mount_opts rw)"
    if [[ -n "${extra_mounts}" ]]; then
        mount_opt="${mount_opt} ${extra_mounts}"
    fi

    local out
    out=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
        --userns=keep-id \
        ${mount_opt} \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        sh -c "${container_cmd}" 2>&1 || true)
    
    echo "[${test_name}] Output: ${out}" | tee -a "${LOG_FILE}"
    echo "${out}"
}

# (1) worktree内（許可対象: 書込み成功すること）
out=$(run_write_test "Worktree Internal (Allowed)" "echo 'allowed' > /workspace/test_ok.txt && echo SUCCESS || echo FAIL")
if [[ "${out}" == *"SUCCESS"* ]]; then
    ISOLATION_RESULTS["worktree_internal"]="PASS(Writable)"
else
    ISOLATION_RESULTS["worktree_internal"]="FAIL(NotWritable)"
fi

# (2) worktree親ディレクトリへの書込み（失敗すること）
out=$(run_write_test "Worktree Parent (Blocked)" "echo 'leak' > /workspace/../parent_leak.txt && echo LEAK || echo BLOCKED")
if [[ "${out}" == *"BLOCKED"* ]] && [[ ! -f "${MOCK_PARENT}/parent_leak.txt" ]]; then
    ISOLATION_RESULTS["parent_dir"]="PASS(Blocked)"
else
    ISOLATION_RESULTS["parent_dir"]="FAIL(Leaked)"
fi

# (3) Homeへの書込み（ホストのHomeに届かないこと）
HOST_HOME_SENTINEL="${HOME}/.novui_spike02_sentinel_$$"
out=$(run_write_test "Home Directory (Blocked)" "echo 'leak' > \"${HOST_HOME_SENTINEL}\" && echo LEAK || echo BLOCKED")
if [[ ! -f "${HOST_HOME_SENTINEL}" ]]; then
    ISOLATION_RESULTS["home_dir"]="PASS(Blocked)"
else
    rm -f "${HOST_HOME_SENTINEL}"
    ISOLATION_RESULTS["home_dir"]="FAIL(Leaked)"
fi

# (4) /tmp への書込み（ホストの /tmp に影響しないこと）
CONTAINER_TMP_TEST="/tmp/novui_spike02_test_$$.txt"
out=$(run_write_test "Host /tmp (Isolated)" "echo 'local_tmp' > \"${CONTAINER_TMP_TEST}\" && echo DONE")
if [[ ! -f "${CONTAINER_TMP_TEST}" ]]; then
    ISOLATION_RESULTS["tmp_isolated"]="PASS(Isolated)"
else
    rm -f "${CONTAINER_TMP_TEST}"
    ISOLATION_RESULTS["tmp_isolated"]="FAIL(LeakedToHostTmp)"
fi

# (5) Git common directory（非マウント時: コンテナ内に存在せず書込み不可）
out=$(run_write_test "Git Common Dir (Unmounted)" "echo 'leak' > \"${MOCK_GIT_COMMON}/config\" && echo LEAK || echo BLOCKED")
if [[ ! -f "${MOCK_GIT_COMMON}/leak" ]] && [[ "$(cat "${MOCK_GIT_COMMON}/config")" == "[core]" ]]; then
    ISOLATION_RESULTS["git_common_unmounted"]="PASS(Blocked)"
else
    ISOLATION_RESULTS["git_common_unmounted"]="FAIL(Leaked)"
fi

# (6) 設定ファイルの保護試験（v0.4 §43改修構成）
# worktree全体をrwマウントした上で、設定ファイル群をro重ねマウント
echo "--- Testing Settings Protection (§43 RO overlay on worktree) ---" | tee -a "${LOG_FILE}"

assert_safe_work_path "${MOCK_WORKTREE}"
assert_safe_work_path "${MOCK_WORKTREE}/world"
assert_safe_work_path "${MOCK_WORKTREE}/characters"
assert_safe_work_path "${MOCK_WORKTREE}/plot"
assert_safe_work_path "${MOCK_WORKTREE}/foreshadowing"
assert_safe_work_path "${MOCK_WORKTREE}/rules"
assert_safe_work_file "${MOCK_WORKTREE}/project.md"
assert_safe_work_file "${MOCK_WORKTREE}/chapters-order.md"

SETTINGS_MOUNTS=(
    -v "${MOCK_WORKTREE}:/workspace:$(mount_opts rw)"
    -v "${MOCK_WORKTREE}/world:/workspace/world:$(mount_opts ro)"
    -v "${MOCK_WORKTREE}/characters:/workspace/characters:$(mount_opts ro)"
    -v "${MOCK_WORKTREE}/plot:/workspace/plot:$(mount_opts ro)"
    -v "${MOCK_WORKTREE}/foreshadowing:/workspace/foreshadowing:$(mount_opts ro)"
    -v "${MOCK_WORKTREE}/rules:/workspace/rules:$(mount_opts ro)"
    -v "${MOCK_WORKTREE}/project.md:/workspace/project.md:$(mount_opts ro)"
    -v "${MOCK_WORKTREE}/chapters-order.md:/workspace/chapters-order.md:$(mount_opts ro)"
)

out_settings=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --userns=keep-id \
    "${SETTINGS_MOUNTS[@]}" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "
        echo 'bad' > /workspace/world/world.md 2>&1 && echo 'WORLD_WRITE_LEAK' || echo 'WORLD_RO_BLOCKED';
        echo 'bad' > /workspace/characters/chara.md 2>&1 && echo 'CHARA_WRITE_LEAK' || echo 'CHARA_RO_BLOCKED';
        echo 'bad' > /workspace/plot/plot.md 2>&1 && echo 'PLOT_WRITE_LEAK' || echo 'PLOT_RO_BLOCKED';
        echo 'bad' > /workspace/foreshadowing/fs.md 2>&1 && echo 'FS_WRITE_LEAK' || echo 'FS_RO_BLOCKED';
        echo 'bad' > /workspace/rules/rules.md 2>&1 && echo 'RULES_WRITE_LEAK' || echo 'RULES_RO_BLOCKED';
        echo 'bad' > /workspace/project.md 2>&1 && echo 'PROJECT_WRITE_LEAK' || echo 'PROJECT_RO_BLOCKED';
        echo 'bad' > /workspace/chapters-order.md 2>&1 && echo 'ORDER_WRITE_LEAK' || echo 'ORDER_RO_BLOCKED';
        echo 'draft modified' > /workspace/chapters/ch-001/draft.md 2>&1 && echo 'DRAFT_WRITE_OK' || echo 'DRAFT_WRITE_FAIL';
    " 2>&1 || true)

echo "[Settings Overlay Test] Output: ${out_settings}" | tee -a "${LOG_FILE}"

ALL_SETTINGS_BLOCKED="true"
if [[ "${out_settings}" != *"WORLD_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/world/world.md")" != "world_setting" ]]; then ALL_SETTINGS_BLOCKED="false"; fi
if [[ "${out_settings}" != *"CHARA_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/characters/chara.md")" != "character_setting" ]]; then ALL_SETTINGS_BLOCKED="false"; fi
if [[ "${out_settings}" != *"PLOT_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/plot/plot.md")" != "plot_setting" ]]; then ALL_SETTINGS_BLOCKED="false"; fi
if [[ "${out_settings}" != *"FS_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/foreshadowing/fs.md")" != "foreshadowing_setting" ]]; then ALL_SETTINGS_BLOCKED="false"; fi
if [[ "${out_settings}" != *"RULES_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/rules/rules.md")" != "rules_setting" ]]; then ALL_SETTINGS_BLOCKED="false"; fi
if [[ "${out_settings}" != *"PROJECT_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/project.md")" != "format_version: 0.4" ]]; then ALL_SETTINGS_BLOCKED="false"; fi
if [[ "${out_settings}" != *"ORDER_RO_BLOCKED"* ]] || [[ "$(cat "${MOCK_WORKTREE}/chapters-order.md")" != "order_info" ]]; then ALL_SETTINGS_BLOCKED="false"; fi

DRAFT_PASS="false"
if [[ "${out_settings}" == *"DRAFT_WRITE_OK"* ]] && [[ "$(cat "${MOCK_WORKTREE}/chapters/ch-001/draft.md")" == "draft modified" ]]; then
    DRAFT_PASS="true"
fi

if [[ "${ALL_SETTINGS_BLOCKED}" == "true" && "${DRAFT_PASS}" == "true" ]]; then
    ISOLATION_RESULTS["settings_ro_overlay"]="PASS(DraftWritable_AllSettingsBlocked)"
else
    ISOLATION_RESULTS["settings_ro_overlay"]="FAIL(DraftPass:${DRAFT_PASS}_SettingsBlocked:${ALL_SETTINGS_BLOCKED})"
fi

# ------------------------------------------------------------------------------
# 5. UIDマッピング試験（--userns=keep-id の有無両方）
# ------------------------------------------------------------------------------
echo "--- 4. UID Mapping Test (keep-id vs no keep-id) ---" | tee -a "${LOG_FILE}"

UID_WORK="${TEST_ROOT}/uid_check"
assert_safe_work_path "${UID_WORK}"
HOST_UID="$(id -u):$(id -g)"

# (A) keep-id あり
assert_safe_work_path "${UID_WORK}"
timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --userns=keep-id \
    -v "${UID_WORK}:/workspace:$(mount_opts rw)" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "echo 'keep-id' > /workspace/file_keepid.txt" >> "${LOG_FILE}" 2>&1 || true

FILE_OWNER_KEEPID=$(stat -c '%u:%g' "${UID_WORK}/file_keepid.txt" 2>/dev/null || echo "unknown")
echo "File owner with keep-id: ${FILE_OWNER_KEEPID} (host user: ${HOST_UID})" | tee -a "${LOG_FILE}"

# (B) keep-id なし
assert_safe_work_path "${UID_WORK}"
timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    -v "${UID_WORK}:/workspace:$(mount_opts rw)" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "echo 'no-keep-id' > /workspace/file_nokeepid.txt" >> "${LOG_FILE}" 2>&1 || true

FILE_OWNER_NOKEEPID=$(stat -c '%u:%g' "${UID_WORK}/file_nokeepid.txt" 2>/dev/null || echo "unknown")
echo "File owner without keep-id: ${FILE_OWNER_NOKEEPID} (host user: ${HOST_UID})" | tee -a "${LOG_FILE}"

if [[ "${FILE_OWNER_KEEPID}" == "${HOST_UID}" ]]; then
    ISOLATION_RESULTS["uid_mapping_keepid"]="PASS(MatchesHostUID)"
else
    ISOLATION_RESULTS["uid_mapping_keepid"]="FAIL(OwnerMismatch:${FILE_OWNER_KEEPID})"
fi
ISOLATION_RESULTS["uid_mapping_nokeepid"]="RECORDED(Owner:${FILE_OWNER_NOKEEPID})"

# ------------------------------------------------------------------------------
# 6. Git common directory 未マウント状態でのAGY挙動観察
# ------------------------------------------------------------------------------
echo "--- 5. Observing AGY/Git behavior when Git common dir is NOT mounted ---" | tee -a "${LOG_FILE}"
AGY_OBSERVE_WORK="${TEST_ROOT}/agy_git_observe"
assert_safe_work_path "${AGY_OBSERVE_WORK}"

# 使い捨てジョブ用HOMEの準備（Spike-11 方式Aの構成）
rm -rf "${AGY_JOB_HOME}"
assert_safe_work_path "${AGY_JOB_HOME}"
mkdir -p "${AGY_JOB_HOME}/.gemini/antigravity-cli"
chmod 700 "${AGY_JOB_HOME}/.gemini" "${AGY_JOB_HOME}/.gemini/antigravity-cli"
if [[ -f "${AGY_HOST_TOKEN_FILE}" ]]; then
    cp "${AGY_HOST_TOKEN_FILE}" "${AGY_JOB_HOME}/.gemini/antigravity-cli/antigravity-oauth-token"
    chmod 600 "${AGY_JOB_HOME}/.gemini/antigravity-cli/antigravity-oauth-token"
else
    echo "WARNING: AGY_HOST_TOKEN_FILE not found at '${AGY_HOST_TOKEN_FILE}'." | tee -a "${LOG_FILE}"
fi

# 模擬 .git 参照ファイル（参照先はコンテナ外のため存在しない）
echo "gitdir: /nonexistent/git/common/dir" > "${AGY_OBSERVE_WORK}/.git"
echo "initial text" > "${AGY_OBSERVE_WORK}/draft.md"

FILES_BEFORE="${TEST_ROOT}/observe_files_before.txt"
find "${AGY_OBSERVE_WORK}" -type f | sort > "${FILES_BEFORE}"
DOT_GIT_HASH_BEFORE="$(get_sha256 "${AGY_OBSERVE_WORK}/.git")"

SAFE_GIT_PROMPT="git status を実行して結果を1行で報告してください。ファイルは変更しないでください。"
PROMPT_OBSERVE_FILE="${TEST_ROOT}/prompt_observe.txt"
echo "${SAFE_GIT_PROMPT}" > "${PROMPT_OBSERVE_FILE}"
chmod 600 "${PROMPT_OBSERVE_FILE}"

echo "Checking git status behavior directly inside container..." | tee -a "${LOG_FILE}"
GIT_DIRECT_OUT=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --userns=keep-id \
    -v "${AGY_OBSERVE_WORK}:/workspace:$(mount_opts rw)" \
    -w /workspace \
    "${CONTAINER_IMAGE}" \
    sh -c "git status 2>&1 || echo 'GIT_COMMAND_FAILED_EXPECTED'" 2>&1 || true)
echo "[Direct Git Status Output]: ${GIT_DIRECT_OUT}" | tee -a "${LOG_FILE}"

echo "Testing AGY CLI with safe prompt in unmounted common dir..." | tee -a "${LOG_FILE}"
OBSERVE_STDOUT="${LOGS_DIR}/spike02_${RUN_ID}_observe_stdout.log"
OBSERVE_STDERR="${LOGS_DIR}/spike02_${RUN_ID}_observe_stderr.log"

run_monitored_container "${AGY_OBSERVE_CONTAINER}" "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
    "${OBSERVE_STDOUT}" "${OBSERVE_STDERR}" "${PROMPT_OBSERVE_FILE}" \
    podman run --name "${AGY_OBSERVE_CONTAINER}" --rm -i \
        --userns=keep-id \
        -e HOME=/home/agy \
        -v "${AGY_JOB_HOME}:/home/agy:$(mount_opts rw)" \
        -v "${AGY_OBSERVE_WORK}:/workspace:$(mount_opts rw)" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS}

echo "AGY Observation Completed: Exit code ${LAST_CMD_EXIT_CODE}, Duration: ${LAST_CMD_DURATION}s" | tee -a "${LOG_FILE}"

FILES_AFTER="${TEST_ROOT}/observe_files_after.txt"
find "${AGY_OBSERVE_WORK}" -type f | sort > "${FILES_AFTER}"
DOT_GIT_HASH_AFTER="$(get_sha256 "${AGY_OBSERVE_WORK}/.git")"

FILE_DIFF="$(diff -u "${FILES_BEFORE}" "${FILES_AFTER}" || true)"
echo "File list diff before/after AGY observe:" | tee -a "${LOG_FILE}"
echo "${FILE_DIFF:-none}" | tee -a "${LOG_FILE}"

if [[ "${DOT_GIT_HASH_BEFORE}" == "${DOT_GIT_HASH_AFTER}" ]]; then
    echo "PASS: .git reference pointer was not altered or deleted by git/AGY commands." | tee -a "${LOG_FILE}"
    ISOLATION_RESULTS["git_unmounted_observation"]="PASS(PointerPreserved)"
else
    echo "WARNING: .git reference pointer was modified or recreated!" | tee -a "${LOG_FILE}"
    ISOLATION_RESULTS["git_unmounted_observation"]="RECORDED(PointerAltered)"
fi

# 観察完了後、使い捨てジョブ用HOMEを削除
rm -rf "${AGY_JOB_HOME}" 2>/dev/null || true

# ------------------------------------------------------------------------------
# 残存コンテナ確認（修正P）
# ------------------------------------------------------------------------------
echo "--- Checking Residual Containers (name prefix: spike02_${RUN_ID}) ---" | tee -a "${LOG_FILE}"
RESIDUAL_CONTAINERS=$(podman ps -a --filter "name=spike02_${RUN_ID}" --format '{{.ID}} {{.Names}}' 2>/dev/null || true)
if [[ -n "${RESIDUAL_CONTAINERS}" ]]; then
    echo "WARNING: Residual containers found: ${RESIDUAL_CONTAINERS}" | tee -a "${LOG_FILE}"
    ISOLATION_RESULTS["residual_containers"]="FAIL(Found:${RESIDUAL_CONTAINERS})"
else
    echo "PASS: No residual containers found." | tee -a "${LOG_FILE}"
    ISOLATION_RESULTS["residual_containers"]="PASS"
fi

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
RESULT_STATUS="PASS"
FAILURE_REASON=""

for k in "${!ISOLATION_RESULTS[@]}"; do
    if [[ "${ISOLATION_RESULTS[$k]}" == FAIL* ]]; then
        RESULT_STATUS="FAIL"
        FAILURE_REASON="${FAILURE_REASON} ${k}:${ISOLATION_RESULTS[$k]}"
    fi
done

LOG_HASH="$(get_sha256 "${LOG_FILE}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-02" \
    "${RUN_ID}" \
    "podman_isolation_shell" \
    "podman_rootless" \
    "none" \
    "-v worktree:$(mount_opts rw) --userns=keep-id" \
    "podman run [write tests for 8 targets + settings overlay]" \
    0 \
    "spike/phase0/.logs/spike02_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "mount_label_${MOUNT_LABEL:-none}"

echo "=== Spike-02 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
