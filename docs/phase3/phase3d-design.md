# Phase 3-D 設計判断記録：外部変更の検出（sync）と push

**作成：2026-10-20 / 基準：phase3-plan.md §3 の 3-D、仕様 v0.5.2 §7.3、§17.3、§17.6**

## 0. 結論の要約

* `sync --work <key>`：前回確認した commit から main の HEAD までの commit（first-parent）を調べ、保護対象の変更に有効な `NovUI-Edit` も `Approval-Id` も無い commit を「外部変更」として記録・表示する。確認の位置は HEAD まで進め、外部変更は Human が片付けるまで残す。
* `resolve-external --work <key> --commit <sha> --analyze | --ack`：外部変更ごとに、影響範囲分析（3-C の `run_impact_job`）を実行するか、分析せずに確認済みにする。
* `push --work <key>`：main だけを origin に push する（force しない）。origin/main がローカルの main の祖先でなければ push せず「要対応（diverged）」として残し、自動で merge しない。
* `show --work <key>`（章の指定なし）に、未 push の commit 数、push の状態、未処理の外部変更を出す。

## 1. 調査で分かった事実

1. 保護対象は `protected.py` の `PROTECTED_PATHS`（project.yaml、chapters-order.yaml、world/、characters/、plot/、foreshadowing/registry.yaml、rules/、flags/、assets/、.novui/）。`chapters/` は保護対象ではない。
2. Controller が main に作る commit は、`commit_all` の検査（`audit_changes(..., allow_edit=True)`）を通っている。作品の最初の commit は `NovUI-Edit: human-content` 付き。
3. 最終承認の merge commit は、作業ブランチの `NovUI-Job` と `Approval-Id` をまとめて trailer に持つ。first-parent との差分には、作業ブランチで追加した承認記録が `A` で入る。
4. `audit_changes` の `is_used`（承認記録がすでに main にあるか）は、今の main を見る。過去の commit を調べるときに今の main を見ると、正しい承認もすべて「使用済み」になる。
5. remote の設定、push、fetch の処理はまだ無い。

## 2. sync

### 2.1 調べる範囲

* 確認の位置は `<data_dir>/works/<key>/sync.yaml` の `checked_commit`。無ければ main の最初の commit から調べる。
* `git rev-list --first-parent --reverse <checked>..main` の各 commit を、first-parent との差分（`--no-renames`）で調べる。最初の commit（親が無い）は空の tree との差分。
* `checked_commit` が main の祖先でない（履歴が書き換えられた）ときは、`merge-base` から調べ直し、表示に警告を出す（決定 2）。

### 2.2 判定

* 各 commit に `audit_changes(changes, trailers, read_added, is_used, allow_edit=True)` を当てる。`read_added` はその commit の中身、`is_used` は**その commit の first-parent に**承認記録があるか（事実 4）。
* 違反が1つでもあれば外部変更。記録は commit、subject、保護対象のパス、違反の文、検出の時刻、status `pending`。
* 章のファイル（`chapters/<id>/` の下）を変えた commit で、`NovUI-Job`・`NovUI-Edit`・`Approval-Id` のどれも無いもの（Controller も edit も通っていない変更）も、外部変更（kind `chapter`）として記録する（決定 3）。分析の対象ではなく、`--ack` だけで片付ける。章の状態は自動で変えない。
* 調べ終えたら `checked_commit` を調べた範囲の最後に進める。同じ commit を二度記録しない。

### 2.3 片付け

* `resolve-external --commit <sha> --analyze`：`run_impact_job(base=<sha の first-parent>, head=<sha>, reason="external change <sha の先頭12文字>")`。COMPLETED なら status `analyzed`（Job ID を残す）。失敗なら pending のまま。kind `chapter` には使えない。
* `resolve-external --commit <sha> --ack [--note]`：status `acknowledged`。
* 外部変更が pending のままでも、他の操作は止めない（§17.6 は「促す」だけ）。

## 3. push

1. origin の remote が無ければエラー（remote の設定は Human。GitHub の作成は Phase 5）。
2. main の作業ツリーの状態は問わない（push は commit だけを送る）。
3. `git fetch origin main`（失敗したら status `failed`、理由を残して終える）。
4. `origin/main` が無い、またはローカルの main の祖先なら `git push origin main:main`（force しない、作業ブランチは送らない）。成功で status `pushed`、失敗で `failed`。
5. `origin/main` がローカルの main の祖先でなければ push せず status `diverged`（§17.3 の WAITING_HUMAN）。Human が main で origin/main を取り込んで（Controller の外で merge など）からもう一度 push する。取り込んだ commit は次の sync で外部変更として出る。
6. 状態は `<data_dir>/works/<key>/push.yaml`（最後の試行の時刻、status、理由、push した commit）。Job 記録は作らない（決定 1）。
7. 試験は、一時ディレクトリの bare リポジトリを origin にする。

## 4. show

章の指定が無い `show` に次を足す（どれもネットワークを使わない）。

* 未 push：`refs/remotes/origin/main..main` の commit 数（origin が無ければ「origin なし」）。
* push の状態：push.yaml の status と時刻。
* 外部変更：pending の件数と、各 commit の先頭12文字・subject。sync の確認位置が main の HEAD より古ければ「未 sync」。

## 5. 範囲外

* GitHub のリポジトリ作成と認証（Phase 5）
* 外部変更の自動の取り消し、origin/main の自動 merge
* 外部で変えられた章の状態の自動の変更（決定 3）

## 6. 決定事項

| # | 内容 | 推奨案 | 決定 |
|---|---|---|---|
| 1 | push の「WAITING_HUMAN」の表し方 | Job にせず、push.yaml の status `diverged` で表し、show に出す（push は Claude・AGY を呼ばず、Job の状態機械に乗せる利点が無い） | |
| 2 | 前回確認した commit が main の祖先でない（履歴の書き換え） | merge-base から調べ直し、警告を出す | |
| 3 | 保護対象でない章のファイルの外部変更 | 外部変更（kind `chapter`）として一覧に出し、確認済みにするだけ。章の状態は変えない | |
| 4 | 外部変更の片付け | commit ごとに「影響範囲分析」か「確認済み」を選ぶ。pending のままでも他の操作は止めない | |
