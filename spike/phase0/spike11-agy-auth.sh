#!/usr/bin/env bash
# ==============================================================================
# Spike-11: AGY認証情報の受け渡し (spike11-agy-auth.sh)
# 
# 目的: コンテナ内のAGY CLIへ認証情報を渡す方式（方式A: トークンコピーrw、
#       方式B: トークンコピーro、方式C: 環境変数、方式D: Podman secret）を
#       比較検証し、漏洩・残存検査、起動時間計測、トークン更新要件を確認する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       build-image.sh でビルドしたコンテナイメージを使用すること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike11-agy-auth.sh
# 想定所要時間: 約5〜10分
# 変更するパス:
#   - spike/phase0/.work/spike11/ (使い捨て作業領域)
#   - spike/phase0/.logs/spike11_*.log
#   - docs/spike/results/spike11_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
# AGY実行タイムアウト（手順書5章: 全体900秒、無出力180秒）
TIMEOUT_AGY_TOTAL=900
TIMEOUT_NO_OUTPUT=180

# TODO: build-image.sh の出力したタグを指定（例: novui-spike:agy-2.0.0）
CONTAINER_IMAGE="novui-spike:agy-<バージョン>"

# ホスト側トークンファイルパス
AGY_HOST_TOKEN_FILE="${AGY_HOST_TOKEN_FILE:-$HOME/.gemini/antigravity-cli/antigravity-oauth-token}"

# APIキー環境変数名（方式C・D用）
# TODO: Spike-00の--help出力でAPIキー認証に対応している場合のみ設定（例: AGY_API_KEY）
AGY_API_KEY_ENV_NAME=""

# AGY CLI バイナリ設定（コンテナ内）
AGY_BIN="/usr/local/bin/agy"
AGY_NONINTERACTIVE_FLAGS="" # TODO: Spike-00の--help出力で確認してHumanが設定

# ホスト上のAGYバイナリパス（ホスト基準測定用）
AGY_HOST_BIN="${AGY_HOST_BIN:-$HOME/.local/bin/agy}"

# 固定プロンプト（無害な指示）
SAFE_AUTH_PROMPT="AUTH_SUCCESS とだけ返答してください。ファイルは作成・変更しないでください。"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK_DIR="${WORK_DIR}/spike11"
assert_safe_work_path "${TEST_WORK_DIR}"

LOG_FILE="${LOGS_DIR}/spike11_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike11_${RUN_ID}.yaml"

PODMAN_SECRET_NAME="novui_spike11_secret_${RANDOM}"
SECRETS_TEMP_PATTERNS="${TEST_WORK_DIR}/secret_patterns.txt"

METHOD_A_HOME="${TEST_WORK_DIR}/method_a/home"
METHOD_B_HOME="${TEST_WORK_DIR}/method_b/home"
METHOD_C_HOME="${TEST_WORK_DIR}/method_c/home"
METHOD_D_HOME="${TEST_WORK_DIR}/method_d/home"
HOST_BASELINE_DIR="${TEST_WORK_DIR}/host_baseline"

# trap 設定: 異常終了時でもすべてのジョブ用HOME、一時ファイル、podman secretを削除
cleanup_auth_resources() {
    podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true
    rm -rf "${METHOD_A_HOME}" "${METHOD_B_HOME}" "${METHOD_C_HOME}" "${METHOD_D_HOME}" >/dev/null 2>&1 || true
    rm -f "${SECRETS_TEMP_PATTERNS}" >/dev/null 2>&1 || true
}
trap cleanup_auth_resources EXIT INT TERM

echo "=== Spike-11: AGY Auth Delivery Verification Started ===" | tee "${LOG_FILE}"

