# AGYへの作業指示：Phase 2-C 状態更新と最終承認

**この指示書は 2-C1〜2-C4 の全体を定める。実際の作業は、Human が段階ごと（2-C1、2-C2、2-C3、2-C4）に「○○を実施せよ」と指示したときに、その段階だけを行う。指示された段階以外のコードを先取りして書いてはならない。**

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストと（2-C4 では）結合試験を実行し、phase2/chapter-flow branch に commit・push します。
設計の判断はしません。本指示・`docs/phase2/phase2c-design.md`・仕様書が矛盾する場合、または決まっていない判断が必要になった場合は、作業を止めて報告してください。
結合試験で想定と違う結果が出た場合は、コードや試験を変えて辻褄を合わせず、その時点で止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase2/phase2c-design.md`（**設計の正本。特に §1〜§7**）
* `docs/phase2/phase2-plan.md`（§2 の表と §3 の 2-C、§4）
* `docs/novel-system-spec-v0.5.2.md` の §6.4、§6.5、§7.1、§7.2、§8、§10、§12、§15、§17、§18
* `prompts/claude/preamble.md`、`prompts/claude/state_update.md`、`prompts/claude/state_patch.md`（読み込んで使う。**変更しない**）
* `src/novui/` の次のファイルと関数（**変更しない**。呼び出して使う）：
  * `claudejob.run_claude_call`、`ClaudeCallOutcome`、`claude_version`
  * `claude_output.parse_claude_output`、`CLAUDE_OUTPUT_TYPES`
  * `prompt.build_claude_prompt`、`load_prompt_template(name, *, group=…, **values)`
  * `states.py`（`ChapterEvent`、`ChapterState`、`JobEvent`、`JobState`、`next_chapter_state`、`next_job_state`）
  * `workrepo.py`（`JobWorktree`、`format_message`、`parse_trailers`、`check_commit_guard`、`head_commit`、`chapter_branches`、`commit_all`、`changed_paths`、`plan_merge`、`merge_job_branch`、`remove_job_worktree`、`MergeConflict`、`CommitGuardError`）
  * `protected.is_protected`、`checks.check_allowed_paths`、`checks.CheckResult`、`gitinspect.get_changes`・`run_git`
  * `jobrecord.py`（`new_job_record`、`apply_transition`、`save_job_record`、`load_job_record`、`now_iso`）、`jobrunner.jobs_dir`、`ids.next_job_id`
  * `chapters.py`（`ensure_main_ready`、`read_chapter_meta`、`ChapterError`）、`yamlio.py`、`schema.py`（`validate_or_raise`）、`semantics.py`
  * `validatejob.py` の次のもの（**private の名前だが、そのまま import して使う。validatejob.py は変更しない**）：`find_chapter_branch`、`_check_draft_consistency`、`_read_branch_yaml`、`_read_branch_chapter_meta`、`_restore_paths`、`_chapter_rel`
  * `draftjob.py`：`collect_draft_context`、`build_draft_instruction`、`read_plan`、`run_chapter_draft_job` の本体（redraft はこれと同じ手順を `requestflow.py` に実装する。draftjob.py は変更しない）
* 2-B までに作成した `tests/`

## 書込みの許可範囲

段階ごとに、許可される新規ファイルが違う。

```text
共通で許可：
  tests/**（追加と、既存テストの必要最小限の修正。既存テストの期待値を変えて通してはならない）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。終了時に削除する）

2-C1：
  src/novui/ の新規ファイル：statepatch.py、approvals.py、statecheck.py、proposals.py
  schemas/proposal.schema.json（新規。Human が許可した場合だけ。許可がなければ作らない）
  src/novui/semantics.py（proposal の SEMANTIC_CHECKS の登録だけ。schema を作った場合のみ）

2-C2：
  src/novui/ の新規ファイル：stateupdate.py

2-C3：
  src/novui/ の新規ファイル：finalize.py、requestflow.py

2-C4：
  src/novui/cli.py（サブコマンドの追加と、show の表示の追加だけ。既存のサブコマンドの動作は変えない）
  tests/integration/test_statefinal_integration.py（新規）

禁止：上記以外すべて。次を含む。
  docs/、prompts/、spike/、tools/、container/、templates/、.git/、.gitignore、pyproject.toml（依存を追加しない）
  既存の schemas/*.json（変更しない）
  src/novui の既存ファイル（上記の許可を除く）。特に states.py、workrepo.py、protected.py、checks.py、jobrunner.py、
  draftjob.py、validatejob.py、mechanical.py、claudejob.py、claude_output.py、prompt.py、planjob.py、chapters.py
```

2-B1・2-B2（draft Job・validate Job・mechanical.py・review.yaml・既存の状態遷移・既存の commit guard・既存の worktree/merge 処理）は**変更しない**。変更が必要だと思ったら、作業を止めて報告してください。

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
ls -la ~/.local/share/novui-itest
podman ps -a --filter name=novui-itest --format '{{.Names}}'
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase2/chapter-flow
```

Phase 1・2-A・2-B の禁止事項はすべて引き続き有効です（ログイン、OAuth の更新、アカウントの切替、元アカウントの token への接触を含む）。

## 背景（設計の要点。詳細は phase2c-design.md）

1. state_update は、**summary を1回、Patch を対象ファイルごとに1回ずつ** Claude に出力させる（Claude の出力は1回に1つの schema）。Patch の対象ファイルは Controller が summary から決める。
2. 提案は作品リポジトリの外（Controller のデータ `<data_dir>/works/<work_key>/proposals/<chapter_id>/<job_id>.yaml`）に保存する。承認前に作業ブランチ・main へは書かない。
3. 承認（approve-state）は、**1つの commit** に、承認記録・Patch を適用した設定ファイル・summary.yaml・chapter.yaml（HUMAN_APPROVED）を入れ、trailer に `NovUI-Job` と `Approval-Id` を付ける。既存の `commit_all` の guard をそのまま通る。
4. 最終承認（final-approve）は、保護対象変更の監査のあと、既存の `merge_job_branch` で merge し、chapter.yaml を FINAL にして、作業ブランチと worktree を削除する。
5. 【要確認】の draft は、resolve_request で判断を記録し、`redraft`（作業ブランチを捨てて draft を再実行）で先へ進む。

## 共通の規則

* 純粋な関数は、副作用（ファイル・git・Claude）を持たない形で書く。
* Controller のデータへの書込みは `yamlio.write_yaml_atomic`。
* 日付時刻は `jobrecord.now_iso()`。
* hash は `sha256:<16進64桁（小文字）>`。
* 例外はモジュールごとに定義する（`PatchError`、`ApprovalError`、`ProposalError`、`FinalizeError`）。`chapters.ChapterError` は、章の状態・前提が満たされない場合に使う（validate と同じ）。
* ログ・Job 記録・コミットメッセージにモデル名の識別子以外の秘密情報を入れない（Job 記録の `cli.actual_model` は既存どおり）。

---

# 2-C1：Patch 適用・hash・承認記録・検査・提案の保存

純粋なロジックと Controller データの読み書きだけを作る。**Claude・AGY・podman・git の worktree を使わない。**

## 作業 1：src/novui/statepatch.py

```python
class PatchError(Exception)

def parse_pointer(ptr: str) -> list[str]
def apply_operations(doc: Any, operations: Sequence[Mapping[str, Any]]) -> Any
def apply_patch(doc: Any, patch: Mapping[str, Any]) -> Any
def canonical_json(obj: Any) -> bytes
def sha256_bytes(data: bytes) -> str
def file_sha256(path: Path) -> str
def patch_sha256(patch: Mapping[str, Any]) -> str
def patch_set_sha256(patches: Sequence[Mapping[str, Any]]) -> str
def is_noop_patch(patch: Mapping[str, Any]) -> bool
```

* `parse_pointer`（RFC 6901）：`""` は `[]`。それ以外は `/` で始まること（違えば PatchError）。各要素の `~1` を `/`、`~0` を `~` にする（この順）。`~` の後が 0・1 以外なら PatchError。
* `apply_operations`：`doc`（JSON 互換のデータ。dict・list・str・int・float・bool・None）を**変更せず**、`copy.deepcopy` に対して `operations` を順に適用して返す。1つでも失敗したら PatchError（何も返さない）。
  - `add`：親が dict なら、キーが既にあっても置換する。親が list なら、`-` は末尾に追加、数字は 0〜len の範囲で挿入（範囲外は PatchError）。親が存在しなければ PatchError。path が `""` のときは文書全体を value に置換。
  - `remove`：対象が存在しなければ PatchError。
  - `replace`：対象が存在しなければ PatchError。path が `""` のときは文書全体を置換。
  - `test`：対象が存在し、value と JSON として等しいこと。等価は型を区別する（`True` と `1`、`1` と `1.0` を等しいとしない。dict・list は再帰的に比較）。存在しない・不一致は PatchError。
  - list の添字は、先頭ゼロなしの10進数（`0` は可、`01` は不可）。負数・符号つき・空文字は不可。
  - op がこの4つ以外なら PatchError（`move`・`copy` は受け付けない）。
* `apply_patch`：`patch` を `schema.validate_or_raise(patch, "state_patch")` と `semantics.check_state_patch` で検証してから、`apply_operations(doc, patch["operations"])` を返す。
* `canonical_json`：`json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`。
* `sha256_bytes`：`"sha256:" + hashlib.sha256(data).hexdigest()`。`file_sha256` は `sha256_bytes(path.read_bytes())`（Context の `prompt.ContextEntry.sha256` と同じ値になること）。
* `patch_sha256`：`sha256_bytes(canonical_json(patch))`。patch の operations の配列の順序は変えない。
* `patch_set_sha256`：patches を `target` の昇順に並べた配列の `sha256_bytes(canonical_json(…))`。patches が空、または target が重複していれば ValueError。
* `is_noop_patch`：operations のすべてが `test` なら True。

## 作業 2：src/novui/approvals.py

```python
class ApprovalError(Exception)

APPROVALS_DIR: str = ".novui/approvals"

def approval_rel(approval_id: str) -> str
def reserve_approval_id(settings: Settings, work: WorkInfo, *, extra_dirs: Sequence[Path] = ()) -> str
def build_approval(approval_id: str, *, job_id: str, patches: Sequence[Mapping[str, Any]],
                   approved_at: str | None = None) -> dict[str, Any]
def check_approval_applicable(approval: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> None
def is_approval_used(repo: Path, approval_id: str, *, branch: str | None = None) -> bool
```

* `approval_rel`：`.novui/approvals/<approval_id>.yaml`（approval_id が `^A-[0-9]{4,}$` でなければ ValueError）。
* `reserve_approval_id`：`<settings.data_dir>/works/<work.work_key>/approvals.next`（テキスト。次に使う番号の10進数。なければ 1）を読み、`max(カウンタ, repo（main）の .novui/approvals/ にある最大番号 + 1, extra_dirs の各ディレクトリ内の最大番号 + 1)` を番号 n とし、カウンタを n+1 にして原子的に書いてから、`A-` ＋ n を4桁以上ゼロ詰めで返す。モジュールの `threading.Lock` で排他する。**番号は再利用しない**（返したあとで呼び出し側が失敗しても戻さない）。`extra_dirs` は、作業ブランチの worktree の `.novui/approvals/` を渡すために使う（存在しなくてもよい）。
* `build_approval`：`{approval_id, source: "proposal", instruction_id: None, job_id, patch_sha256: patch_set_sha256(patches), targets: 昇順の target 一覧, approved_by: "human", approved_at}`。`approval` schema と `check_approval` で検証して返す（patches が空なら ValueError：Patch が0件の提案に承認記録は作らない）。
* `check_approval_applicable`：`patch_set_sha256(patches) == approval["patch_sha256"]` で、各 patch の target が `approval["targets"]` に含まれること（そうでなければ ApprovalError）。
* `is_approval_used`：`git cat-file -e main:<approval_rel>` が成功する、または `branch` が与えられて `git cat-file -e <branch>:<approval_rel>` が成功すれば True。

## 作業 3：src/novui/statecheck.py

```python
def derive_patch_targets(summary: Mapping[str, Any]) -> list[str]
def check_summary_refs(summary: Mapping[str, Any], *, chapter_id: str,
                       character_ids: Collection[str], foreshadow_ids: Collection[str]) -> CheckResult
def check_patch_target(patch: Mapping[str, Any], expected_target: str) -> CheckResult
def check_patch_policy(patch: Mapping[str, Any], *, chapter_id: str, scene_ids: Sequence[str],
                       doc_before: Any) -> CheckResult
def dry_run_patch(doc_before: Any, patch: Mapping[str, Any]) -> CheckResult
def check_summary_patch_consistency(summary: Mapping[str, Any],
                                    patches: Sequence[Mapping[str, Any]]) -> CheckResult
def next_knowledge_number(character_docs: Iterable[Mapping[str, Any]]) -> int
def pointer_hints(target: str, doc_before: Any) -> str
```

すべて `checks.CheckResult(name, status, details)` を返す（`status` は PASS／WARNING／FAIL。details は文字列のタプル）。

* `derive_patch_targets`：`characters/<id>.yaml`（summary の `characters[]` のうち `knowledge_added` か `relationship_changes` が空でない人物。id の昇順）、続けて、summary の `foreshadowing` が1件以上あれば `foreshadowing/registry.yaml`。
* `check_summary_refs`（name `summary_refs`、FAIL）：design §2.4 の 1。`chapter_id` の一致、`characters[].id` の重複なし・すべて `character_ids` に含まれる、`foreshadowing[].id` が重複なし・すべて `foreshadow_ids` に含まれる、summary のすべての文字列（再帰）に `{{` と `}}` を含まない。
* `check_patch_target`（name `patch_target`、FAIL）：`patch["target"] == expected_target`。
* `check_patch_policy`（name `patch_policy`、FAIL）：design §3.4 の表のとおり。
  - 共通：target が `characters/<ID>.yaml` か `foreshadowing/registry.yaml`、op が add・replace・test、path に使える pointer の形。
  - `characters/`：表の各行の path・条件。`/knowledge/-` の value は `{id, fact, source_chapter}` で `source_chapter == chapter_id`、`id` は `K` ＋3桁以上で、同じ patch 内で重複しない。`/relationships/<i>/changes/-` と `/address/<ID2>/changes/-` の value は `common.change` の形で `from.chapter == chapter_id`、`from.scene` は `scene_ids` のいずれか。`/relationships/<i>/changes/-` は、それより前の operations に `test /relationships/<i>/with` があること。`replace /address/<ID2>` は、それより前に `test /address/<ID2>` があり、`doc_before` の現在の値が文字列で、value が `{default: <その文字列>, changes: [...]}` であること。`personality`・`behavior`・`speech`・`exceptions`・`name`・`id`・`notes` への add・replace は不可。
  - `foreshadowing/registry.yaml`：design §3.4 の registry の規則。各伏線について先頭の操作が `test /<i>/id` で、以降の pointer が同じ `/<i>/` で始まること。許可される path は `/<i>/introduced/-`、`/<i>/hints/-`、`/<i>/developments/-`（value は `position`、`chapter == chapter_id`、`scene` が `scene_ids` のいずれか）、`/<i>/status`（replace。value は planned・active・resolved のいずれか。cancelled は不可）、`/<i>/resolved`（replace。value は `position`）。`status` を `resolved` にする場合は同じ patch 内に `/<i>/resolved` の replace があること。`/<i>/planned_resolution` は不可。
  - details に、違反した operations の添字と理由を列挙する。
* `dry_run_patch`（name `patch_apply`、FAIL）：`statepatch.apply_patch(doc_before, patch)` を行い、結果を patch の target に応じた schema（`characters/` なら `character`、registry なら `registry`）で検証し、`semantics.SEMANTIC_CHECKS` の対応する関数（`character`・`registry`）で検証する。PatchError・SchemaError・semantic error はすべて FAIL の details にする。
* `check_summary_patch_consistency`（name `summary_patch_consistency`、**WARNING**）：summary の各 `characters[]` の `knowledge_added` の各文が、その人物の `characters/<id>.yaml` の Patch の `add /knowledge/-` の value の `fact` のいずれかと**文字列として一致する**か。一致しない文を details に `<人物 ID>: <文>` として列挙し、1件でもあれば WARNING。なければ PASS。
* `next_knowledge_number`：与えられた人物の dict 群の `knowledge[].id`（`K` ＋数字）の最大の数字 + 1（なければ 1）。
* `pointer_hints`：プロンプトの `{{pointer_hints}}` に入れる文字列。
  - registry の場合：`F001 → /0` のように、配列の各要素の `id` と添字を1行ずつ（添字の昇順）。
  - 人物の場合：`関係：C002 → /relationships/0` を relationships の各要素について1行ずつ、`呼び方：C002 → /address/C002（文字列）` または `（object）` を address の各相手について1行ずつ。どちらもなければ `なし` の1行。
  - 先頭に対象の説明の1行を置かない（行だけを返す）。

## 作業 4：src/novui/proposals.py と schemas/proposal.schema.json

**schemas/proposal.schema.json は、Human が許可した場合だけ作る**（design §8 の 2）。許可がなければ作らず、`proposals.py` は提案ファイルの内容を、`summary` と `state_patch` の既存 schema による個別検証（と型の確認）だけで検証する。

schema（許可された場合）：`type: state_proposal`、`job_id`、`chapter_id`、`branch`（`ai/<chapter_id>/<job_id>` の形）、`branch_head`（`common.commit`）、`status`（pending／approved／rejected／superseded／invalid）、`status_reason`（文字列か null）、`created_at`、`decided_at`（datetime か null）、`approval_id`（`common.approval_id` か null）、`summary`（`summary.schema.json` の `$ref`）、`patches`（各要素 `{patch: state_patch の $ref, patch_sha256: common.sha256}`）、`patch_set_sha256`（common.sha256 か null）。`additionalProperties: false`、すべて必須。既存の schema と同じ書き方（`$id: https://novui.local/schemas/proposal.schema.json`、`$ref: common.schema.json#/$defs/...`）にする。semantics（`check_proposal`）：`patches` の target が重複しない・昇順、各 `patch_sha256` が Patch の hash と一致、`patch_set_sha256` が patches から計算した値（空なら null）と一致、`status` が approved のとき `approval_id` が null でない（patches が1件以上のとき）、`status` が pending のとき `decided_at` が null。`semantics.SEMANTIC_CHECKS` に `"proposal": check_proposal` を登録する（semantics.py はこの登録だけ変更してよい）。

```python
class ProposalError(Exception)

def proposals_dir(settings: Settings, work_key: str) -> Path
def new_proposal(*, job_id: str, chapter_id: str, branch: str, branch_head: str,
                 summary: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> dict[str, Any]
def save_proposal(settings: Settings, work_key: str, proposal: Mapping[str, Any]) -> Path
def load_proposal(path: Path) -> dict[str, Any]
def find_proposals(settings: Settings, work_key: str, chapter_id: str) -> list[dict[str, Any]]
def pending_proposal(settings: Settings, work_key: str, chapter_id: str) -> dict[str, Any] | None
def with_status(proposal: Mapping[str, Any], status: str, *, reason: str | None = None,
                approval_id: str | None = None, decided_at: str | None = None) -> dict[str, Any]
```

* `proposals_dir`：`<data_dir>/works/<work_key>/proposals`。ファイルは `<proposals_dir>/<chapter_id>/<job_id>.yaml`。
* `new_proposal`：`status: pending`、`status_reason: None`、`created_at: now_iso()`、`decided_at: None`、`approval_id: None`。`patches` は target の昇順に並べ、各要素に `patch_sha256` を付け、`patch_set_sha256` を計算する（patches が空なら null）。
* `save_proposal`：検証してから `write_yaml_atomic`。検証に失敗したら SchemaError（ファイルは書かない）。
* `find_proposals`：その章のすべての提案を `created_at` の昇順（同じなら job_id の数字の昇順）で返す。
* `pending_proposal`：`status == pending` のもの。2件以上あれば ProposalError（あってはならない）。
* `with_status`：新しい dict を返す（元を変更しない）。`status` が pending 以外のとき `decided_at` を与える（省略なら `now_iso()`）。pending から pending への変更、approved・rejected・superseded・invalid から他への変更は ProposalError（終了した提案は変えない）。

## 作業 5：テスト（2-C1）

* `tests/test_statepatch.py`：
  - parse_pointer（空、`/a/b`、`~0`・`~1`、不正な形）
  - add（dict の新規キー・既存キー、list の途中・`-`・範囲外、親なし）、remove（あり・なし）、replace（あり・なし）、test（一致・不一致・型の違い `true` と `1`・存在しない）、move・copy は PatchError、全か無か（途中で失敗した場合に入力が変わっていない）、入力を変更しない
  - list の添字の形（`01`・`-1`・空文字は不可）
  - canonical_json の安定性（キーの順序によらない・日本語がそのまま）、`file_sha256` が `prompt._collect_context` の `ContextEntry.sha256` と一致すること、patch_sha256 が operations の順序に敏感であること、patch_set_sha256 が入力の並び順に依存しないこと・空と重複で ValueError
  - is_noop_patch
* `tests/test_approvals.py`（`tmp_path` の作品リポジトリ。git init してよい）：reserve_approval_id（初回が `A-0001`、連続で `A-0002`、`extra_dirs` や main に `A-0005` があれば `A-0006`、再利用しない）、build_approval が schema・semantics に適合、patches が空で ValueError、check_approval_applicable（正常・hash 不一致・target 外）、is_approval_used。
* `tests/test_statecheck.py`：derive_patch_targets、check_summary_refs（各 FAIL 条件：chapter_id 違い・人物の重複・Context にない人物・登録のない伏線・`{{` を含む文字列）、check_patch_target、check_patch_policy（design §3.4 の許可される例のすべて、許可されない例：remove、性格の replace、planned_resolution の replace、cancelled、`test` なしの `/relationships/<i>/changes/-`、`test /<i>/id` なしの registry、from.scene が plan の場面にない、source_chapter が対象の章でない、リスト全体の replace）、dry_run_patch（適用結果が schema に適合しない・check_registry に違反する場合の FAIL）、consistency の WARNING、next_knowledge_number、pointer_hints。`tests/fixtures` の既存のフィクスチャを使ってよい（変更しない）。
* `tests/test_proposals.py`：new_proposal の hash、save・load の往復、不正な提案の保存拒否、find_proposals の順序、pending_proposal、with_status の遷移の許可・拒否。schema を作らなかった場合は、個別検証に合わせる。

## 2-C1 の作業の手順

1. 本指示と参照文書を読み、これから作成・変更するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る**（特に、`proposal.schema.json` を作るかどうか、jsonpatch 依存を追加しないこと）。
3. 作業 1〜5 を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする（既存のテストが1件も変わっていないこと）。
4. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase2: 2-C1 json patch, hashes, approval records, state checks and proposal store
   ```
5. `git push origin phase2/chapter-flow`（「fetch first」等で拒否された場合に限り `git pull --rebase origin phase2/chapter-flow` を1回だけ実行してから再度 push。競合は解決せずに止めて報告）。
6. 報告して止まる。

---

# 2-C2：state_update Job、承認・却下

2-C1 のコードを使う。Claude の呼び出しはすべて**偽の `claude_runner`**でテストする（実際の Claude は 2-C4 まで使わない）。

## 作業 6：src/novui/stateupdate.py

```python
STATE_UPDATE_JOB_TYPE = "state_update"

def run_state_update(settings: Settings, work: WorkInfo, chapter_id: str, *,
                     job_id: str | None = None, replace: bool = False,
                     claude_runner: ClaudeRunner = run_claude) -> dict[str, Any]
def approve_state(settings: Settings, work: WorkInfo, chapter_id: str) -> dict[str, Any]
def reject_state(settings: Settings, work: WorkInfo, chapter_id: str, reason: str) -> dict[str, Any]
def show_proposal(settings: Settings, work: WorkInfo, chapter_id: str) -> dict[str, Any] | None
```

### run_state_update（design §2）

戻り値は Job 記録（validate と同じ作り）。

1. 前提（満たされなければ Job 記録を作らず `ChapterError`）：`chapters.ensure_main_ready`。`find_chapter_branch`。作業ブランチの chapter.yaml（`_read_branch_chapter_meta`）の state が AI_VALIDATED。`pending_proposal` が None（あれば、`replace` が False のとき ChapterError：メッセージに「先に reject-state するか --replace を使う」と書く。True のときは 11 で処理する）。
2. `actual_job_id = job_id or next_job_id(...)`。`record = new_job_record(actual_job_id, "state_update", chapter_id=chapter_id)`、`record["branch"] = branch`。保存。`LOCK_ACQUIRED`（実行 lock は取らない）。
3. `draft, details = _check_draft_consistency(settings, work, chapter_id, branch)` を実行する（validate と同じ）。`record` に `worktree`・`base_commit` を入れ、`checks` に `draft_consistency`（PASS／FAIL）を加える。FAIL なら `CLI_EXITED` → `CHECK_FAILED`（FAILED）で返す。
4. Context：`ctx_paths = [e["path"] for e in draft["context"]]`、`chapters/<id>/draft.md` が無ければ末尾に追加（validate と同じ）。plan は `draftjob.read_plan(worktree, chapter_id)`、`scene_ids = [s["id"] for s in plan["scenes"]]`。
5. Claude 呼び出しの共通処理は、`validatejob.run_validate_job` の内部関数 `call` と同じ形で書く（`build_claude_prompt(worktree, ctx_paths, task_text)`、`run_claude_call(settings, job_id=actual_job_id, prompt_text=…, expected_type=…, model=settings.claude_model, log_dir=jdir / f"{actual_job_id}-logs", log_name=…, claude_runner=claude_runner)`、`record["context"]`・`cli`・`run`・`attempt`・`checks` の更新、`save_job_record`）。`cli.actual_model` と `run` は最後の呼び出しの値でよい。
6. **summary**：`load_prompt_template("state_update", chapter_id=…, scene_ids=", ".join(scene_ids), character_ids=…, foreshadow_ids=…)`。`character_ids` は `ctx_paths` のうち `characters/<ID>.yaml` の ID をカンマ区切り（なければ `なし`）、`foreshadow_ids` は worktree の `foreshadowing/registry.yaml` の各 `id` のカンマ区切り（なければ `なし`）。`expected_type="summary"`、`log_name=f"{id}-summary"`、`checks` の名前 `claude_summary`。
7. 各呼び出しの後：timeout なら `TIMED_OUT`（FAILED）で返す。Claude の出力が得られなかった（`outcome.data is None`、利用不能を含む）なら `CLI_EXITED` → `CHECK_FAILED`（FAILED）。
8. summary が得られたら、`statecheck.check_summary_refs` を実行して `checks` に加える。FAIL なら `CLI_EXITED` → `CHECK_FAILED`。
9. **Patch**：`targets = statecheck.derive_patch_targets(summary)`。target ごとに（`derive_patch_targets` の順）：
   - target のファイルが worktree に無い、または `ctx_paths` に無ければ FAIL（`checks` に `patch_target`、details に target）で `CHECK_FAILED`。
   - `doc_before = load_yaml(worktree / target)`。`base_hash = statepatch.file_sha256(worktree / target)`。
   - `load_prompt_template("state_patch", chapter_id=…, target=…, scene_ids=…, summary_yaml=yamlio.dumps_yaml(summary), base_hash=…, pointer_hints=statecheck.pointer_hints(target, doc_before), next_knowledge_id=f"K{n:03d}")`。`n` は `next_knowledge_number` で、worktree の全 `characters/*.yaml` から求め、それまでに受け入れた Patch の `add /knowledge/-` の個数を加えた値（registry の場合は使わないので `K001` でよい）。
   - `expected_type="state_patch"`、`log_name=f"{id}-patch-{i}"`、`checks` の名前 `claude_state_patch_<i>`（i は 1 から）。
   - 得られた Patch の `base_hash` が `base_hash` と違えば、Controller の値に置き換え、`checks` に `{"name": "base_hash_corrected", "status": "WARNING", "details": [target]}` を加える。
   - `check_patch_target`、`check_patch_policy`、`dry_run_patch` を順に実行して `checks` に加える。1つでも FAIL なら `CHECK_FAILED`。
   - `is_noop_patch` なら、その Patch を提案に入れない（`checks` に `{"name": "patch_noop", "status": "PASS", "details": [target]}`）。
10. `check_summary_patch_consistency` を実行して `checks` に加える（WARNING でも止めない）。
11. `branch_head = head_commit(worktree, "HEAD")`。`proposals.new_proposal(...)` を作る。`replace` が True なら、既存の pending の提案を `with_status(..., "superseded", reason=f"superseded by {actual_job_id}")` にして保存し、その Job 記録（`state` が WAITING_HUMAN であること）を `HUMAN_CANCEL`（reason：`superseded by <job_id>`）で CANCELLED にして保存する。その後に新しい提案を保存する。
12. `CLI_EXITED`（まだなら）→ `NEEDS_HUMAN_INPUT`（reason：`awaiting approval of state update proposal`）→ WAITING_HUMAN。Job 記録を保存して返す。
13. 例外が起きた場合は、validate と同じ後始末（RUNNING なら CLI_EXITED、CHECKING なら CHECK_FAILED）をしてから再送出する。
14. state_update は作業ブランチに**何も書かない**。終了時に worktree の未 commit の変更が0件であることを `get_changes` で確認し、あれば `CHECK_FAILED`（`checks` に `worktree_clean` の FAIL）にして、その変更を `_restore_paths` で戻す。

### approve_state（design §3.3）

戻り値は更新後の Job 記録。

1. `ensure_main_ready`、`find_chapter_branch`、`pending_proposal`（None なら ChapterError）。state_update の Job 記録（`proposal["job_id"]`）を読み、`job_type == "state_update"`、`chapter_id` 一致、state が WAITING_HUMAN であること（違えば ChapterError）。
2. worktree は Job 記録の `worktree`。`run_git(worktree, "branch", "--show-current")` が `branch` と一致し、`get_changes(worktree)` が空であること（違えば ChapterError）。
3. **無効の検査**（design §2.7）：作業ブランチの HEAD が `proposal["branch_head"]` と違う、章の state が AI_VALIDATED でない、各 Patch の target ファイルの `file_sha256` が `base_hash` と違う、`check_patch_policy`・`dry_run_patch` が FAIL、のいずれかなら、提案を `with_status(…, "invalid", reason=…)` にして保存し、Job 記録を `HUMAN_CANCEL`（reason：`invalid: <理由>`）で CANCELLED にして保存してから、`ProposalError`（メッセージに理由と「state-update を再実行する」と書く）を送出する。
4. Patch が1件以上なら、`approval_id = approvals.reserve_approval_id(settings, work, extra_dirs=[worktree / ".novui" / "approvals"])`、`approval = approvals.build_approval(approval_id, job_id=proposal["job_id"], patches=[p["patch"] for p in proposal["patches"]])`、`approvals.check_approval_applicable(approval, patches)`。`is_approval_used(work.path, approval_id, branch=branch)` が True なら ApprovalError。
5. 書込み（worktree）。許可集合 `allowed` は、各 Patch の target、`chapters/<id>/summary.yaml`、`chapters/<id>/chapter.yaml`、（Patch が1件以上なら）`approvals.approval_rel(approval_id)`。
   - 各 target：`doc_after = statepatch.apply_patch(load_yaml(worktree / target), patch)`、`write_yaml_atomic`。
   - 承認記録：`write_yaml_atomic(worktree / approval_rel, approval)`。
   - summary：`schema.validate_or_raise(proposal["summary"], "summary")` のあと `write_yaml_atomic(worktree / "chapters" / chapter_id / "summary.yaml", summary)`。
   - chapter.yaml：作業ブランチの meta をコピーし、`state = next_chapter_state(ChapterState.AI_VALIDATED, ChapterEvent.STATE_PATCH_APPROVED, review_required=bool(meta["review_required"])).name`、`last_job = proposal["job_id"]`。`validate_or_raise(meta, "chapter")` と `check_chapter` のあと書く。
6. `checks.check_allowed_paths(get_changes(worktree), allowed)` が PASS でなければ、書いたファイルを `_restore_paths(worktree, <書いたパス>)` で戻して ChapterError。
7. `workrepo.commit_all(worktree, f"state update approved {chapter_id} by {job_id}", trailers, name=settings.git_name, email=settings.git_email)`。trailers は `[("NovUI-Job", job_id)]`、Patch が1件以上なら `("Approval-Id", approval_id)` を加える。`None`（変更なし）なら ChapterError。commit に失敗（`CommitGuardError` を含む）した場合は、書いたファイルを戻して再送出する。**既存の commit guard を迂回する手段を作ってはならない**（`git commit` を直接呼ばない。`NovUI-Edit` を使わない）。
8. Job 記録：`record["approval_id"] = approval_id`（Patch が1件以上のとき）、`HUMAN_CONTINUE` → COMPLETED、保存。提案：`with_status(proposal, "approved", approval_id=approval_id)` を保存。
9. **復旧**：approve_state の冒頭で、作業ブランチの HEAD の commit の message を `parse_trailers` で読み、`NovUI-Job` が `pending` の提案の `job_id` と一致し、章の chapter.yaml が HUMAN_APPROVED なら、1〜7 は済んでいるので、8 だけを行って返す（この場合 `Approval-Id` は trailer から取る）。
10. 返す Job 記録は更新後のもの。

### reject_state

`pending_proposal` と WAITING_HUMAN の Job を確認し、reason が空（空白のみを含む）なら ValueError。提案を `rejected`（reason）にして保存、Job を `HUMAN_CANCEL`（reason）で CANCELLED にして保存して返す。作業ブランチは変更しない。

### show_proposal

その章の最新の提案（`find_proposals` の最後。なければ None）を返す。pending でなくても返す。

## 作業 7：テスト（2-C2）

`tests/test_stateupdate.py`（実 git の作品リポジトリ＋作業ブランチを `tests/test_validatejob.py` と同じ作り方で用意する。偽の `claude_runner` は、`expected_type` ごとに決めた JSON の封筒を返す。`tests/test_validatejob.py` の既存の偽 runner の作りを参考にする）。

* 正常（人物1名・伏線1件）：Job が WAITING_HUMAN、提案が pending で `patches` が2件（target の昇順）、`checks` に各呼び出しと検査が並ぶ、作業ブランチに何も書かれていない（`git status` が clean で HEAD が変わらない）、main が変わらない。
* 変更なしの Patch（`test` だけ）が提案から除かれる。Patch が0件の提案も WAITING_HUMAN になり、承認で承認記録が作られない（後述）。
* Claude の `base_hash` が違う場合に Controller の値に置き換わり、`base_hash_corrected` の WARNING が `checks` に残る。
* FAILED になる場合：summary の参照が不正、summary の schema 不適合が再要求を尽くす、Patch の policy 違反、dry-run の失敗（添字のずれ）、Claude が利用不能（`is_error: true` が続く）、timeout、draft_consistency の FAIL。いずれも提案は保存されず、作業ブランチは変わらない。
* ChapterError（Job 記録を作らない）：AI_VALIDATED でない、作業ブランチがない、pending の提案がある（`replace=False`）。
* `replace=True`：古い提案が superseded、古い Job が CANCELLED、新しい提案が pending。
* approve_state の正常（Patch あり）：作業ブランチの HEAD が1コミット増え、そのコミットに、承認記録・Patch を適用した設定ファイル・summary.yaml・chapter.yaml（HUMAN_APPROVED）だけが含まれる。trailer に `NovUI-Job` と `Approval-Id`。承認記録が schema・semantics に適合し `patch_sha256`・`targets` が正しい。Job が COMPLETED で `approval_id` がある。提案が approved。**`merge_job_branch` を経由しない `commit_all` の既存の guard が実際に通っていること**（`Approval-Id` を付けない場合は `CommitGuardError` になることを、`workrepo.commit_all` を直接呼ぶテストで確認）。
* approve_state の正常（Patch なし）：承認記録なし、`Approval-Id` なし、`NovUI-Job` のみ、HUMAN_APPROVED。
* approve_state の無効：作業ブランチの HEAD が進んでいる、target が書き換わっている（base_hash の不一致）、章が AI_VALIDATED でない → 提案が invalid、Job が CANCELLED、ProposalError、作業ブランチが変わらない。
* approve_state の途中失敗（許可範囲外の変更を worktree に混ぜる）：書いたファイルが元に戻り、HEAD が変わらず、提案が pending のまま。
* 復旧：commit 後に Job 記録・提案を更新する前に落ちた状況（コミットだけ作った状態）を作り、approve_state で 8 だけが行われる。
* 承認記録の番号：2回目の承認（別の章）が `A-0002` になる。1つの承認記録は1回しか使えない（同じ番号の再利用を ApprovalError にする）。
* reject_state：rejected・CANCELLED、reason 空で ValueError。

## 2-C2 の作業の手順

2-C1 の「作業の手順」と同じ（説明 → Human の許可 → 実装 → 全テスト PASS → commit → push → 報告）。commit メッセージ：

```text
phase2: 2-C2 state_update job, proposal approval and rejection
```

---

# 2-C3：最終承認、保護対象の監査、resolve_request、redraft、discard

実 git を使う単体テストだけ（AGY・Claude を使わない。redraft のテストは偽の `container_runner` を使う）。

## 作業 8：src/novui/finalize.py

```python
class FinalizeError(Exception)

def audit_protected_changes(repo: Path, jw: JobWorktree) -> list[str]
def final_approve(settings: Settings, work: WorkInfo, chapter_id: str) -> str
def discard_cycle(settings: Settings, work: WorkInfo, chapter_id: str, *, force: bool = False) -> None
```

* `audit_protected_changes`（design §4.3）：`git rev-list --reverse <jw.base_commit>..<jw.branch>` の各 commit について、変更されたパス（`git diff-tree --no-commit-id --name-only -r -z --no-renames <commit>`。最初の commit は親との差）を調べ、保護対象（`protected.is_protected`）を変更していれば、`parse_trailers(message)` の `Approval-Id` が1つ以上あり、各 `Approval-Id` について `.novui/approvals/<id>.yaml` が**同じ commit で追加**（`git diff-tree --diff-filter=A`）されており、その内容が `approval` schema・semantics に適合して `approval_id` が trailer と一致し、その commit が変更した保護対象のパス（`.novui/approvals/` 以下を除く）がすべて記録の `targets`（複数の Approval-Id があればその和集合）に含まれること。さらに、すべての `Approval-Id` が main に `.novui/approvals/<id>.yaml` として既にない（`approvals.is_approval_used(repo, id)` が False）こと。違反を人が読める文字列のリストで返す（空なら適合）。`NovUI-Edit` だけの commit は、保護対象を変更していれば違反とする。
* `final_approve`：
  1. `chapters.ensure_main_ready(repo)`。`find_chapter_branch`。`jw = JobWorktree(branch, Path(draft["worktree"]), draft["base_commit"])`（`_check_draft_consistency` の戻り値の draft 記録から作る。ただし HUMAN_APPROVED 以降は characters/・registry.yaml が承認 commit で変わっているので、`_check_draft_consistency` の Context SHA-256 の不一致は**無視する**。代わりに、draft Job の記録が `job_type == "draft"`・`chapter_id` 一致・`branch` 一致で、`worktree` と `base_commit` があり、worktree が `branch` を指していることだけを `finalize.py` 内の関数で確認する。`_check_draft_consistency` は呼ばない）。
  2. 作業ブランチの chapter.yaml の state が HUMAN_APPROVED でなければ ChapterError。worktree に未 commit の変更があれば ChapterError。
  3. main の chapter.yaml（`chapters.read_chapter_meta`）と作業ブランチの chapter.yaml の `review_required` を確認し、どちらかが true なら `states.InvalidTransition` を、`next_chapter_state(ChapterState.HUMAN_APPROVED, ChapterEvent.FINAL_APPROVED, review_required=…)` の送出に任せる形で送出する（メッセージはそのまま）。
  4. `audit_protected_changes` の違反が1件以上なら `FinalizeError`（違反を改行で連結）。
  5. `workrepo.plan_merge(repo, jw)` の `kind == "overlap"` なら、`merge_job_branch` が `MergeConflict` を送出するので、その例外をそのまま伝える（main・作業ブランチ・chapter.yaml は変えない）。
  6. trailers：作業ブランチの `base_commit..branch` の全 commit の message を古い順に `parse_trailers` で読み、`NovUI-Job` の値、`Approval-Id` の値をそれぞれ重複を除いて集める。`[("NovUI-Job", v) for v in jobs] + [("Approval-Id", v) for v in approvals]`。
  7. FINAL の chapter.yaml：作業ブランチの meta をコピーして `state = next_chapter_state(HUMAN_APPROVED, FINAL_APPROVED, review_required=False).name`。`validate_or_raise(meta, "chapter")` と `check_chapter` のあと `dumps_yaml(meta).encode("utf-8")`。
  8. `workrepo.merge_job_branch(repo, jw, f"final approve {chapter_id}", trailers, name=settings.git_name, email=settings.git_email, extra_writes={f"chapters/{chapter_id}/chapter.yaml": <7 のバイト列>})`。戻り値の commit を保持する。`MergeConflict`・`CommitGuardError` はそのまま伝える。
  9. 成功後：`workrepo.remove_job_worktree(repo, jw, delete_branch=True)`。失敗しても main は FINAL で確定しているので、例外を握りつぶさず、`FinalizeError`（「merge は成功した。worktree/branch の削除に失敗した。もう一度 final-approve を実行すると後始末だけをやり直す」）を送出する。
  10. **後始末の再実行**：冒頭で、作業ブランチがあり、その先端が main の祖先（`git merge-base --is-ancestor <branch> main`）で、main の chapter.yaml の state が FINAL なら、merge 済みなので 9 だけを行い、main の HEAD の commit を返す。
  11. 戻り値は merge commit のハッシュ（`head_commit(repo, "main")`）。
* `discard_cycle`（design §7.6）：作業ブランチが無ければ ChapterError。作業ブランチの chapter.yaml の state が HUMAN_APPROVED 以降（HUMAN_APPROVED）で `force` が False なら ChapterError（承認済みの設定変更を捨てることを示すメッセージ）。`jw` は上の 1 と同じ作り方（draft の記録から。draft の記録がなければ、branch 名から job_id を取り、`settings.worktree_root / work.work_key / <job_id>` と `git merge-base main <branch>` を使う）。`remove_job_worktree(repo, jw, delete_branch=True)`。この作業ブランチに紐づく WAITING_HUMAN の Job（draft・validate・state_update。`jobs_dir` の記録のうち `chapter_id` が一致し `state == WAITING_HUMAN` のもの）を `HUMAN_CANCEL`（reason：`discarded`）で CANCELLED にし、pending の提案があれば `rejected`（reason：`discarded`）にする。main は変えない。

## 作業 9：src/novui/requestflow.py

```python
def unresolved_requests(requests: Sequence[Mapping[str, Any]]) -> list[int]
def build_decision_section(requests: Sequence[Mapping[str, Any]]) -> str
def resolve_request(settings: Settings, work: WorkInfo, chapter_id: str, index: int, decision: str, *,
                    job_id: str | None = None) -> dict[str, Any]
def redraft(settings: Settings, work: WorkInfo, chapter_id: str, *, run_lock: RunLock | None = None,
            job_id: str | None = None, container_runner: ContainerRunner = run_container) -> dict[str, Any]
```

* `unresolved_requests`：type が `request` の要素のうち、その添字を `request_index` に持つ `resolution` がないものの添字のリスト。
* `build_decision_section`：design §7.5 の定型。`requests` のうち type が request で、それを指す resolution があるものについて、`* 確認事項：<message>\n  判断：<decision>` を並べる（resolution が複数あれば最後のもの）。
* `resolve_request`（design §7.3）：
  1. `ensure_main_ready`、`find_chapter_branch`、作業ブランチの `chapters/<id>/requests.yaml`（`_read_branch_yaml`）を `requests` スキーマと `semantics.check_requests` で検証して読む。無い・空なら ChapterError。`decision` が空白のみなら ValueError。
  2. 添字が範囲内で、type が request で、`unresolved_requests` に含まれること（違えば ChapterError）。
  3. worktree は、その request の `job_id` の Job 記録（draft）の `worktree`。`get_changes` が空、`branch --show-current` が branch と一致すること。
  4. `actual_job_id = job_id or next_job_id(...)`。Job 記録 `new_job_record(actual_job_id, "resolve_request", chapter_id=chapter_id)`、`branch`・`worktree`・`base_commit` を入れる。LOCK_ACQUIRED → CLI_EXITED → CHECKING。
  5. 新しい requests = 元 + `{"type": "resolution", "request_index": index, "decision": decision, "resolved_at": now_iso()}`。検証して `write_yaml_atomic`。`check_allowed_paths(get_changes(worktree), {"chapters/<id>/requests.yaml"})` が PASS でなければ戻して `CHECK_FAILED`。**追記のみ**であること（元の要素がすべてそのまま先頭に残る）を、書く前に確認する。
  6. `commit_all(worktree, f"resolve request {index} of {chapter_id}", [("NovUI-Job", actual_job_id)], …)`。
  7. `CHECK_PASSED(needs_validation=False)` → COMPLETED、reason は `request <index>: <decision>`（`apply_transition(..., reason=…)` の reason に入れる。`CHECK_PASSED` の reason に渡す）。保存して返す。
  * 章の状態、draft の Job 記録は変えない。
* `redraft`（design §7.4）：
  1. `ensure_main_ready`、`find_chapter_branch`。作業ブランチの chapter.yaml の state が PLAN_APPROVED（draft が DRAFTED になっていない）であること、requests.yaml があり、`unresolved_requests` が空で、request が1件以上あること（違えば ChapterError）。
  2. requests の最初の request の `job_id` の draft Job 記録が WAITING_HUMAN であること。
  3. `section = build_decision_section(requests)`。
  4. `discard_cycle(settings, work, chapter_id)` と同じ後始末を行う（draft の Job を `HUMAN_CANCEL`（reason：`redrafted by <新 job_id>`）で CANCELLED にする。reason を `discarded` ではなくこれにするため、`discard_cycle` を呼ぶのではなく、内部の共通関数 `_remove_cycle(settings, work, chapter_id, *, cancel_reason)` を使う）。
  5. 新しい draft Job：`draftjob.run_chapter_draft_job` と同じ手順（`ensure_main_ready`、`can_accept_write_job`、PLAN_APPROVED の確認、`read_plan`、`collect_draft_context`、`build_draft_instruction`、`resolve_model`、`DraftJobRequest`、`run_draft_job`、COMPLETED なら `_mark_drafted_on_branch`）で、`instruction = build_draft_instruction(chapter_id, plan) + "\n\n" + section` にする。`draftjob` の非公開関数（`_mark_drafted_on_branch` など）を import して使ってよい（draftjob.py は変更しない）。**手順の実装は `draftjob.run_chapter_draft_job` のコードを読み、同じ呼び出し順にする。差があれば報告する。**
  6. 新しい draft Job の記録を返す。

## 作業 10：テスト（2-C3）

* `tests/test_finalize.py`（実 git。作業ブランチは `tests/test_validatejob.py` と同じ作り方に、2-C2 の approve_state で HUMAN_APPROVED まで進めたものを使う。承認の途中は 2-C2 の関数でよい）：
  - 正常（Patch あり）：main が merge commit になり、`chapters/<id>/chapter.yaml` が FINAL、summary.yaml・設定ファイルの変更・承認記録が main にある。merge commit の trailer に `NovUI-Job`（draft・validate・state_update のすべて。plan のものは含まない）と `Approval-Id` のすべて。作業ブランチ・worktree が無い。`find_chapter_branch` が ChapterError。`can_accept_write_job` が True。
  - 正常（Patch なし）：`Approval-Id` なしで merge できる。
  - main が進んだ（作業ブランチが変えていないパスだけを main が変更）：merge が通る。main が作業ブランチと同じパス（`characters/C001.yaml`）を変更した：`MergeConflict`、main の HEAD が変わらない、working tree が clean、作業ブランチが残る。
  - 章が HUMAN_APPROVED でない（AI_VALIDATED）：ChapterError。
  - `review_required` が true（main 側・作業ブランチ側のそれぞれ）：`InvalidTransition`。
  - 監査の違反：作業ブランチに、Approval-Id なしで characters/ を変更した commit を足す／承認記録のない Approval-Id の commit／targets に含まれないパスを変更した commit／main に既にある承認番号の再利用 → `audit_protected_changes` が違反を返し、`final_approve` が `FinalizeError`、main が変わらない。`NovUI-Edit: human-content` の trailer の commit が作業ブランチにあれば違反。
  - 後始末の失敗（worktree の削除に失敗するよう細工）：`FinalizeError`、main は FINAL、再実行で後始末だけが完了する。
  - discard_cycle：main が変わらず、章が main 上の状態（PLAN_APPROVED）に戻る。WAITING_HUMAN の Job が CANCELLED。HUMAN_APPROVED の作業ブランチは `force=False` で ChapterError、`force=True` で削除できる。pending の提案が rejected。
* `tests/test_requestflow.py`：
  - 【要確認】2件で止まった draft（偽の `container_runner` で作る。2-B の `tests/test_draftjob.py` の方法）に対して、`resolve_request` を1件目 → 2件目の順に実行：requests.yaml に resolution が追記される（元の要素が変わらない）、commit に `NovUI-Job`、章・draft の Job 記録が変わらない、resolve_request の Job が COMPLETED で history の reason に判断が残る。範囲外・request でない・解決済み・decision が空白のみ → エラー。
  - `build_decision_section` の出力。
  - `redraft`：未解決があると ChapterError。全解決後に実行すると、古い作業ブランチと worktree が消え、古い draft の Job が CANCELLED（reason `redrafted by <job_id>`）、新しい draft が実行され、偽の `container_runner` に渡された AGY のプロンプトの末尾に判断の節が含まれる。新しい draft が COMPLETED なら chapter.yaml が DRAFTED（新しい作業ブランチ上）。新しい draft がまた【要確認】を出せば WAITING_HUMAN。

## 2-C3 の作業の手順

2-C1 の「作業の手順」と同じ。commit メッセージ：

```text
phase2: 2-C3 final approval, protected change audit, resolve_request, redraft and discard
```

---

# 2-C4：CLI と結合試験

## 作業 11：src/novui/cli.py の変更

サブコマンドの追加と `show` の表示の追加だけ。既存のサブコマンドの引数・動作・終了コードは変えない。

| サブコマンド | 引数 | 動作 |
|---|---|---|
| state-update | `--work <key> --chapter <id> [--replace]` | `stateupdate.run_state_update`。結果を表示（下記） |
| approve-state | `--work <key> --chapter <id>` | `stateupdate.approve_state`。承認記録の番号・commit・HUMAN_APPROVED を表示 |
| reject-state | `--work <key> --chapter <id> --reason <text>` | `stateupdate.reject_state` |
| final-approve | `--work <key> --chapter <id>` | `finalize.final_approve`。merge commit を表示 |
| resolve-request | `--work <key> --chapter <id> --index <n> --decision <text>` | `requestflow.resolve_request` |
| redraft | `--work <key> --chapter <id>` | `requestflow.redraft`（実行 lock は CLI の中で `RunLock()` を作る）。「この判断はこの章の本文にだけ効く。設定に残すには Human が設定ファイルを直接編集する」旨の注意を表示 |
| discard | `--work <key> --chapter <id> [--force]` | `finalize.discard_cycle` |
| show（追加） | 既存 | `--chapter` 指定時、pending の提案があれば、その job_id、summary の events と characters・foreshadowing の要約、Patch の target と operations の件数、WARNING の checks を表示。章の未解決の requests（作業ブランチにあれば）も表示 |

* state-update の結果表示：Job の状態、提案の job_id、summary の events の件数、Patch の target ごとの operations の件数、WARNING の checks（`base_hash_corrected`・`summary_patch_consistency`）。**正常終了は WAITING_HUMAN（承認待ち）で、終了コードは 3**（2-A・2-B の規則：FAILED は 2、WAITING_HUMAN は 3）。メッセージに「承認待ち。approve-state か reject-state を実行する」と書く。
* final-approve は、`MergeConflict`・`CommitGuardError` のとき終了コード 3 と重なったパス・理由を表示する（design §6.2）。`FinalizeError`・`ChapterError`・`InvalidTransition`・`ProposalError`・`ApprovalError` は終了コード 2 とメッセージ。成功は 0。
* `tests/test_cli.py` に追加：各サブコマンドの引数解析と終了コード（実際の処理は monkeypatch した関数に置き換えてよい。既存の test_cli.py のやり方に合わせる）。

## 作業 12：結合試験（tests/integration/test_statefinal_integration.py、`@pytest.mark.integration`）

実際の AGY と Claude を使う。一時領域に作品を作り、終了時に削除する。既存の `tests/integration/test_validatejob_integration.py` と同じ作品・plan・モデル選択（`NOVUI_AGY_MODEL`）を使う。Human の手元のマシンで実行する（cloud のセッションでは実行できない）。

1. **S1 outline → FINAL（受入 1）**：
   - 作品（world/setting.md、characters/C001.yaml、F001）を用意し、add_chapter（ch-001）、plan は fixture で置いて PLAN_APPROVED にする（`test_validatejob_integration.py` と同じ）。
   - `run_chapter_draft_job` → `run_validate_job`。draft が WAITING_HUMAN（【要確認】）なら、ここで試験を止め、requests の内容を print して `pytest.skip` で終える。validate が WARNING／STOP で WAITING_HUMAN なら、`decide_validation(..., "OVERRIDE", reason="integration test")`（STOP のとき）か `"CONTINUE"` で AI_VALIDATED にする。
   - `run_state_update`（実際の Claude）。期待：Job が WAITING_HUMAN、提案が pending、`checks` に FAIL がない。
   - `approve_state`。期待：作業ブランチの chapter.yaml が HUMAN_APPROVED、summary.yaml・承認記録（Patch がある場合）が作業ブランチにあり、承認 commit の trailer が `NovUI-Job`・`Approval-Id`、Job が COMPLETED。
   - `final_approve`。期待：main の chapter.yaml が FINAL、`chapters/ch-001/summary.yaml` が main にある、merge commit に全 `NovUI-Job` と `Approval-Id`、作業ブランチ・worktree がない、`find_chapter_branch` が ChapterError。
   - 次を print する：各 Job の elapsed_seconds と `actual_model`、summary の内容（events・characters・foreshadowing・next_start_state）、Patch の target と operations（全文）、`checks` の全件、承認記録、`git log --graph --oneline` と merge commit のメッセージ、main の `characters/C001.yaml`・`foreshadowing/registry.yaml` の差分（FINAL の前後）。
2. **S2 不明な設定で WAITING_HUMAN（受入 2）**：
   - outline に、設定にない固有名（例：門番に名前を付ける必要がある出来事）が必要になる内容を書き、plan を fixture で置く（plan の scenes の summary に「門番が名乗る」を含める）。
   - `run_chapter_draft_job`。期待：WAITING_HUMAN（【要確認】あり）、requests.yaml に request、作業ブランチの chapter.yaml は PLAN_APPROVED のまま。AGY が【要確認】を出さなかった場合は、**設定を変えて合わせず**、結果を print して止める（試験は失敗ではなく `pytest.skip`。報告に書く）。
   - `resolve_request`（全件。decision は固定の文）→ requests.yaml に resolution が追記される。`redraft`。期待：古い作業ブランチが消え、新しい draft Job が実行される（COMPLETED または再度 WAITING_HUMAN。どちらでも可。結果を print）。
3. 結合試験の最後に、`ls -la ~/.local/share/novui-itest` と `podman ps -a` に残りがないこと（既存の結合試験と同じ後始末）。

## 2-C4 の作業の手順

1. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る。**
3. 作業 11・12 のうち試験以外を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする。
4. 結合試験を実行する（既存の分も含む）。失敗したら直さずに止めて報告する。

   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=gemini-3.8-flash-high .venv/bin/python -m pytest -s -m integration tests/integration
   ```
5. 残存確認（`ls -la ~/.local/share/novui-itest`、`podman ps -a --filter name=novui-itest --format '{{.Names}}'`）。
6. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase2: 2-C4 state update and final approval CLI, integration tests
   ```
7. `git push origin phase2/chapter-flow`（2-C1 と同じ規則）。
8. 報告して止まる。

---

## 報告の内容（各段階共通）

1. 変更・作成したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力
3. （2-C4 のみ）結合試験の出力の全文（print を含む）と、残存確認の結果
4. **既存ファイルを変更していないことの確認**：`git diff --stat <段階の開始時の HEAD> -- src/novui schemas prompts docs` で、許可範囲のファイル以外に差分がないこと
5. 指示どおりにできなかった点、指示と異なる実装をした点、判断に迷った点（なければ「なし」）
6. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
