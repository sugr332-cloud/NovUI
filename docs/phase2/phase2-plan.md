# Phase 2 実装計画：Claude / AGY 連携（1章を outline から FINAL まで）

**作成：2026-10-03 / 基準：docs/novel-system-spec-v0.5.2.md §24 Phase 2**
**branch：phase2/chapter-flow（main 17615b6 から分岐）**

## 1. 目的と受入条件

1章を、outline → plan → plan 承認 → 執筆 → 検証 → 状態更新の承認 → 最終承認（main への merge）まで、Controller の操作だけで通せるようにする。

受入条件（§24 Phase 2）：

1. outline から FINAL まで（§9）を1章分、実際の Claude と AGY で実行できる
2. 不明な設定で WAITING_HUMAN になる
3. 一人称・相手ごとの呼び方（途中の変更を含む）の違反を Validator が検出できる
4. 伏線 ID・時系列・未回収を検査できる

Phase 2 では Web UI を作らない。Human の操作は CLI（`python -m novui <サブコマンド>`）で行う。Web UI は Phase 4。

## 2. 章の1サイクルでの Git の扱い（Phase 2 で確定）

v0.5.2 §7.1・§17 を、次のとおり具体化する。

| 段階 | 章状態 | 書く場所 | 書くもの | trailer |
|---|---|---|---|---|
| Human が章を追加 | OUTLINED | main | outline.md、chapter.yaml、chapters-order.yaml | `NovUI-Edit: human-content`（chapters-order.yaml が保護対象のため） |
| plan Job | PLANNED | main | plan.yaml、chapter.yaml | `NovUI-Job` |
| Human が plan を承認・却下 | PLAN_APPROVED / OUTLINED | main | chapter.yaml | なし（保護対象を含まない） |
| draft Job | DRAFTED | 作業ブランチ `ai/<章>/<draft の job_id>` | draft.md、requests.yaml、chapter.yaml | `NovUI-Job` |
| validate Job、Human の判断 | AI_VALIDATED | 同じ作業ブランチ | review.yaml、chapter.yaml | `NovUI-Job` |
| state_update Job、Human の承認 | HUMAN_APPROVED | 同じ作業ブランチ | summary.yaml、承認記録、Patch を適用した設定ファイル、chapter.yaml | `NovUI-Job`、`Approval-Id`（承認ごと） |
| Human の最終承認 | FINAL | main への merge | merge commit に chapter.yaml（FINAL） | `NovUI-Job`、作業ブランチの全 `Approval-Id` |

* 1つの章サイクルの作業ブランチは、draft Job が作ったもの1本とする。validate・state_update・range_edit・chapter_rewrite の Job は同じ作業ブランチ（worktree）で行う。章 lock（§18.1）は「その章の作業ブランチがあること」なので、同じサイクルの後続 Job はこの作業ブランチを使い、新しい作業ブランチは作らない。
* 作業ブランチ上の chapter.yaml は、そのサイクルの途中の状態を表す。main の chapter.yaml は merge の時点で確定する（§7.1）。
* plan と plan の承認は、本文を含まないため main に直接 commit する（Controller の commit。AI は main に書かない）。
* FINAL の後、作業ブランチと worktree は削除する。

## 3. 段階

各段階で、AGY が実装・テスト・commit・push（phase2/chapter-flow のみ）を行い、Claude が差分をレビューする。

### 2-A：作品の初期化、CLI、Claude の Job 実行器、plan Job

* 作品テンプレート（templates/novel/v1/）と、ローカルでの作品の初期化（GitHub への作成は Phase 5）、作品登録簿
* 章の追加（outline）と、main 上での章状態の遷移
* JSON Schema の束ね（Claude の `--json-schema` に渡すため、`$ref` を1つの schema の中に解決する）
* Claude の Job 実行器（プロンプトの組み立て、空の作業ディレクトリ、再要求、Job 記録、実際のモデル名の記録）
* plan Job、plan の承認・却下
* CLI：init-work、add-chapter、plan、approve-plan、reject-plan、show
* 指示書：docs/phase2/agy-instruction-2a.md

### 2-B：plan に基づく執筆と検証

* draft Job を plan に接続する（plan の場面・目標文字数・前後の接続をプロンプトへ。場面の区切り `<!-- scene: S1 -->` の出力指示）
* 場面の区切りの機械検査（§6.5.1）、ID の存在の機械検査（§11.3）
* validate Job（integrity_review と writing_review）、機械検査の結果と合わせて review.yaml を作業ブランチに書く
* WARNING・STOP の Human の判断（CONTINUE、REQUEST_FIX、CANCEL、OVERRIDE）、AI_VALIDATED への遷移
* Claude を利用できない場合の検証の省略（§11.6）

### 2-C：状態更新と最終承認

* state_update Job（summary と state_patch の提案）
* JSON Patch（add、remove、replace、test）の適用と base_hash の確認（§12.1）
* 承認記録（.novui/approvals/）と Approval-Id（§15.4）
* 最終承認：作業ブランチの main への merge（chapter.yaml を FINAL に）、作業ブランチの削除
* requests の解決（resolve_request）
* 受入1・2 の結合試験（実際の Claude・AGY で1章を通す）

### 2-D：キャラクター一貫性・伏線、書き直し、指示の振り分け

* 章・場面の順序（chapters-order.yaml と場面の番号）と、changes・exceptions の適用位置の解決
* キャラクター一貫性の検査（§11.7）：場面の位置で有効な一人称・呼び方などをプロンプトに入れる
* 伏線の検査（§11.8）：機械検査（ID、時系列、回収予定を過ぎた major）と Claude の検査
* range_edit（splice_range）、chapter_rewrite、plan_revision
* instruction_routing（Human の指示 → Job の提案 → Human の承認 → 実行）
* Job のキュー
* 受入3・4 の結合試験（違反を仕込んだ本文で Validator が検出すること）

## 4. 方針

* Claude へのプロンプトの文面は `prompts/claude/<job>.md` に置き、NovUI と一緒に版管理する。文面は Claude（設計担当）が書き、AGY は変更しない。
* Claude の Job は読み取り専用の分析であり、実行 lock（§18.1）の対象外とする。Controller が出力を検証してから書き込む。
* Claude のモデルは環境変数 `NOVUI_CLAUDE_MODEL`（既定 `opus`）で指定する。実際のモデル名は出力の modelUsage から Job 記録に残す。
* Phase 1 と同じく、結合試験で想定と違う結果が出たら、直さずに止めて報告させる。