# 事前バリデーション
if [[ -z "${CONTAINER_IMAGE}" ]] || [[ "${CONTAINER_IMAGE}" == *"<バージョン>"* ]]; then
    echo "ERROR: CONTAINER_IMAGE is not set correctly. Please run build-image.sh and specify the image tag (novui-spike:agy-<VERSION>)." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${AGY_NONINTERACTIVE_FLAGS}" ]]; then
    echo "ERROR: AGY_NONINTERACTIVE_FLAGS is not set. Please inspect Spike-00 --help output and configure non-interactive flags." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ ! -f "${AGY_HOST_TOKEN_FILE}" ]]; then
    echo "ERROR: AGY host token file not found at '${AGY_HOST_TOKEN_FILE}'." | tee -a "${LOG_FILE}"
    exit 1
fi

# プロンプト入力ファイル準備
PROMPT_FILE="${TEST_WORK_DIR}/prompt_input.txt"
echo "${SAFE_AUTH_PROMPT}" > "${PROMPT_FILE}"
chmod 600 "${PROMPT_FILE}"

# ジョブ用HOME初期化関数（トークンコピー配置）
setup_job_home() {
    local job_home="$1"
    rm -rf "${job_home}"
    assert_safe_work_path "${job_home}"
    local cli_dir="${job_home}/.gemini/antigravity-cli"
    mkdir -p "${cli_dir}"
    chmod 700 "${job_home}/.gemini" "${cli_dir}"
    cp "${AGY_HOST_TOKEN_FILE}" "${cli_dir}/antigravity-oauth-token"
    chmod 600 "${cli_dir}/antigravity-oauth-token"
}

# 空のジョブ用HOME初期化関数
setup_empty_job_home() {
    local job_home="$1"
    rm -rf "${job_home}"
    assert_safe_work_path "${job_home}"
    mkdir -p "${job_home}"
    chmod 700 "${job_home}"
}

declare -A AUTH_RESULTS
SUCCESSFUL_METHODS=()
FAILURES=()

# ------------------------------------------------------------------------------
# 方式A（本命: トークンのコピーを書込み可能で渡す）& 起動時間計測（修正L）
# ------------------------------------------------------------------------------
echo "--- Method A: Writable Token Copy Mount + Launch Time Measurement ---" | tee -a "${LOG_FILE}"
setup_job_home "${METHOD_A_HOME}"
TOKEN_COPY_A="${METHOD_A_HOME}/.gemini/antigravity-cli/antigravity-oauth-token"
HASH_A_BEFORE="$(get_sha256 "${TOKEN_COPY_A}")"

WORKSPACE_A="${TEST_WORK_DIR}/workspace_a"
assert_safe_work_path "${WORKSPACE_A}"

# 1回目起動前のコンテナ内 agy --version
VERSION_A_BEFORE=$(podman run --rm --userns=keep-id -e HOME=/home/agy \
    -v "${METHOD_A_HOME}:/home/agy:$(mount_opts rw)" \
    "${CONTAINER_IMAGE}" "${AGY_BIN}" --version 2>&1 || echo "version_error")
echo "Container AGY Version Before Run 1: ${VERSION_A_BEFORE}" | tee -a "${LOG_FILE}"

# (1) 1回目起動（初回起動）
echo "Executing Method A - Run 1 (Cold Start)..." | tee -a "${LOG_FILE}"
M_A_RUN1_STDOUT="${LOGS_DIR}/spike11_${RUN_ID}_m_a_run1_stdout.log"
M_A_RUN1_STDERR="${LOGS_DIR}/spike11_${RUN_ID}_m_a_run1_stderr.log"

run_monitored_command "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
    "${M_A_RUN1_STDOUT}" "${M_A_RUN1_STDERR}" "${PROMPT_FILE}" \
    podman run --rm \
        --userns=keep-id \
        -e HOME=/home/agy \
        -v "${METHOD_A_HOME}:/home/agy:$(mount_opts rw)" \
        -v "${WORKSPACE_A}:/workspace:$(mount_opts rw)" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS}

DURATION_A_RUN1="${LAST_CMD_DURATION}"
EXIT_A_RUN1="${LAST_CMD_EXIT_CODE}"
echo "Method A Run 1: Exit code ${EXIT_A_RUN1}, Duration: ${DURATION_A_RUN1}s" | tee -a "${LOG_FILE}"

