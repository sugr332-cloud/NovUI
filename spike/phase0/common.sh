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
    local canonical_work
    local canonical_target

    canonical_work="$(realpath -m "${WORK_DIR}")"
    canonical_target="$(realpath -m "${target_path}")"

    # 厳格なプレフィックス判定（完全一致 または ${canonical_work}/ 配下）
    if [[ "${canonical_target}" != "${canonical_work}" && "${canonical_target}" != "${canonical_work}/"* ]]; then
        echo "SECURITY ERROR: Target path '${canonical_target}' is outside '${canonical_work}'." >&2
        echo "SELinux label modification (:z / :Z) must NEVER be applied to repository root, Home, or .git." >&2
        exit 1
    fi

    # 安全性確認後にディレクトリ作成
    mkdir -p "${canonical_target}"
}

# ------------------------------------------------------------------------------
# 3.1 ワーク領域内ファイルの安全性検証（ディレクトリ作成は行わない）
# ------------------------------------------------------------------------------
assert_safe_work_file() {
    local target_file="$1"
    local canonical_work
    local canonical_target

    canonical_work="$(realpath -m "${WORK_DIR}")"
    canonical_target="$(realpath -m "${target_file}")"

    # 厳格なプレフィックス判定（${canonical_work}/ 配下）
    if [[ "${canonical_target}" != "${canonical_work}/"* ]]; then
        echo "SECURITY ERROR: Target file '${canonical_target}' is outside '${canonical_work}'." >&2
        echo "Target file must be strictly inside spike/phase0/.work/." >&2
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# 3.2 マウントオプション組み立て共通関数
# ------------------------------------------------------------------------------
# MOUNT_LABEL は "z" または "Z" または ""（ラベルなし）
MOUNT_LABEL="Z"  # TODO: Spike-02の結果で確定

mount_opts() {
    local mode="$1" # rw または ro
    local label="${2:-${MOUNT_LABEL}}"
    if [[ -n "${label}" ]]; then
        echo "${mode},${label}"
    else
        echo "${mode}"
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
# 6. プロセス監視付きコマンド実行（全体タイムアウト・無出力タイムアウト・PGID監視対応）
#    引数: total_timeout no_output_timeout stdout_log stderr_log stdin_file cmd...
#    戻り値: 呼び出し元が set -e で停止しないよう常に 0 を返す。
#            結果は LAST_CMD_PGID, LAST_CMD_EXIT_CODE, LAST_CMD_TIMED_OUT,
#            LAST_CMD_DURATION, LAST_CMD_SIGNAL を参照すること。
# ------------------------------------------------------------------------------
run_monitored_command() {
    local total_timeout="$1"
    local no_output_timeout="$2"
    local stdout_log="$3"
    local stderr_log="$4"
    local stdin_file="$5"
    shift 5
    local cmd=("$@")

    local fifo_out fifo_err
    fifo_out="$(mktemp -u "${WORK_DIR}/fifo_out.XXXXXX")"
    fifo_err="$(mktemp -u "${WORK_DIR}/fifo_err.XXXXXX")"
    mkfifo "${fifo_out}"
    mkfifo "${fifo_err}"

    # stdout / stderr バックグラウンド書き込み処理
    tee "${stdout_log}" < "${fifo_out}" &
    local tee_out_pid=$!
    tee "${stderr_log}" < "${fifo_err}" >&2 &
    local tee_err_pid=$!

    # コマンドをバックグラウンド（新しいセッション/プロセスグループ PGID）で起動
    # setsid --wait により子プロセスの完了を待ち、setsidのPIDがPGIDとなる
    if [[ -n "${stdin_file}" && -f "${stdin_file}" ]]; then
        setsid --wait "${cmd[@]}" < "${stdin_file}" > "${fifo_out}" 2> "${fifo_err}" &
    else
        setsid --wait "${cmd[@]}" > "${fifo_out}" 2> "${fifo_err}" &
    fi
    local cmd_pid=$!

    local start_time
    start_time=$(date +%s)
    local timed_out="none"
    local term_signal="none"
    local exit_code=0

    # 監視ループ
    while kill -0 "${cmd_pid}" 2>/dev/null; do
        sleep 1
        local now
        now=$(date +%s)

        # 全体タイムアウト検査
        if [[ $((now - start_time)) -ge ${total_timeout} ]]; then
            timed_out="total_timeout"
            break
        fi

        # stdout / stderr の最新更新時刻で無出力タイムアウト検査
        local out_mod err_mod latest_mod
        out_mod=$(stat -c %Y "${stdout_log}" 2>/dev/null || echo "${now}")
        err_mod=$(stat -c %Y "${stderr_log}" 2>/dev/null || echo "${now}")
        latest_mod=$(( out_mod > err_mod ? out_mod : err_mod ))
        if [[ $((now - latest_mod)) -ge ${no_output_timeout} ]]; then
            timed_out="no_output_timeout"
            break
        fi
    done

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    # タイムアウト時の強制終了処理（プロセスグループ全体へシグナル送信）
    if [[ "${timed_out}" != "none" ]]; then
        term_signal="SIGTERM"
        echo -e "\n[TIMEOUT DETECTED: ${timed_out} after ${duration}s. Sending SIGTERM to PGID -${cmd_pid}...]" >> "${stderr_log}"
        kill -TERM -"${cmd_pid}" 2>/dev/null || true
        sleep 2
        if kill -0 "${cmd_pid}" 2>/dev/null; then
            term_signal="SIGKILL"
            echo "[Process did not terminate on SIGTERM. Sending SIGKILL to PGID -${cmd_pid}...]" >> "${stderr_log}"
            kill -KILL -"${cmd_pid}" 2>/dev/null || true
            sleep 1
        fi
    fi

    # プロセスの終了待ち
    wait "${cmd_pid}" 2>/dev/null || exit_code=$?
    rm -f "${fifo_out}" "${fifo_err}" 2>/dev/null || true
    wait "${tee_out_pid}" 2>/dev/null || true
    wait "${tee_err_pid}" 2>/dev/null || true

    # 状態を環境変数に設定
    export LAST_CMD_PGID="${cmd_pid}"
    export LAST_CMD_TIMED_OUT="${timed_out}"
    export LAST_CMD_DURATION="${duration}"
    export LAST_CMD_SIGNAL="${term_signal}"
    export LAST_CMD_EXIT_CODE="${exit_code}"

    # 呼び出し元が set -e で停止しないよう return 0
    return 0
}

# ------------------------------------------------------------------------------
# 7. AI CLI実行前後の git status 差分検査ヘルパー
#    .work/, .logs/, docs/spike/results/ 以外に変更・未追跡ファイルがあれば UNSAFE を記録
# ------------------------------------------------------------------------------
check_git_status_safety() {
    local repo_root="${REPO_ROOT}"
    local diffs
    diffs="$(git -C "${repo_root}" status --porcelain --untracked-files=all 2>/dev/null || true)"

    local unsafe_diffs=()
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        # porcelain 形式: "XY path" または "XY orig -> path"
        local file_path="${line:3}"
        # クォートがあれば除去
        file_path="${file_path%\"}"
        file_path="${file_path#\"}"
        # リネーム形式 "orig -> new" の場合は new を抽出
        if [[ "${file_path}" == *" -> "* ]]; then
            file_path="${file_path##* -> }"
            file_path="${file_path%\"}"
            file_path="${file_path#\"}"
        fi

        # 許可パスの判定: spike/phase0/.work/, spike/phase0/.logs/, docs/spike/results/
        if [[ "${file_path}" != "spike/phase0/.work/"* && \
              "${file_path}" != "spike/phase0/.logs/"* && \
              "${file_path}" != "docs/spike/results/"* ]]; then
            unsafe_diffs+=("${line}")
        fi
    done <<< "${diffs}"

    if [[ ${#unsafe_diffs[@]} -gt 0 ]]; then
        echo "UNSAFE_DIFF_DETECTED"
        echo "Unintended git modifications detected outside allowable areas:" >&2
        printf '  %s\n' "${unsafe_diffs[@]}" >&2
        return 0
    else
        echo "CLEAN"
        return 0
    fi
}

# ------------------------------------------------------------------------------
# 8. 手順書14章準拠の結果記録 YAML 出力ヘルパー
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
