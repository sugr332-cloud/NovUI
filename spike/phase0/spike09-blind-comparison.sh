#!/usr/bin/env bash
# ==============================================================================
# Spike-09: Claude / AGY 文章ブラインド比較 (spike09-blind-comparison.sh)
# 
# 目的: 同一の入力パッケージ（outline, plan, world, characters, foreshadowing, constraints）
#       から Claude CLI および AGY CLI にそれぞれ本文（4,000〜6,000字）を生成させ、
#       前置き・モデル名・見出しの差異を除去した上でランダムに Sample A / B を割り当て、
#       Humanによるブラインド評価用シートを出力する。
#       ※ A/Bの対応表は spike/phase0/.work/ にのみ秘匿保存する。
# 前提: ホストOS（Bazzite）上で実行すること。
#       AGY CLI および Claude CLI が利用可能であること。
# 実行方法:
#   cd /path/to/NovUI
#   ./spike/phase0/spike09-blind-comparison.sh
# 想定所要時間: 約10〜20分（各モデルの生成時間による）
# 変更するパス:
#   - spike/phase0/.work/spike09/ (生成物一時領域)
#   - spike/phase0/.work/spike09-mapping.txt (秘匿対応表: 評価完了まで閲覧厳禁)
#   - spike/phase0/.logs/spike09_*.log
#   - docs/spike/results/spike09_blind_evaluation_sheet.md (Human評価用シート)
#   - docs/spike/results/spike09_result.yaml
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
TIMEOUT_AGY_TOTAL=900
TIMEOUT_AGY_NO_OUTPUT=180
TIMEOUT_CLAUDE_TOTAL=300
TIMEOUT_CLAUDE_NO_OUTPUT=120

# CLIバイナリおよびフラグ
# TODO: Spike-00の--help出力で確認してHumanが設定
AGY_BIN="agy"
AGY_FLAGS="" # TODO: Spike-00の--help出力で確認 (例: --non-interactive 等)

CLAUDE_BIN="claude"
CLAUDE_READONLY_FLAGS="" # TODO: Spike-00の--help出力で確認 (例: --tools "" 等)

INPUT_DIR="${SCRIPT_DIR}/spike09-input"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK="${WORK_DIR}/spike09"
rm -rf "${TEST_WORK}"
# 使い捨て作業領域の安全性検証と作成
assert_safe_work_path "${TEST_WORK}"

MAPPING_FILE="${WORK_DIR}/spike09-mapping.txt"
LOG_FILE="${LOGS_DIR}/spike09_${RUN_ID}.log"
REMOVED_LINES_LOG="${LOGS_DIR}/spike09_${RUN_ID}_removed_lines.log"
EVAL_SHEET="${RESULTS_DIR}/spike09_blind_evaluation_sheet_${RUN_ID}.md"
RESULT_YAML="${RESULTS_DIR}/spike09_${RUN_ID}.yaml"

echo "=== Spike-09: Blind Comparison Generation Started ===" | tee "${LOG_FILE}"
echo "# Spike-09 Removed Lines Log (${RUN_ID})" > "${REMOVED_LINES_LOG}"

# フラグ変数空チェック（修正E）
if [[ -z "${AGY_FLAGS}" ]]; then
    echo "ERROR: AGY_FLAGS is not set. Please inspect Spike-00 --help output and configure non-interactive flags." | tee -a "${LOG_FILE}"
    exit 1
fi

if [[ -z "${CLAUDE_READONLY_FLAGS}" ]]; then
    echo "ERROR: CLAUDE_READONLY_FLAGS is not set. Please inspect Spike-00 --help output and configure read-only flags." | tee -a "${LOG_FILE}"
    exit 1
fi

# 入力ファイルの存在確認
for req_file in outline.md plan.md world.md characters.md foreshadowing.md constraints.md; do
    if [[ ! -f "${INPUT_DIR}/${req_file}" ]]; then
        echo "ERROR: Required input file '${INPUT_DIR}/${req_file}' not found." | tee -a "${LOG_FILE}"
        exit 1
    fi
done

# ------------------------------------------------------------------------------
# プロンプトの組み立て
# ------------------------------------------------------------------------------
PROMPT_FILE="${TEST_WORK}/full_prompt.txt"
cat << EOF > "${PROMPT_FILE}"
あなたはプロの小説家です。以下の設定資料および執筆計画に基づき、第1章の本文を執筆してください。
前置き（挨拶、了解の返答、解説など）や後置き（所感など）は一切含めず、純粋な小説本文のみを出力してください。

