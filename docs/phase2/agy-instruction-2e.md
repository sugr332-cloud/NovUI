# AGYへの作業指示：Phase 2-E ノベルゲームの台本形式（2-E1〜2-E5）

**この指示書は 2-E1〜2-E5 の全体を定める。実際の作業は、Human が段階ごとに「○○を実施せよ」と指示したときに、その段階だけを行う。指示された段階以外のコードを先取りして書いてはならない。**
**フラグに結び付いた設定（2-F）は範囲外。**

## あなたの役割

2-D の指示書（agy-instruction-2d.md）と同じ。設計の判断はしない。本指示・`docs/phase2/phase2e-design.md`・`docs/script-format.md`・仕様書が矛盾する場合、決まっていない判断が必要になった場合、結合試験で想定と違う結果が出た場合は、止めて報告する。本指示と異なる実装をした場合は、報告の「指示どおりにできなかった点」に書く。

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/script-format.md`（**台本の形式の正本。解析器はこの文書のとおりに作る**）
* `docs/phase2/phase2e-design.md`（設計。特に §1〜§5、§8・§9）
* `docs/phase2/phase2d-design.md`（規則表・伏線の検査）
* `docs/novel-system-spec-v0.5.2.md` の §6.4、§6.5、§11.3、§12、§15
* `src/novui/` の `mechanical.py`、`charrules.py`、`positions.py`、`foreshadowcheck.py`、`draftjob.py`、`validatejob.py`、`planjob.py`、`stateupdate.py`、`statecheck.py`、`semantics.py`、`protected.py`、`checks.py`、`schema.py`

## 書込みの許可範囲

```text
共通で許可：
  tests/**（追加と、既存テストの必要最小限の修正。既存テストの期待値を変えて通してはならない）
  .venv/**、~/.local/share/novui-itest/**

2-E1：
  schemas/project.schema.json、character.schema.json、plan.schema.json、common.schema.json（下の欄の追加だけ）
  schemas/ の新規：flags.schema.json、backgrounds.schema.json、script_rules.schema.json
  src/novui/semantics.py（check_character・check_plan への追加、新しい schema の検査の登録）
  src/novui/protected.py（PROTECTED_PATHS に "flags"・"assets" を足すだけ）
  src/novui/ の新規：script.py、textformat.py

2-E2：
  src/novui/ の新規：scriptcheck.py
  src/novui/draftjob.py（台本の形式の作品の分岐だけ）
  src/novui/requestflow.py（redraft での同じ分岐だけ）

2-E3：
  src/novui/charrules.py（規則表に display_name・expressions を足すだけ）
  src/novui/validatejob.py（台本の形式の作品の分岐だけ）
  src/novui/planjob.py（台本の形式の作品のプロンプトの差し込みだけ）

2-E4：
  src/novui/scriptcheck.py（分岐とフラグの検査の追加）
  src/novui/statecheck.py・stateupdate.py（分岐の中の場面の変化の WARNING だけ）

2-E5：
  tests/integration/test_script_integration.py（新規）

禁止：上記以外すべて（docs/、prompts/、既存の schemas の上記以外の変更、jobrunner.py、mechanical.py など）。
```

小説の形式（`text_format` が無いか `novel`）の作品の動作は**一切変えない**。既存のテストは、期待値を変えずにすべて通ること。

## プロンプト（Claude が書く。AGY は変更しない）

各段階の開始前に、Claude（設計担当）が次を commit する。AGY は、それが phase2/chapter-flow にあることを確かめてから始める（なければ止めて報告）。

| 段階 | ファイル | 差し込み |
|---|---|---|
| 2-E2 | `prompts/agy/draft_script.md`（新規。台本の形式の draft の指示） | `{{chapter_id}}`・`{{scene_ids}}`・`{{scene_count}}`・`{{scene_flow}}`・`{{min_chars}}`・`{{max_chars}}`・`{{character_rules}}`・`{{extras}}` |
| 2-E3 | `prompts/claude/script_notes.md`（新規。台本の読み方） | なし（そのまま差し込む文） |
| 2-E3 | `prompts/claude/integrity_review.md`・`writing_review.md`・`plan.md` | `{{format_notes}}`（小説の形式では空文字） |

## 共通の規則

* 2-D と同じ（純粋な関数、引数を変更しない、WARNING の details は英語で ID と位置を含める、重複を除き初出の順）。
* 行番号は 1 始まりで、draft.md のファイル全体の行番号（場面の中の番号ではない）。
* 台本の検査の結果は、draft Job では `script_syntax`・`script_flow`・`script_flags` だけを FAIL にし、他は WARNING。validate ではすべて PASS か WARNING（§11.3）。

---

# 2-E1：解析器と schema

## 作業 1：schema

* `project.schema.json`：任意の `text_format`（`enum: [novel, script]`）。
* `character.schema.json`：任意の `display_name`（`pattern: ^[^\s@<\-［「（][^\s［「（]*$`）、任意の `expressions`（文字列の配列、`minItems: 1`、`uniqueItems: true`、各要素 `pattern: ^\S+$` で `］` を含まない）。
* `plan.schema.json` の scene：任意の `background`（`^\S+$`）、`extras`（表示名と同じ pattern の配列、uniqueItems）、`choice`（配列、`minItems: 2`、要素は `{text: nonempty_string, goto: scene_id, flag?: flag_name}`、additionalProperties false）、`next`（scene_id）。`flag_name` は `^[a-z][a-z0-9_]{0,63}$`（common.schema.json に `flag_name` として足してよい。common の変更はこの追加だけ）。
* 新規 `flags.schema.json`：配列。要素 `{name: flag_name, description: nonempty_string, notes: string}`、すべて必須。
* 新規 `backgrounds.schema.json`：`^\S+$` の文字列の配列、uniqueItems。
* 新規 `script_rules.schema.json`：`{max_line_chars: integer >= 1}`（任意の欄。additionalProperties false）。`rules/script.yaml` の schema。
* semantics：
  - `check_character`：`expressions` の要素の重複（schema でも見るが、明示的に）。
  - `check_plan`：`choice` と `next` を同じ場面に書かない。choice の goto の重複なし。choice・next の行き先が plan にあり、自分より後ろ（plan の並びの添字が大きい）。最後の場面に `next` を書かない。すべての場面が先頭から到達できる（場面のつながりは script-format §3）。extras の名前が同じ場面の重複なし。
  - `check_flags`：name の重複なし。`SEMANTIC_CHECKS` に `flags`・`backgrounds`（重複は schema が見るので no-op でよい）・`script_rules` を登録。
* `protected.py`：PROTECTED_PATHS に `"flags"` と `"assets"` を足す。

## 作業 2：src/novui/textformat.py

```python
TEXT_FORMATS = ("novel", "script")
def read_text_format(root: Path) -> str
```

* `project.yaml` を読み、`text_format` を返す（無ければ `"novel"`）。project.yaml が無い・不正なら ChapterError。

## 作業 3：src/novui/script.py

```python
@dataclass(frozen=True)
class ScriptLine:
    lineno: int
    kind: str            # scene, bg, enter, exit, face, choice, option, goto, if, else, endif, say, think, narration
    args: Mapping[str, Any]   # 種類ごとの値（下の表）
    raw: str

@dataclass(frozen=True)
class ScriptError:
    lineno: int
    message: str

@dataclass(frozen=True)
class SceneBlock:
    scene_id: str
    lineno: int
    lines: tuple[ScriptLine, ...]   # 区切りの行を含まない。空行を含まない

@dataclass(frozen=True)
class ParsedScript:
    preamble: tuple[ScriptLine, ...]   # 最初の区切りより前の行（あれば誤り）
    scenes: tuple[SceneBlock, ...]
    errors: tuple[ScriptError, ...]

def parse_line(raw: str, lineno: int) -> ScriptLine | ScriptError
def parse_script(text: str) -> ParsedScript
def body_text(parsed: ParsedScript) -> str
```

* `parse_line`：script-format §2 の表の正規表現を**上から順に**当てる（台詞は心の声より先）。行の前後の空白は取り除いてから判定する。args：
  - scene `{scene_id}`、bg `{name}`、enter `{character_id, position, expression|None}`、exit `{character_id}`、face `{character_id, expression}`、choice `{}`、option `{text, goto, flag|None}`、goto `{scene_id}`、if `{flag, negated}`、else・endif `{}`、say・think `{speaker, expression|None, text}`、narration `{text}`
  - `@`・`<!--` で始まりどれにも当たらない行は ScriptError（メッセージに行の頭の語）。
* `parse_script`：行ごとに `parse_line`。空行は飛ばす。構造の誤りも errors に入れる：
  - 最初の区切りより前に空行以外の行がある
  - option の行が `@choice` の直後の連続した行でない、`@choice` の後の option が2つ未満
  - `@choice`（とその option）・`@goto` の後に、同じ場面の行がある（場面の最後でない）
  - `@if` の入れ子、`@else`・`@endif` の対応の誤り、場面の終わりで閉じていない `@if`、`@if` の中に say・think・narration・face 以外の行
  - 同じ場面 ID の区切りが2回（場面の順序と plan との一致は scriptcheck が見る）
  - 誤りがあっても解析を続け、解析できた行は scenes に入れる。
* `body_text`：say・think・narration の text を改行でつないだもの（文字数を数えるため）。

## 作業 4：テスト（2-E1）

* `tests/test_script.py`：script-format §2 の各行の種類（正例と、`@bg` の引数なし・`@enter` の位置の誤り・`@if` のフラグ名の誤り・全角でない括弧など負例）、§5 の例を解析して scenes・行の種類の並びが期待どおり、各構造の誤り、表示名の規則（`看板には「準備中」` は say になる、`看板には「準備中」とあった。` は narration）、`body_text`。
* `tests/test_schema.py`・`test_semantics.py`：新しい欄・schema の正例と負例、`check_plan` の各条件（後ろへの行き先、到達できない場面、choice と next の両方、最後の場面の next）。
* `tests/test_protected.py`：`flags/registry.yaml`・`assets/backgrounds.yaml` が保護対象。
* `tests/test_textformat.py`：既定 novel、script、不正な値。

コミットメッセージ：`phase2: 2-E1 script parser and schemas for the script format`

---

# 2-E2：台本の機械検査と draft

## 作業 5：src/novui/scriptcheck.py

```python
@dataclass(frozen=True)
class ScriptInputs:
    characters: Mapping[str, Mapping[str, Any]]      # mechanical.MechanicalInputs.characters
    backgrounds: frozenset[str] | None                # assets/backgrounds.yaml（無ければ None）
    flags: frozenset[str] | None                      # flags/registry.yaml（無ければ None）
    max_line_chars: int | None                        # rules/script.yaml
    load_errors: tuple[str, ...]

def load_script_inputs(root: Path, characters: Mapping[str, Mapping[str, Any]]) -> ScriptInputs
def display_names(characters: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, str], list[str]]
def check_script_syntax(parsed: ParsedScript) -> CheckResult
def check_script_flow(parsed: ParsedScript, plan: Mapping[str, Any]) -> CheckResult
def check_script_speakers(parsed: ParsedScript, plan: Mapping[str, Any], inputs: ScriptInputs) -> CheckResult
def check_script_expressions(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult
def check_script_stage(parsed: ParsedScript, plan: Mapping[str, Any], inputs: ScriptInputs) -> CheckResult
def check_script_backgrounds(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult
def check_script_speaker_words(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult
def check_script_line_length(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult
def check_script_suspicious_lines(parsed: ParsedScript) -> CheckResult
def run_script_checks(text: str, plan: Mapping[str, Any], inputs: ScriptInputs, *, for_draft: bool) -> list[CheckResult]
def script_char_count(text: str, min_chars: int, max_chars: int) -> CheckResult
```

* `display_names`：人物 ID → display_name の dict の逆（表示名 → 人物 ID）と、表示名の重複の警告を返す。
* 各検査（name は関数名の `check_` を除いたもの。`script_syntax` など）：
  - `script_syntax`：parsed.errors を `f"line {lineno}: {message}"` で列挙。
  - `script_flow`：場面の ID・順序が plan と同じで各1回（既存の scene_markers と同じ規則）。各場面の最後が plan のつながりと一致（choice があれば `@choice` と option が同じ text・goto・flag を同じ順で、next があれば `@goto <next>`、どちらも無ければ choice・goto が無い）。
  - `script_speakers`：say・think の speaker が、その場面の plan の characters の表示名か extras にある。plan の characters の人物で display_name が無い人物も列挙。extras の名前が人物の表示名と同じなら警告。
  - `script_expressions`：enter・face・say・think の表情が、その人物の expressions にある（expressions が無い人物は飛ばす。端役の表情は警告）。
  - `script_stage`（決定 4）：場面ごとに立ち絵の状態を先頭から追う（場面の始まりは空。script-format §3）。`@if`・`@else` の中は say・think・narration・face だけなので、状態を分けずに追ってよい。警告：場面に `@bg` がない、または最初の `@bg` より前に say・think・narration がある／登場していない人物の say・think（`画面外の声`）／`@exit` した・登場していない人物の `@exit`・`@face`／登場中の人物の `@enter`／同じ位置への2人目の `@enter`／`@enter`・`@exit`・`@face` の人物が plan の場面の characters にない。
  - `script_backgrounds`（決定 3）：backgrounds が None なら PASS。`@bg` の名前が一覧にない。plan の場面の background と、その場面の最初の `@bg` が違う。
  - `script_speaker_words`：話者の台詞（say の text）に、その人物の speech.forbidden の語がある。
  - `script_line_length`（§9）：max_line_chars が None なら PASS。say・think・narration の text の `count_chars` が上限を超える行。
  - `script_suspicious_lines`：narration のうち、最初の `「` より前が10字以下で `。`・`、` を含まず、行が `」` で終わるもの（地の文として読まれた台詞の書き損じの可能性）。
* `run_script_checks`：syntax・flow・speakers・expressions・stage・backgrounds・speaker_words・line_length・suspicious_lines の順（2-E4 で flags を flow の後に足す）。`for_draft` が True のとき、`script_syntax`・`script_flow` の WARNING を FAIL に変えて返す（他は WARNING のまま）。load_errors があれば先頭に `script_inputs`（WARNING）。
* `script_char_count`：`check_char_range(body_text(parse_script(text)), ...)`（name は `char_count` のまま）。

## 作業 6：draftjob.py・requestflow.py

* `run_chapter_draft_job`：`read_text_format(repo)` が `script` のとき：
  - plan の各場面の characters の人物に display_name が無ければ ChapterError（draft の前に止める。Job を作らない）。
  - instruction は `build_script_draft_instruction(chapter_id, plan, *, character_rules: str) -> str`（`draft_script.md`）。`{{scene_flow}}` は場面ごとに1行：`S1 → 選択肢：一緒に市場を回る → S2（フラグ market_together）／船を見に行く → S3`、`S2 → S4`、`S3 → S4`（next か次の場面）、最後の場面は `S4 → 章の終わり`。`{{extras}}` は `S1：門番、店主` のように場面ごとに1行（無ければ `なし`）。
  - DraftJobRequest の `target_chars` は None にし、`extra_checks` を `lambda text: [script_char_count(text, min, max)] + run_draft_mechanical_checks(text, plan, inputs) + run_script_checks(text, plan, script_inputs, for_draft=True)` にする。
  - novel のときは何も変えない。
* `requestflow.redraft`：同じ分岐（instruction の作り方と extra_checks）。共通の部分は draftjob に関数として置き、requestflow から呼ぶ。

## 作業 7：テスト（2-E2）

* `tests/test_scriptcheck.py`：各検査の正例と負例（script-format §5 の例は、適切な plan・characters・一覧のもとで全 PASS になること）、`for_draft` の FAIL 化、`script_char_count` が `@` の行と表示名を数えないこと。
* `tests/test_draftjob.py`：script の作品で instruction が draft_script.md から作られ、scene_flow・extras・規則表が入る、解析の誤りのある出力で Job が FAILED、display_name の無い人物で ChapterError、novel の作品で今までと同じ instruction。

コミットメッセージ：`phase2: 2-E2 script checks and script drafts`

---

# 2-E3：validate と plan

## 作業 8

* `charrules.character_table`：doc にあれば `display_name`（`name` の直後）と `expressions`（`speech` の直前）を出す。順序：character, name, display_name, expressions, speech, …。
* `validatejob.run_validate_job`：script の作品では、char_count を `script_char_count` にし、mechanical に `run_script_checks(..., for_draft=False)` を `character_rules` の前に足す。integrity_review・writing_review に `format_notes=load_prompt_template("script_notes")` を渡す（novel では `format_notes=""`）。
* `planjob`：plan のプロンプトに `format_notes`（script では script_notes、novel では空文字）を渡す。script の作品の Context に `assets/backgrounds.yaml`・`flags/registry.yaml`（あれば）を足す。

## 作業 9：テスト（2-E3）

* 規則表の display_name・expressions、validate の mechanical の並び（script）、プロンプトに script_notes が入ること（script）・入らないこと（novel）、plan のプロンプトと Context。

コミットメッセージ：`phase2: 2-E3 script format in validate and plan`

---

# 2-E4：フラグと分岐の中の変化

## 作業 10：scriptcheck.py の追加

```python
def flags_set_before(order: Sequence[str], chapter_id: str, plans: Mapping[str, Mapping[str, Any]],
                     scene_id: str) -> frozenset[str]
def check_script_flags(parsed: ParsedScript, plan: Mapping[str, Any], inputs: ScriptInputs, *,
                       order: Sequence[str], chapter_id: str,
                       earlier_plans: Mapping[str, Mapping[str, Any]]) -> CheckResult
def branch_only_scenes(plan: Mapping[str, Any]) -> frozenset[str]
```

* `flags_set_before`：前の章の plan（`earlier_plans`：chapters-order でこの章より前の章の plan.yaml。読めない章は飛ばす）のすべての choice の flag と、この章の plan で `scene_id` より前の場面の choice の flag。
* `check_script_flags`（name `script_flags`。draft では FAIL）：option の flag・`@if` の flag が flags の一覧にある（一覧が None なら「flags/registry.yaml がない」1件）。option の flag が plan の choice の flag と一致（flow が見るので重ねない）。`@if` の flag が、その場面より前で立ちうる（`flags_set_before`）。
* `run_script_checks` に `order`・`chapter_id`・`earlier_plans` のキーワード引数を足し、flow の後に flags を入れる。draftjob・validatejob の呼び出しを合わせる（この変更は 2-E4 で許可）。
* `branch_only_scenes`：先頭から章の終わりまでのすべての道が通る場面以外の場面の集合（場面のつながりは script-format §3）。

## 作業 11：statecheck.py・stateupdate.py（2-F までの扱い。design §5.3）

* `statecheck.check_branch_scene_changes(patch, *, branch_scenes: Collection[str]) -> CheckResult`（name `branch_scene_change`、WARNING）：patch の operations の value のうち、位置を持つもの（changes の `from`、registry の introduced・hints・developments・resolved）で、その場面が branch_scenes にあるものを列挙する。knowledge の追加は位置を持たない（source_chapter だけ）ので対象にしない。details に「2-F まではフラグごとの設定を扱えない」旨の英文を1行目に置く。
* `stateupdate.run_state_update`：script の作品で、各 Patch の dry_run が PASS したあとに `check_branch_scene_changes` を add_check（WARNING でも提案は作る）。

## 作業 12：テスト（2-E4）

* flags_set_before（前の章・同じ章の前の場面・後ろの場面のフラグは含まない）、check_script_flags の各条件、branch_only_scenes（分岐なし・分かれて合流・選択肢の片方だけが通る場面）、branch_scene_change の WARNING と提案の作成。

コミットメッセージ：`phase2: 2-E4 flags and branch scene checks`

---

# 2-E5：結合試験

## 作業 13：tests/integration/test_script_integration.py

* 2-D4 と同じく、本文は偽の AGY が fixture の台本を返し、validate は実際の Claude。script-format §5 の例を基に、作品（text_format: script、characters に display_name・expressions、assets/backgrounds.yaml、flags/registry.yaml、rules/script.yaml）を作る。
* E1：§5 の例そのもの → draft COMPLETED、mechanical の script_* がすべて PASS、Claude の integrity_review が得られる（判定は固定しない。print）。
* E2：解析の誤り（`@enter C001 top`）→ draft FAILED（script_syntax）。Claude を呼ばない。
* E3：未登録の表情・画面外の声・長すぎる台詞を仕込む → validate の mechanical に該当の WARNING。
* E4：台本の形式で、2-D の D1（一人称の違反）と同じ仕込み → character の finding（`speech.first_person`、期待「俺」、本文「僕」）。
* E5：実際の AGY で台本の形式の draft を1回（`NOVUI_AGY_MODEL`）。結果の draft の state と script_* の結果を print する。**AGY が形式を守れなくても試験は失敗にせず**（pytest.skip）、出力を print して報告する（AGY が台本の形式を書けるかの確認のため）。
* 期待と違う結果は、直さずに止めて報告する。

コミットメッセージ：`phase2: 2-E5 script format integration tests`

---

## 各段階の手順と報告

2-D の指示書と同じ（作業の説明 → Human の許可 → 実装 → `.venv/bin/python -m pytest -q` 全件 PASS → 許可範囲だけ commit → `git push origin phase2/chapter-flow` → 報告して止まる）。報告の内容も 2-D と同じ。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
