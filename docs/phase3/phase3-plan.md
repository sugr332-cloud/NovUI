# Phase 3 実装計画：状態管理と Git 反映

**作成：2026-10-18 / 基準：docs/novel-system-spec-v0.5.2.md §24 Phase 3**
**branch：phase3/state-git（main da0e728 から分岐）**

## 1. 目的と受入条件

Phase 2 で、章のサイクルの中での承認記録・Patch の適用・最終承認の merge はできた。Phase 3 では、章のサイクルの外での設定の変更（Human の指示、直接編集、UI を経由しない変更）と、その影響を受ける章の扱い、GitHub への push の規則を作る。

受入条件（§24 Phase 3）：

1. 承認記録なしに保護対象を変更できない
2. 競合時に自動 merge しない
3. 伏線の状態変更と人物の changes・exceptions の追加を Human の承認付きで反映できる

Phase 3 でも Web UI は作らない。Human の操作は CLI で行う。

## 2. Phase 2 で済んでいるもの（調査の結果）

| 対象 | 状態 | 場所 |
|---|---|---|
| 承認記録（§15.4）の採番・保存・1回限りの使用 | 済み | approvals.py、stateupdate.approve_state |
| state_update の Patch（伏線の状態、人物の changes・exceptions・knowledge） | 済み | stateupdate.py、statepatch.py |
| 作業ブランチの保護対象の検査（§15.5 の 2） | 済み | finalize.audit_protected_changes、checks |
| main が進んだときの merge（重なれば止める、§17.2） | 済み | workrepo.plan_merge、finalize.final_approve |
| main への commit の保護（§15.5 の 3） | **一部**：trailer の有無だけを見る。`Approval-Id` が有効か（記録があり、Patch の hash と target が合うか）は見ていない | workrepo.check_commit_guard |
| 要確認フラグ（§7.3） | **一部**：chapter.yaml の欄と、FINAL への遷移を止める処理だけ。立てる処理と下ろす処理が無い | chapters.py、states.py |
| setting_change（§8.1、§15.3） | 無い。振り分けの scope は range・chapter・plan だけ | routingjob.py |
| 影響範囲分析 | 無い | — |
| Human の直接編集（§14） | 無い（CLI のコマンドが無い） | — |
| 外部変更の検出（§17.6） | 無い | — |
| push の規則（§17.3） | 無い（remote を扱う処理が無い） | — |

## 3. 段階

各段階で、AGY が実装・テスト・commit・push（phase3/state-git のみ）を行い、Claude が差分をレビューする（Phase 2 と同じ）。

### 3-A：main への commit の保護と要確認フラグ

* commit の保護を強める：main への commit で保護対象を変える場合、`NovUI-Edit: human-*` か、次をすべて満たす `Approval-Id` を求める。(a) 同じ commit に `.novui/approvals/<id>.yaml` が追加されている、(b) 記録の targets に変更したすべての保護対象のパスが入っている（`.novui/approvals/` 自身を除く）、(c) その id が前の commit までに使われていない。満たさなければ commit しない。
* 作業ブランチの merge でも同じ規則を、作業ブランチの各 commit に当てる（今の audit_protected_changes を、この規則に合わせる）。
* 要確認フラグを立てる・下ろす処理：`mark_review_required(chapter_ids, reason)`（main に Controller が commit）、`clear_review_required(chapter_id)`（Human の「確認済み」）。CLI：`review-done`、`show` に要確認の章と理由を出す。
* §7.3 の 1：FINAL の章を変える操作（後の段階の直接編集・setting_change）と、前の章の再執筆で、後続章すべてに要確認を立てる。

### 3-B：setting_change と影響範囲分析

