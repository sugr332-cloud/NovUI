# AGYへの作業指示：Phase 2-B plan に基づく執筆と検証

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストと結合試験を実行し、phase2/chapter-flow branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。
結合試験で想定と違う結果が出た場合は、コードや試験を変えて辻褄を合わせず、その時点で止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase2/phase2-plan.md`（§2 の表と §4）
* `docs/novel-system-spec-v0.5.2.md` の §6.5.1、§7.1、§7.2、§11、§16
* `prompts/agy/preamble.md`、`prompts/agy/draft.md`、`prompts/claude/preamble.md`、`prompts/claude/integrity_review.md`、`prompts/claude/writing_review.md`（読み込んで使う。変更しない）
* 2-A までに作成した `src/novui/`、`tests/`

## 書込みの許可範囲

```text
許可：
  src/novui/prompt.py（本指示の変更のみ）
  src/novui/jobrunner.py（本指示の変更のみ）
  src/novui/cli.py（サブコマンドの追加のみ）
  src/novui/ の新規ファイル：cycle.py、mechanical.py、draftflow.py、validatejob.py
  tests/**（追加と、既存テストの必要最小限の修正）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。終了時に削除する）
禁止：上記以外すべて（docs/、prompts/、schemas/、templates/、spike/、tools/、container/、.git/、.gitignore、pyproject.toml、src/novui のその他のファイルを含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
ls -la ~/.local/share/novui-itest
podman ps -a --filter name=novui-itest --format '{{.Names}}'
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase2/chapter-flow
```

Phase 1・2-A の禁止事項はすべて引き続き有効です。

## 背景：書き足してよい範囲（Human の決定）

AI は、情景・天候・感覚の描写、人物の表情・しぐさ・内心、名前のない端役とその短い台詞、場面のつなぎを、設定になくても書いてよい。固有名、人物の過去・経歴・能力・人間関係、世界のしくみ・歴史・規則は、設定になければ決めない（AGY は `【要確認：…】` を書く）。この方針は prompts/ の preamble に反映済みです。

## 作業 1：src/novui/prompt.py の変更

* `load_prompt_template` に引数 `kind: str = "claude"` を追加し、`PROMPTS_DIR/<kind>/<name>.md` を読むようにする。kind は `claude` か `agy`（それ以外は ValueError）。既存の呼び出しは変えない。
* AGY の `PREAMBLE` 定数を、`prompts/agy/preamble.md` の内容（末尾の改行を除く）をモジュールの読み込み時に読んだものにする。名前 `PREAMBLE` は残す。
* tests/test_prompt.py の既存テストは PREAMBLE を参照しているので、そのまま通ることを確認する。PREAMBLE が prompts/agy/preamble.md と一致するテストを加える。

## 作業 2：src/novui/cycle.py（章サイクルの作業ブランチ。phase2-plan §2）

```python
class CycleError(Exception)

@dataclass(frozen=True)
class Cycle:
    chapter_id: str
    job_id: str          # 作業ブランチを作った draft Job の job_id
    jw: JobWorktree      # branch、path、base_commit（base_commit は git merge-base main <branch>）

def find_cycle(settings: Settings, work: WorkInfo, chapter_id: str) -> Cycle | None
def read_branch_chapter_meta(cycle: Cycle) -> dict
def commit_on_cycle(settings: Settings, cycle: Cycle, subject: str, trailers: Sequence[tuple[str, str]],
                    files: Mapping[str, bytes]) -> str
def transition_on_cycle(settings: Settings, cycle: Cycle, event: ChapterEvent, *, subject: str,
                        trailers: Sequence[tuple[str, str]], extra_files: Mapping[str, bytes] | None = None,
                        last_job: str | None = None, validation_skipped: bool | None = None) -> str
def discard_cycle(settings: Settings, work: WorkInfo, chapter_id: str) -> None
```

* `find_cycle`：`chapter_branches(repo, chapter_id)` が空なら None。2本以上なら CycleError。1本なら、branch 名 `ai/<chapter_id>/<job_id>` から job_id を取り、path は `<worktree_root>/<work_key>/<job_id>`（存在しなければ CycleError）、base_commit は `merge-base main <branch>`。
* `read_branch_chapter_meta`：worktree の chapters/<id>/chapter.yaml を read_chapter_meta で読む（None なら CycleError）。
* `commit_on_cycle`：worktree に files を書き（is_safe_relpath、worktree の外に出ないこと）、`commit_all(worktree, subject, trailers, ...)` の commit を返す（変更なしなら CycleError）。
* `transition_on_cycle`：worktree の chapter.yaml について next_chapter_state で次状態を求め、state・last_job・validation_skipped（与えられた場合）を更新し、schema と semantics で検証し、extra_files と一緒に commit_on_cycle する。
* `discard_cycle`：作業ブランチと worktree を削除する（remove_job_worktree、delete_branch=True）。main は変えない（作業ブランチ上の状態は main に入っていないため、章は main 上の状態＝PLAN_APPROVED に戻る）。

## 作業 3：src/novui/mechanical.py（機械検査。§6.5.1、§11.3）

すべて CheckResult を返す。

```python
SCENE_MARKER_RE: re.Pattern[str]    # r"^<!-- scene: (S[0-9]+) -->$"（行全体に一致）

def strip_scene_markers(text: str) -> str
def check_scene_markers(text: str, scene_ids: Sequence[str]) -> CheckResult
def check_prohibited_phrases(text: str, phrases: Sequence[str]) -> CheckResult
def check_repeated_endings(text: str, threshold: int) -> CheckResult
def check_char_count(text: str, min_chars: int, max_chars: int) -> CheckResult
```

* `strip_scene_markers`：SCENE_MARKER_RE に一致する行を取り除いた本文。
* `check_scene_markers`（name `scene_markers`）：本文の各行のうち SCENE_MARKER_RE に一致するものの ID を順に取り出し、scene_ids と完全に一致（同じ ID が同じ順で各1回）すれば PASS。違えば FAIL、detail に `expected=[...] actual=[...]`。さらに、最初の空でない行が区切りの行でなければ FAIL（detail `text_before_first_marker`）。
* `check_prohibited_phrases`（name `prohibited_phrases`）：strip_scene_markers 後の本文に phrases のどれかが含まれれば WARNING、detail に `<語>: <回数>`。
* `check_repeated_endings`（name `repeated_endings`）：strip_scene_markers 後の本文を「。」「！」「？」の直後で文に分け、各文の末尾（終わりの記号と閉じ括弧「」』）を除いた最後の2文字）を比べ、同じ末尾が threshold 回以上連続する箇所があれば WARNING、detail に `<末尾>: <連続回数> at sentence <開始番号>`。空白と改行は比較の前に除く。
* `check_char_count`（name `char_count`）：strip_scene_markers 後の本文で checks.check_char_range と同じ判定（範囲外は WARNING）。

## 作業 4：src/novui/jobrunner.py の変更

* DraftJobRequest に次を追加する（既定値で既存の呼び出しは変わらないこと）：
  - `scene_ids: tuple[str, ...] | None = None`
  - `update_chapter_state: bool = False`
* 検査（手順13）に追加：parse に成功し scene_ids が None でなければ、`check_scene_markers(text, scene_ids)` を加える（FAIL なら Job は FAILED。場面の区切りは後の検査の前提のため）。target_chars の検査は、scene_ids があるときは `mechanical.check_char_count`（区切りを除いて数える）を使う。
* update_chapter_state が True のとき：
  - 許可パスに `chapters/<chapter_id>/chapter.yaml` を加える。
  - FAIL がなく commit する前に、worktree の chapter.yaml を DRAFT_COMPLETED で遷移させ（next_chapter_state）、last_job を job_id にして、schema・semantics で検証して書く。markers がある場合も DRAFTED にする（判断要求は requests.yaml で管理する）。
  - これにより、draft.md・requests.yaml・chapter.yaml が1つの commit に入る。
* 既存のテスト（test_jobrunner.py）がすべて通ること。

## 作業 5：src/novui/draftflow.py（plan に基づく draft Job）

```python
def collect_draft_context(repo: Path, chapter_id: str, plan: dict, *, include_past_drafts: bool) -> list[str]
def run_chapter_draft(settings: Settings, work: WorkInfo, chapter_id: str, run_lock: RunLock, *,
                      model: str | None = None, job_id: str | None = None,
                      container_runner: ContainerRunner = run_container) -> dict
```

* `collect_draft_context`：存在するファイルだけを、重複なく、次の順で並べる。rules/style.md、rules/prohibited.yaml、plan の context.settings のパス（plan の順）、plan の scenes に登場する各人物の characters/<ID>.yaml（ID の昇順）、foreshadowing/registry.yaml、plan の context.past_summaries の各章の summary.yaml（plan の順）、include_past_drafts が True なら plan の context.past_drafts の各章の draft.md（plan の順）、chapters/<id>/outline.md、最後に chapters/<id>/plan.yaml。
* `run_chapter_draft`：
  1. ensure_main_ready。main の chapter.yaml が PLAN_APPROVED でなければ ChapterError。find_cycle が None でなければ ChapterError（既に作業ブランチがある）。
  2. main の plan.yaml を読み、schema `plan` で検証する。
  3. model は None なら `resolve_model(settings, "draft", work_key=work.work_key)`。job_id は None なら next_job_id。
  4. instruction は `load_prompt_template("draft", kind="agy", chapter_id=..., min_chars=str(plan.target_chars.min), max_chars=str(plan.target_chars.max))`。
  5. DraftJobRequest（context_paths=collect_draft_context(..., include_past_drafts=True)、target_chars=(min, max)、scene_ids=plan の scenes の id の順、update_chapter_state=True、needs_validation=False）で run_draft_job を呼ぶ。
  6. 結果が WAITING_HUMAN で、history の最後の reason が `prompt_too_large` で始まり、かつ plan の past_drafts が空でない場合に限り、新しい job_id で `include_past_drafts=False` にして1回だけ再実行する（§16.1：過去の本文を外し要約で代える）。
  7. 最後の記録を返す。

## 作業 6：src/novui/validatejob.py（validate Job。§11）

```python
VALIDATE_JOB_TYPE = "validate"

def collect_validate_context(worktree: Path, chapter_id: str, plan: dict) -> list[str]
def run_mechanical_checks(worktree: Path, chapter_id: str, plan: dict) -> list[CheckResult]
def run_validate(settings: Settings, work: WorkInfo, chapter_id: str, *, job_id: str | None = None,
                 claude_runner: ClaudeRunner = run_claude) -> dict
def decide(settings: Settings, work: WorkInfo, chapter_id: str, action: str, reason: str | None) -> dict
def skip_validation(settings: Settings, work: WorkInfo, chapter_id: str, reason: str) -> str
def unresolved_requests(worktree: Path, chapter_id: str) -> list[dict]
```

* `unresolved_requests`：worktree の requests.yaml（なければ空）の要素のうち、type が request で、それを request_index で指す resolution がないもの。
* `collect_validate_context`：collect_draft_context と同じ順（past_drafts は含めない）で worktree から集め、最後に chapters/<id>/draft.md を加える。
* `run_mechanical_checks`：worktree の draft.md に対し、check_scene_markers（plan の scenes の id）、check_char_count（plan の target_chars）、check_prohibited_phrases（rules/prohibited.yaml の phrases）、check_repeated_endings（同 repeated_ending_threshold）をこの順で行う。rules/prohibited.yaml がなければ後の2つは行わない。
* `run_validate`：
  1. find_cycle が None なら ChapterError。作業ブランチの chapter.yaml が DRAFTED でなければ ChapterError。unresolved_requests が1件以上なら ChapterError（判断要求が解決されるまで検証しない）。
  2. job_id は None なら next_job_id。worktree の plan.yaml を読み検証する。
  3. mechanical = run_mechanical_checks。
  4. integrity：`run_claude_job`（job_type `validate`、root=worktree、context_paths=collect_validate_context、task_text=`load_prompt_template("integrity_review", chapter_id=...)`、expected_type `integrity_review`、model=settings.claude_model、job_id=<job_id>）。
  5. integrity が None（Claude の Job が FAILED）なら、その記録を返す（§11.6：Human が再試行か skip_validation を選ぶ）。
  6. writing：同じく `writing_review`。job_id は `<job_id>` ではなく next_job_id で別の番号を取る（Job 記録が別になるため）。writing が None の場合は writing を null として続ける（文章レビューは Job を止めない。§11.2）。
  7. integrity の Job 記録（CHECKING のまま返ってくる）の checks に mechanical の各結果を加える。
  8. 総合判定：integrity.result が STOP なら STOP。そうでなく integrity.result が WARNING か、mechanical に WARNING か FAIL があれば WARNING。それ以外は PASS。
  9. review.yaml を `{chapter_id, job_id, base_commit: cycle.jw.base_commit, mechanical: [...], integrity, writing, decision: null}` で作り、schema `review` で検証する。
  10. PASS：`transition_on_cycle(VALIDATION_ACCEPTED, subject=f"validate {chapter_id} by {job_id}", trailers=[("NovUI-Job", job_id)], extra_files={review.yaml}, last_job=job_id)`。integrity の Job 記録を CHECK_PASSED（needs_validation=True）→ VALIDATING → VALIDATION_PASSED → COMPLETED にして保存。
  11. WARNING・STOP：`commit_on_cycle` で review.yaml だけを commit（章は DRAFTED のまま）。Job 記録を CHECK_PASSED（needs_validation=True）→ VALIDATING → VALIDATION_NEEDS_HUMAN → WAITING_HUMAN にして保存。
  12. integrity の Job 記録を返す。
* `decide`（§11.4）：
  - action は `CONTINUE`、`OVERRIDE`、`REQUEST_FIX`、`CANCEL` のいずれか（それ以外は ValueError）。OVERRIDE と REQUEST_FIX は reason が必須（空白のみも不可。ValueError）。
  - find_cycle、worktree の review.yaml を読む。review.yaml の job_id の Job 記録が WAITING_HUMAN でなければ ChapterError。
  - 総合判定（8 と同じ計算を review.yaml から行う）が STOP のとき、CONTINUE は ValueError（STOP は OVERRIDE でしか進めない）。
  - review.yaml の decision に `{action, reason, decided_at: now_iso()}` を入れる。
  - CONTINUE・OVERRIDE：transition_on_cycle(VALIDATION_ACCEPTED, extra_files={review.yaml}, subject=f"decide {action} {chapter_id}", trailers=[]) で AI_VALIDATED。Job 記録は HUMAN_CONTINUE または HUMAN_OVERRIDE（reason）で COMPLETED。
  - REQUEST_FIX：review.yaml だけを commit_on_cycle（章は DRAFTED のまま）。Job 記録は HUMAN_REQUEST_FIX（reason）で COMPLETED。修正 Job は 2-D で作る。
  - CANCEL：review.yaml だけを commit_on_cycle。Job 記録は HUMAN_CANCEL で CANCELLED。
  - 更新した Job 記録を返す。
* `skip_validation`（§11.6）：find_cycle、作業ブランチの chapter.yaml が DRAFTED であること。reason が空なら ValueError。transition_on_cycle(VALIDATION_SKIPPED, validation_skipped=True, subject=f"skip validation {chapter_id}: {reason}" の1行目、trailers=[]) の commit を返す。

## 作業 7：CLI の追加

| サブコマンド | 引数 | 動作 |
|---|---|---|
| draft | `--work <key> --chapter <id>` | run_chapter_draft（実行 lock は CLI の中で RunLock() を作る） |
| validate | `--work <key> --chapter <id>` | run_validate。総合判定、integrity の観点ごとの result と findings、mechanical の結果、writing の findings の件数を表示 |
| decide | `--work <key> --chapter <id> --action <action> [--reason <text>]` | decide |
| skip-validation | `--work <key> --chapter <id> --reason <text>` | skip_validation |
| discard | `--work <key> --chapter <id>` | discard_cycle |
| show（変更） | 既存 | `--chapter` 指定時、作業ブランチがあればその chapter.yaml の state も「作業中の状態」として表示する |

終了コードの規則は 2-A と同じ（FAILED は 2、WAITING_HUMAN は 3）。

## 作業 8：テスト

### 単体テスト（claude・podman は使わない）

* test_prompt.py：作業1。
* test_cycle.py：find_cycle（なし・1本・2本・worktree がない）、transition_on_cycle で作業ブランチの chapter.yaml だけが変わり main が変わらない、discard_cycle の後に find_cycle が None で main の chapter.yaml が PLAN_APPROVED のまま。
* test_mechanical.py：区切りの正常・順序違い・欠落・重複・余分・最初の区切りより前に本文がある場合。禁止語。文末の連続（threshold ちょうどで WARNING、1少なければ PASS、括弧の扱い、区切りの行を数えない）。文字数（区切りを数えない）。
* test_jobrunner.py に追加：scene_ids を与えて区切りが正しい・欠けている場合、update_chapter_state で chapter.yaml が DRAFTED になり draft.md と同じ commit に入る場合。
* test_draftflow.py（偽の container_runner と偽の resolve_model）：collect_draft_context の順序と重複除去。成功時に作業ブランチの chapter.yaml が DRAFTED、main は PLAN_APPROVED のまま。PLAN_APPROVED でない・既に作業ブランチがある場合の ChapterError。prompt_too_large のとき past_drafts を外して1回だけ再実行されること（偽の container_runner が呼ばれた回数と、2回目のプロンプトに過去の draft.md が含まれないこと）。
* test_validatejob.py（偽の claude_runner。integrity と writing で返す内容を切り替える）：
  - PASS：作業ブランチの chapter.yaml が AI_VALIDATED、review.yaml が schema に適合、Job 記録が COMPLETED。
  - integrity WARNING：DRAFTED のまま、Job 記録が WAITING_HUMAN。decide CONTINUE で AI_VALIDATED・COMPLETED。
  - integrity STOP：decide CONTINUE が ValueError、OVERRIDE（reason あり）で AI_VALIDATED、reason なしで ValueError。
  - mechanical の WARNING だけでも総合 WARNING。
  - REQUEST_FIX・CANCEL の遷移。
  - writing の Claude が失敗しても integrity が PASS なら AI_VALIDATED（writing は null）。
  - integrity の Claude が失敗したら Job は FAILED、章は DRAFTED、skip_validation で AI_VALIDATED かつ validation_skipped が true。
  - 未解決の requests があると ChapterError。
* test_cli.py に追加：draft、validate、decide、skip-validation、discard の終了コード。

### 結合試験（tests/integration/test_chapterflow_integration.py、`@pytest.mark.integration`）

実際の AGY と Claude を使う。一時領域に作品を作り、終了時に削除する。

1. **L1 plan → draft → validate**：
   - 2-A の K1 と同じ作品（world/setting.md、characters/C001.yaml、F001）を用意し、add_chapter（ch-001、outline は K1 と同じ）。
   - Claude のコストと時間を抑えるため、plan は Claude に作らせず、次の plan.yaml を作業の main に置いて `transition_on_main(PLAN_WRITTEN, extra_files={plan.yaml}, subject="plan ch-001 (fixture)", trailers=[])` → approve_plan で PLAN_APPROVED にする：
     - type plan、chapter_id ch-001、pov `三人称・C001 に寄り添う`、style_notes `["C001 の一人称は「俺」"]`、target_chars min 800・max 1600、context（past_summaries []、past_drafts []、settings ["world/setting.md"]）、scenes：S1（summary `カイが港町ミナトに着き、港の門へ向かう`、actions ["港に着く", "門へ向かう"]、characters ["C001"]、settings_used ["港町ミナト", "港の門"]、foreshadowing []）と S2（summary `門番がカイの持つ紋章に反応し、カイは理由が分からないまま門を通る`、actions ["門番が紋章に反応する", "カイが門を通る"]、characters ["C001"]、settings_used ["港の門"]、foreshadowing ["F001"]）、prohibitions `["紋章の意味を明かさない"]`、connection（from_previous `冒頭`、to_next `港町での行動へ`）
   - `select_model(settings, "draft", "gemini-3.8-flash-high")` の前に fetch_agy_models と save_catalog を行う。
   - run_chapter_draft。期待：記録が COMPLETED または WAITING_HUMAN（後者は【要確認】が出た場合）、checks に FAIL がない（scene_markers を含む）、作業ブランチの chapter.yaml が DRAFTED、main の chapter.yaml が PLAN_APPROVED。
   - WAITING_HUMAN（【要確認】あり）の場合は、validate の前に試験を止め、requests の内容を print して pytest.skip で終える（判断要求の解決は 2-C）。
   - run_validate。期待：記録が COMPLETED（AI_VALIDATED）か WAITING_HUMAN、review.yaml が schema に適合。
   - 次を print する：draft の run.elapsed_seconds、本文の文字数（区切りを除く）、本文の先頭 400 文字、mechanical の各結果、integrity の result と観点ごとの result・findings の message、writing の findings の件数と message、validate の総合判定。

## 作業の手順

1. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る。**
3. 作業1〜8（結合試験を除く）を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする。テストを通すために本指示の仕様を変えてはならない。
4. 結合試験を実行する（既存の分も含む）。失敗したら直さずに止めて報告する。
   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=gemini-3.8-flash-high .venv/bin/python -m pytest -s -m integration tests/integration
   ```
5. `ls -la ~/.local/share/novui-itest` と `podman ps -a --filter name=novui-itest --format '{{.Names}}'` で残存がないことを確認する。
6. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。
   ```text
   phase2: 2-B plan-based draft, mechanical checks, validate job and decisions
   ```
7. `git push origin phase2/chapter-flow`。「fetch first」等で拒否された場合に限り、`git pull --rebase origin phase2/chapter-flow` を1回だけ実行してから再度 push してよい（競合が出たら解決せずに止めて報告する）。
8. 報告して止まる。

## 報告の内容

1. 変更・作成したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力（単体）
3. 結合試験の出力の全文（print を含む）
4. 残存確認の結果
5. 指示どおりにできなかった点、指示と異なる実装をした点、判断に迷った点（なければ「なし」）
6. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
