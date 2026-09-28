#!/usr/bin/env bash
# ==============================================================================
# Spike-11: AGY認証情報の受け渡し (spike11-agy-auth.sh)
# 
# 目的: コンテナ内のAGY CLIへ認証情報を渡す3つの方式（環境変数、Podman secret、
#       ROファイルマウント）を比較検証し、Git管理対象・worktree・コンテナ層への
#       トークン漏洩・残存がないか、トークン更新要件があるかを確認する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       使用するコンテナイメージが事前にpullされていること。
# 実行方法:
#   cd /path/to/NovUI
#   # 認証情報を環境変数として渡して実行（スクリプト内へハードコードしない）
#   AGY_TEST_TOKEN="your_token_here" ./spike/phase0/spike11-agy-auth.sh
# 想定所要時間: 約3〜5分
# 変更するパス:
#   - spike/phase0/.work/spike11/ (使い捨て作業ディレクトリ)
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
TIMEOUT_ISOLATION_TEST=60

# TODO: Humanが選択（事前にpodman pullを完了させておくこと）
CONTAINER_IMAGE="" # 例: "ghcr.io/your-org/agy-container:latest" または "fedora:latest"

# 認証設定変数
# TODO: HumanがAGY CLIの認証要件に合わせて確認・設定
AUTH_ENV_VAR_NAME="AGY_API_KEY"

# AGY CLI バイナリ設定
AGY_BIN="agy"
AGY_FLAGS=""

# トークンの対話入力（echoなし）。環境変数が既に設定されている場合はそれを優先
TEST_SECRET_VALUE="${AGY_TEST_TOKEN:-}"
if [[ -z "${TEST_SECRET_VALUE}" ]]; then
    if [ -t 0 ]; then
        read -s -p "Enter AGY API Token for Spike-11 verification (input hidden): " TEST_SECRET_VALUE
        echo ""
    fi
fi

# Podman secret 名
PODMAN_SECRET_NAME="novui_spike11_secret_${RANDOM}"

# ROファイルマウント用パス
# TODO: Humanが設定（ホスト側認証情報ファイルが存在する場合）
AUTH_RO_HOST_FILE=""
# 認証ファイルコンテナ内パスを変数化（--userns=keep-id 使用時は /home/<user>/.config/... に変更の必要性あり。Humanがコンテナ内の環境に合わせて設定）
# TODO: コンテナ内ユーザーのHOMEパスに合わせてHumanが確認・設定
AUTH_RO_CONTAINER_TARGET="/root/.config/agy/credentials.json"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK_DIR="${WORK_DIR}/spike11"
rm -rf "${TEST_WORK_DIR}"
# 使い捨て作業領域の安全性検証と作成
assert_safe_work_path "${TEST_WORK_DIR}"

LOG_FILE="${LOGS_DIR}/spike11_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike11_${RUN_ID}.yaml"

echo "=== Spike-11: AGY Auth Delivery Verification Started ===" | tee "${LOG_FILE}"

# 事前チェック
if [[ -z "${CONTAINER_IMAGE}" ]]; then
    echo "ERROR: CONTAINER_IMAGE is not set. Please specify the image at the top of the script." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${TEST_SECRET_VALUE}" ]]; then
    echo "NOTICE: Secret value not provided. Secret presence/leakage checks will be skipped." | tee -a "${LOG_FILE}"
fi

# 成功した方式を追跡する配列
SUCCESSFUL_METHODS=()
FAILURES=()

SAFE_AUTH_PROMPT="Reply with 'AUTH_SUCCESS' if authentication is working."

# ------------------------------------------------------------------------------
# 方式1: 環境変数渡し (-e VAR_NAME 値なし引き渡し)
# ------------------------------------------------------------------------------
echo "--- Method 1: Environment Variable (-e VAR_NAME) ---" | tee -a "${LOG_FILE}"
M1_WORK="${TEST_WORK_DIR}/m1_env"
assert_safe_work_path "${M1_WORK}"

# 環境変数をホスト側で export し、podman run には -e VAR_NAME (値なし) で渡す（ps等での露出防止）
export "${AUTH_ENV_VAR_NAME}=${TEST_SECRET_VALUE}"