# 1回目起動後のコンテナ内 agy --version
VERSION_A_AFTER=$(podman run --rm --userns=keep-id -e HOME=/home/agy \
    -v "${METHOD_A_HOME}:/home/agy:$(mount_opts rw)" \
    "${CONTAINER_IMAGE}" "${AGY_BIN}" --version 2>&1 || echo "version_error")
echo "Container AGY Version After Run 1: ${VERSION_A_AFTER}" | tee -a "${LOG_FILE}"

# 1回目後の自動更新関連ログ確認
UPDATE_LINES_RUN1=$(grep -i -c "update" "${M_A_RUN1_STDOUT}" "${M_A_RUN1_STDERR}" 2>/dev/null || echo 0)
echo "Update-related output lines in Run 1: ${UPDATE_LINES_RUN1}" | tee -a "${LOG_FILE}"

# 1回目後のジョブ用HOMEディレクトリ/ファイル一覧とサイズ記録
echo "--- Method A Run 1 Directory Tree and Sizes ---" | tee -a "${LOG_FILE}"
find "${METHOD_A_HOME}" -mindepth 1 | sort | tee -a "${LOG_FILE}"
du -s "${METHOD_A_HOME}"/* 2>/dev/null | tee -a "${LOG_FILE}" || true

# 判定1: 標準出力に AUTH_SUCCESS が含まれ、かつ exit code 0
RUN1_SUCCESS="false"
if [[ ${EXIT_A_RUN1} -eq 0 ]] && grep -q "AUTH_SUCCESS" "${M_A_RUN1_STDOUT}"; then
    RUN1_SUCCESS="true"
fi

# (2) 2回目起動（同一ジョブ用HOMEを使用）
echo "Executing Method A - Run 2 (Warm Start, same job HOME)..." | tee -a "${LOG_FILE}"
M_A_RUN2_STDOUT="${LOGS_DIR}/spike11_${RUN_ID}_m_a_run2_stdout.log"
M_A_RUN2_STDERR="${LOGS_DIR}/spike11_${RUN_ID}_m_a_run2_stderr.log"

run_monitored_command "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
    "${M_A_RUN2_STDOUT}" "${M_A_RUN2_STDERR}" "${PROMPT_FILE}" \
    podman run --rm \
        --userns=keep-id \
        -e HOME=/home/agy \
        -v "${METHOD_A_HOME}:/home/agy:$(mount_opts rw)" \
        -v "${WORKSPACE_A}:/workspace:$(mount_opts rw)" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS}

DURATION_A_RUN2="${LAST_CMD_DURATION}"
EXIT_A_RUN2="${LAST_CMD_EXIT_CODE}"
echo "Method A Run 2: Exit code ${EXIT_A_RUN2}, Duration: ${DURATION_A_RUN2}s" | tee -a "${LOG_FILE}"

# 2回目後のジョブ用HOMEディレクトリ/ファイル一覧とサイズ記録
echo "--- Method A Run 2 Directory Tree and Sizes ---" | tee -a "${LOG_FILE}"
find "${METHOD_A_HOME}" -mindepth 1 | sort | tee -a "${LOG_FILE}"
du -s "${METHOD_A_HOME}"/* 2>/dev/null | tee -a "${LOG_FILE}" || true

RUN2_SUCCESS="false"
if [[ ${EXIT_A_RUN2} -eq 0 ]] && grep -q "AUTH_SUCCESS" "${M_A_RUN2_STDOUT}"; then
    RUN2_SUCCESS="true"
fi

# トークン更新検査（ハッシュ一致比較）
HASH_A_AFTER="$(get_sha256 "${TOKEN_COPY_A}")"
TOKEN_A_UPDATED="false"
if [[ "${HASH_A_BEFORE}" != "${HASH_A_AFTER}" ]]; then
    TOKEN_A_UPDATED="true"
    echo "Observation: Token copy SHA-256 changed (token was refreshed by AGY)." | tee -a "${LOG_FILE}"
else
    echo "Observation: Token copy SHA-256 unchanged." | tee -a "${LOG_FILE}"
fi

if [[ "${RUN1_SUCCESS}" == "true" && "${RUN2_SUCCESS}" == "true" ]]; then
    SUCCESSFUL_METHODS+=("method_a_writable_token")
    AUTH_RESULTS["method_a"]="PASS(Run1:${DURATION_A_RUN1}s,Run2:${DURATION_A_RUN2}s,TokenUpdated:${TOKEN_A_UPDATED})"
    echo "PASS: Method A succeeded on both runs." | tee -a "${LOG_FILE}"
else
    FAILURES+=("method_a_failed(run1:${RUN1_SUCCESS},run2:${RUN2_SUCCESS})")
    AUTH_RESULTS["method_a"]="FAIL(Run1:${RUN1_SUCCESS},Run2:${RUN2_SUCCESS})"
    echo "FAIL: Method A authentication did not succeed." | tee -a "${LOG_FILE}"
fi

# (3) 比較用: ホスト上でのAGY起動測定
echo "--- Host Baseline Measurement ---" | tee -a "${LOG_FILE}"
rm -rf "${HOST_BASELINE_DIR}"
assert_safe_work_path "${HOST_BASELINE_DIR}"
HOST_STDOUT="${LOGS_DIR}/spike11_${RUN_ID}_host_stdout.log"
HOST_STDERR="${LOGS_DIR}/spike11_${RUN_ID}_host_stderr.log"

if [[ -f "${AGY_HOST_BIN}" ]]; then
    (
        cd "${HOST_BASELINE_DIR}"
        run_monitored_command "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
            "${HOST_STDOUT}" "${HOST_STDERR}" "${PROMPT_FILE}" \
            "${AGY_HOST_BIN}" ${AGY_NONINTERACTIVE_FLAGS}
    )
    DURATION_HOST="${LAST_CMD_DURATION}"
    EXIT_HOST="${LAST_CMD_EXIT_CODE}"
    echo "Host Baseline: Exit code ${EXIT_HOST}, Duration: ${DURATION_HOST}s" | tee -a "${LOG_FILE}"
    AUTH_RESULTS["host_baseline"]="RECORDED(Duration:${DURATION_HOST}s,Exit:${EXIT_HOST})"
else
    echo "Host AGY binary not found; skipping host baseline." | tee -a "${LOG_FILE}"
    AUTH_RESULTS["host_baseline"]="skipped(not_found)"
fi

# ------------------------------------------------------------------------------
# 方式B（トークンのコピーを読み取り専用で渡す）
# ------------------------------------------------------------------------------
echo "--- Method B: Read-Only Token Copy Mount ---" | tee -a "${LOG_FILE}"
setup_job_home "${METHOD_B_HOME}"
TOKEN_COPY_B="${METHOD_B_HOME}/.gemini/antigravity-cli/antigravity-oauth-token"
WORKSPACE_B="${TEST_WORK_DIR}/workspace_b"
assert_safe_work_path "${WORKSPACE_B}"

M_B_STDOUT="${LOGS_DIR}/spike11_${RUN_ID}_m_b_stdout.log"
M_B_STDERR="${LOGS_DIR}/spike11_${RUN_ID}_m_b_stderr.log"

run_monitored_command "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
    "${M_B_STDOUT}" "${M_B_STDERR}" "${PROMPT_FILE}" \
    podman run --rm \
        --userns=keep-id \
        -e HOME=/home/agy \
        -v "${METHOD_B_HOME}:/home/agy:$(mount_opts rw)" \
        -v "${TOKEN_COPY_B}:/home/agy/.gemini/antigravity-cli/antigravity-oauth-token:$(mount_opts ro)" \
        -v "${WORKSPACE_B}:/workspace:$(mount_opts rw)" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS}

EXIT_B="${LAST_CMD_EXIT_CODE}"
DURATION_B="${LAST_CMD_DURATION}"
echo "Method B: Exit code ${EXIT_B}, Duration: ${DURATION_B}s" | tee -a "${LOG_FILE}"

# ROマウント時の警告・エラーの確認
RO_WARNING_FOUND="false"
if grep -i -E "read-only|rofs|permission denied|token update failed" "${M_B_STDOUT}" "${M_B_STDERR}" >/dev/null 2>&1; then
    RO_WARNING_FOUND="true"
fi

if [[ ${EXIT_B} -eq 0 ]] && grep -q "AUTH_SUCCESS" "${M_B_STDOUT}"; then
    SUCCESSFUL_METHODS+=("method_b_ro_token")
    AUTH_RESULTS["method_b"]="PASS(AuthSuccess_ROWarning:${RO_WARNING_FOUND})"
    echo "PASS: Method B succeeded (RO Warning: ${RO_WARNING_FOUND})." | tee -a "${LOG_FILE}"
else
    AUTH_RESULTS["method_b"]="RECORDED(Exit:${EXIT_B}_ROWarning:${RO_WARNING_FOUND})"
    echo "RECORDED: Method B finished with exit code ${EXIT_B} (RO Warning: ${RO_WARNING_FOUND})." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 方式C・D（環境変数・Podman secret）
# ------------------------------------------------------------------------------
if [[ -z "${AGY_API_KEY_ENV_NAME}" ]]; then
    echo "--- Methods C & D: Skipped (AGY_API_KEY_ENV_NAME is not set) ---" | tee -a "${LOG_FILE}"
    AUTH_RESULTS["method_c"]="skipped(not_supported)"
    AUTH_RESULTS["method_d"]="skipped(not_supported)"
else
    echo "--- Methods C & D: Interactive Token Entry for API Key Testing ---" | tee -a "${LOG_FILE}"
    if [[ ! -t 0 ]]; then
        echo "ERROR: Standard input is not a terminal. Interactive API key entry required." | tee -a "${LOG_FILE}"
        exit 1
    fi
    read -s -p "Enter AGY API Token for Methods C/D verification (input hidden): " API_KEY_VALUE
    echo ""
    if [[ -z "${API_KEY_VALUE}" ]]; then
        echo "ERROR: Entered API key is empty." | tee -a "${LOG_FILE}"
        exit 1
    fi

    # 方式C: 環境変数
    echo "--- Method C: Environment Variable (-e ${AGY_API_KEY_ENV_NAME}) ---" | tee -a "${LOG_FILE}"
    setup_empty_job_home "${METHOD_C_HOME}"
    WORKSPACE_C="${TEST_WORK_DIR}/workspace_c"
    assert_safe_work_path "${WORKSPACE_C}"

    export "${AGY_API_KEY_ENV_NAME}=${API_KEY_VALUE}"
    M_C_STDOUT="${LOGS_DIR}/spike11_${RUN_ID}_m_c_stdout.log"
    M_C_STDERR="${LOGS_DIR}/spike11_${RUN_ID}_m_c_stderr.log"

    run_monitored_command "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
        "${M_C_STDOUT}" "${M_C_STDERR}" "${PROMPT_FILE}" \
        podman run --rm \
            --userns=keep-id \
            -e HOME=/home/agy \
            -e "${AGY_API_KEY_ENV_NAME}" \
            -v "${METHOD_C_HOME}:/home/agy:$(mount_opts rw)" \
            -v "${WORKSPACE_C}:/workspace:$(mount_opts rw)" \
            -w /workspace \
            "${CONTAINER_IMAGE}" \
            "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS}

    unset "${AGY_API_KEY_ENV_NAME}"
    EXIT_C="${LAST_CMD_EXIT_CODE}"
    if [[ ${EXIT_C} -eq 0 ]] && grep -q "AUTH_SUCCESS" "${M_C_STDOUT}"; then
        SUCCESSFUL_METHODS+=("method_c_env_var")
        AUTH_RESULTS["method_c"]="PASS"
        echo "PASS: Method C succeeded." | tee -a "${LOG_FILE}"
    else
        AUTH_RESULTS["method_c"]="FAIL(Exit:${EXIT_C})"
        echo "FAIL: Method C failed." | tee -a "${LOG_FILE}"
    fi

    # 方式D: Podman Secret
    echo "--- Method D: Podman Secret (--secret) ---" | tee -a "${LOG_FILE}"
    setup_empty_job_home "${METHOD_D_HOME}"
    WORKSPACE_D="${TEST_WORK_DIR}/workspace_d"
    assert_safe_work_path "${WORKSPACE_D}"

    podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true
    echo -n "${API_KEY_VALUE}" | podman secret create "${PODMAN_SECRET_NAME}" -

    M_D_STDOUT="${LOGS_DIR}/spike11_${RUN_ID}_m_d_stdout.log"
    M_D_STDERR="${LOGS_DIR}/spike11_${RUN_ID}_m_d_stderr.log"

    run_monitored_command "${TIMEOUT_AGY_TOTAL}" "${TIMEOUT_NO_OUTPUT}" \
        "${M_D_STDOUT}" "${M_D_STDERR}" "${PROMPT_FILE}" \
        podman run --rm \
        --userns=keep-id \
        -e HOME=/home/agy \
        --secret "${PODMAN_SECRET_NAME},type=env,target=${AGY_API_KEY_ENV_NAME}" \
        -v "${METHOD_D_HOME}:/home/agy:$(mount_opts rw)" \
        -v "${WORKSPACE_D}:/workspace:$(mount_opts rw)" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        "${AGY_BIN}" ${AGY_NONINTERACTIVE_FLAGS}

    podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true
    EXIT_D="${LAST_CMD_EXIT_CODE}"
    if [[ ${EXIT_D} -eq 0 ]] && grep -q "AUTH_SUCCESS" "${M_D_STDOUT}"; then
        SUCCESSFUL_METHODS+=("method_d_secret")
        AUTH_RESULTS["method_d"]="PASS"
        echo "PASS: Method D succeeded." | tee -a "${LOG_FILE}"
    else
        AUTH_RESULTS["method_d"]="FAIL(Exit:${EXIT_D})"
        echo "FAIL: Method D failed." | tee -a "${LOG_FILE}"
    fi
fi

# ------------------------------------------------------------------------------
# 修正K: 漏洩検査（すべてのジョブ用HOME削除後に実施）
# ------------------------------------------------------------------------------
echo "--- Leakage Inspection (Post-Cleanup) ---" | tee -a "${LOG_FILE}"

# 1. すべてのジョブ用HOMEを削除
rm -rf "${METHOD_A_HOME}" "${METHOD_B_HOME}" "${METHOD_C_HOME}" "${METHOD_D_HOME}" 2>/dev/null || true

JOB_HOMES_REMOVED="true"
for dir in "${METHOD_A_HOME}" "${METHOD_B_HOME}" "${METHOD_C_HOME}" "${METHOD_D_HOME}"; do
    if [[ -d "${dir}" ]]; then
        JOB_HOMES_REMOVED="false"
        break
    fi
done

RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${JOB_HOMES_REMOVED}" != "true" ]]; then
    echo "CRITICAL ERROR: Failed to remove all job HOME directories! Skipping leakage check." | tee -a "${LOG_FILE}"
    RESULT_STATUS="FAIL"
    FAILURE_REASON="job_home_not_removed"
else
    echo "PASS: All job HOME directories removed successfully." | tee -a "${LOG_FILE}"

    # 2. python3 でトークンファイルから秘密文字列（20文字以上）を抽出
    touch "${SECRETS_TEMP_PATTERNS}"
    chmod 600 "${SECRETS_TEMP_PATTERNS}"

    python3 - "${AGY_HOST_TOKEN_FILE}" "${SECRETS_TEMP_PATTERNS}" <<'PYEOF'
import sys
import json

token_file = sys.argv[1]
out_file = sys.argv[2]
patterns = []

def extract_strings(obj):
    if isinstance(obj, str):
        s = obj.strip()
        if len(s) >= 20:
            patterns.append(s)
    elif isinstance(obj, dict):
        for v in obj.values():
            extract_strings(v)
    elif isinstance(obj, list):
        for item in obj:
            extract_strings(item)

try:
    with open(token_file, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        data = json.loads(content)
        extract_strings(data)
    except Exception:
        for line in content.splitlines():
            s = line.strip()
            if len(s) >= 20:
                patterns.append(s)
except Exception:
    sys.exit(1)

unique_patterns = sorted(list(set(patterns)))
if not unique_patterns:
    sys.exit(2)

with open(out_file, "w", encoding="utf-8") as f:
    for p in unique_patterns:
        f.write(p + "\n")
PYEOF
    PY_STATUS=$?

    if [[ ${PY_STATUS} -ne 0 ]] || [[ ! -s "${SECRETS_TEMP_PATTERNS}" ]]; then
        echo "WARNING: Could not extract secrets of length >= 20 from token file." | tee -a "${LOG_FILE}"
        LEAK_LOGS="none"
        LEAK_RESULTS="none"
        LEAK_GIT="none"
    else
        # 3. 照合（対象ごとに一致あり / なし だけを出力）
        # (A) spike/phase0/.logs/
        if grep -r -F -q -f "${SECRETS_TEMP_PATTERNS}" "${LOGS_DIR}" 2>/dev/null; then
            LEAK_LOGS="MATCH_FOUND"
        else
            LEAK_LOGS="NO_MATCH"
        fi

        # (B) docs/spike/results/
        if grep -r -F -q -f "${SECRETS_TEMP_PATTERNS}" "${RESULTS_DIR}" 2>/dev/null; then
            LEAK_RESULTS="MATCH_FOUND"
        else
            LEAK_RESULTS="NO_MATCH"
        fi

        # (C) Git 管理対象ファイル
        if git -C "${REPO_ROOT}" ls-files -z | xargs -0 grep -F -q -f "${SECRETS_TEMP_PATTERNS}" 2>/dev/null; then
            LEAK_GIT="MATCH_FOUND"
        else
            LEAK_GIT="NO_MATCH"
        fi

        echo "Secret leakage in .logs: ${LEAK_LOGS}" | tee -a "${LOG_FILE}"
        echo "Secret leakage in docs/results: ${LEAK_RESULTS}" | tee -a "${LOG_FILE}"
        echo "Secret leakage in tracked git files: ${LEAK_GIT}" | tee -a "${LOG_FILE}"

        if [[ "${LEAK_LOGS}" == "MATCH_FOUND" ]] || \
           [[ "${LEAK_RESULTS}" == "MATCH_FOUND" ]] || \
           [[ "${LEAK_GIT}" == "MATCH_FOUND" ]]; then
            RESULT_STATUS="FAIL"
            FAILURE_REASON="secret_string_leaked"
        fi
    fi

    # 一時パターンファイルを即座に削除
    rm -f "${SECRETS_TEMP_PATTERNS}"
fi

# その他の失敗判定
if [[ ${#FAILURES[@]} -gt 0 ]] && [[ "${RESULT_STATUS}" == "PASS" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="${FAILURES[*]}"
fi

LOG_HASH="$(get_sha256 "${LOG_FILE}")"
ACTUAL_ALTERNATIVE="${SUCCESSFUL_METHODS[*]:-none}"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-11" \
    "${RUN_ID}" \
    "agy" \
    "${VERSION_A_BEFORE}" \
    "none" \
    "method_a_writable_token / method_b_ro_token" \
    "podman run [agy auth and timing measurement]" \
    0 \
    "spike/phase0/.logs/spike11_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "${ACTUAL_ALTERNATIVE}"

echo "=== Spike-11 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
