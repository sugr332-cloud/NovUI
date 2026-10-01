#!/usr/bin/env bash
# ==============================================================================
# Spike-05: Claude CLI出力挙動 (spike05-claude-output.sh)
# 
# 目的: Claude CLIの読み取り専用実行時の変更防止、および同一プロンプトに対する
#       10回連続の構造化出力試験（JSONパース成功率、コードフェンス囲み、
#       前置き・後置き文、JSON Schema適合率、欠落・未知フィールド率）を実測・集計する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       python3 および jsonschema ライブラリが利用可能であること（自動インストールは行わない）。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike05-claude-output.sh
# 想定所要時間: 約5〜10分
# 変更するパス:
#   - spike/phase0/.work/spike05/ (使い捨て作業ディレクトリ)
#   - spike/phase0/.logs/spike05_*.log
#   - docs/spike/results/spike05_*.yaml
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_host_environment
ensure_directories

# ------------------------------------------------------------------------------
# 設定・変数定義
# ------------------------------------------------------------------------------
# タイムアウト値（手順書5章）
TIMEOUT_CLAUDE_TOTAL=300
TIMEOUT_CLAUDE_NO_OUTPUT=120

# CLIバイナリおよびフラグ（Spike-00 で確定）
CLAUDE_BIN="claude"
# 読み取り専用フラグ（Write/Edit/Bash 等のツールを制限するフラグ）
CLAUDE_READONLY_FLAGS="-p --tools Read --permission-prompts none --no-session-persistence"
# 構造化出力オプション
CLAUDE_JSON_FLAGS="--output-format text"

SCHEMA_FILE="${SCRIPT_DIR}/spike05-schema.json"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_ROOT="${WORK_DIR}/spike05"
rm -rf "${TEST_ROOT}"
# 使い捨て作業領域の安全性検証と作成
assert_safe_work_path "${TEST_ROOT}"

LOG_FILE="${LOGS_DIR}/spike05_${RUN_ID}.log"
RESULT_YAML="${RESULTS_DIR}/spike05_${RUN_ID}.yaml"

echo "=== Spike-05: Claude CLI Output Behavior Verification Started ===" | tee "${LOG_FILE}"

# 読み取り専用フラグ未設定時のエラー終了
if [[ -z "${CLAUDE_READONLY_FLAGS}" ]]; then
    echo "ERROR: CLAUDE_READONLY_FLAGS is not set. Please inspect Spike-00 --help output and configure read-only flags (e.g., --tools '')." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${CLAUDE_JSON_FLAGS}" ]]; then
    echo "ERROR: CLAUDE_JSON_FLAGS is not set. Please inspect Spike-00 --help output and configure JSON flags." | tee -a "${LOG_FILE}"
    exit 1
fi

# ------------------------------------------------------------------------------
# 追加条件3: python3 および jsonschema ライブラリの存在検査
# ------------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is not installed or not in PATH." | tee -a "${LOG_FILE}"
    echo "Please install python3 on your system." | tee -a "${LOG_FILE}"
    exit 1
fi

if ! python3 -c "import jsonschema" 2>/dev/null; then
    echo "======================================================================" | tee -a "${LOG_FILE}"
    echo "ERROR: Python library 'jsonschema' is required for schema validation." | tee -a "${LOG_FILE}"
    echo "The script will not install packages automatically." | tee -a "${LOG_FILE}"
    echo "To run this test, please install jsonschema in your Python environment:" | tee -a "${LOG_FILE}"
    echo "  python3 -m pip install jsonschema" | tee -a "${LOG_FILE}"
    echo "  (or: pip install --user jsonschema / python3 -m venv .venv && source .venv/bin/activate && pip install jsonschema)" | tee -a "${LOG_FILE}"
    echo "======================================================================" | tee -a "${LOG_FILE}"
    exit 1
fi

# ------------------------------------------------------------------------------
# 11.1 読み取り専用実行の検証
# ------------------------------------------------------------------------------
echo "--- 11.1 Read-Only Execution Test ---" | tee -a "${LOG_FILE}"
RO_TEST_DIR="${TEST_ROOT}/readonly_test"
assert_safe_work_path "${RO_TEST_DIR}"

# 実行前の状態を記録
SENTINEL_FILE="${RO_TEST_DIR}/sentinel.txt"
echo "initial_content" > "${SENTINEL_FILE}"
RO_HASH_BEFORE="$(get_sha256 "${SENTINEL_FILE}")"

# ファイル作成・変更を明示的に指示するプロンプトを与えて起動
PROMPT_FORCE_WRITE="Please write a file named created_by_ai.txt containing 'malicious write test' and overwrite sentinel.txt with 'overwritten'."
RO_PROMPT_FILE="${RO_TEST_DIR}/prompt_force_write.txt"
echo "${PROMPT_FORCE_WRITE}" > "${RO_PROMPT_FILE}"

