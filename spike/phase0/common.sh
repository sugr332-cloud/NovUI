#!/usr/bin/env bash
# ==============================================================================
# Phase 0 Spike Common Library: common.sh
# 
# 目的: 各Spikeスクリプトで共通利用する環境検証、タイムアウト管理、ログ出力、
#       ハッシュ記録、機密情報安全照合、結果フォーマット出力関数を提供する。
# 前提: ホストOS（Bazzite）上で実行されること（コンテナ内実行は不可）。
# 実行方法: 各Spikeスクリプトから `source "$(dirname "$0")/common.sh"` で読み込む。
# 想定所要時間: 単体実行は想定しない（読み込みのみ）。
# 変更するパス: なし（共通関数の定義のみ）
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# 1. 実行環境検証（Bazzite ホスト検証）
# ------------------------------------------------------------------------------
ensure_host_environment() {
    if [[ -f /run/.containerenv ]] || [[ -f /run/.toolboxenv ]]; then
        echo "ERROR: This script must be executed on the Bazzite host, not inside a container (distrobox/toolbox)." >&2
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# 2. ディレクトリ準備
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LOGS_DIR="${SCRIPT_DIR}/.logs"
WORK_DIR="${SCRIPT_DIR}/.work"
RESULTS_DIR="${REPO_ROOT}/docs/spike/results"

ensure_directories() {
    mkdir -p "${LOGS_DIR}"
    mkdir -p "${WORK_DIR}"
    mkdir -p "${RESULTS_DIR}"
}

# ------------------------------------------------------------------------------
# 3. SELinux ラベル変更対象パスの安全性検証
#    :z / :Z を適用するパスが spike/phase0/.work/ 配下に限定されているかを検査
# ------------------------------------------------------------------------------
assert_safe_work_path() {
    local target_path="$1"
    local canonical_target
    local canonical_work

    mkdir -p "${target_path}"
    canonical_target="$(cd "${target_path}" && pwd -P)"
    canonical_work="$(cd "${WORK_DIR}" && pwd -P)"

    if [[ "${canonical_target}" != "${canonical_work}"* ]]; then
        echo "SECURITY ERROR: Target path '${canonical_target}' is outside '${canonical_work}'." >&2
        echo "SELinux label modification (:z / :Z) must NEVER be applied to repository root, Home, or .git." >&2
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# 4. SHA-256 ハッシュ取得
# ------------------------------------------------------------------------------
get_sha256() {
    local file_path="$1"
    if [[ -f "${file_path}" ]]; then
        sha256sum "${file_path}" | awk '{print $1}'
    else
        echo "none"
    fi
}

# ------------------------------------------------------------------------------
# 5. 機密情報安全照合
#    照合は「一致あり / なし」のみを出力し、キー本体をログや標準出力に出さない。
#    set -x を無効化して実行トレースによる露出を防止する。
# ------------------------------------------------------------------------------
check_secret_presence() {
    local secret_val="$1"
    local search_path="$2"
    local was_xtrace=0

    # xtrace (set -x) が有効な場合は一時的に無効化
    if [[ "$-" == *x* ]]; then
        was_xtrace=1
        set +x
    fi

    if [[ -z "${secret_val}" ]]; then
        if [[ ${was_xtrace} -eq 1 ]]; then set -x; fi
        echo "not_tested(empty_secret)"
        return 0
    fi

    # grep で検索。結果の一致有無のみ判定
    if grep -r -F -q "${secret_val}" "${search_path}" 2>/dev/null; then
        if [[ ${was_xtrace} -eq 1 ]]; then set -x; fi
        echo "MATCH_FOUND"
    else
        if [[ ${was_xtrace} -eq 1 ]]; then set -x; fi
        echo "NO_MATCH"
    fi
}

# ------------------------------------------------------------------------------
# 6. プロセス監視付きコマンド実行（全体タイムアウト・無出力タイムアウト対応）
# ------------------------------------------------------------------------------
run_monitored_command() {
    local total_timeout="$1"
    local no_output_timeout="$2"
    local log_file="$3"
    shift 3
    local cmd=("$@")

    local fifo_path
    fifo_path="$(mktemp -u "${WORK_DIR}/fifo.XXXXXX")"
    mkfifo "${fifo_path}"

    # コマンドをバックグラウンド（新しいプロセスグループ）で起動
    # stdout と stderr を両方ログに書きつつ、タイムアウト監視用のfifoに送る
    setsid "${cmd[@]}" > "${fifo_path}" 2>&1 &
    local cmd_pid=$!

    # ログ書き込みバックグラウンド処理
    tee "${log_file}" < "${fifo_path}" &
    local tee_pid=$!

    local start_time
    start_time=$(date +%s)
    local last_output_time="${start_time}"
    local timed_out="none"
    local exit_code=0

    # 監視ループ
    while kill -0 "${cmd_pid}" 2>/dev/null; do
        sleep 1
        local now
        now=$(date +%s)

        # 全体タイムアウト検査
        if (( now - start_time >= total_timeout )); then
            timed_out="total_timeout"
            break
        fi

        # ログファイルの更新時刻で無出力タイムアウト検査
        if [[ -f "${log_file}" ]]; then
            local file_mod
            file_mod=$(stat -c %Y "${log_file}" 2>/dev/null || echo "${now}")
            if (( now - file_mod >= no_output_timeout )); then
                timed_out="no_output_timeout"
                break
            fi
        fi
    done

    # タイムアウト時の強制終了処理
    if [[ "${timed_out}" != "none" ]]; then
        echo -e "\n[TIMEOUT DETECTED: ${timed_out} after $((now - start_time))s. Sending SIGTERM to process group...]" >> "${log_file}"
        kill -TERM -"${cmd_pid}" 2>/dev/null || true
        sleep 2
        if kill -0 "${cmd_pid}" 2>/dev/null; then
            echo "[Process did not terminate on SIGTERM. Sending SIGKILL to process group...]" >> "${log_file}"
            kill -KILL -"${cmd_pid}" 2>/dev/null || true
        fi
    fi

    # プロセスの終了待ち
    wait "${cmd_pid}" 2>/dev/null || exit_code=$?
    rm -f "${fifo_path}" 2>/dev/null || true
    wait "${tee_pid}" 2>/dev/null || true

    # タイムアウト情報を環境変数や戻り値に設定
    export LAST_CMD_TIMED_OUT="${timed_out}"
    export LAST_CMD_DURATION=$(( $(date +%s) - start_time ))
    export LAST_CMD_EXIT_CODE="${exit_code}"

    return "${exit_code}"
}

# ------------------------------------------------------------------------------
# 7. 手順書14章準拠の結果記録 YAML 出力ヘルパー
# ------------------------------------------------------------------------------
write_spike_result_yaml() {
    local output_file="$1"
    local spike_id="$2"
    local run_id="$3"
    local cli_name="$4"
    local cli_version="$5"
    local reported_model="$6"
    local container_settings="$7"
    local command_sanitized="$8"
    local exit_code="$9"
    local log_rel_path="${10}"
    local log_hash="${11}"
    local result="${12}"
    local failure_reason="${13}"
    local alternative_used="${14}"

    local timestamp
    timestamp="$(date -Iseconds)"
    local host_name
    host_name="$(uname -n)"
    local os_kernel
    os_kernel="$(uname -sr)"
    local selinux_state
    selinux_state="$(getenforce 2>/dev/null || echo 'unknown/not_installed')"
    local podman_ver
    podman_ver="$(podman --version 2>/dev/null || echo 'not_installed')"
    local git_ver
    git_ver="$(git --version 2>/dev/null || echo 'not_installed')"
    local repo_head
    repo_head="$(cd "${REPO_ROOT}" && git rev-parse HEAD 2>/dev/null || echo 'unknown')"
    local current_worktree
    current_worktree="$(cd "${REPO_ROOT}" && pwd -P)"

    cat <<EOF > "${output_file}"
spike_id: "${spike_id}"
run_id: "${run_id}"
timestamp: "${timestamp}"
host: "${host_name}"
os_kernel: "${os_kernel}"
selinux_state: "${selinux_state}"
podman_version: "${podman_ver}"
git_version: "${git_ver}"
cli_name: "${cli_name}"
cli_version: "${cli_version}"
reported_model: "${reported_model}"
repository_head: "${repo_head}"
worktree: "${current_worktree}"
container_settings: "${container_settings}"
command: "${command_sanitized}"
exit_code: ${exit_code}
log_file: "${log_rel_path}"
log_hash: "${log_hash}"
result: "${result}"
failure_reason: "${failure_reason}"
alternative_used: "${alternative_used}"
EOF
}
