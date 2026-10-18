# AGYへの作業指示：Phase 3-A main への commit の保護と要確認フラグ

**この指示書は 3-A の全体を定める。3-B 以降（setting_change、影響範囲分析、直接編集、外部変更、push）はこの指示書の範囲外。先取りして書いてはならない。**

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストを実行し、phase3/state-git branch に commit・push します。
設計の判断はしません。本指示・`docs/phase3/phase3-plan.md`・仕様書が矛盾する場合、または決まっていない判断が必要になった場合は、作業を止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase3/phase3-plan.md`（§2、§3 の 3-A、§6）
* `docs/novel-system-spec-v0.5.2.md` の §7.3、§15、§17.4
* `src/novui/` の次のファイル：`workrepo.py`（`commit_all`、`check_commit_guard`、`merge_job_branch`、`parse_trailers`）、`finalize.py`（`audit_protected_changes`、`final_approve`、`cycle_info`、`waiting_jobs_on_branch`）、`approvals.py`、`semantics.py`（`check_approval`、`check_chapter`）、`chapters.py`、`states.py`、`protected.py`、`cli.py`
* `schemas/chapter.schema.json`（`review_reasons` の code は `previous_chapter_changed`・`setting_changed`・`external_change`）、`schemas/approval.schema.json`
* 既存の `tests/test_finalize.py`、`tests/test_workrepo.py`、`tests/test_chapters.py`、`tests/test_cli_statefinal.py`

## 書込みの許可範囲

```text
許可：
  src/novui/protectaudit.py（新規）
  src/novui/workrepo.py（commit_all の検査の差し替えだけ）
  src/novui/finalize.py（audit_protected_changes の書き換えと、final_approve への要確認の追加だけ）
  src/novui/chapters.py（要確認の関数の追加だけ）
  src/novui/cli.py（review-done の追加と show の表示の追加だけ）
  tests/**（追加と、既存テストの必要最小限の修正。既存テストの期待値を変えて通してはならない）
  .venv/**（Git 管理外）

禁止：上記以外すべて。次を含む。
  docs/、prompts/、schemas/、spike/、tools/、container/、templates/、.git/、.gitignore、pyproject.toml
  src/novui の上記以外の既存ファイル（states.py、approvals.py、semantics.py、protected.py、stateupdate.py、
  editjob.py、planjob.py を含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由。ただし NOVUI_INTEGRATION=1 は付けない）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase3/state-git
```

Phase 1・2 の禁止事項はすべて引き続き有効です（ログイン、OAuth の更新、アカウントの切替、token への接触を含む）。3-A に結合試験はありません。実物の AGY・Claude を呼ばないでください。

## 3-A1：保護対象の変更の検査を1つにまとめる

### protectaudit.py（新規）

```python
def audit_changes(
    changes: Sequence[tuple[str, str]],          # (status, path)。status は A・M・D・T（--no-renames）
    trailers: Mapping[str, Sequence[str]],       # parse_trailers の戻り値
    read_added: Callable[[str], bytes],          # 同じ変更で追加されたファイルの中身を読む
    is_used: Callable[[str], bool],              # approval_id がすでに使われていれば True
    *,
    allow_edit: bool,                            # NovUI-Edit を受け付けるか
) -> list[str]
```

保護対象（`protected.is_protected`）の変更について、次の規則で違反を英語の文の list で返す（空なら問題なし）。

1. 安全でないパス（`is_safe_relpath` が False）は違反。
2. 保護対象の変更が無ければ、ここで空を返す。
3. `allow_edit` が True で、`NovUI-Edit` の trailer があれば、空を返す。
4. `Approval-Id` が無ければ違反（`allow_edit` が False のときは、今の文の「NovUI-Edit is not accepted on a job branch」をそのまま付ける）。
5. 各 `Approval-Id` について、今の `audit_protected_changes` と同じ検査をする：id の形、記録 `.novui/approvals/<id>.yaml` が同じ変更で追加（A）されていること、schema と `check_approval`、`approval_id` の一致、`is_used` が False であること。加えて、**記録の `job_id` が null でなければ**、`NovUI-Job` の trailer に入っていること（今は null を許していない。setting_change の承認は 3-B で job_id が null になりうるため）。
6. `.novui/approvals/` の下の変更は、この変更で追加した有効な記録だけを許す。保護対象の削除（D）は違反。それ以外の保護対象は、有効な記録の targets のどれかに入っていなければ違反。

今の `audit_protected_changes` の違反の文は、先頭の `<commit の先頭10文字>: ` を除いて**同じ文言を保つ**（既存テストを変えないため）。

### finalize.audit_protected_changes

merge commit の検査はそのまま。各 commit について `audit_changes(..., allow_edit=False)` を呼び、返った文の先頭に `<label>: ` を付ける。`read_added` は `git show <commit>:<path>`、`is_used` は今と同じ `is_approval_used(repo, id)`。

### workrepo.commit_all

`check_commit_guard` の代わりに、index（stage した変更）に対して `audit_changes(..., allow_edit=True)` を呼ぶ。

* changes：`git diff --cached --name-status -z --no-renames`（初回 commit で HEAD が無い場合は、すべてを A とする）
* trailers：引数の trailers を `parse_trailers` と同じ形の dict にしたもの
* read_added：`git show :<path>`
* is_used：`is_approval_used(worktree, id)`（main にある）か、`HEAD` にすでにある
* 違反があれば、今と同じく `git reset -q` してから `CommitGuardError`（`paths` は違反に関係する保護対象のパスを昇順にしたもの、message に違反の文を含める）

`check_commit_guard` 自体は消さない（`merge_job_branch` が使い続ける。merge では作業ブランチの各 commit を先に `audit_protected_changes` で検査しているため）。

### テスト

* `tests/test_protectaudit.py`：規則 1〜6 のそれぞれ。`job_id` が null の instruction の記録が通ること、null でない記録で `NovUI-Job` が無いと違反になること。
* `commit_all`：main で、(a) `NovUI-Edit` 付きの保護対象の変更は通る、(b) 有効な記録と `Approval-Id` の変更は通る、(c) `Approval-Id` だけで記録が無い、(d) 記録の targets に無いパスを変える、(e) 使用済みの id、がそれぞれ `CommitGuardError` になり、index が空に戻り HEAD が変わらないこと。
* 既存の `test_finalize.py`・`test_workrepo.py`・`test_stateupdate.py` が変更なしで通ること。

## 3-A2：要確認フラグ

### 置き場所の規則（phase3-plan.md §6.1）

章の chapter.yaml の「今の値」は、その章の作業ブランチがあれば作業ブランチの chapter.yaml、無ければ main の chapter.yaml とする。要確認の値もそこに書く。main に書くと、作業ブランチの chapter.yaml と重なって最終承認の merge が止まるため。

### chapters.py に追加

```python
def review_marked(meta: Mapping[str, Any], code: str, detail: str, at: str) -> dict[str, Any]
def mark_review_required(settings, work, chapter_ids: Sequence[str], code: str, detail: str, *,
                         branch_only: bool = False) -> list[str]
def clear_review_required(settings, work, chapter_id: str) -> str
def subsequent_chapters(order: Sequence[str], chapter_id: str) -> list[str]
```

* `review_marked`：純粋な関数。新しい dict を返す。`review_required` を True にし、`review_reasons` に `{code, detail, at}` を足す。同じ code と detail の理由がすでにあれば足さない（at も更新しない）。
* `mark_review_required`：
  - chapter_ids は重複を除き、chapters-order.yaml の順に並べる。chapters-order.yaml に無い章は `ChapterError`（何も書かない）。
  - 作業ブランチがある章：そのブランチに紐づく Job 記録に、state が RUNNING・CHECKING・VALIDATING のものがあれば `ChapterError`（何も書かない）。この検査は、書く前にすべての章についてまとめて行う。
  - 作業ブランチがある章は、その worktree の chapter.yaml を書き換えて、章ごとに1つ commit する（subject：`review required <章> (<code>)`、trailer なし）。
  - 作業ブランチが無い章は、`branch_only` が False なら、main の chapter.yaml を書き換えて、まとめて1つ commit する（subject：`review required <章,章,...> (<code>)`、trailer なし）。`ensure_main_ready` を先に呼ぶ。
  - 書いた chapter.yaml はすべて schema と `check_chapter` で検査してから書く。変化の無い章（同じ理由がすでにある）は書かない。
  - 戻り値は作った commit の list（作った順）。
* `clear_review_required`：今の値の置き場所（上の規則）の chapter.yaml で、`review_required` を False、`review_reasons` を [] にして commit する（subject：`review done <章>`、trailer なし）。すでに False なら `ChapterError`。作業ブランチの Job の検査は `mark_review_required` と同じ。
* `subsequent_chapters`：order の中で chapter_id より後ろの章（chapter_id が無ければ `ValueError`）。

### final_approve への追加（§7.3 の「前の章の内容変更・再執筆」）

main の chapter.yaml が**すでに FINAL**（FINAL の章を開き直したサイクル）の章を最終承認するとき、後続の章すべてに `previous_chapter_changed`（detail：`<章> was changed and final-approved again`）の要確認を立てる。

1. 他の検査（今の final_approve の検査すべて）が通った後、merge の前に、後続の章のうち作業ブランチがある章について `mark_review_required(..., branch_only=True)` を呼ぶ。
2. 作業ブランチが無い後続の章は、`review_marked` で作った chapter.yaml を、merge の `extra_writes` に足す（merge commit に入る）。

最初の FINAL（main の chapter.yaml が FINAL でない）のときは、何もしない。merge が失敗した場合、1 で立てた要確認は残してよい（多めに立つ側の失敗なので、戻さない）。

### CLI

* `review-done --work <key> --chapter <id>`：`clear_review_required` を呼び、commit を表示する。
* `show`（`--chapter` 無し）：要確認の章の行の末尾に ` (review required)` を付け、その下に理由を1行ずつ `  - <code>: <detail> (<at>)` で出す。作業ブランチがある章は作業ブランチの chapter.yaml を読む。
* `show --chapter`：今の表示のままでよい（作業ブランチがあれば `_show_cycle` が出す）。

### テスト

* `review_marked`：同じ理由を2回足しても1件、元の dict を変えない。
* `mark_review_required`：main だけ、作業ブランチだけ、両方の混在。作業ブランチに RUNNING の Job があると何も書かずに `ChapterError`。order に無い章で `ChapterError`。`branch_only=True` で main が変わらないこと。
* `clear_review_required`：main と作業ブランチのそれぞれ。False の章で `ChapterError`。
* final_approve：(a) 初回の FINAL では後続に何も立たない。(b) FINAL の章を開き直したサイクルの最終承認で、作業ブランチの無い後続の章は merge commit の中で、作業ブランチのある後続の章はそのブランチで、要確認が立つ。(c) 要確認の立った後続の章は、`review-done` までは final_approve できない（今の `InvalidTransition`）。(b) の後、作業ブランチのある後続の章の最終承認が overlap で止まらないこと。
* CLI：`review-done` と `show` の表示。

## 報告

`docs/phase3/reports/3a.md` を新規に作り（この1ファイルだけは docs/ の下に書いてよい）、次を書いて commit・push する。

* 実装したもの（ファイルと関数）
* テストの結果（全体の件数：passed・skipped・failed）
* 指示どおりにできなかった点（無ければ「なし」）
* 判断に迷った点