RO_OUT="${TEST_ROOT}/ro_output.log"
RO_OUT_ERR="${TEST_ROOT}/ro_output_err.log"

# CLI実行前の git status 差分検査
GIT_SAFETY_BEFORE="$(check_git_status_safety)"
if [[ "${GIT_SAFETY_BEFORE}" != "CLEAN" ]]; then
    echo "WARNING: Pre-existing git status modifications detected." | tee -a "${LOG_FILE}"
fi

# 監視付きコマンド実行（全体300s, 無出力120s）
run_monitored_command \
    "${TIMEOUT_CLAUDE_TOTAL}" \
    "${TIMEOUT_CLAUDE_NO_OUTPUT}" \
    "${RO_OUT}" \
    "${RO_OUT_ERR}" \
    "${RO_PROMPT_FILE}" \
    env -C "${RO_TEST_DIR}" "${CLAUDE_BIN}" ${CLAUDE_READONLY_FLAGS}

RO_HASH_AFTER="$(get_sha256 "${SENTINEL_FILE}")"

RO_PASS="true"
if [[ -f "${RO_TEST_DIR}/created_by_ai.txt" ]]; then
    echo "FAIL: File created_by_ai.txt was created despite read-only restrictions!" | tee -a "${LOG_FILE}"
    RO_PASS="false"
fi

if [[ "${RO_HASH_BEFORE}" != "${RO_HASH_AFTER}" ]]; then
    echo "FAIL: sentinel.txt was modified despite read-only restrictions!" | tee -a "${LOG_FILE}"
    RO_PASS="false"
fi

if [[ "${RO_PASS}" == "true" ]]; then
    echo "PASS: Read-only restrictions successfully prevented file creation and modification." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 11.2 構造化出力（10回反復実行・統計集計）
# ------------------------------------------------------------------------------
echo "--- 11.2 Structured Output 10-Iteration Test ---" | tee -a "${LOG_FILE}"

STRUCTURED_PROMPT="You are a novel planning assistant. Output ONLY a valid JSON object strictly complying with the following JSON Schema for chapter ch-001.
Do NOT include any markdown code fences, greetings, preambles, or postscripts.
Schema:
$(cat "${SCHEMA_FILE}")
"

ITERATION_DIR="${TEST_ROOT}/iterations"
assert_safe_work_path "${ITERATION_DIR}"

STRUCT_PROMPT_FILE="${TEST_ROOT}/prompt_structured.txt"
echo "${STRUCTURED_PROMPT}" > "${STRUCT_PROMPT_FILE}"

COUNT_TOTAL=10
COUNT_RAW_JSON_PARSE=0
COUNT_CODE_FENCE=0
COUNT_PRE_POST_TEXT=0
COUNT_SCHEMA_VALID=0
COUNT_SCHEMA_INVALID=0

VALIDATOR_PY="${WORK_DIR}/spike05_validator.py"
cat << 'PYEOF' > "${VALIDATOR_PY}"
import sys
import json
import re
import jsonschema

schema_path = sys.argv[1]
output_file = sys.argv[2]

with open(schema_path, 'r', encoding='utf-8') as f:
    schema = json.load(f)

with open(output_file, 'r', encoding='utf-8') as f:
    content = f.read()

raw_parse = False
code_fence = False
pre_post_text = False
schema_valid = False
error_reason = ""

# 1. 直接パース判定
try:
    data = json.loads(content.strip())
    raw_parse = True
except Exception:
    raw_parse = False

# 2. コードフェンス検出
if "```" in content:
    code_fence = True

# 3. JSON抽出と前置き/後置き判定
extracted_json = None
fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
if fence_match:
    extracted_json = fence_match.group(1).strip()
    prefix = content[:fence_match.start()].strip()
    suffix = content[fence_match.end():].strip()
    if prefix or suffix:
        pre_post_text = True
else:
    # フェンスなしで前後に余分なテキストがあるか
    brace_start = content.find('{')
    brace_end = content.rfind('}')
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        extracted_json = content[brace_start:brace_end+1].strip()
        if content[:brace_start].strip() or content[brace_end+1:].strip():
            pre_post_text = True

target_json = content.strip() if raw_parse else (extracted_json if extracted_json else "")

# 4. Schema検証
if target_json:
    try:
        obj = json.loads(target_json)
        jsonschema.validate(instance=obj, schema=schema)
        schema_valid = True
    except jsonschema.ValidationError as ve:
        error_reason = f"ValidationError: {ve.message}"
    except Exception as e:
        error_reason = f"ParseError: {str(e)}"
else:
    error_reason = "NoValidJsonFound"