【執筆制約】
$(cat "${INPUT_DIR}/constraints.md")

【世界観設定】
$(cat "${INPUT_DIR}/world.md")

【キャラクター設定】
$(cat "${INPUT_DIR}/characters.md")

【伏線設定】
$(cat "${INPUT_DIR}/foreshadowing.md")

【第1章 概要】
$(cat "${INPUT_DIR}/outline.md")

【第1章 執筆計画】
$(cat "${INPUT_DIR}/plan.md")

それでは、第1章の本文を開始してください。
EOF

echo "Prompt assembled successfully ($(wc -c < "${PROMPT_FILE}") bytes)." | tee -a "${LOG_FILE}"

# CLI実行前の git status 差分検査
GIT_SAFETY_BEFORE="$(check_git_status_safety)"
if [[ "${GIT_SAFETY_BEFORE}" != "CLEAN" ]]; then
    echo "WARNING: Pre-existing git status modifications detected." | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 1. Claude による本文生成（監視付き実行: 300s/120s）
# ------------------------------------------------------------------------------
echo "--- 1. Generating chapter with Claude CLI ---" | tee -a "${LOG_FILE}"
RAW_CLAUDE="${TEST_WORK}/raw_claude.txt"
RAW_CLAUDE_ERR="${TEST_WORK}/raw_claude_err.log"

run_monitored_command \
    "${TIMEOUT_CLAUDE_TOTAL}" \
    "${TIMEOUT_CLAUDE_NO_OUTPUT}" \
    "${RAW_CLAUDE}" \
    "${RAW_CLAUDE_ERR}" \
    "${PROMPT_FILE}" \
    env -C "${TEST_WORK}" "${CLAUDE_BIN}" ${CLAUDE_READONLY_FLAGS}

CLAUDE_EXIT="${LAST_CMD_EXIT_CODE}"
echo "Claude generation finished with exit code: ${CLAUDE_EXIT} (timed_out: ${LAST_CMD_TIMED_OUT})" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 2. AGY による本文生成（監視付き実行: 900s/180s）
# ------------------------------------------------------------------------------
echo "--- 2. Generating chapter with AGY CLI ---" | tee -a "${LOG_FILE}"
RAW_AGY="${TEST_WORK}/raw_agy.txt"
RAW_AGY_ERR="${TEST_WORK}/raw_agy_err.log"

run_monitored_command \
    "${TIMEOUT_AGY_TOTAL}" \
    "${TIMEOUT_AGY_NO_OUTPUT}" \
    "${RAW_AGY}" \
    "${RAW_AGY_ERR}" \
    "${PROMPT_FILE}" \
    env -C "${TEST_WORK}" "${AGY_BIN}" ${AGY_FLAGS}

AGY_EXIT="${LAST_CMD_EXIT_CODE}"
echo "AGY generation finished with exit code: ${AGY_EXIT} (timed_out: ${LAST_CMD_TIMED_OUT})" | tee -a "${LOG_FILE}"

# CLI実行後の git status 差分検査
GIT_SAFETY_AFTER="$(check_git_status_safety)"
GIT_DIFF_PASS="true"
if [[ "${GIT_SAFETY_AFTER}" != "CLEAN" ]]; then
    echo "FAIL: Unintended modifications detected outside allowable areas after AI generation!" | tee -a "${LOG_FILE}"
    GIT_DIFF_PASS="false"
fi

# 空出力検査
CLAUDE_EMPTY="false"
if [[ ! -s "${RAW_CLAUDE}" ]] || [[ -z "$(tr -d '[:space:]' < "${RAW_CLAUDE}")" ]]; then
    CLAUDE_EMPTY="true"
    echo "ERROR: Claude output is empty!" | tee -a "${LOG_FILE}"
fi

AGY_EMPTY="false"
if [[ ! -s "${RAW_AGY}" ]] || [[ -z "$(tr -d '[:space:]' < "${RAW_AGY}")" ]]; then
    AGY_EMPTY="true"
    echo "ERROR: AGY output is empty!" | tee -a "${LOG_FILE}"