M1_OUT=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --name "spike11_m1_${RUN_ID}" \
    --userns=keep-id \
    -v "${M1_WORK}:/workspace:rw" \
    -w /workspace \
    -e "${AUTH_ENV_VAR_NAME}" \
    "${CONTAINER_IMAGE}" \
    sh -c "
        if [ -n \"\${${AUTH_ENV_VAR_NAME}:-}\" ]; then
            echo 'ENV_AUTH_DETECTED'
            if command -v '${AGY_BIN}' >/dev/null 2>&1; then
                echo '${SAFE_AUTH_PROMPT}' | '${AGY_BIN}' ${AGY_FLAGS} 2>&1 || echo 'AGY_EXEC_FAILED'
            fi
        else
            echo 'ENV_AUTH_MISSING'
        fi
    " 2>&1 || true)

echo "[Method 1 Output]: ${M1_OUT}" | tee -a "${LOG_FILE}"
if [[ "${M1_OUT}" == *"ENV_AUTH_DETECTED"* ]]; then
    SUCCESSFUL_METHODS+=("env_var")
else
    FAILURES+=("m1_env_missing")
fi

# ------------------------------------------------------------------------------
# 方式2: Podman Secret (--secret)
# ------------------------------------------------------------------------------
echo "--- Method 2: Podman Secret (--secret) ---" | tee -a "${LOG_FILE}"
M2_WORK="${TEST_WORK_DIR}/m2_secret"
assert_safe_work_path "${M2_WORK}"

# シークレットの作成
podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true
if [[ -n "${TEST_SECRET_VALUE}" ]]; then
    echo -n "${TEST_SECRET_VALUE}" | podman secret create "${PODMAN_SECRET_NAME}" -
fi

M2_OUT=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
    --name "spike11_m2_${RUN_ID}" \
    --userns=keep-id \
    -v "${M2_WORK}:/workspace:rw" \
    -w /workspace \
    --secret "${PODMAN_SECRET_NAME},type=env,target=${AUTH_ENV_VAR_NAME}" \
    "${CONTAINER_IMAGE}" \
    sh -c "
        if [ -n \"\${${AUTH_ENV_VAR_NAME}:-}\" ]; then
            echo 'SECRET_AUTH_DETECTED'
            if command -v '${AGY_BIN}' >/dev/null 2>&1; then
                echo '${SAFE_AUTH_PROMPT}' | '${AGY_BIN}' ${AGY_FLAGS} 2>&1 || echo 'AGY_EXEC_FAILED'
            fi
        else
            echo 'SECRET_AUTH_MISSING'
        fi
    " 2>&1 || true)

echo "[Method 2 Output]: ${M2_OUT}" | tee -a "${LOG_FILE}"

# シークレット削除
podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true

if [[ "${M2_OUT}" == *"SECRET_AUTH_DETECTED"* ]]; then
    SUCCESSFUL_METHODS+=("podman_secret")
else
    FAILURES+=("m2_secret_missing")
fi

# ------------------------------------------------------------------------------
# 方式3: 認証ファイル/ディレクトリのRO mount (:ro)
# ------------------------------------------------------------------------------
echo "--- Method 3: Read-Only File Mount (:ro) ---" | tee -a "${LOG_FILE}"
M3_WORK="${TEST_WORK_DIR}/m3_ro"
assert_safe_work_path "${M3_WORK}"

M3_CRED_COPY="${TEST_WORK_DIR}/cred_copy_m3.json"
M3_TESTED="false"
CRED_COPY_REMOVED="not_applicable"

if [[ -n "${AUTH_RO_HOST_FILE}" && -f "${AUTH_RO_HOST_FILE}" ]]; then
    # 実物ファイルには触れず、.work/spike11/ 配下に安全にコピーして chmod 600
    cp "${AUTH_RO_HOST_FILE}" "${M3_CRED_COPY}"
    chmod 600 "${M3_CRED_COPY}"
    assert_safe_work_path "${M3_CRED_COPY}"
    M3_TESTED="true"

    M3_OUT=$(timeout "${TIMEOUT_ISOLATION_TEST}s" podman run --rm \
        --name "spike11_m3_${RUN_ID}" \
        --userns=keep-id \
        -v "${M3_WORK}:/workspace:rw" \
        -v "${M3_CRED_COPY}:${AUTH_RO_CONTAINER_TARGET}:ro" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        sh -c "
            if [ -r \"${AUTH_RO_CONTAINER_TARGET}\" ]; then
                echo 'RO_AUTH_FILE_READABLE'
                if command -v '${AGY_BIN}' >/dev/null 2>&1; then
                    echo '${SAFE_AUTH_PROMPT}' | '${AGY_BIN}' ${AGY_FLAGS} 2>&1 || echo 'AGY_EXEC_FAILED'
                fi
            fi
            echo 'test_write' >> \"${AUTH_RO_CONTAINER_TARGET}\" 2>&1 || echo 'RO_WRITE_BLOCKED'
        " 2>&1 || true)

    echo "[Method 3 Output]: ${M3_OUT}" | tee -a "${LOG_FILE}"

    if [[ "${M3_OUT}" == *"RO_AUTH_FILE_READABLE"* ]] && [[ "${M3_OUT}" == *"RO_WRITE_BLOCKED"* ]]; then
        SUCCESSFUL_METHODS+=("ro_file_mount")
    else
        FAILURES+=("m3_ro_mount_failed")
    fi

    # 試験直後にコピーを確実に削除
    echo "Removing temporary credential copy before leakage tests..." | tee -a "${LOG_FILE}"
    rm -f "${M3_CRED_COPY}" 2>/dev/null || true
    if [[ ! -f "${M3_CRED_COPY}" ]]; then
        CRED_COPY_REMOVED="true"
        echo "PASS: Temporary credential copy successfully removed." | tee -a "${LOG_FILE}"
    else
        CRED_COPY_REMOVED="false"
        echo "CRITICAL ERROR: Failed to remove temporary credential copy '${M3_CRED_COPY}'!" | tee -a "${LOG_FILE}"
    fi
else
    echo "NOTICE: AUTH_RO_HOST_FILE not set. Method 3 skipped." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 4. 残存コンテナ・シークレット確認
# ------------------------------------------------------------------------------
echo "--- 4. Checking Residual Containers and Secrets ---" | tee -a "${LOG_FILE}"
RESIDUAL_CONTAINERS=$(podman ps -a --filter "name=spike11" --format '{{.ID}} {{.Names}}' 2>/dev/null || true)
RESIDUAL_SECRETS=$(podman secret ls --filter "name=${PODMAN_SECRET_NAME}" --format '{{.ID}} {{.Name}}' 2>/dev/null || true)

CLEAN_CONTAINERS="true"
if [[ -n "${RESIDUAL_CONTAINERS}" ]]; then
    echo "WARNING: Residual containers found: ${RESIDUAL_CONTAINERS}" | tee -a "${LOG_FILE}"
    CLEAN_CONTAINERS="false"
    FAILURES+=("residual_containers_found")
else
    echo "PASS: No residual containers found." | tee -a "${LOG_FILE}"
fi

CLEAN_SECRETS="true"
if [[ -n "${RESIDUAL_SECRETS}" ]]; then
    echo "WARNING: Residual secrets found: ${RESIDUAL_SECRETS}" | tee -a "${LOG_FILE}"
    CLEAN_SECRETS="false"
    FAILURES+=("residual_secrets_found")
else
    echo "PASS: No residual secrets found." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 5. 機密情報漏洩検査（追加条件: コピー削除確認後に実行）
# ------------------------------------------------------------------------------
echo "--- 5. Secret Leakage Inspection across .work, .logs, and results ---" | tee -a "${LOG_FILE}"

LEAK_TEST_PERFORMED="false"
LEAK_RESULT_WORK="none"
LEAK_RESULT_LOGS="none"
LEAK_RESULT_DOCS="none"

RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${CRED_COPY_REMOVED}" == "false" ]]; then
    # 追加条件: コピーの削除に失敗した場合は、漏洩検査を行わずに FAIL（credential_copy_not_removed）として記録
    echo "FAIL: Skipping leakage check because credential copy was not removed. Setting verdict to FAIL." | tee -a "${LOG_FILE}"
    RESULT_STATUS="FAIL"
    FAILURE_REASON="credential_copy_not_removed"
else
    # 削除成功（または方式3スキップ）時は漏洩検査を実施
    if [[ -n "${TEST_SECRET_VALUE}" ]]; then
        LEAK_TEST_PERFORMED="true"
        echo "Performing secret leakage inspection..." | tee -a "${LOG_FILE}"

        LEAK_RESULT_WORK="$(check_secret_presence "${TEST_SECRET_VALUE}" "${WORK_DIR}")"
        LEAK_RESULT_LOGS="$(check_secret_presence "${TEST_SECRET_VALUE}" "${LOGS_DIR}")"
        LEAK_RESULT_DOCS="$(check_secret_presence "${TEST_SECRET_VALUE}" "${RESULTS_DIR}")"

        echo "Secret leakage in .work: ${LEAK_RESULT_WORK}" | tee -a "${LOG_FILE}"
        echo "Secret leakage in .logs: ${LEAK_RESULT_LOGS}" | tee -a "${LOG_FILE}"
        echo "Secret leakage in docs/results: ${LEAK_RESULT_DOCS}" | tee -a "${LOG_FILE}"

        if [[ "${LEAK_RESULT_WORK}" == "MATCH_FOUND" ]] || \
           [[ "${LEAK_RESULT_LOGS}" == "MATCH_FOUND" ]] || \
           [[ "${LEAK_RESULT_DOCS}" == "MATCH_FOUND" ]]; then
            RESULT_STATUS="FAIL"
            FAILURE_REASON="secret_string_leaked"
        fi
    else
        echo "Secret value was empty; leakage inspection skipped." | tee -a "${LOG_FILE}"
    fi
fi

# その他の失敗条件チェック
if [[ ${#FAILURES[@]} -gt 0 ]] && [[ "${RESULT_STATUS}" == "PASS" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="${FAILURES[*]}"
fi

# 実際に成功した方式を記録
ACTUAL_ALTERNATIVE="${SUCCESSFUL_METHODS[*]:-none}"

LOG_HASH="$(get_sha256 "${LOG_FILE}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-11" \
    "${RUN_ID}" \
    "agy/podman" \
    "podman_auth_delivery" \
    "none" \
    "env_vs_secret_vs_ro" \
    "podman run [auth delivery comparison]" \
    0 \
    "spike/phase0/.logs/spike11_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "${ACTUAL_ALTERNATIVE}"

echo "=== Spike-11 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
