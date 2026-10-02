#!/usr/bin/env bash
# ==============================================================================
# NovUI Production AGY Container Image Builder
#
# Usage:
#   ./container/build-agy-image.sh
#
# After building the image, set the following environment variable:
#   export NOVUI_AGY_IMAGE="localhost/novui-agy:<VERSION>"
#
# Note:
#   This script packages the host's agy CLI binary into an isolated container.
#   It NEVER copies credentials or anything from ~/.gemini.
# ==============================================================================

set -euo pipefail

# 1. Locate agy on host
AGY_BIN="$(command -v agy || true)"
if [[ -z "${AGY_BIN}" || ! -x "${AGY_BIN}" ]]; then
    echo "ERROR: 'agy' command not found in PATH or not executable." >&2
    exit 1
fi

# 2. Extract and validate version
RAW_VERSION="$("${AGY_BIN}" --version 2>&1 | tr -d '\r' | xargs)"
if [[ ! "${RAW_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Version output '${RAW_VERSION}' does not match pattern ^[0-9]+\\.[0-9]+\\.[0-9]+$" >&2
    exit 1
fi
AGY_VERSION="${RAW_VERSION}"
IMAGE_TAG="localhost/novui-agy:${AGY_VERSION}"

echo "Found agy: ${AGY_BIN}"
echo "Detected agy version: ${AGY_VERSION}"
echo "Target image tag: ${IMAGE_TAG}"

# 3. Check if image already exists (do not overwrite)
if podman image exists "${IMAGE_TAG}"; then
    echo "ERROR: Image '${IMAGE_TAG}' already exists. Aborting to avoid overwrite." >&2
    exit 1
fi

# 4. Prepare temporary build context
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINERFILE="${SCRIPT_DIR}/Containerfile"

if [[ ! -f "${CONTAINERFILE}" ]]; then
    echo "ERROR: Containerfile not found at '${CONTAINERFILE}'" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT INT TERM

# Copy agy binary and Containerfile into build context
# (Never copy anything from ~/.gemini)
cp "${AGY_BIN}" "${TMP_DIR}/agy"
chmod 0755 "${TMP_DIR}/agy"
cp "${CONTAINERFILE}" "${TMP_DIR}/Containerfile"

# 5. Build container image
echo "Building container image ${IMAGE_TAG}..."
podman build \
    --build-arg "AGY_VERSION=${AGY_VERSION}" \
    -t "${IMAGE_TAG}" \
    -f "${TMP_DIR}/Containerfile" \
    "${TMP_DIR}"

echo ""
echo "Successfully built ${IMAGE_TAG}"
echo "To use this image, set:"
echo "  export NOVUI_AGY_IMAGE=${IMAGE_TAG}"
