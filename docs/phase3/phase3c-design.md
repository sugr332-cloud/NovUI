# Phase 3-C 設計判断記録：Human の直接編集（CLI）

**作成：2026-10-19 / 基準：phase3-plan.md §3 の 3-C、仕様 v0.5.2 §14.2〜§14.4、§7.3、§15**

## 0. 結論の要約

* `edit --work <key> --kind typo|content [--no-impact] [--note <text>]`：Human が main の作業ツリーで直したファイルを、検査してから main に1つの commit で書く（`NovUI-Edit: human-typo`／`human-content`）。
* 本文の内容変更（content で draft.md を変えた）は、章を DRAFTED に戻し、この章と後続章に要確認を立てる。**DRAFTED に戻った章を FINAL に戻す道が今は無い**ので、`reopen` で作業ブランチを作り、既存の流れ（validate → state-update → final-approve）に乗せる（決定 1）。
* 保護対象を直したときは、既定で影響範囲分析（3-B の `analyze_impact`）を実行し、影響のある章に要確認を立てる。

## 1. 調査で分かった事実

1. 章の本文（draft.md）が main にあるのは、最終承認で merge された章（FINAL）と、FINAL の後に plan を改訂した章だけ。作業中の本文は作業ブランチにある。
2. validate・state-update・final-approve は、すべて作業ブランチと draft Job の記録を前提にしている（`find_chapter_branch`、`_check_draft_consistency`）。main の上の DRAFTED の章を検証する道は無い。
3. 本文の内容を変えると、その章の summary.yaml（state_update で作った要約）は古くなる。要約を作り直すのは state_update だけ。
4. 状態の遷移表は、FINAL から CONTENT_CHANGED で DRAFTED に戻ることを、すでに許している。

## 2. edit コマンド

1. main が checkout されていて、変更（未 commit、未追跡を含む）があること。変更が無ければエラー。
2. 変更のパスがすべて直接編集の対象（§14.2）であること：`chapters/<id>/draft.md`・`outline.md`・`plan.yaml` と、保護対象（`.novui/` を除く）。それ以外（review.yaml、requests.yaml、summary.yaml、chapter.yaml、.novui/、その他）が1つでも混ざれば、何も commit しない。削除も対象外（ファイルの削除はしない）。
3. 作業ブランチがある章の draft.md・outline.md・plan.yaml が含まれていれば、何も commit しない（§14.4）。
4. YAML のファイルは、schema と semantics で検査する（plan、character、registry、chapters_order、project、flags など、既存の schema の対応どおり）。FAIL なら何も commit しない。
5. 種別ごとの章の扱い：
   * typo：章の状態も要確認も変えない。
   * content で draft.md を変えた章：chapter.yaml を CONTENT_CHANGED で DRAFTED にし、この章と後続章（作業ブランチのある後続章はそのブランチに）に要確認 `previous_chapter_changed`（この章自身は detail `<章> content edited`）。
   * content で plan.yaml を変えた章（決定 2）：PLAN_APPROVED なら PLANNED に戻す（approve-plan をやり直す）。PLANNED・OUTLINED は変えない。DRAFTED 以降（main にある FINAL など）は状態を変えず、後続章に要確認。
   * content で outline.md だけを変えた章：状態を変えない。
6. 1つの commit（変更したファイル ＋ 書き換えた chapter.yaml）。trailer は `NovUI-Edit` だけ。
7. 保護対象が含まれ、`--no-impact` が無ければ、commit の後に影響範囲分析（変更の前後の差分）を実行し、影響のある章に要確認 `setting_changed`（detail `human edit <commit の先頭12文字>`）を立てる。分析が失敗しても commit は戻さない（失敗を表示し、Human が `analyze-impact` をやり直せるようにする）。

## 3. reopen コマンド（決定 1）

`reopen --work <key> --chapter <id>`：main で DRAFTED（直接編集で戻った）で、作業ブランチの無い章について、

* main の今の HEAD から作業ブランチと worktree を作る（draft Job と同じ名前の規則）。
* Job 記録を1つ作る：job_type `draft`、state COMPLETED、`base_commit`、`branch`、`worktree`、`context`（draft Job と同じ Context の集め方で、今の main の hash）。AGY は呼ばない。記録に `reopened: true` と理由（`human content edit`）を残す。
* それ以降は、今の流れ（validate／skip-validation → state-update → approve-state → final-approve）がそのまま使える。最終承認は「FINAL を開き直したサイクル」に当たらないので、後続章の要確認は edit の時点で立てたものだけ。

## 4. 範囲外

* 添削（§14.5）は Phase 4（画面）で扱う。
* 内容変更の後の state_update は、前の版の状態の Patch（人物の知識など）をすでに設定に持っている。重複や矛盾は、影響範囲分析と Human の確認で扱う（自動で取り消さない）。

## 5. 決定事項

| # | 内容 | 推奨案 | 決定 |
|---|---|---|---|
| 1 | 本文の内容変更で DRAFTED に戻った章を、どう FINAL に戻すか | `reopen` で作業ブランチを作り、今の validate → state-update → final-approve に乗せる（要約も作り直される） | |
| 2 | plan.yaml の内容変更 | PLAN_APPROVED の章は PLANNED に戻して承認をやり直す。FINAL などは状態を変えず後続章に要確認 | |
| 3 | 直接編集の対象外のファイルが混ざったとき | 何も commit しない（一部だけ commit しない） | |
| 4 | 保護対象の直接編集の後の影響範囲分析が失敗したとき | commit は残し、`analyze-impact` で後からやり直せるようにする | |
