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
CLAUDE_FLAGS="" # TODO: Spike-00の--help出力で確認

INPUT_DIR="${SCRIPT_DIR}/spike09-input"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
TEST_WORK="${WORK_DIR}/spike09"
rm -rf "${TEST_WORK}"
mkdir -p "${TEST_WORK}"

MAPPING_FILE="${WORK_DIR}/spike09-mapping.txt"
LOG_FILE="${LOGS_DIR}/spike09_${RUN_ID}.log"
EVAL_SHEET="${RESULTS_DIR}/spike09_blind_evaluation_sheet_${RUN_ID}.md"
RESULT_YAML="${RESULTS_DIR}/spike09_${RUN_ID}.yaml"

echo "=== Spike-09: Blind Comparison Generation Started ===" | tee "${LOG_FILE}"

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

# ------------------------------------------------------------------------------
# 1. Claude による本文生成
# ------------------------------------------------------------------------------
echo "--- 1. Generating chapter with Claude CLI ---" | tee -a "${LOG_FILE}"
RAW_CLAUDE="${TEST_WORK}/raw_claude.txt"
CLAUDE_EXIT=0
(
    cat "${PROMPT_FILE}" | "${CLAUDE_BIN}" ${CLAUDE_FLAGS} > "${RAW_CLAUDE}" 2>> "${LOG_FILE}"
) || CLAUDE_EXIT=$?
echo "Claude generation finished with exit code: ${CLAUDE_EXIT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 2. AGY による本文生成
# ------------------------------------------------------------------------------
echo "--- 2. Generating chapter with AGY CLI ---" | tee -a "${LOG_FILE}"
RAW_AGY="${TEST_WORK}/raw_agy.txt"
AGY_EXIT=0
(
    cat "${PROMPT_FILE}" | "${AGY_BIN}" ${AGY_FLAGS} > "${RAW_AGY}" 2>> "${LOG_FILE}"
) || AGY_EXIT=$?
echo "AGY generation finished with exit code: ${AGY_EXIT}" | tee -a "${LOG_FILE}"

# ------------------------------------------------------------------------------
# 3. 匿名化・整形処理（モデル名や前置きの機械的除去）
# ------------------------------------------------------------------------------
CLEAN_CLAUDE="${TEST_WORK}/clean_claude.txt"
CLEAN_AGY="${TEST_WORK}/clean_agy.txt"

# 簡易クリーニング関数（マークダウンコードブロックや前置き行の除去）
clean_novel_text() {
    local src="$1"
    local dst="$2"
    python3 -c "
import re, sys
with open(sys.argv[1], 'r', encoding='utf-8') as f:
    text = f.read()

# コードフェンス除去
text = re.sub(r'^```.*$', '', text, flags=re.MULTILINE)

# 前置きの挨拶などを除去（行頭の典型的定型句）
lines = text.splitlines()
cleaned_lines = []
skip = True
for line in lines:
    s = line.strip()
    if skip:
        if any(s.startswith(p) for p in ['了解しました', '承知しました', '以下に', '第1章', '#']):
            continue
        if s == '':
            continue
        skip = False
    cleaned_lines.append(line)

with open(sys.argv[2], 'w', encoding='utf-8') as f:
    f.write('\n'.join(cleaned_lines).strip() + '\n')
" "${src}" "${dst}"
}

clean_novel_text "${RAW_CLAUDE}" "${CLEAN_CLAUDE}"
clean_novel_text "${RAW_AGY}" "${CLEAN_AGY}"

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

if [[ ${CLAUDE_EXIT} -ne 0 ]] || [[ ${AGY_EXIT} -ne 0 ]]; then
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
