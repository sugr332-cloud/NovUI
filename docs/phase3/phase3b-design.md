# Phase 3-B 設計判断記録：setting_change と影響範囲分析

**作成：2026-10-18 / 基準：phase3-plan.md §3 の 3-B、仕様 v0.5.2 §8.1・§11.7・§13.3・§15・§17.2**

## 0. 結論の要約

* Human の設定変更の指示は、今の振り分け（`instruct`）に scope `setting` を足して受ける。承認すると、キューに setting_change Job が入る。
* setting_change Job は Claude を3段で呼ぶ：(1) 変える対象のファイルと方針、(2) 対象ごとの Patch、(3) 影響範囲分析。提案は Controller の作品別データに保存し、Human の承認で main に1つの commit で書く（承認記録、`Approval-Id`、影響章の要確認）。
* Phase 3 で setting_change が変えられるのは、YAML の設定（`characters/*.yaml`、`foreshadowing/registry.yaml`）だけ。world/・plot/・rules/ の Markdown は直接編集（3-C）で変える。
* **章の作業中に main の設定が変わった場合**、作業ブランチを main に追従させる（rebase）処理を足す（§17.2 が許している「追従」）。phase3-plan.md の決定 2 に書いた「提案が invalid になる」は不正確だったので、ここで改める。

## 1. 調査で分かった事実

1. 章の作業ブランチは、draft Job の時点の main から分かれる。state_update の Patch は**作業ブランチの**設定ファイル（分かれた時点の版）に当てる。
2. そのため、作業中に setting_change が main の `characters/C001.yaml` を変えると、作業ブランチの state_update の Patch は古い版に当たり、提案は無効にならない。最終承認の merge で、main と作業ブランチの両方が同じファイルを変えたことになり、overlap で止まる（自動 merge しない、は守られるが、Human には解き方が無い）。
3. 作業ブランチが設定ファイルを変えるのは、サイクルの最後の approve_state だけ。それより前の commit は、その章のファイル（draft.md、review.yaml、chapter.yaml など）だけを変える。
4. 振り分けの schema（`instruction_routing.schema.json`）は、scope に `setting` を、chapter_id に null を、すでに許している。

## 2. 入口：振り分けの scope `setting`

* `instruct --scope setting [--chapter <id>] --instruction "..."`。章は任意（validate の指摘から来た場合は、その章を付ける）。
* Claude の振り分けは、scope が setting のとき proposed_jobs を setting_change 1件だけにする（違えば scope_mismatch）。
* 承認でキューに setting_change が入る。章の作業ブランチは捨てない。

## 3. setting_change Job

### 3.1 Claude の3段

1. **方針（setting_change_plan）**：入力は指示、設定ファイル一式、章の一覧。出力は、変える対象のファイルの list（それぞれ、何をどう変えるかの文）と questions。曖昧なら questions だけを返し、Job は WAITING_HUMAN。
2. **Patch（setting_patch）**：対象ごとに1回。state_update の state_patch と同じ JSON Patch の形と base_hash。state_patch との違いは、Human の指示に基づくので、変えてよい範囲が広いこと（下の 3.2）。
3. **影響範囲分析（impact_analysis）**：入力は、変わる前と後の設定の差分、各章の summary.yaml（FINAL の章）と plan.yaml（plan のある章）。出力は、影響のある章とその理由。3-C・3-D でも同じ関数を使う。

### 3.2 変えてよい範囲（setting_patch）

* 人物：全項目（personality、behavior、speech、address、relationships、knowledge、exceptions、changes を含む）。ただし `id` は変えない。ファイルの追加・削除はしない。
* 伏線：既存の伏線の全項目（planned_resolution、status の `cancelled` を含む）と、新しい伏線の追加（ID は Controller が次の番号を渡す）。伏線の削除はしない。
* 各 Patch は、当てた結果を schema・semantics と今の機械検査（statecheck、foreshadowcheck、flagcheck）で検査し、FAIL があれば Job を FAILED にする（state_update と同じ）。

### 3.3 提案の保存と承認

