# AGYへの作業指示：Phase 3-C Human の直接編集（edit、reopen、analyze-impact）

**この指示書は 3-C の全体を定める。3-D 以降（外部変更の検出、push）はこの指示書の範囲外。先取りして書いてはならない。**

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストを実行し、phase3/state-git branch に commit・push します。
設計の判断はしません。本指示・`docs/phase3/phase3c-design.md`・`docs/phase3/phase3-plan.md`・仕様書が矛盾する場合、または決まっていない判断が必要になった場合は、作業を止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase3/phase3c-design.md`（全体）
* `docs/novel-system-spec-v0.5.2.md` の §7.3、§14.2〜§14.4、§15
* `src/novui/` の次のファイル：`impact.py`、`settingchange.py`（`run_setting_change_job` の Claude の呼び出しの組み立て）、`chapters.py`（`review_marked`、`mark_review_required`、`subsequent_chapters`、`ensure_main_ready`、`read_chapter_meta`）、`protected.py`、`paths.py`、`workrepo.py`（`commit_all`、`create_job_worktree`、`chapter_branches`）、`draftjob.py`（`read_plan`、`collect_draft_context`、`run_chapter_draft_job`、`_mark_drafted_on_branch`）、`validatejob.py`（`_check_draft_consistency`）、`finalize.py`（`cycle_info`、`final_approve`）、`states.py`、`jobrecord.py`、`schema.py`、`semantics.py`、`flagcheck.py`、`mechanical.py`、`scriptcheck.py`、`cli.py`
* `schemas/common.schema.json`（job_type に `impact_analysis` を足した）

## 書込みの許可範囲

```text
許可：
  src/novui/directedit.py（新規。edit と reopen）
  src/novui/impactjob.py（新規。影響範囲分析の Job）
  src/novui/cli.py（edit・reopen・analyze-impact の追加だけ）
  tests/**（追加と、既存テストの必要最小限の修正。既存テストの期待値を変えて通してはならない）
  .venv/**（Git 管理外）

禁止：上記以外すべて。次を含む。
  docs/、prompts/、schemas/、spike/、tools/、container/、templates/、.git/、.gitignore、pyproject.toml
  src/novui の上記以外の既存ファイル（impact.py、settingchange.py、chapters.py、workrepo.py、draftjob.py、
  validatejob.py、finalize.py、states.py を含む）
```

既存のファイルを変えないと実現できない場合は、変えずに止めて報告してください。

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由。ただし NOVUI_INTEGRATION=1 は付けない）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase3/state-git
```

Phase 1・2 の禁止事項はすべて引き続き有効です（ログイン、OAuth の更新、アカウントの切替、token への接触を含む）。3-C に結合試験はありません。実物の AGY・Claude を呼ばないでください。テストの Claude はすべて偽物にしてください。

## 3-C1：影響範囲分析の Job（impactjob.py）

```python
def run_impact_job(settings, work, *, base: str, head: str, reason: str, job_id: str | None = None,
                   claude_runner: ClaudeRunner = run_claude) -> tuple[dict[str, Any], list[dict], list[str]]
```

* `base..head`（main の上の commit。`base` は `head` の祖先でなければ `ValueError`）で変わった**保護対象**のパスについて、変更の前（`base` の版）と後（`head` の版）の unified diff を作る（`git diff base head -- <パス>` でよい）。保護対象の変更が無ければ、Job 記録を作らずに `ValueError("no protected change between ...")` を投げる。
* Job 記録：job_type `impact_analysis`、chapter_id null、`base_commit` は `base`。LOCK_ACQUIRED から始め、Claude の呼び出しは `run_setting_change_job` の `call` と同じ組み立て（`run_claude_call`、log `<job>-impact`、record の cli・run・attempt・checks の更新、timeout と出力の取得失敗の扱い）。
* `analyze_impact(settings, repo=work.path, instruction=reason, diff_text=<diff>, call=<call>)` を呼ぶ。`head` が main の HEAD でなければ `ValueError`（analyze_impact は main の作業ツリーを読むため）。
* checks のどれかが FAIL、または Claude の出力が得られなければ Job FAILED（理由はそれぞれ `impact analysis check failed`、`claude unavailable` など state_update と同じ形）。
* 通れば、affected の章に `mark_review_required(settings, work, <章の list>, code="setting_changed", detail=<detail>)` で要確認を立てる（detail：`<reason の先頭60文字>: <affected のその章の reason の先頭100文字>` を章ごとに。章ごとに detail が違うので、章ごとに呼んでよい）。affected が空なら何も立てない。`mark_review_required` が `ChapterError` なら Job FAILED（理由 `review flags could not be set: <exc>`）。
* Job を COMPLETED（`CHECK_PASSED`、`needs_validation=False`）。記録の checks に `impact_affected`（PASS、details に `<章>: <reason>` の list）と、notes があれば `impact_notes`（WARNING、details に notes）を残す。
* 戻り値は（Job 記録、affected、notes）。

## 3-C2：edit（directedit.py）

```python
def direct_edit(settings, work, *, kind: str, note: str | None = None, run_impact: bool = True,
                claude_runner: ClaudeRunner = run_claude) -> dict[str, Any]
```

phase3c-design §2 のとおり。戻り値は `{"commit": <sha>, "paths": [...], "chapters_drafted": [...], "chapters_replanned": [...], "review_marked": [...], "impact_job": <job_id or None>, "impact_error": <str or None>}`。

1. kind は `typo` か `content`（それ以外は `ValueError`）。main が checkout されていなければ `ChapterError`。
2. 変更の一覧：`git status --porcelain=v1 -z --untracked-files=all`（rename を検出しない設定で）。変更が無ければ `ChapterError("nothing to edit")`。削除（D）があれば `ChapterError`（何も変えない）。
3. 対象の検査（どれかに当たれば、何も commit せずに `ChapterError`。メッセージに該当するパスを並べる）：
   * 直接編集の対象：`chapters/<章 ID>/draft.md`・`outline.md`・`plan.yaml`（章 ID は chapters-order.yaml にあるもの）、および `is_protected` が True で `.novui/` の下でないもの。それ以外のパスは対象外。
   * 安全でないパス（`is_safe_relpath` が False）は対象外。
   * 作業ブランチがある章（`chapter_branches`）の draft.md・outline.md・plan.yaml。
4. 内容の検査（FAIL なら何も commit せずに `ChapterError`）。変わった各ファイルを読み、次の対応で schema と semantics を検査する。対応の無い `.yaml` は YAML として読めることだけ、`.md` は UTF-8 で読めることだけを確かめる。
   * `project.yaml` → project、`chapters-order.yaml` → chapters_order、`characters/<ID>.yaml` → character（`id` がファイル名と一致）、`foreshadowing/registry.yaml` → registry、`rules/prohibited.yaml` → prohibited、`rules/script.yaml` → script_rules、`assets/backgrounds.yaml` → backgrounds、フラグの一覧のファイル（`flagcheck.load_flag_names` が読むパス）→ flags、`chapters/<ID>/plan.yaml` → plan。
   * 本文（draft.md）は、その章の plan.yaml の場面と `check_scene_markers` で照らし、FAIL なら commit しない（区切りの行を壊した編集を防ぐ）。
5. 章の扱い（kind が content のときだけ。typo は章の状態も要確認も変えない）：
   * draft.md を変えた章：chapter.yaml の state を `next_chapter_state(<今>, CONTENT_CHANGED)` で DRAFTED に（`InvalidTransition` なら `ChapterError`）、`review_marked(meta, "previous_chapter_changed", "<章> content edited", at)` も付ける。後続章（`subsequent_chapters`）は「要確認を立てる章」に入れる。
   * plan.yaml を変えた章（draft.md を変えていない）：state が PLAN_APPROVED なら PLANNED に（`PLAN_REJECTED` ではなく、直接 PLANNED に書く。遷移表に無いので、ここだけは `next_chapter_state` を使わず、理由をコメントに書く）。DRAFTED 以降なら状態を変えず、後続章を「要確認を立てる章」に入れる。PLANNED・OUTLINED は何もしない。
   * outline.md だけを変えた章：何もしない。
   * 「要確認を立てる章」（重複を除き、今回の編集で DRAFTED にした章は除く）：作業ブランチがある章は、commit の**前**に `mark_review_required(..., code="previous_chapter_changed", detail="<編集した章> content edited", branch_only=True)`。作業ブランチの無い章は、`review_marked` で作った chapter.yaml を同じ commit に入れる。
   * 書き換えた chapter.yaml は、schema と `check_chapter` で検査する。
6. 1つの commit：`git add -A` に当たる変更のすべて（3 で確かめたファイル）と、5 で書き換えた chapter.yaml。subject は `human edit (<kind>)`、note があれば `human edit (<kind>): <note の1行目>`。trailer は `NovUI-Edit: human-<kind>` だけ。`commit_all` を使う。失敗したら 5 で書いた chapter.yaml を元に戻す（Human の編集は戻さない）。
7. 保護対象が含まれ、`run_impact` が True なら、`run_impact_job(base=<commit の親>, head=<commit>, reason=<note か "human edit">)` を呼ぶ。例外または Job が COMPLETED でなければ、commit は戻さず、戻り値の `impact_error` に理由を入れる（決定 4）。

## 3-C3：reopen（directedit.py）

```python
def reopen_chapter(settings, work, chapter_id: str) -> dict[str, Any]   # 作った Job 記録
```

phase3c-design §3 と §6.2 のとおり。

1. `ensure_main_ready`。章の main の chapter.yaml が DRAFTED で、作業ブランチが無く、`chapters/<章>/draft.md` と plan.yaml が main にあること。どれかが違えば `ChapterError`。
2. 新しい Job ID で、draft Job と同じ規則の作業ブランチと worktree を `create_job_worktree` で作る。
3. Context：`collect_draft_context(<worktree>, chapter_id, read_plan(...))` のパスと、その worktree での sha256。
4. Job 記録：job_type `draft`、chapter_id、`base_commit`（main の HEAD）、`branch`、`worktree`、`context`、cli と container は null。QUEUED → LOCK_ACQUIRED → CLI_EXITED → CHECK_PASSED（`needs_validation=False`）で COMPLETED にし、最後の遷移の理由を `reopened after human content edit` にする（遷移の順が状態機械で許されなければ、許される最短の順を使い、報告に書く）。checks は空でよい。
5. 作業ブランチには何も commit しない（chapter.yaml は main で DRAFTED のまま）。
6. これで validate（または skip-validation）→ state-update → approve-state → final-approve が今のコードのまま通ること（テストで確かめる）。失敗したら（2 の後）、作った worktree とブランチを消し、Job 記録を残さない。

## 3-C4：CLI

* `edit --work <key> --kind typo|content [--no-impact] [--note <text>]`：commit、変えたパス、DRAFTED にした章、PLANNED に戻した章、要確認を立てた章、影響範囲分析の Job と結果（affected と notes）または失敗の理由を表示する。失敗の理由があれば、`analyze-impact --work <key> --commit <sha>` でやり直せると表示する。
* `reopen --work <key> --chapter <id>`：作業ブランチと Job ID を表示し、次は validate か skip-validation と案内する。
* `analyze-impact --work <key> --commit <sha> [--reason <text>]`：`run_impact_job(base=<sha の親>, head=<sha>, ...)`。`head` が main の HEAD でない場合は、`run_impact_job` の `ValueError` をそのまま表示する。

## テスト

* edit typo：誤字の修正が `NovUI-Edit: human-typo` の1つの commit になり、章の状態も要確認も変わらない。保護対象を含むと影響範囲分析の Job が走る（`--no-impact` で走らない）。
* edit content（draft.md）：FINAL の章が DRAFTED、この章に `previous_chapter_changed`、後続章（作業ブランチの無い章は同じ commit で、ある章はそのブランチに）に要確認。
* edit content（plan.yaml）：PLAN_APPROVED → PLANNED。FINAL の章の plan.yaml は状態が変わらず後続章に要確認。PLANNED は何も変わらない。
* 拒否（何も commit されず、作業ツリーの Human の変更が残る）：対象外のファイル（summary.yaml、chapter.yaml、.novui/ の下、README など）が混ざる、削除がある、作業ブランチのある章の draft.md、schema に合わない人物の YAML、場面の区切りを壊した draft.md、変更なし。
* 影響範囲分析の失敗：Claude の出力が得られないとき commit は残り、`impact_error` が入る。その後 `analyze-impact` で成功し、要確認が立つ。
* reopen：edit content で DRAFTED にした FINAL の章を reopen → skip-validation → state-update（偽の Claude）→ approve-state → review-done → final-approve で FINAL に戻り、summary.yaml が新しい要約になること（**この一連を1つのテストにする**）。条件の違う章（DRAFTED でない、作業ブランチがある）で `ChapterError`。
* run_impact_job：保護対象の変更が無いと `ValueError`、base が祖先でないと `ValueError`、affected の章に要確認、notes が WARNING に残る。
* CLI：edit・reopen・analyze-impact の表示。
* 既存のテストが、期待値を変えずに通ること。

## 報告

`docs/phase3/reports/3c.md` を新規に作り（この1ファイルだけは docs/ の下に書いてよい）、次を書いて commit・push する。

* 実装したもの（ファイルと関数）
* テストの結果（全体の件数：passed・skipped・failed）
* 指示どおりにできなかった点（無ければ「なし」）
* 判断に迷った点
