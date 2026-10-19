# AGYへの作業指示：Phase 3-B setting_change と影響範囲分析、作業ブランチの追従

**この指示書は 3-B の全体を定める。3-C 以降（直接編集、外部変更、push）はこの指示書の範囲外。先取りして書いてはならない。**

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストを実行し、phase3/state-git branch に commit・push します。
設計の判断はしません。本指示・`docs/phase3/phase3b-design.md`・`docs/phase3/phase3-plan.md`・仕様書が矛盾する場合、または決まっていない判断が必要になった場合は、作業を止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase3/phase3b-design.md`（全体。特に §3、§4、§7）
* `docs/phase3/phase3-plan.md`（§3 の 3-B、§6）
* `docs/novel-system-spec-v0.5.2.md` の §8.1、§13.3、§15、§17.2
* `src/novui/` の次のファイル：`routingjob.py`、`jobqueue.py`、`stateupdate.py`（構成の手本）、`proposals.py`（保存の手本）、`statecheck.py`（`check_patch_target`、`dry_run_patch`、`pointer_hints`、`next_knowledge_number`）、`statepatch.py`、`approvals.py`、`protectaudit.py`、`chapters.py`（`review_marked`、`mark_review_required`）、`finalize.py`（`cycle_info`、`final_approve`）、`validatejob.py`（`_check_draft_consistency`）、`workrepo.py`（`commit_all`、`merge_job_branch`、`chapter_branches`）、`planjob.py`（`collect_plan_context`）、`foreshadowcheck.py`、`flagcheck.py`、`semantics.py`、`claude_output.py`、`claudejob.py`（`run_claude_call`）、`cli.py`
* 新しい schema（Claude が用意した）：`schemas/setting_change_plan.schema.json`、`schemas/impact_analysis.schema.json`、`schemas/setting_proposal.schema.json`。変更した schema：`schemas/queue.schema.json`（job_type に `setting_change`、chapter_id に null）
* 新しいプロンプト（Claude が用意した）：`prompts/claude/instruction_routing_setting.md`、`setting_change_plan.md`、`setting_patch.md`、`setting_patch_character.md`、`setting_patch_registry.md`、`impact_analysis.md`。`{{…}}` の値は下の各節のとおり。
* 既存の `tests/test_routingjob.py`（あれば）、`tests/test_jobqueue.py`、`tests/test_stateupdate.py`、`tests/test_finalize.py` と、それらのテストの用意（fixture、偽の Claude）

## 書込みの許可範囲

```text
許可：
  src/novui/settingchange.py（新規。setting_change Job、提案、承認・却下）
  src/novui/settingcheck.py（新規。setting_patch の範囲の検査と、設定全体の検査）
  src/novui/impact.py（新規。影響範囲分析。3-C・3-D でも使う）
  src/novui/follow.py（新規。作業ブランチの追従）
  src/novui/routingjob.py（scope setting の追加だけ）
  src/novui/jobqueue.py（setting_change の追加と、chapter_id が null の item の扱いだけ）
  src/novui/claude_output.py（CLAUDE_OUTPUT_TYPES に setting_change_plan・impact_analysis を足すだけ）
  src/novui/semantics.py（check_setting_change_plan・check_impact_analysis・check_setting_proposal の追加と、SEMANTIC_CHECKS への登録だけ）
  src/novui/stateupdate.py（run_state_update の始めの追従の呼び出しと、approve_state の無効の検査の追加だけ）
  src/novui/finalize.py（cycle_info の base の変更だけ）
  src/novui/validatejob.py（_check_draft_consistency の追従の扱いだけ）
  src/novui/cli.py（instruct の scope setting、approve-setting・reject-setting・setting-proposal の追加、表示の追加だけ）
  tests/**（追加と、既存テストの必要最小限の修正。既存テストの期待値を変えて通してはならない）
  .venv/**（Git 管理外）

禁止：上記以外すべて。次を含む。
  docs/、prompts/、schemas/、spike/、tools/、container/、templates/、.git/、.gitignore、pyproject.toml
  src/novui の上記以外の既存ファイル（approvals.py、protectaudit.py、chapters.py、workrepo.py、statecheck.py、
  states.py、editjob.py、planjob.py を含む）
```

prompts/・schemas/ に足りないものや誤りがあれば、変えずに報告してください。

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由。ただし NOVUI_INTEGRATION=1 は付けない）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase3/state-git
```

Phase 1・2 の禁止事項はすべて引き続き有効です（ログイン、OAuth の更新、アカウントの切替、token への接触を含む）。3-B に結合試験はありません（結合試験は 3-E で Human の指示により行う）。実物の AGY・Claude を呼ばないでください。テストの Claude はすべて偽物（既存テストの `claude_runner` の差し替えと同じ方法）にしてください。

## 共通の定義

* **設定の対象（setting target）**：`characters/<ID>.yaml`（main にすでにあるファイル）と `foreshadowing/registry.yaml`。これ以外のパスは、setting_change では変えられない。
* **設定の Context**：`collect_plan_context(main, <任意の章>)` から、章ごとの summary.yaml を除いたもの（project.yaml、rules/、chapters-order.yaml、world/、characters/、foreshadowing/registry.yaml、plot/timeline.yaml、台本の形式なら背景とフラグ）。章の指定が無くても使えるように、settingchange.py に `setting_context(repo) -> list[str]` を作る（存在するファイルだけ、collect_plan_context と同じ順）。
* setting_change Job は**常に main を読む**（`work.path`、`ensure_main_ready` の後）。作業ブランチは読まない。

## 3-B1：振り分けの scope setting と、キュー

### routingjob.py

* `ROUTING_SCOPES` に `"setting"` を足す。`ROUTABLE_JOBS` に `"setting_change"` を足す。
* `run_routing_job` の `chapter_id` を `str | None` にする。None は scope setting のときだけ許す（他の scope で None なら `ValueError`）。scope setting では anchor を取らない。
* scope setting のとき：
  - 章の状態は読まない。`base` は `{"ref": "main", "commit": <main の HEAD>}`。章が指定されていれば、その章が chapters-order.yaml にあることだけ確かめる（無ければ `ChapterError`）。
  - Context は `setting_context(main)`。章が指定されていれば、その章の plan.yaml・outline.md を（あれば）足す。
  - プロンプトは `instruction_routing_setting`。値：`instruction_id`、`instruction`、`chapter_id_value`（章があれば `` `ch-003` ``、無ければ `` `null` ``）、`chapter_note`（章があれば `「この指示は章 ch-003 を見ているときに出されました。chapters/ch-003/plan.yaml がその章の執筆計画です。」`、無ければ空文字列）。
  - Controller の値で上書きする項目は今と同じ（instruction_id、scope、chapter_id、instruction）。
  - 検査（`routing_jobs`）：scope_mismatch が false で questions が空なら、proposed_jobs は `setting_change` ちょうど1件でなければ FAIL。scope_mismatch が true なら proposed_jobs は空でなければ FAIL。
  - scope_mismatch が true のときは、questions があるときと同じく status `questions`、Job は WAITING_HUMAN（理由：`scope mismatch: <scope_mismatch_reason>`）。
* scope setting 以外の scope で `setting_change` が提案されたら FAIL（`check_proposed_jobs` の今の文の形で）。
* `routing_items`：`setting_change` は `new_item("setting_change", doc["chapter_id"], instruction=<今と同じ text>, source=<今と同じ>, instruction_id=ins)`。
* `approve_routing`：scope setting のときは、`base.commit` と今の main の HEAD を比べる（違えば今と同じ文の形で拒否）。作業ブランチは捨てない。
* `_print_routing`（cli.py）：chapter_id が None なら `for work` と表示する。

### jobqueue.py

* `QUEUE_JOB_TYPES` に `"setting_change"` を足す。`new_item` の chapter_id を `str | None` にする。
* `_run_item`：`setting_change` は `run_setting_change_job(settings, work, instruction_id=item["instruction_id"], instruction=item["instruction"], chapter_id=item["chapter_id"], claude_runner=claude_runner)`。
* `run_queue` の表示で chapter_id が None のときは `-` と出す。

## 3-B2：setting_change Job（settingchange.py、settingcheck.py、impact.py）

### run_setting_change_job

```python
def run_setting_change_job(settings, work, *, instruction_id: str, instruction: str,
                           chapter_id: str | None = None, job_id: str | None = None,
                           claude_runner: ClaudeRunner = run_claude) -> dict[str, Any]
```

構成は `run_state_update` にならう（Job 記録の作成、LOCK_ACQUIRED、`run_claude_call` による各段の呼び出し、`call_failed` と同じ失敗の扱い、例外時の CHECK_FAILED）。job_type は `setting_change`、Job 記録の chapter_id は引数のまま（None 可）、`instruction_id` を記録に入れる。`record["base_commit"]` は main の HEAD。

1. **方針**（expected_type `setting_change_plan`、log `<job>-plan`、check `claude_setting_change_plan`）
   * Context：`setting_context(main)`。プロンプト `setting_change_plan`。値：`instruction_id`、`instruction`、`allowed_targets`（設定の対象のうち main にあるものを `* <path>` で1行ずつ）。
   * 出力の instruction_id は Controller の値で上書きする。
   * 検査 `setting_plan`（FAIL で Job FAILED）：targets の target が設定の対象で main にある、targets に重複が無い、`new_foreshadowing > 0` なら targets に `foreshadowing/registry.yaml` がある、questions が空なら targets が1件以上。
   * questions があれば、方針を Job 記録の `setting_questions` に入れ（targets は捨てる）、Job を WAITING_HUMAN（理由 `questions: <件数>`）にして終える。提案は作らない。Human は質問に答えた指示を出し直す（この Job は cancel-job で閉じる。cancel-job が setting_change を受け付けなければ、受け付けるように直してよい）。
2. **Patch**（targets ごとに1回。expected_type `state_patch`、log `<job>-patch-<n>`、check `claude_setting_patch_<n>`）
   * プロンプト `setting_patch`。値：`target`、`instruction_id`、`instruction`、`change`（方針のその target の change）、`base_hash`（main のファイルの `file_sha256`）、`pointer_hints`（`pointer_hints(target, doc_before)`）、`target_rules`（下）、`noop_example`（人物なら `` `{"op": "test", "path": "/id", "value": "<ID>"}` ``、伏線なら `` `{"op": "test", "path": "/0/id", "value": "<最初の伏線の ID>"}` ``。伏線が0件なら `` `{"op": "test", "path": "", "value": []}` ``）。
   * `target_rules`：人物なら `setting_patch_character`（値 `next_knowledge_id`：`K` + `next_knowledge_number(main の全人物)` + これまでの Patch で足した knowledge の数、3桁0埋め）。伏線なら `setting_patch_registry`（値 `new_ids_rule`：新しい伏線が0件なら `この変更では、新しい伏線は追加しません。`、n 件なら `` 新しい伏線を n 件、`/-` への `add` で追加します。ID は順に F009, F010, … を使ってください（この順に、ほかの ID は使えません）。 ``。ID は registry の最大の番号の次から、`F` + 3桁以上0埋め）。
   * base_hash の写し間違いは、state_update と同じく直して WARNING `base_hash_corrected`。
   * 検査（どれかが FAIL なら Job FAILED）：`check_patch_target`、`check_setting_patch_policy`（下）、`dry_run_patch`、`check_setting_result`（下）。
   * 変更の無い Patch（`is_noop_patch`）は除き、check `patch_noop` を PASS で残す。すべてが変更なしなら、Job FAILED（理由 `no setting change`）。
3. **影響範囲分析**（`analyze_impact`、下）。
4. **提案の保存**：`<data_dir>/works/<key>/setting_proposals/<instruction_id>.yaml` に、`setting_proposal` の schema と `check_setting_proposal` で検査してから書く。`patches[].sha256` は `patch_sha256(patch)`。`base_commit` は main の HEAD（Job の始めに読んだもの。Job の間に main が動いていたら、保存せずに Job FAILED、理由 `main moved during setting_change`）。status `pending`。
   * 同じ対象（targets の集合が1つでも重なる）の pending の提案があれば、それを `superseded`（status_reason `superseded by <instruction_id>`）にする。
   * main には何も書かない。Job の後に main の worktree に変更があれば、state_update の `worktree_clean` と同じく戻して FAIL。
5. Job を COMPLETED にする（`CHECK_PASSED`、`needs_validation=False`。phase3b-design §7.1）。

### settingcheck.py

```python
def check_setting_patch_policy(patch, doc_before, *, target: str, new_foreshadow_ids: Sequence[str]) -> CheckResult
def check_setting_result(repo: Path, target: str, doc_before, doc_after) -> list[CheckResult]
```

* `check_setting_patch_policy`（name `setting_patch_policy`、問題があれば FAIL）：
  - target が設定の対象でなければ FAIL。
  - 人物：`/id` への replace・remove、空 path（文書全体）への replace・remove は FAIL。
  - 伏線：トップレベルの要素（`/<添字>`）の remove・replace は FAIL。既存の要素の `/<添字>/id` の replace・remove は FAIL。トップレベルへの add は `/-` だけで、value の id は `new_foreshadow_ids` を順に使っていなければ FAIL（数が合わない、順が違う、重複も FAIL）。
* `check_setting_result`（Patch を当てた後の doc で）：
  - name `setting_schema`：schema（`character`／`registry`）と semantics。問題があれば FAIL。人物の `id` がファイル名と合わなければ FAIL。
  - 伏線のとき、name `foreshadow_positions`・`foreshadow_order`：`check_foreshadow_positions`・`foreshadow_order_issues` を before と after の両方にかけ、**after で新しく生じた問題だけ**を WARNING（chapters-order.yaml が読めなければ WARNING 1件）。
  - 台本の形式のとき、name `flag_conditions`：`check_new_flag_conditions` と同じ考え方で、after で新しく生じた問題だけを WARNING（`flag_check_args` には chapters-order.yaml の最後の章と、その章の plan（無ければ空の scenes）を渡す。渡し方が決められなければ、この検査を入れずに報告する）。

### impact.py

```python
def impact_chapters(repo: Path) -> list[tuple[str, str]]          # (chapter_id, "summary" | "plan")
def analyze_impact(settings, *, repo: Path, instruction: str, diff_text: str, call) -> tuple[list[dict], list[str], list[CheckResult]]
```

* `impact_chapters`：chapters-order.yaml の順に、chapter.yaml の state が FINAL で summary.yaml がある章は `summary`、それ以外で plan.yaml がある章は `plan`。どちらも無い章は入れない（phase3b-design 決定 3）。章の作業ブランチは見ない（main だけ）。
* `analyze_impact`：対象の章が0件なら Claude を呼ばずに `([], [], [CheckResult("impact_analysis", "PASS", ("no chapter to analyse",))])`。
  - そうでなければ、Context は変更の**後**の設定（呼び出しの時点で、Patch を当てた後の内容を一時ディレクトリに置いて読む。main の worktree に書いてはならない。一時ディレクトリは `settings.data_dir` の下に作り、終わったら消す）と、対象の章の summary.yaml または plan.yaml。
  - プロンプト `impact_analysis`。値：`instruction`、`diff`（対象ごとの unified diff を `dumps_yaml` した前後で作り、つないだもの）、`chapters`（`* ch-001（summary）` の形で1行ずつ）。`call` は呼び出し側（run_setting_change_job の `call` と同じもの）で、expected_type `impact_analysis`、log `<job>-impact`、check `claude_impact_analysis`。
  - 検査 `impact_refs`（FAIL）：affected の chapter_id が対象の章にあり、重複が無い。
  - 戻り値は (affected、notes、checks)。3-C・3-D でも使うので、setting_change に固有のもの（instruction_id など）に依存しないこと。

### 承認と却下

```python
def approve_setting(settings, work, instruction_id: str) -> dict[str, Any]   # 更新した提案を返す
def reject_setting(settings, work, instruction_id: str, reason: str) -> dict[str, Any]
def load_setting_proposal(settings, work_key, instruction_id) -> dict[str, Any]
```

`approve_setting`（phase3b-design §3.3）：

1. 提案が pending でなければ `ChapterError`。`ensure_main_ready(main)`。
2. 無効の検査：各 Patch の target の、今の main の `file_sha256` が base_hash と違えば、提案を `invalid`（status_reason に理由）にして `ProposalError`（`run instruct again` を含める）。各 Patch の `patch_sha256` が記録と違っても同じ。
3. 作業ブランチの検査：`chapter_branches(main)` の各ブランチについて、`git merge-base main <branch>` からそのブランチまでに変わったパスに、どれかの target があれば `ChapterError`（`<branch> already changed <target>; final-approve or discard <chapter> first`）。提案は pending のまま。
4. 影響章の振り分け：影響章のうち作業ブランチがある章と無い章に分ける。作業ブランチのある影響章は、`mark_review_required(..., code="setting_changed", detail=<detail>, branch_only=True)` を先に呼ぶ（ここで `ChapterError` なら、何も書かずに止まる）。detail は `<instruction_id>: <targets をカンマでつないだもの> changed`。
5. main への1つの commit：Patch を当てた設定ファイル、承認記録（`source: instruction`、`instruction_id`、`job_id: null`、`patch_sha256` は `patch_set_sha256`、targets、approved_by human。`build_approval` は job_id を必須にしているので、settingchange.py に同じ検査をする `build_instruction_approval` を書く。schema と `check_approval` を通すこと）、作業ブランチの無い影響章の chapter.yaml（`review_marked` で作り、schema と `check_chapter` で検査）。`reserve_approval_id(settings, work)` で id を取る。subject は `setting change <instruction_id>`、trailer は `Approval-Id: <id>` だけ（`NovUI-Job` は付けない）。`commit_all` を使う（3-A の検査が承認記録を確かめる）。失敗したら書いたファイルを戻す（state_update の `_restore_paths` と同じ考え方）。
6. 提案を `approved`（approval_id、commit、decided_at）にする。setting_change Job の記録は変えない（すでに COMPLETED）。

`reject_setting`：pending でなければ `ChapterError`。reason は必須。`rejected` にする。

## 3-B3：作業ブランチの追従（follow.py、stateupdate.py、finalize.py、validatejob.py）

phase3b-design §4、§7.2 のとおり。

### follow.py

```python
def follow_main(settings, work, chapter_id: str) -> str | None   # rebase した後の作業ブランチの HEAD、しなかったら None
```

1. `info = cycle_info(..., require_worktree=True)`。`mb = git merge-base main <branch>`。main の HEAD が mb なら None。
2. 作業ブランチに紐づく Job 記録に、state が RUNNING・CHECKING・VALIDATING・WAITING_HUMAN のものがあれば `ChapterError`（何もしない）。その章に pending の state_update の提案があれば `ChapterError`。
3. 作業ブランチが変えたパス（`mb..branch`）と、main が変えたパス（`mb..main`）が重なれば `FollowConflict`（`ChapterError` の子クラス。`paths` に重なったパス）を投げる。rebase しない。
4. 作業ブランチの worktree で `git rebase main` を、Controller の git の名前・メールで行う（`-c user.name=… -c user.email=…`）。失敗したら `git rebase --abort` して `ChapterError`。rebase の後、worktree に変更が無く、ブランチの HEAD の親を辿ると main の HEAD に着くことを確かめる。
5. 戻り値は新しい HEAD。

### run_state_update

始め（AI_VALIDATED の確認と pending の確認の後、Job 記録を作る前）に `follow_main` を呼ぶ。

* `FollowConflict` のとき：Job 記録を作り、check `follow_main` を FAIL（details に重なったパス）で残して、Job を WAITING_HUMAN（理由 `main changed <paths> that the job branch also changed`）にして返す。Human の手段は 3-C・3-D で用意する。今は discard だけ。
* rebase したときは、Job 記録の checks に `follow_main` を PASS（details に `rebased onto <main の HEAD の先頭12文字>`）で残す。
* `ChapterError`（それ以外）はそのまま投げる。

### approve_state

無効の検査に1つ足す：提案の各 Patch の target について、main で `git merge-base main <branch>` から main の HEAD までに変わっていれば invalid（理由 `<target> changed on main since the job branch; run state-update again`）。

### cycle_info

`JobWorktree` の base を、draft の記録の `base_commit` ではなく `git merge-base main <branch>` にする。draft の記録の `base_commit` が branch の祖先であることの検査はそのまま残す。`CycleInfo.draft_record` は変えない。

### _check_draft_consistency

Context の hash の検査で、draft の `base_commit` から `git merge-base main <branch>` までに main で変わったパスは、hash の違いを details に入れない。代わりに戻り値で分かるようにし（関数の形を変える場合は、呼び出し側をすべて直す）、validate と state_update の Job 記録に check `context_followed_main`（WARNING、details にパス）を残す。違いが無ければ check は残さない。

## テスト

* 振り分け：scope setting で章あり・章なし、setting_change 1件の承認でキューに入る、scope_mismatch・questions で承認できない、setting_change を他の scope で出すと FAIL、main が進んだ後の approve-routing の拒否。
* setting_change Job：人物1件、伏線（status を cancelled、planned_resolution の変更）、新しい伏線の追加（ID が順どおりでないと FAIL）、`/id` の変更で FAIL、schema に合わない結果で FAIL、questions で WAITING_HUMAN、すべて変更なしで FAILED、同じ対象の古い提案が superseded、main に何も書かないこと。キューから実行して COMPLETED になり、キューが次へ進むこと。
* 影響範囲分析：対象の章の選び方（FINAL は summary、plan だけの章、outline だけの章は入らない）、0件で Claude を呼ばない、存在しない章を返すと FAIL、一時ディレクトリが残らないこと、main の worktree が変わらないこと。
* approve-setting：main の commit が1つで、承認記録（job_id null）、設定、作業ブランチの無い影響章の要確認が入る。作業ブランチのある影響章は、そのブランチに要確認が立つ。main の対象が変わった後は invalid。作業ブランチがすでに対象を変えていると拒否。commit の後に `audit_changes`（allow_edit=False ではなく commit_all の検査）で通ること。reject-setting。
* 追従：作業ブランチの作業中（AI_VALIDATED）に setting_change を承認 → state-update で rebase され、Context の hash の違いが `context_followed_main` の WARNING になり、提案を承認し、final-approve の merge が overlap で止まらずに通ること（**この一連を1つのテストにする**）。重なるときは WAITING_HUMAN で rebase しない。pending の提案がある間は rebase しない。approve_state の時点で main の対象が変わっていれば invalid。final_approve の監査（`audit_protected_changes`）が rebase の後に main の commit を対象にしないこと。
* CLI：`instruct --scope setting`（`--chapter` 無し）、`approve-setting --work <key> --id <INS>`、`reject-setting --work <key> --id <INS> --reason <text>`、`setting-proposal --work <key> --id <INS>`（提案の対象、各 Patch の reason と操作、影響章と理由、notes、status を表示）。
* 既存のテストが、期待値を変えずに通ること。

## 報告

`docs/phase3/reports/3b.md` を新規に作り（この1ファイルだけは docs/ の下に書いてよい）、次を書いて commit・push する。

* 実装したもの（ファイルと関数）
* テストの結果（全体の件数：passed・skipped・failed）
* 指示どおりにできなかった点（無ければ「なし」）
* 判断に迷った点