* 振り分けの scope に `setting` を足す（§11.7 の「設定の変更を伴う場合」の行き先）。
* setting_change Job（Claude）：Human の指示から、保護対象への Patch と、影響範囲（影響のある章と理由）を提案する。章の作業ブランチではなく、Controller の作品別データに提案として保存する（§15.3：未承認の提案は Canonical に含めない）。
* 承認：承認記録（`source: instruction`、`instruction_id`）を作り、Patch を当て、main に1つの commit で書く（`Approval-Id`）。影響のある章に要確認を立てる（同じ commit）。
* 提案の無効化：base_hash が合わなくなった提案は invalid、同じ target への新しい提案が出たら古い提案は superseded（§15.3。state_update の提案と setting_change の提案の両方にまたがる）。
* 影響範囲分析（Claude、読み取り専用）：変わった設定の差分と、各章の summary・plan から、影響のある章を返す。setting_change、直接編集（3-C）、外部変更（3-D）で使う。

### 3-C：Human の直接編集（CLI）

* `edit` コマンド：Human が作業ツリーで直したファイルを、種別（`typo`／`content`）を付けて main に commit する（`NovUI-Edit: human-typo`／`human-content`）。対象は §14.2 のファイルだけ。対象外のファイルが混ざれば commit しない。
* 章の作業ブランチがある章の draft.md・plan.yaml・outline.md は直接編集できない（§14.4）。
* `content` で draft.md を変えた章は DRAFTED に戻し、この章と後続章に要確認を立てる（§14.3）。
* 保護対象を直接編集したときは、影響範囲分析を実行するか選ぶ（既定は実行）。分析で影響ありの章に要確認を立てる。

### 3-D：外部変更の検出と push

* 同期（`sync`）：前回確認した commit（Controller の作品別データ）から main の HEAD までの commit を調べ、保護対象の変更に `NovUI-Edit` も有効な `Approval-Id` も無いものを「外部変更」として一覧にする。Human は影響範囲分析を実行するか、確認済みにするかを選ぶ。
* push（`push`）：main だけを origin に push する。失敗しても作業は続けられ、`show` に「未 push」と出す。origin/main が進んでいる（ローカルの main の祖先でない）ときは push せず WAITING_HUMAN とし、自動で merge しない。
* 試験の origin は、ローカルの bare リポジトリで代える（GitHub のリポジトリ作成は Phase 5）。

### 3-E：受入の結合試験

* 受入 1：承認記録の無い保護対象の変更（Controller の commit、作業ブランチの commit、外部変更）が、それぞれ拒否または検出されること。
* 受入 2：main が進んで作業ブランチと重なる場合、origin/main が進んだ場合に、自動 merge しないこと。
* 受入 3：実際の Claude で、setting_change（伏線の状態変更、人物の changes・exceptions の追加）を提案させ、承認して main に反映し、影響章に要確認が立つこと。

## 4. 方針

* Phase 2 の方針（§4）を引き継ぐ。Claude のプロンプトは `prompts/claude/` に置き、Claude（設計担当）が書く。
* 影響範囲分析と setting_change の Claude の Job は読み取り専用で、Controller が出力を検証してから書く。
* 結合試験で想定と違う結果が出たら、直さずに止めて報告させる。

## 5. 決定事項

| # | 内容 | 推奨案 | 決定 |
|---|---|---|---|
| 1 | Phase 3 のブランチ | `phase3/state-git` を main から切り、AGY の push もここだけにする | |
| 2 | setting_change の書き先 | 章の作業ブランチではなく main に直接（承認記録付きの Controller の commit）。章の作業ブランチと同じ target を変えた場合、その章の state_update の提案は base_hash が合わなくなり invalid になる | |
| 3 | 直接編集の操作 | Phase 3 は CLI（Human が作業ツリーで直し、`edit` で commit）。エディタの画面は Phase 4 | |
| 4 | push の試験 | ローカルの bare リポジトリを origin にする。GitHub への実際の push は Phase 5 で確かめる | |
| 5 | 正気度・ルート・視点ごとの台詞（2-E 決定 9） | Phase 3 には入れない。別に設計する | |