fi

# ------------------------------------------------------------------------------
# 3. 匿名化・整形処理（先頭・末尾の挨拶とコードフェンスのみに限定、除去行をすべて記録）
# ------------------------------------------------------------------------------
CLEAN_CLAUDE="${TEST_WORK}/clean_claude.txt"
CLEAN_AGY="${TEST_WORK}/clean_agy.txt"

clean_novel_text() {
    local src="$1"
    local dst="$2"
    local model_tag="$3"
    python3 -c "
import sys, re

src_path = sys.argv[1]
dst_path = sys.argv[2]
model_tag = sys.argv[3]
log_path = sys.argv[4]

with open(src_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

removed_log = []
cleaned_lines = []

# 前置きの挨拶パターン（先頭のみ）
lead_greetings = ['了解しました', '承知しました', '承知いたしました', 'はい、', '以下に', 'お待たせしました']
# 後置きの挨拶パターン（末尾のみ）
trail_greetings = ['いかがでしょうか', '以上です', '執筆を終了します', 'ご参考になれば幸いです']

# 先頭処理: 先頭のコードフェンスや挨拶を除去
idx = 0
while idx < len(lines):
    line = lines[idx]
    s = line.strip()
    if s.startswith('\`\`\`'):
        removed_log.append(f'[{model_tag}:LEADING_CODE_FENCE] {line}')
        idx += 1
    elif any(s.startswith(p) for p in lead_greetings):
        removed_log.append(f'[{model_tag}:LEADING_GREETING] {line}')
        idx += 1
    elif s == '' and len(cleaned_lines) == 0:
        # 本文開始前の空行
        removed_log.append(f'[{model_tag}:LEADING_EMPTY] {line}')
        idx += 1
    else:
        break

# 本文本体を一旦保持
while idx < len(lines):
    line = lines[idx]
    s = line.strip()
    # 途中のコードフェンス行のみ除去（中身のテキストは保持）
    if s.startswith('\`\`\`'):
        removed_log.append(f'[{model_tag}:INTERNAL_CODE_FENCE] {line}')
        idx += 1
        continue
    cleaned_lines.append(line)
    idx += 1

# 末尾処理: 末尾のコードフェンスや挨拶を除去
while cleaned_lines:
    last_line = cleaned_lines[-1]
    s = last_line.strip()
    if s.startswith('\`\`\`'):
        removed_log.append(f'[{model_tag}:TRAILING_CODE_FENCE] {last_line}')
        cleaned_lines.pop()
    elif any(s.startswith(p) for p in trail_greetings):
        removed_log.append(f'[{model_tag}:TRAILING_GREETING] {last_line}')
        cleaned_lines.pop()
    elif s == '':
        cleaned_lines.pop()
    else:
        break

with open(dst_path, 'w', encoding='utf-8') as f:
    f.writelines(cleaned_lines)

with open(log_path, 'a', encoding='utf-8') as f:
    for r in removed_log:
        f.write(r if r.endswith('\n') else r + '\n')
" "${src}" "${dst}" "${model_tag}" "${REMOVED_LINES_LOG}"
}

clean_novel_text "${RAW_CLAUDE}" "${CLEAN_CLAUDE}" "Claude"
clean_novel_text "${RAW_AGY}" "${CLEAN_AGY}" "AGY"

echo "Text cleaned and removed lines recorded to: ${REMOVED_LINES_LOG}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 4. ランダムラベル割り当て（Sample A / Sample B）
# ------------------------------------------------------------------------------
# ランダム判定 (0 または 1)
RAND_CHOICE=$(( RANDOM % 2 ))

SAMPLE_A_FILE="${TEST_WORK}/sample_A.txt"
SAMPLE_B_FILE="${TEST_WORK}/sample_B.txt"

if [[ ${RAND_CHOICE} -eq 0 ]]; then
    # A = Claude, B = AGY
    cp "${CLEAN_CLAUDE}" "${SAMPLE_A_FILE}"
    cp "${CLEAN_AGY}" "${SAMPLE_B_FILE}"
    SECRET_MAPPING="Sample A = Claude CLI
Sample B = AGY CLI
Generated At = $(date -Iseconds)"
else
    # A = AGY, B = Claude
    cp "${CLEAN_AGY}" "${SAMPLE_A_FILE}"
    cp "${CLEAN_CLAUDE}" "${SAMPLE_B_FILE}"
    SECRET_MAPPING="Sample A = AGY CLI
Sample B = Claude CLI
Generated At = $(date -Iseconds)"
fi

# 対応表を spike/phase0/.work/ にのみ保存（Humanが評価完了するまで開かない）
cat << EOF > "${MAPPING_FILE}"
# ==============================================================================
# CONFIDENTIAL: Spike-09 Blind Comparison Mapping
# 警告: Humanが評価を完了し、評価シートを記録するまで開かないでください！
# ==============================================================================
${SECRET_MAPPING}
EOF
chmod 600 "${MAPPING_FILE}"

echo "Confidential mapping saved to: ${MAPPING_FILE}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 5. Humanブラインド評価用シートの出力
# ------------------------------------------------------------------------------
cat << 'EOF' > "${EVAL_SHEET}"
# Spike-09: Claude / AGY 文章ブラインド評価シート

**注意：公平な評価のため、評価をすべて完了するまで `spike/phase0/.work/spike09-mapping.txt` を開かないでください。**

---

## 評価基準（各項目 1: 劣悪 〜 5: 極めて優秀）

| 評価項目 | Sample A (1〜5) | Sample B (1〜5) |
|---|---|---|
| キャラクターの声（口調・個性の一貫性） | | |
| 日本語の自然さ（文法、リズム、語彙） | | |
| 情景描写（五感、空間把握、雰囲気） | | |
| 心情表現（感情の深み、動機の納得感） | | |
| plan準拠度（シーン構成・目標の達成） | | |
| 設定整合性（世界観・アイテムの正確さ） | | |
| 冗長さ（無駄な引き伸ばしの少なさ） | | |
| 文体の一貫性（トーンのブレの少なさ） | | |
| 読みやすさ（没入感、テンポ） | | |
| **総合評価（平均または総合評点）** | | |

### Human所感
- **Sample A に対する所感・特徴:**
  - 

- **Sample B に対する所感・特徴:**
  - 

- **どちらをNovUIの本文生成として採用したいか:**
  - [ ] Sample A
  - [ ] Sample B
  - [ ] 併用 / 用途別分担（具体的に: ）

---

## Sample A 本文
```text
EOF

cat "${SAMPLE_A_FILE}" >> "${EVAL_SHEET}"

cat << 'EOF' >> "${EVAL_SHEET}"
```

---

## Sample B 本文
```text
EOF

cat "${SAMPLE_B_FILE}" >> "${EVAL_SHEET}"

cat << 'EOF' >> "${EVAL_SHEET}"
```

---
*評価完了後に `spike/phase0/.work/spike09-mapping.txt` を確認し、結果を `docs/spike/results/` の記録に反映してください。*
EOF

echo "Human evaluation sheet generated at: ${EVAL_SHEET}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 結果記録
# ------------------------------------------------------------------------------
LOG_HASH="$(get_sha256 "${LOG_FILE}")"
RESULT_STATUS="PASS"
FAILURE_REASON=""

if [[ "${GIT_DIFF_PASS}" != "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="unintended_git_diff"
elif [[ "${CLAUDE_EMPTY}" == "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="empty_output_claude"
elif [[ "${AGY_EMPTY}" == "true" ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="empty_output_agy"
elif [[ ${CLAUDE_EXIT} -ne 0 ]] || [[ ${AGY_EXIT} -ne 0 ]]; then
    RESULT_STATUS="FAIL"
    FAILURE_REASON="claude_exit_${CLAUDE_EXIT}_agy_exit_${AGY_EXIT}"
fi

write_spike_result_yaml \
    "${RESULT_YAML}" \
    "Spike-09" \
    "${RUN_ID}" \
    "claude / agy" \
    "unknown" \
    "blind_samples_generated" \
    "host_cli_generation" \
    "spike09-blind-comparison.sh" \
    0 \
    "spike/phase0/.logs/spike09_${RUN_ID}.log" \
    "${LOG_HASH}" \
    "${RESULT_STATUS}" \
    "${FAILURE_REASON:-none}" \
    "none"

echo "=== Spike-09 Completed: ${RESULT_STATUS} ===" | tee -a "${LOG_FILE}"
echo "Result recorded at: ${RESULT_YAML}"
