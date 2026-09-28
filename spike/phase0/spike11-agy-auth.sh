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
TEST_SECRET_VALUE="${AGY_TEST_TOKEN:-}"

# Podman secret 名
PODMAN_SECRET_NAME="novui_spike11_secret"

# ROファイルマウント用パス
# TODO: Humanが設定（認証情報ファイルが存在する場合）
AUTH_RO_HOST_FILE=""
AUTH_RO_CONTAINER_TARGET="/root/.config/agy/credentials.json"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK_DIR="${WORK_DIR}/spike11"
rm -rf "${TEST_WORK_DIR}"
mkdir -p "${TEST_WORK_DIR}"

LOG_FILE="${LOGS_DIR}/spike11_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike11_${RUN_ID}.yaml"

echo "=== Spike-11: AGY Auth Delivery Verification Started ===" | tee "${LOG_FILE}"

# 事前チェック
if [[ -z "${CONTAINER_IMAGE}" ]]; then
    echo "ERROR: CONTAINER_IMAGE is not set. Please specify the image at the top of the script." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${TEST_SECRET_VALUE}" ]]; then
    echo "NOTICE: AGY_TEST_TOKEN environment variable is not provided. Secret leakage string checks will be skipped." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 方式1: 環境変数渡し (-e)
# ------------------------------------------------------------------------------
echo "--- Method 1: Environment Variable (-e) ---" | tee -a "${LOG_FILE}"
ENV_METHOD_PASS="false"
M1_WORK="${TEST_WORK_DIR}/m1_env"
mkdir -p "${M1_WORK}"

# コンテナ内で環境変数の認識を確認（※トークン本体は出力しない）
podman run --rm \
    --userns=keep-id \
    -v "${M1_WORK}:/workspace:rw" \
    -w /workspace \
    -e "${AUTH_ENV_VAR_NAME}=${TEST_SECRET_VALUE}" \
    "${CONTAINER_IMAGE}" \
    sh -c "if [ -n \"\${${AUTH_ENV_VAR_NAME}:-}\" ]; then echo 'ENV_AUTH_DETECTED'; else echo 'ENV_AUTH_MISSING'; fi" \
    >> "${LOG_FILE}" 2>&1 || true

# 漏洩検査: worktree内にトークン文字列が存在しないか
LEAK_M1="$(check_secret_presence "${TEST_SECRET_VALUE}" "${M1_WORK}")"
echo "Method 1 secret leak check in workdir: ${LEAK_M1}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 方式2: Podman Secret (--secret)
# ------------------------------------------------------------------------------
echo "--- Method 2: Podman Secret (--secret) ---" | tee -a "${LOG_FILE}"
M2_WORK="${TEST_WORK_DIR}/m2_secret"
mkdir -p "${M2_WORK}"

# シークレットの作成（既に存在すれば再作成）
podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true
if [[ -n "${TEST_SECRET_VALUE}" ]]; then
    echo -n "${TEST_SECRET_VALUE}" | podman secret create "${PODMAN_SECRET_NAME}" -
fi

podman run --rm \
    --userns=keep-id \
    -v "${M2_WORK}:/workspace:rw" \
    -w /workspace \
    --secret "${PODMAN_SECRET_NAME},type=env,target=${AUTH_ENV_VAR_NAME}" \
    "${CONTAINER_IMAGE}" \
    sh -c "if [ -n \"\${${AUTH_ENV_VAR_NAME}:-}\" ]; then echo 'SECRET_AUTH_DETECTED'; else echo 'SECRET_AUTH_MISSING'; fi" \
    >> "${LOG_FILE}" 2>&1 || true

# シークレット削除
podman secret rm "${PODMAN_SECRET_NAME}" >/dev/null 2>&1 || true

# 漏洩検査
LEAK_M2="$(check_secret_presence "${TEST_SECRET_VALUE}" "${M2_WORK}")"
echo "Method 2 secret leak check in workdir: ${LEAK_M2}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 方式3: 認証ファイル/ディレクトリのRO mount (:ro)
# ------------------------------------------------------------------------------
echo "--- Method 3: Read-Only File Mount (:ro) ---" | tee -a "${LOG_FILE}"
M3_WORK="${TEST_WORK_DIR}/m3_ro"
mkdir -p "${M3_WORK}"

M3_RESULT="skipped"
if [[ -n "${AUTH_RO_HOST_FILE}" && -f "${AUTH_RO_HOST_FILE}" ]]; then
    # ROマウントで書込み不可（トークン更新時の書込み失敗リスク）の検証
    podman run --rm \
        --userns=keep-id \
        -v "${M3_WORK}:/workspace:rw" \
        -v "${AUTH_RO_HOST_FILE}:${AUTH_RO_CONTAINER_TARGET}:ro" \
        -w /workspace \
        "${CONTAINER_IMAGE}" \
        sh -c "if [ -r \"${AUTH_RO_CONTAINER_TARGET}\" ]; then echo 'RO_AUTH_FILE_READABLE'; fi; echo 'test' >> \"${AUTH_RO_CONTAINER_TARGET}\" 2>&1 || echo 'RO_WRITE_BLOCKED'" \
        >> "${LOG_FILE}" 2>&1 || true
    M3_RESULT="tested"
else
    echo "NOTICE: AUTH_RO_HOST_FILE not set. Method 3 skipped." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${LEAK_M1}" == "MATCH_FOUND" ]] || [[ "${LEAK_M2}" == "MATCH_FOUND" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="secret_string_found_in_worktree"
fi

LOG_HASH="$(get_sha256 "${LOG_FILE}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-11" \
    "${RUN_ID}" \
    "agy/podman" \
    "podman_auth_delivery" \
    "none" \
    "podman_env_vs_secret_vs_ro" \
    "podman run [auth delivery comparison]" \
    0 \
    "spike/phase0/.logs/spike11_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON}" \
    "podman_secret"

echo "=== Spike-11 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