* 提案は `<data_dir>/works/<key>/setting_proposals/<instruction_id>.yaml`。中身は、指示、対象ごとの Patch と hash、影響章と理由、`base_commit`（main）、status（pending／approved／rejected／superseded／invalid）。
* `approve-setting --instruction <id>`：
  1. main が `base_commit` から進んでいて、対象のファイルが変わっていれば invalid（指示し直す）。
  2. 対象のファイルを、どこかの作業ブランチが**すでに変えている**（approve_state 済みで未 merge）なら承認を拒否する（最終承認の merge が overlap で止まるため。先にその章を最終承認する）。
  3. 承認記録（`source: instruction`、`instruction_id`、`job_id: null`）を作り、Patch を当て、影響章の要確認（`setting_changed`）と合わせて、main に1つの commit で書く。作業ブランチのある影響章は、3-A の規則どおり作業ブランチに書く。
* `reject-setting --instruction <id> --reason`。
* 同じ対象への新しい setting_change の提案が保存されたら、古い pending の提案は superseded（§15.3）。

## 4. 作業ブランチの追従（rebase）

* run_state_update の開始時に、main が作業ブランチの分岐点から進んでいれば、作業ブランチを main の上に rebase する。条件：作業ブランチが変えたパスと、main が分岐点から変えたパスが重ならない（3 のとおり、approve_state の前は重ならないのが普通）。重なれば rebase せず、Job を始めずに WAITING_HUMAN とする（自動 merge しない）。
* rebase は承認待ちの提案が無いときだけ行う（提案の branch_head が変わるため）。approve_state の時点で main の対象ファイルが分岐点から変わっていれば、提案を invalid にし、state-update をやり直させる（やり直すと rebase される）。
* 分岐点（cycle_info の base）は、draft の記録の `base_commit` ではなく `git merge-base main <branch>` を使うように変える（rebase の後も、作業ブランチの検査の範囲が作業ブランチの commit だけになるように）。
* 本文を書いた後で設定が変わったことは、影響範囲分析が要確認として示す。追従は、設定ファイルを最新にするだけで、本文を書き直さない。

## 5. 範囲外

* world/・plot/・rules/ の Markdown の setting_change（3-C の直接編集で扱う）
* 人物ファイル・伏線の削除、人物ファイルの新規作成（直接編集で扱う）
* 影響範囲分析の結果に基づく自動の書き直し（Human が指示する）

## 6. 決定事項

| # | 内容 | 推奨案 | 決定 |
|---|---|---|---|
| 1 | setting_change で変えられる設定 | YAML の人物と伏線だけ。Markdown の設定は直接編集で変える | 推奨案（2026-10-19） |
| 2 | 作業中に設定が変わった場合 | 作業ブランチを main に追従させる（rebase、重ならないときだけ）。重なれば止める | 推奨案（2026-10-19） |
| 3 | 影響範囲分析で見る章 | plan のある章すべて（FINAL は summary、それ以外は plan）。outline だけの章は見ない | 推奨案（2026-10-19） |
| 4 | Claude の呼び出し | 方針・対象ごとの Patch・影響範囲分析の3段に分ける | 推奨案（2026-10-19） |

## 7. 実装の細部（指示書を書くときに決めたこと、2026-10-19）

1. **setting_change Job の終わり方**：提案を保存したら Job は COMPLETED にする（plan_revision と同じ。承認の待ちは提案の status で表す）。questions のときだけ WAITING_HUMAN。キューは止まらずに次へ進む。
2. **追従の後の draft_consistency**：rebase すると、draft Job の Context の hash（設定ファイル）が今の作業ブランチと合わなくなる。draft の `base_commit` から `git merge-base main <branch>` までに main で変わったパスの hash の違いは、FAIL にせず、WARNING `context_followed_main` に出す。
3. **振り分けの scope setting で scope_mismatch のとき**：proposed_jobs を空にし、status `questions` と同じ扱い（承認できない。Human は reject-routing して直接編集する）。
4. **setting_patch の出力の形**：state_patch と同じ schema（type `state_patch`）を使う。変えてよい範囲の検査は、state_update とは別の関数で行う。