res = {
    "raw_parse": raw_parse,
    "code_fence": code_fence,
    "pre_post_text": pre_post_text,
    "schema_valid": schema_valid,
    "error_reason": error_reason
}
print(json.dumps(res))
PYEOF

for i in $(seq 1 "${COUNT_TOTAL}"); do
    echo "Running iteration ${i}/${COUNT_TOTAL}..." | tee -a "${LOG_FILE}"
    OUT_FILE="${ITERATION_DIR}/run_${i}.txt"
    ERR_FILE="${ITERATION_DIR}/run_${i}_err.txt"

    # Claude CLI 監視付き実行（全体300s, 無出力120s）
    run_monitored_command \
        "${TIMEOUT_CLAUDE_TOTAL}" \
        "${TIMEOUT_CLAUDE_NO_OUTPUT}" \
        "${OUT_FILE}" \
        "${ERR_FILE}" \
        "${STRUCT_PROMPT_FILE}" \
        env -C "${ITERATION_DIR}" "${CLAUDE_BIN}" ${CLAUDE_READONLY_FLAGS} ${CLAUDE_JSON_FLAGS}

    # Pythonによる判定
    ANALYSIS="$(python3 "${VALIDATOR_PY}" "${SCHEMA_FILE}" "${OUT_FILE}")"
    echo "  Iteration ${i} analysis: ${ANALYSIS}" | tee -a "${LOG_FILE}"

    IS_RAW=$(echo "${ANALYSIS}" | python3 -c "import sys, json; print(json.load(sys.stdin)['raw_parse'])")
    IS_FENCE=$(echo "${ANALYSIS}" | python3 -c "import sys, json; print(json.load(sys.stdin)['code_fence'])")
    IS_PREPOST=$(echo "${ANALYSIS}" | python3 -c "import sys, json; print(json.load(sys.stdin)['pre_post_text'])")
    IS_VALID=$(echo "${ANALYSIS}" | python3 -c "import sys, json; print(json.load(sys.stdin)['schema_valid'])")

    if [[ "${IS_RAW}" == "True" ]]; then COUNT_RAW_JSON_PARSE=$((COUNT_RAW_JSON_PARSE + 1)); fi
    if [[ "${IS_FENCE}" == "True" ]]; then COUNT_CODE_FENCE=$((COUNT_CODE_FENCE + 1)); fi
    if [[ "${IS_PREPOST}" == "True" ]]; then COUNT_PRE_POST_TEXT=$((COUNT_PRE_POST_TEXT + 1)); fi
    if [[ "${IS_VALID}" == "True" ]]; then COUNT_SCHEMA_VALID=$((COUNT_SCHEMA_VALID + 1)); else COUNT_SCHEMA_INVALID=$((COUNT_SCHEMA_INVALID + 1)); fi
done

echo "--- 11.2 Aggregated Results (${COUNT_TOTAL} runs) ---" | tee -a "${LOG_FILE}"
echo "Direct JSON Parseable: ${COUNT_RAW_JSON_PARSE}/${COUNT_TOTAL}" | tee -a "${LOG_FILE}"
echo "Code Fences Present: ${COUNT_CODE_FENCE}/${COUNT_TOTAL}" | tee -a "${LOG_FILE}"
echo "Pre/Post Explanations: ${COUNT_PRE_POST_TEXT}/${COUNT_TOTAL}" | tee -a "${LOG_FILE}"
echo "Schema Valid: ${COUNT_SCHEMA_VALID}/${COUNT_TOTAL}" | tee -a "${LOG_FILE}"
echo "Schema Invalid: ${COUNT_SCHEMA_INVALID}/${COUNT_TOTAL}" | tee -a "${LOG_FILE}"

# CLI実行後の git status 差分検査
GIT_SAFETY_AFTER="$(check_git_status_safety)"
GIT_DIFF_PASS="true"
if [[ "${GIT_SAFETY_AFTER}" != "CLEAN" ]]; then
    echo "FAIL: Unintended modifications detected outside allowable areas after Claude CLI execution!" | tee -a "${LOG_FILE}"
    GIT_DIFF_PASS="false"
fi

# ------------------------------------------------------------------------------
# 結果判定・記録
# ------------------------------------------------------------------------------
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${GIT_DIFF_PASS}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="unintended_git_diff"
elif [[ "${RO_PASS}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="readonly_restrictions_failed"
fi

LOG_HASH="$(get_sha256 "${LOG_FILE}")"

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-05" \
    "${RUN_ID}" \
    "claude" \
    "$("${CLAUDE_BIN}" --version 2>/dev/null || echo 'unknown')" \
    "unknown" \
    "host_readonly_and_structured" \
    "${CLAUDE_BIN} [structured output prompt x10]" \
    0 \
    "spike/phase0/.logs/spike05_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "pre_post_strip_and_schema_retry"

echo "=== Spike-05 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
