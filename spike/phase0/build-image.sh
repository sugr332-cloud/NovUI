#!/usr/bin/env bash
# ==============================================================================
# Phase 0 Spike: Container Image Builder (build-image.sh)
# 
# 目的: ホストの AGY CLI バイナリ（ELF 64-bit）と最小依存パッケージを含む
#       Spike検証用コンテナイメージ（novui-spike:agy-<VERSION>）をビルドする。
# 前提: ホストOS（Bazzite）上でHumanが実行すること。
#       ホスト上に ~/.local/bin/agy が存在すること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/build-image.sh
# 想定所要時間: 約1〜3分（ベースイメージのpullおよびパッケージインストール時間）
# 変更するパス:
#   - spike/phase0/.work/image-build/ (ビルド用作業領域、完了後agyは削除)
#   - spike/phase0/.logs/build_image_*.log
#   - docs/spike/results/build_image_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
# ホスト上の AGY CLI バイナリパス
AGY_HOST_BIN="${AGY_HOST_BIN:-$HOME/.local/bin/agy}"

# ベースイメージの定義
# TODO: Humanが指定（例: registry.fedoraproject.org/fedora:40 等）
BASE_IMAGE=""

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
BUILD_DIR="${WORK_DIR}/image-build"
LOG_FILE="${LOGS_DIR}/build_image_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/build_image_${RUN_ID}.yaml"

# ビルド作業ディレクトリのクリーンアップ trap（完了後、必ずagyバイナリを削除）
cleanup_build_artifacts() {
    if [[ -f "${BUILD_DIR}/agy" ]]; then
        echo "Cleaning up temporary agy binary in build context..." >> "${LOG_FILE}" 2>/dev/null || true
        rm -f "${BUILD_DIR}/agy" 2>/dev/null || true
    fi
}
trap cleanup_build_artifacts EXIT INT TERM

echo "=== Spike Container Image Build Started ===" | tee "${LOG_FILE}"

# 1. BASE_IMAGE の検証
if [[ -z "${BASE_IMAGE}" ]] || [[ "${BASE_IMAGE}" == *"TODO"* ]]; then
    echo "ERROR: BASE_IMAGE is not set in build-image.sh." | tee -a "${LOG_FILE}"
    echo "Please edit build-image.sh and specify a valid base image (e.g., registry.fedoraproject.org/fedora:40)." | tee -a "${LOG_FILE}"
    exit 1
fi

# 2. ホストバイナリの存在と形式検証
if [[ ! -f "${AGY_HOST_BIN}" ]]; then
    echo "ERROR: AGY host binary not found at '${AGY_HOST_BIN}'." | tee -a "${LOG_FILE}"
    exit 1
fi

FILE_TYPE="$(file "${AGY_HOST_BIN}" 2>/dev/null || true)"
if [[ "${FILE_TYPE}" != *"ELF 64-bit"* ]]; then
    echo "ERROR: '${AGY_HOST_BIN}' is not an ELF 64-bit executable." | tee -a "${LOG_FILE}"
    echo "file check result: ${FILE_TYPE}" | tee -a "${LOG_FILE}"
    exit 1
fi

# 3. ビルド用作業ディレクトリの準備（assert_safe_work_path で検証）
rm -rf "${BUILD_DIR}"
assert_safe_work_path "${BUILD_DIR}"

# 4. ビルドコンテキストの配置（agyバイナリとContainerfileのみ。~/.geminiは一切含めない）
cp "${AGY_HOST_BIN}" "${BUILD_DIR}/agy"
chmod 0755 "${BUILD_DIR}/agy"
cp "${SCRIPT_DIR}/Containerfile" "${BUILD_DIR}/Containerfile"

# 5. バージョンとハッシュの採取
AGY_SHA256="$(get_sha256 "${BUILD_DIR}/agy")"
RAW_VERSION="$("${BUILD_DIR}/agy" --version 2>&1 || true)"
AGY_VERSION="$(echo "${RAW_VERSION}" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

if [[ -z "${AGY_VERSION}" ]]; then
    echo "ERROR: Could not obtain version from agy binary." | tee -a "${LOG_FILE}"
    exit 1
fi

IMAGE_TAG="novui-spike:agy-${AGY_VERSION}"

echo "AGY Host Binary: ${AGY_HOST_BIN}" | tee -a "${LOG_FILE}"
echo "AGY Binary SHA256: ${AGY_SHA256}" | tee -a "${LOG_FILE}"
echo "AGY Version: ${AGY_VERSION}" | tee -a "${LOG_FILE}"
echo "Target Image Tag: ${IMAGE_TAG}" | tee -a "${LOG_FILE}"
echo "Base Image: ${BASE_IMAGE}" | tee -a "${LOG_FILE}"

# 6. Podman イメージのビルド
echo "--- Executing podman build ---" | tee -a "${LOG_FILE}"
BUILD_EXIT=0
podman build \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "AGY_VERSION=${AGY_VERSION}" \
    -t "${IMAGE_TAG}" \
    -f "${BUILD_DIR}/Containerfile" \
    "${BUILD_DIR}" 2>&1 | tee -a "${LOG_FILE}" || BUILD_EXIT=$?

if [[ ${BUILD_EXIT} -ne 0 ]]; then
    echo "ERROR: podman build failed with exit code ${BUILD_EXIT}." | tee -a "${LOG_FILE}"
    write_spike_result_yaml \
        "${RESULT_YAML}" \
        "build-image" \
        "${RUN_ID}" \
        "agy" \
        "${AGY_VERSION}" \
        "none" \
        "base:${BASE_IMAGE}" \
        "podman build -t ${IMAGE_TAG}" \
        "${BUILD_EXIT}" \
        "spike/phase0/.logs/build_image_${RUN_ID}.log" \
        "$(get_sha256 "${LOG_FILE}")" \
        "FAIL" \
        "podman_build_failed" \
        "none"
    exit 1
fi

# 7. イメージIDの取得と記録
IMAGE_ID="$(podman images -q "${IMAGE_TAG}" 2>/dev/null | head -n 1 || true)"
echo "Image built successfully. Image ID: ${IMAGE_ID}" | tee -a "${LOG_FILE}"

# 8. ビルドディレクトリの agy バイナリ削除
rm -f "${BUILD_DIR}/agy"
echo "Temporary agy binary removed from build context." | tee -a "${LOG_FILE}"

# 9. 結果記録
LOG_HASH="$(get_sha256 "${LOG_FILE}")"
write_spike_result_yaml \
    "${RESULT_YAML}" \
    "build-image" \
    "${RUN_ID}" \
    "agy" \
    "${AGY_VERSION}" \
    "none" \
    "image_id:${IMAGE_ID}" \
    "podman build -t ${IMAGE_TAG}" \
    0 \
    "spike/phase0/.logs/build_image_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "PASS" \
    "none" \
    "built_tag:${IMAGE_TAG}"

echo "=== Build Image Completed: PASS ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
echo "Use the following tag for CONTAINER_IMAGE in Spike-02, Spike-03, and Spike-11:"
echo "CONTAINER_IMAGE=\"${IMAGE_TAG}\""
