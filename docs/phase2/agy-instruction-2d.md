# AGYへの作業指示：Phase 2-D キャラクター一貫性・伏線の検査（2-D1〜2-D4）

**この指示書は 2-D1〜2-D4 の全体を定める。実際の作業は、Human が段階ごと（2-D1、2-D2、2-D3、2-D4）に「○○を実施せよ」と指示したときに、その段階だけを行う。指示された段階以外のコードを先取りして書いてはならない。**
**2-D5 以降（range_edit、chapter_rewrite、plan_revision、instruction_routing、Job のキュー）はこの指示書の範囲外。**

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストと（2-D4 では）結合試験を実行し、phase2/chapter-flow branch に commit・push します。
設計の判断はしません。本指示・`docs/phase2/phase2d-design.md`・仕様書が矛盾する場合、または決まっていない判断が必要になった場合は、作業を止めて報告してください。
結合試験で想定と違う結果が出た場合は、コードや試験やプロンプトを変えて辻褄を合わせず、その時点で止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase2/phase2d-design.md`（**設計の正本。特に §2〜§6 と §8・§9 の決定**）
* `docs/phase2/phase2-plan.md`（§3 の 2-D、§4）
* `docs/novel-system-spec-v0.5.2.md` の §6.4、§6.5.1、§11.1、§11.3、§11.4、§11.7、§11.8、§12、§16
* `prompts/claude/integrity_review.md`、`prompts/agy/draft.md`（読み込んで使う。**変更しない**。2-D2・2-D3 の開始前に Claude（設計担当）が更新する。下の「プロンプトの差し込み」を参照）
* `schemas/character.schema.json`、`schemas/registry.schema.json`、`schemas/common.schema.json`、`schemas/integrity_review.schema.json`、`schemas/review.schema.json`
* `src/novui/` の次のファイル：`mechanical.py`、`validatejob.py`、`draftjob.py`、`requestflow.py`、`stateupdate.py`、`statecheck.py`、`statepatch.py`、`semantics.py`、`chapters.py`（`read_chapters_order`）、`checks.py`（`CheckResult`）、`yamlio.py`（`dumps_yaml`）、`prompt.py`（`load_prompt_template`）
* 2-C までに作成した `tests/`（特に `tests/test_validatejob.py`、`tests/test_draftjob.py` の `FakeRunner`、`tests/integration/test_validatejob_integration.py`）

## 書込みの許可範囲

段階ごとに、許可されるファイルが違う。

```text
共通で許可：
  tests/**（追加と、既存テストの必要最小限の修正。既存テストの期待値を変えて通してはならない。
            2-D2 の schema の変更に合わせて finding に `character: null` を足す修正、
            build_draft_instruction の引数の追加に合わせる修正は「必要最小限の修正」に当たる）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。終了時に削除する）

2-D1：
  src/novui/ の新規ファイル：positions.py、charrules.py

2-D2：
  schemas/integrity_review.schema.json（finding に character の欄を足すだけ）
  src/novui/semantics.py（check_integrity_review への検査の追加だけ）
  src/novui/validatejob.py（規則表の差し込みと mechanical・checks への追加だけ）
  src/novui/draftjob.py（build_draft_instruction の引数の追加、規則表の文字列を作る関数の追加、
                         run_chapter_draft_job での呼び出しだけ）
  src/novui/requestflow.py（redraft での build_draft_instruction の呼び出しの変更だけ）

2-D3：
  src/novui/ の新規ファイル：foreshadowcheck.py
  src/novui/validatejob.py（伏線の機械検査と状況表の差し込みだけ）
  src/novui/stateupdate.py（registry の Patch のあとの foreshadow_order の検査の追加だけ）

2-D4：
  tests/integration/test_consistency_integration.py（新規）

禁止：上記以外すべて。次を含む。
  docs/、prompts/、spike/、tools/、container/、templates/、.git/、.gitignore、pyproject.toml（依存を追加しない）
  上記以外の schemas/*.json
  src/novui の上記以外の既存ファイル。特に mechanical.py、states.py、workrepo.py、protected.py、checks.py、
  jobrunner.py、claudejob.py、claude_output.py、prompt.py、planjob.py、chapters.py、statecheck.py、
  statepatch.py、finalize.py、proposals.py、approvals.py
```

既存の状態遷移、draft・validate・state_update の流れ、review.yaml の構造（2-D2 の finding の欄を除く）、commit guard、worktree・merge の処理は**変更しない**。変更が必要だと思ったら、作業を止めて報告してください。

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
ls -la ~/.local/share/novui-itest
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase2/chapter-flow
```

Phase 1・2-A・2-B・2-C の禁止事項はすべて引き続き有効です（ログイン、OAuth の更新、アカウントの切替、元アカウントの token への接触を含む）。

## 背景（設計の要点。詳細は phase2d-design.md）

1. 「その場面の位置で有効な値」（changes・exceptions を適用した一人称・呼び方など）は **Controller が計算**し、場面ごとの**規則表**としてプロンプトに入れる。Claude と AGY は表を正として使い、changes の計算をし直さない。
2. 伏線の位置の検査（introduced・hints・developments・resolved の順序、回収予定を過ぎた major）は **Controller の機械検査**。結果は WARNING だけで、STOP にしない（§11.3）。
3. 規則表・状況表を作るときに見つかった設定の誤り（比較できない位置など）は、例外にせず、review.yaml の mechanical に WARNING として出す。
4. 2-D の検査は**検出だけ**を行う。characters/・registry.yaml を書き換えない。

## 共通の規則

* positions.py・charrules.py・foreshadowcheck.py の関数は純粋な関数にする（ファイルを読む `load_registry` を除く）。引数を変更しない。
* 位置は `{chapter, scene}` の dict（`common.position`）。章の順序は chapters-order.yaml の list（`chapters.read_chapters_order` の戻り値）を `order` として引数で受け取る。
* WARNING の details の文は英語で、関係する ID と位置を含める（既存の mechanical.py に合わせる。例：`C001 address.C002 change from ch-009 S1: chapter not in chapters-order.yaml`）。同じ文は1回だけ出す（初出の順を保つ）。
* 位置の文字列表記は `format_position` の `ch-018 S004`（元の場面 ID の表記のまま）。
* 表の YAML は `yamlio.dumps_yaml`（キーの順を保つ）で作り、末尾の改行を取り除く。
* hash は `sha256:<16進64桁（小文字）>`（`statepatch.sha256_bytes` を使ってよい）。

## プロンプトの差し込み（Claude が書く。AGY は変更しない）

* **2-D2 の開始前**に、Claude（設計担当）が次を変更して commit する。AGY は、その commit が phase2/chapter-flow にあることを確かめてから 2-D2 を始める（なければ止めて報告）。
  - `prompts/claude/integrity_review.md`：`{{character_rules}}`（場面ごとの人物の規則表）を差し込む。character の観点を「表の値を正とする」に、finding の書き方に `character` の欄を足す。規則表に無い人物（端役など）の台詞は character で指摘しないこと、表の人物が address に無い相手を呼ぶときは `address.default` で照合することを書く（design §4）。`type：\`integrity_review\`` の行は残す（既存のテストが種類の判定に使う）。
  - `prompts/agy/draft.md`：`{{character_rules}}` を差し込む。
* **2-D3 の開始前**に、Claude が `prompts/claude/integrity_review.md` に `{{foreshadow_status}}`（伏線の状況表）を差し込んで commit する。AGY は同じく確かめてから 2-D3 を始める。
* 差し込む値は、表が空のとき `なし`（render 関数が返す）。

---

# 2-D1：位置の順序と、有効な値の計算

純粋な関数と単体テストだけを作る。**Claude・AGY・podman・git を使わない。既存のファイルを変更しない。**

## 作業 1：src/novui/positions.py

```python
class PositionError(ValueError)

def scene_number(scene_id: str) -> int
def chapter_index(order: Sequence[str], chapter_id: str) -> int
def position_key(order: Sequence[str], pos: Mapping[str, Any]) -> tuple[int, int]
def compare_positions(order: Sequence[str], a: Mapping[str, Any], b: Mapping[str, Any]) -> int
def format_position(pos: Mapping[str, Any]) -> str
```

* `scene_number`：`^S[0-9]+$` に一致すれば数字部分を int で返す（`S003` も `S3` も 3）。一致しなければ PositionError。
* `chapter_index`：`order` の中の添字。なければ PositionError（メッセージに章 ID）。
* `position_key`：`(chapter_index(order, pos["chapter"]), scene_number(pos["scene"]))`。`pos` が dict でない、`chapter`・`scene` がない場合も PositionError。
* `compare_positions`：a ＜ b なら -1、等しければ 0、a ＞ b なら 1。比較できなければ PositionError（どちらの位置かが分かるメッセージ）。
* `format_position`：`f"{pos['chapter']} {pos['scene']}"`。

## 作業 2：src/novui/charrules.py

```python
FIXED_RULE_NAMES: tuple[str, ...] = (
    "speech.first_person", "speech.formality", "speech.endings", "speech.habits", "speech.forbidden",
    "address.default", "personality", "behavior",
)

@dataclass(frozen=True)
class RuleTables:
    scenes: tuple[dict[str, Any], ...]   # [{"scene": "S1", "characters": [表, ...]}, ...]（plan の場面の順）
    warnings: tuple[str, ...]

def is_known_rule(rule: str) -> bool
def effective_value(order: Sequence[str], default: str, changes: Sequence[Mapping[str, Any]],
                    pos: Mapping[str, Any], *, label: str) -> tuple[str, list[str]]
def character_table(order: Sequence[str], doc: Mapping[str, Any],
                    pos: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]
def scene_rule_tables(order: Sequence[str], chapter_id: str, plan: Mapping[str, Any],
                      characters: Mapping[str, Mapping[str, Any]]) -> RuleTables
def render_rule_tables(tables: RuleTables) -> str
def check_character_rules(tables: RuleTables) -> CheckResult
```

* `is_known_rule`：`FIXED_RULE_NAMES` のいずれか、または `^address\.C[0-9]{3,}$`・`^relationships\.C[0-9]{3,}$` に一致すれば True。
* `effective_value`（design §3）：`changes` のうち `from` が `pos` 以前（`compare_positions(order, from, pos) <= 0`）のものの中で、`from` が最も後の change の `value`。なければ `default`。
  - `from` を比較できない change は使わず、warnings に `f"{label} change from {format_position(from)}: {理由}"` を足す。
  - 採用候補の中で最も後の `from` が同じ位置の change が2つ以上あれば、リストの**後の方**を採り、warnings に `f"{label} has multiple changes from {format_position(from)}"` を足す（この警告は、その位置が採用される場合に限らず、changes の中に同じ位置が2つ以上あれば出す）。
  - changes のリストの並び順に依存しない（位置で比べる）。
* `character_table`：1人の人物の、1つの位置での規則表（design §3 の表）。キーは次の順で、**元の doc にある欄だけ**を出す（`suspended_rules` は常に出す）。
  1. `character`（doc の id）、`name`
  2. `speech`：doc の `speech` をそのまま（キーの順も元のまま）
  3. `address`：doc に `address` があるとき。doc の address の**すべての**キー（`default` を含む。元の順。同じ場面にいない相手も絞らない。design §3）。値は、文字列ならそのまま、object なら `effective_value(..., label=f"{id} address.{相手}")`。結果が空の dict なら `address` を出さない。
  4. `relationships`：doc にあるとき。各要素を `{with, state}` にし、`state` は `effective_value(order, r["state"], r.get("changes", []), pos, label=f"{id} relationships.{with}")`。
  5. `suspended_rules`：doc の `exceptions` のうち `from <= pos <= to` のものを `{rule, reason}` にして元の順に並べる。`from`・`to` を比較できない例外、`from` が `to` より後の例外は使わず、warnings に足す（`f"{id} exceptions[{i}] ..."`）。`is_known_rule(rule)` が False の例外は warnings に `f"{id} exceptions[{i}] unknown rule {rule!r}"` を足したうえで、範囲に入れば表に載せる。
  6. `knowledge`：doc にあるとき。`source_chapter` の章の添字が `pos["chapter"]` の章の添字より**小さい**ものだけを `{id, fact, source_chapter}` で元の順に。`source_chapter` が order にないものは除き、warnings に足す。
  7. `personality`、`behavior`：doc にあればそのまま。
  - exceptions の検査（`from <= to` など）は位置ごとに同じ結果になるが、warnings の重複は呼び出し側（`scene_rule_tables`）で取り除く。
* `scene_rule_tables`：plan の各場面について `pos = {chapter: chapter_id, scene: 場面 ID}` とし、場面の `characters` の各人物（plan の順）のうち `characters` にある人物の表を作る（ない人物は飛ばす。存在しない ID は既存の `ref_ids` が報告する）。場面の位置自体を比較できない（chapter_id が order にない）場合は、その章のすべての場面を飛ばし、warnings に `f"chapter {chapter_id} not in chapters-order.yaml"` を1回だけ足す。人物が1人もいない場面は `scenes` に入れない。warnings は重複を除き初出の順。
* `render_rule_tables`：`scenes` が空なら `"なし"`。そうでなければ `dumps_yaml(list(tables.scenes))` の末尾の改行を除いたもの。
* `check_character_rules`：name `character_rules`。warnings があれば WARNING（details は warnings）、なければ PASS。

規則表の例（`C001` の `ch-018 S004`、相手に C002・C003 がいる場面）：

```yaml
- scene: S004
  characters:
  - character: C001
    name: 山田太郎
    speech: {first_person: 俺, formality: casual, endings: [だ, だろ], habits: [], forbidden: [僕]}
    address: {default: お前, C002: 君, C003: 先生}
    relationships: [{with: C002, state: 幼なじみ}]
    suspended_rules: []
    knowledge: [{id: K014, fact: 門番が紋章に反応したことを知っている, source_chapter: ch-004}]
    personality: [冷静, 弱みを見せない]
    behavior: [危険時に仲間を優先する]
```

## 作業 3：テスト（2-D1）

* `tests/test_positions.py`：`scene_number`（`S1`・`S003`・`S10`、不正な `s1`・`S`・`SX`）、`S2 < S10`、`S003 == S3`、章をまたぐ比較（前の章の `S99` ＜ 後の章の `S1`）、order にない章・不正な dict で PositionError、`format_position`。
* `tests/test_charrules.py`：
  - `effective_value`：changes なし、位置より前・同じ位置（含む）・後の change、複数の change の中で最も後のもの、リストの並び順に依存しないこと、同じ位置が2つ（後の方を採る・警告）、比較できない change（使わない・警告）
  - `character_table`：仕様 §6.4.1 の例の人物で `ch-018 S003`（C002 は「美咲」）と `ch-018 S004`（「君」）、address が文字列の相手、同じ場面にいない相手も出る、doc にない欄を出さない（speech だけの人物）、relationships の changes、exceptions の範囲の内・外・境界（`from` と `to` を含む）、`from > to`・不明な rule の警告、knowledge の絞り込み（この章・後の章のものを出さない、order にない章は警告）
  - `scene_rule_tables`：場面ごとの人物、characters にない人物を飛ばす、人物のいない場面を出さない、chapter_id が order にないときの警告が1回、warnings の重複除去
  - `render_rule_tables`：空なら `なし`、YAML として読み戻すと `scenes` と等しいこと
  - `check_character_rules`：PASS と WARNING

## 2-D1 の作業の手順

1. 本指示と参照文書を読み、これから作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る。**
3. 作業 1〜3 を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする（既存のテストが1件も変わっていないこと）。
4. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase2: 2-D1 position order and effective character rules
   ```
5. `git push origin phase2/chapter-flow`（「fetch first」等で拒否された場合に限り `git pull --rebase origin phase2/chapter-flow` を1回だけ実行してから再度 push。競合は解決せずに止めて報告）。
6. 報告して止まる。

---

# 2-D2：キャラクター一貫性の検査

2-D1 のコードを使う。Claude の呼び出しはすべて**偽の `claude_runner`**、AGY は**偽の `container_runner`**でテストする。
**開始前に、Claude によるプロンプトの更新（`{{character_rules}}`）が phase2/chapter-flow にあることを確かめる。**

## 作業 4：schemas/integrity_review.schema.json と semantics.py（決定 1）

* `$defs` に `character_finding` を足す：`type: object`、`additionalProperties: false`、properties は `character_id`（`common.character_id`）、`rule`（`common.nonempty_string`）、`expected`（`common.nonempty_string`）、`actual`（`common.nonempty_string`）、`scene`（`common.scene_id`）、すべて required。
* `finding` に `character` を足し、**required に加える**：`oneOf: [{$ref: "#/$defs/character_finding"}, {type: "null"}]`。
* `semantics.check_integrity_review` に追加：`checks` の `character` 以外の観点の finding で `character` が null でなければ、エラー `f"Check {name!r} finding {i} has character but only 'character' findings may"`。character の観点の finding は null も許す。
* テスト：`tests/test_schema.py`・`tests/test_semantics.py` に、character の欄の正常（null・object）、欄がない finding の拒否、object の欄の不足の拒否、character 以外の観点に object があるときの semantic error を足す。既存のテストデータの finding には `character: null` を足す。

## 作業 5：src/novui/draftjob.py と requestflow.py（決定 3）

```python
def build_character_rules_text(root: Path, chapter_id: str, plan: Mapping[str, Any],
                               inputs: MechanicalInputs) -> str
def build_draft_instruction(chapter_id: str, plan: dict[str, Any], *, character_rules: str) -> str
```

* `build_character_rules_text`：`render_rule_tables(scene_rule_tables(read_chapters_order(root), chapter_id, plan, inputs.characters))`。
* `build_draft_instruction`：既存の値に加えて `character_rules=character_rules` をテンプレートに渡す（キーワード専用・必須。既定値を持たせない）。
* `run_chapter_draft_job`：`inputs = load_mechanical_inputs(repo)` を instruction を作る前に移し、`build_draft_instruction(chapter_id, plan, character_rules=build_character_rules_text(repo, chapter_id, plan, inputs))`。他の処理の順序は変えない。
* `requestflow.redraft`：同じく `character_rules=build_character_rules_text(repo, chapter_id, plan, inputs)`（`inputs` の読み込みを instruction より前に移す）。decision の節を後ろに足す処理は変えない。
* draft Job は規則表の warnings を記録しない（validate が mechanical に出す）。

## 作業 6：src/novui/validatejob.py

`run_validate_job` の「2. 機械検査」と「3. Claude」を次のように変える。他は変えない。

* 機械検査：`inputs = load_mechanical_inputs(worktree)` を変数に取り、既存の検査のあとに、`order = read_chapters_order(worktree)`、`rule_tables = scene_rule_tables(order, chapter_id, plan, inputs.characters)`、`check_character_rules(rule_tables)` を mechanical に足す（`character_rules`。WARNING なら既存の規則どおり総合判定で WAITING_HUMAN になる）。
* プロンプト：`character_rules = render_rule_tables(rule_tables)`。`call(expected_type)` を `call(expected_type, **values)` にし、`load_prompt_template(expected_type, chapter_id=chapter_id, **values)` とする。integrity_review は `call("integrity_review", character_rules=character_rules)`、writing_review は今のまま。
* Job 記録：integrity_review を呼ぶ前に、checks に `{"name": "prompt_tables", "status": "PASS", "details": [f"character_rules {sha256_bytes(character_rules.encode('utf-8'))}"]}` を足す（design §9。表の内容を Job 記録から追えるようにする）。
* integrity_review の finding の `character` の内容を Controller は検査しない（plan の場面・人物との照合はしない）。

## 作業 7：テスト（2-D2）

* `tests/test_draftjob.py`：instruction に規則表が入ること（address の changes がある人物で、場面ごとの値が入る）、人物のいない plan で `なし`、既存の `build_draft_instruction` の呼び出しを新しい引数に合わせる。
* `tests/test_requestflow.py`：redraft の instruction に規則表と decision の節の両方が入ること（既存の期待値の式を新しい引数に合わせる）。
* `tests/test_validatejob.py`：
  - 偽の Claude が受け取った integrity_review のプロンプトに規則表（例：`C002: 君`）が入り、writing_review のプロンプトには入らないこと
  - checks の `prompt_tables` の hash がプロンプトに入れた表の hash と一致すること
  - 比較できない changes を持つ人物で mechanical の `character_rules` が WARNING になり、Job が WAITING_HUMAN になること
  - finding に character の object を持つ integrity_review の出力が受け付けられ、review.yaml にそのまま入ること。character 以外の観点に object があると semantic error として再試行されること
  - 既存の偽の出力の finding に `character: null` を足す

## 2-D2 の作業の手順

1. Claude のプロンプトの更新が phase2/chapter-flow にあることを確かめる（`git log -3 --oneline -- prompts`）。なければ止めて報告する。
2. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
3. **Human の許可を得る。**
4. 作業 4〜7 を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする。
5. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase2: 2-D2 character rule tables in draft and validate prompts, character field in findings
   ```
6. `git push origin phase2/chapter-flow`（2-D1 と同じ規則）。
7. 報告して止まる。

---

# 2-D3：伏線の検査

2-D1・2-D2 のコードを使う。**開始前に、Claude によるプロンプトの更新（`{{foreshadow_status}}`）が phase2/chapter-flow にあることを確かめる。**

## 作業 8：src/novui/foreshadowcheck.py

```python
def load_registry(root: Path) -> list[dict[str, Any]] | None
def check_foreshadow_positions(order: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> CheckResult
def foreshadow_order_issues(order: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> list[str]
def check_foreshadow_order(order: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> CheckResult
def check_foreshadow_overdue(order: Sequence[str], registry: Sequence[Mapping[str, Any]],
                             chapter_id: str) -> CheckResult
def check_foreshadow_plan_refs(registry: Sequence[Mapping[str, Any]], plan: Mapping[str, Any],
                               chapter_id: str) -> CheckResult
def run_foreshadow_checks(order: Sequence[str], registry: Sequence[Mapping[str, Any]] | None,
                          plan: Mapping[str, Any], chapter_id: str) -> list[CheckResult]
def foreshadow_status_rows(order: Sequence[str], registry: Sequence[Mapping[str, Any]] | None,
                           plan: Mapping[str, Any], chapter_id: str) -> list[dict[str, Any]]
def render_foreshadow_status(rows: Sequence[Mapping[str, Any]]) -> str
def check_new_order_issues(order: Sequence[str], before: Sequence[Mapping[str, Any]],
                           after: Sequence[Mapping[str, Any]]) -> CheckResult
```

すべての検査の結果は PASS か WARNING（FAIL にしない。§11.3）。

* `load_registry`：`<root>/foreshadowing/registry.yaml` を読み、`registry` schema と `check_registry` で検証して返す。ファイルがない、読めない、検証に失敗した場合は None（エラーは既存の `mechanical_inputs` が報告するので、ここでは報告しない）。
* `check_foreshadow_positions`（name `foreshadow_positions`）：各伏線の introduced・hints・developments・planned_resolution・resolved のすべての位置について `position_key` が成り立つこと。成り立たない位置ごとに `f"{id} {欄名}[{i}] {format_position(pos)}: {理由}"`（planned_resolution・resolved は `[i]` なし）。
* `foreshadow_order_issues`（design §5.1・§9）：各伏線について、比較できない位置は無視して次を調べ、問題の文を返す（registry の順、各伏線の中は下の順）。
  1. introduced が空（比較できる位置が1つもない場合を含む）で hints・developments・resolved のいずれかがある：`f"{id} has hints/developments/resolved but no introduced"`
  2. hints・developments の各位置が、introduced の**最も早い位置**より前：`f"{id} {欄名}[{i}] {位置} is before introduced {位置}"`
  3. resolved が introduced の最も早い位置より前：`f"{id} resolved {位置} is before introduced {位置}"`
  4. resolved がある場合、hints・developments の各位置が resolved より**後**：`f"{id} {欄名}[{i}] {位置} is after resolved {位置}"`
  - 同じ位置（等しい）は問題にしない。
* `check_foreshadow_order`（name `foreshadow_order`）：issues があれば WARNING。
* `check_foreshadow_overdue`（name `foreshadow_overdue`）：importance が `major`、status が `planned` か `active`、planned_resolution があり、その章の添字が `chapter_id` の章の添字より**小さい**もの（design §5.1。この章の中の回収予定はまだ過ぎていない）：`f"{id} planned_resolution {位置} has passed but not resolved"`。比較できない場合は飛ばす（positions が報告する）。
* `check_foreshadow_plan_refs`（name `foreshadow_plan_refs`）：plan の各場面の `foreshadowing` の ID のうち、registry で status が `cancelled` のもの、または status が `resolved` で resolved の章が `chapter_id` でないもの：`f"{場面 ID} refers to {id} which is {status}"`。registry にない ID は飛ばす（ref_ids が報告する）。
* `run_foreshadow_checks`：registry が None なら `[]`。そうでなければ positions・order・overdue・plan_refs の4つをこの順で返す。
* `foreshadow_status_rows`（design §5.2）：registry が None なら `[]`。対象は、この章の plan が参照する伏線（plan の初出の順）と、それ以外の major で status が planned・active のもの（registry の順）。各行のキーは次の順：`id`、`name`、`status`、`importance`、`introduced`（全部）、`last_hint`（hints の最も後の位置。比較できる位置がなければ null）、`last_development`（同じ）、`planned_resolution`、`resolved`、`planned_resolution_in_this_chapter`（planned_resolution の章が `chapter_id` なら true）、`scenes_in_this_chapter`（この章の plan でこの伏線を参照する場面 ID の list。なければ []）。
* `render_foreshadow_status`：rows が空なら `"なし"`。そうでなければ `dumps_yaml(list(rows))` の末尾の改行を除いたもの。
* `check_new_order_issues`（name `foreshadow_order`、決定 4）：`foreshadow_order_issues(order, after)` のうち `foreshadow_order_issues(order, before)` にない文があれば WARNING（details はその文）。なければ PASS。

## 作業 9：src/novui/validatejob.py

* 機械検査：2-D2 の `character_rules` のあとに、`registry = load_registry(worktree)` と `run_foreshadow_checks(order, registry, plan, chapter_id)` を mechanical に足す。
* プロンプト：`foreshadow_status = render_foreshadow_status(foreshadow_status_rows(order, registry, plan, chapter_id))` を `call("integrity_review", character_rules=..., foreshadow_status=foreshadow_status)` に渡す。
* `prompt_tables` の details に `f"foreshadow_status {hash}"` を2行目として足す。

## 作業 10：src/novui/stateupdate.py（決定 4）

* target が `foreshadowing/registry.yaml` の Patch で、`dry_run_patch` が PASS したあと（`is_noop_patch` の判定の前）に、`after = apply_patch(doc_before, patch)`、`order = read_chapters_order(worktree)` とし、`add_check(check_new_order_issues(order, doc_before, after))` を足す。WARNING でも提案は作る（承認は止めない）。PASS も checks に残す。
* chapters-order.yaml を読めない場合は、この検査を飛ばして `{"name": "foreshadow_order", "status": "WARNING", "details": ["chapters-order.yaml could not be read: ..."]}` を足す。
* 他の処理は変えない。

## 作業 11：テスト（2-D3）

* `tests/test_foreshadowcheck.py`：
  - positions：order にない章、正常
  - order：仕様 §6.4.2 の例（問題なし）、hints が introduced より前、developments が introduced より前、introduced が複数あるとき最も早い位置で比べる、introduced がなく hints がある、resolved が introduced より前、resolved の後の hints・developments、同じ位置は問題にしない、比較できない位置を無視する
  - overdue：前の章が予定で未回収の major（WARNING）、この章が予定（PASS）、後の章が予定（PASS）、minor・normal は対象外、cancelled・resolved は対象外、planned_resolution が null
  - plan_refs：cancelled を参照、前の章で resolved を参照、この章で resolved（書き直し。PASS）
  - run_foreshadow_checks：registry が None で []、4つの順
  - status_rows：対象の選び方と順、`last_hint`・`last_development`、`planned_resolution_in_this_chapter`、`scenes_in_this_chapter`、render の `なし` と YAML の読み戻し
  - check_new_order_issues：前からある問題は出さない、新しい問題だけ出す
* `tests/test_validatejob.py`：mechanical に4つの伏線の検査が入ること、`foreshadow_overdue` の WARNING で WAITING_HUMAN になること、registry がない作品で伏線の検査が入らず状況表が `なし`、integrity_review のプロンプトに状況表が入ること、`prompt_tables` の2行。
* `tests/test_stateupdate.py`：registry の Patch が introduced より前の hints を足す場合に `foreshadow_order` の WARNING が checks に入り、提案は pending で作られること。問題のない Patch で PASS。

## 2-D3 の作業の手順

1. Claude のプロンプトの更新（`{{foreshadow_status}}`）が phase2/chapter-flow にあることを確かめる。なければ止めて報告する。
2. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
3. **Human の許可を得る。**
4. 作業 8〜11 を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする。
5. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase2: 2-D3 foreshadowing position checks and status table
   ```
6. `git push origin phase2/chapter-flow`（2-D1 と同じ規則）。
7. 報告して止まる。

---

# 2-D4：受入3・4 の結合試験

## 作業 12：tests/integration/test_consistency_integration.py（`@pytest.mark.integration`）

実際の **Claude** を使う。**AGY は使わない**：draft Job は `tests/test_draftjob.py` の `FakeRunner` と同じ方法の偽の `container_runner` で、fixture の本文（違反を仕込んだ draft.md）をそのまま AGY の出力として返す（design §6。AGY の出力のゆらぎに左右されないため）。draft Job・validate Job の流れ（作業ブランチ、Context の記録、draft_consistency）は本物を通す。一時領域に作品を作り、終了時に削除する（既存の `tests/integration/test_validatejob_integration.py` と同じやり方）。

共通の作品：`world/setting.md`、`characters/C001.yaml`（first_person: 俺、address: `{default: お前, C002: {default: 美咲, changes: [...]}}`。試験ごとに changes を変える）、`characters/C002.yaml`（first_person: 私、address: `{C001: カイ}`）、`characters/C003.yaml`（D3b で使う。plan のどの場面にも載せない）、`foreshadowing/registry.yaml`、chapters-order は `[ch-001]`。plan は2場面（S1・S2）、両方の場面に C001 と C002。本文は各場面に C001 の台詞を2つ以上含め、**話者が地の文で明示される**ようにする（「カイは言った」など。§4 の「話者を確定できない台詞は finding にしない」に当たらないように）。違反のない本文の部分は、設定どおりの一人称・呼び方にする。

| 試験 | 仕込み | 期待（validate の結果） |
|---|---|---|
| D1 一人称 | C001 の台詞の1つで一人称が「僕」 | `checks.character` に finding があり、`character` が `{character_id: C001, rule: speech.first_person, expected: 俺, actual: 僕}` を含む（scene は仕込んだ場面） |
| D2 呼び方 | changes なし。S1 の C001 の台詞で C002 を「美咲ちゃん」と呼ぶ | `character.rule == "address.C002"`、`expected == "美咲"`、scene S1 |
| D3a 途中の変更（違反） | changes `[{value: 君, from: {chapter: ch-001, scene: S2}, reason: 関係性の変化}]`。S1 で「君」、S2 で「美咲」と呼ぶ | address.C002 の finding が S1（expected 美咲）と S2（expected 君）の両方 |
| D3b 途中の変更（正しい）・不在の人物 | D3a と同じ changes。S1 で「美咲」、S2 で「君」と呼ぶ。C001 の address に `C003: 先生` を足し（C003 は characters/ にいるが plan の場面にいない）、S2 で C001 がその場にいない C003 を台詞で「先生」と呼んで話題にする | address.C002・address.C003・address.default の finding がない（design §4。規則表は不在の相手の呼び方も持つ） |
| D4 伏線の時系列 | registry の F001：introduced `[ch-001 S2]`、hints `[ch-001 S1]` | mechanical の `foreshadow_order` が WARNING（Claude の結果によらない。機械検査の確認） |
| D5 未回収 | chapters-order を `[ch-001, ch-002]` にし、ch-002 を検証。F002：major・active、planned_resolution `ch-001 S2`、resolved null | mechanical の `foreshadow_overdue` が WARNING |

* D1〜D3 は、Job が WAITING_HUMAN（integrity が WARNING か STOP）であることと、上の finding の有無を確かめる。`expected`・`actual` は**完全一致**で比べる。`scene` は場面 ID の数値で比べる（`S1` と `S001` を同じとする）。
* D4・D5 は機械検査の確認なので、Claude の結果は確かめない（print だけ）。D5 は ch-001 を FINAL にする必要はない：ch-002 を add_chapter して plan を置き、PLAN_APPROVED にすればよい（ch-001 の summary は context に入れない）。
* **期待の finding が出なかった場合、または余分な finding（D3b の address.C002 など）が出た場合は、試験・プロンプト・fixture を変えて通さず**、その試験の review.yaml の integrity の全文と、Claude に渡したプロンプトの規則表の部分を print して `pytest.fail` で止める（design §6、2-C4 と同じ方針）。
* 各試験で次を print する：Job の elapsed_seconds と `actual_model`、review.yaml の mechanical の全件、integrity の `checks.character` と `checks.foreshadowing` の全文、`prompt_tables` の details。
* 試験の最後に `ls -la ~/.local/share/novui-itest` に残りがないこと（既存の結合試験と同じ後始末）。

## 2-D4 の作業の手順

1. 本指示と参照文書を読み、これから作成する試験、fixture の本文、実行するコマンドの一覧を具体的に説明する（**fixture の本文の全文を示す**）。
2. **Human の許可を得る。**
3. `.venv/bin/python -m pytest -q` を全件 PASS にする。
4. 結合試験を実行する（既存の分も含む）。失敗したら直さずに止めて報告する。

   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=gemini-3.8-flash-high .venv/bin/python -m pytest -s -m integration tests/integration
   ```
5. 残存確認（`ls -la ~/.local/share/novui-itest`、`podman ps -a --filter name=novui-itest --format '{{.Names}}'`）。
6. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase2: 2-D4 acceptance 3 and 4 integration tests
   ```
7. `git push origin phase2/chapter-flow`（2-D1 と同じ規則）。
8. 報告して止まる。

---

## 報告の内容（各段階共通）

1. 変更・作成したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力
3. （2-D4 のみ）結合試験の出力の全文（print を含む）と、残存確認の結果
4. **許可範囲外のファイルを変更していないことの確認**：`git diff --stat <段階の開始時の HEAD> -- src/novui schemas prompts docs` で、許可範囲のファイル以外に差分がないこと
5. 指示どおりにできなかった点、指示と異なる実装をした点、判断に迷った点（なければ「なし」）
6. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
