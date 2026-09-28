# 小説作成支援システム 仕様書 v0.4

**ステータス：READ-ONLY / 実装開始前**

v0.4 は v0.3 に対し、章完了後の状態更新、過去章要約、AI権限制御、状態遷移、Git運用、監視方式、機械可読形式、並行実行、安全制御を明文化する。

## 1. 文書の位置付け

本書は NovUI における小説作成・管理・AI執筆パイプラインの詳細仕様である。
NovUI 全体の実行基盤・Web UI・CLIセッション管理などを定義する上位仕様が存在する場合、本書は小説データモデル、Claude/AGYの責務、執筆ワークフロー、検証、Git反映の業務仕様を定義する。
実行基盤の共通機能は上位仕様を利用し、ここでは重複定義しない。

目的は、Controller実装時にAIの役割・状態・変更権限について追加の仕様判断が大量に発生しないこととする。

## 2. 用語

| 用語 | 定義 |
|---|---|
| Human | 作品内容、設定変更、採用判断、最終承認を行う人間 |
| Claude | 設計、計画、分析、監督、検証を担当するCLI |
| AGY | 実際の本文・コードの書込みを担当するCLI |
| Controller | CLI実行、安全制御、状態管理、Git操作を担当する中間層 |
| Validator | Claudeを用いた作品内容・変更内容の検証 |
| Canonical | Git上で正本として扱うデータ |
| Summary | 章終了時点の物語状態を記録した承認済み要約 |
| Setting Change Request | 設定変更が必要な場合の提案 |
| PASS | 必須検証項目に問題がない |
| WARNING | 問題の可能性がありHuman確認が必要 |
| STOP | 自動継続してはならない |
| FINAL | Human承認済みの正式な章状態 |

## 3. 基本原則

### 3.1 Gitを正本とする

作品データの正本はLocal Git Repositoryとする。
GitHub Private Repositoryはバックアップ、共有、履歴保存、復旧先として使用する。
Claude、AGY、Controller、Web UIのメモリやセッション状態は正本ではない。

### 3.2 本文は章単位で分離する

1章につき1ディレクトリを持ち、本文原本は chapters/<chapter-id>/draft.md とする。

### 3.3 outputは生成物

output/novel.md は章本文から生成する成果物であり正本ではない。
直接編集せず、原則Gitの通常管理対象から除外する。必要な場合のみリリース生成物として保存する。

### 3.4 AI変更は検証を経て正式化する

AI変更はControllerによる変更範囲検査、Validatorによる内容検証、必要に応じたHuman承認を経て正式化する。

### 3.5 不明情報を発明しない

既存データから判断できない設定、人物情報、時系列、伏線などをAIが勝手に確定してはならない。

## 4. AI役割分担

### 4.1 Claude：設計・計画・監督・検証

担当：
- 世界観、キャラクター、ストーリー、伏線の分析
- 章構成
- 執筆計画
- AGYへの執筆指示
- AGY変更結果の検証
- 設定変更提案
- 章終了時の状態更新提案
- 全体整合性監査
- システム開発時の設計、レビュー、テスト評価

Claudeは原則としてCanonical本文を直接変更しない。

### 4.2 AGY：実際の書込み

担当：
- 本文新規執筆
- 指定範囲の本文編集
- 本文拡張
- Claudeの計画に基づく本文生成
- システム実装時のソースコード作成・変更
- 必要なテストコード作成

AGYは作品設定を独断で変更しない。
設定変更が必要な場合はrequests.md等へ要求を出し、Canonical設定ファイルそのものは変更しない。

### 4.3 Human

担当：
- 章概要
- Claude計画の承認
- 設定変更承認
- WARNING/STOPの判断
- Git反映承認
- 最終章承認

### 4.4 Controller

担当：
- CLI起動、入出力
- CLI権限設定
- 作業対象制限
- branch/worktree管理
- ファイル変更範囲検査
- 状態管理
- timeout、STOP
- lock
- Claude/AGY間受け渡し
- Validator起動
- diff、commit、push/merge
- Web UIへの状態通知

ControllerはAIの自主的なルール遵守を前提にしない。

## 5. システム自身の開発方式

本システムの開発にも同じ責務分離を適用する。

Human要求
→ ClaudeのREAD-ONLY分析
→ Claudeの実装計画
→ Human承認
→ AGYのコード実装
→ テスト
→ Claudeのコードレビュー・仕様照合
→ PASS / 修正要求
→ AGY修正

Claudeは原則として実装コードを直接書き込まない。
AGYは設計・アーキテクチャ・仕様を独断で変更しない。

Claudeの開発用CLIは可能な限りWrite/Edit/Bash等を読み取り専用に制限する。
AGYは専用branchまたはworktreeで実行し、終了後Controllerが変更範囲を検査する。

## 6. プロジェクト構造

    novel-project/
    ├─ project.md
    ├─ chapters-order.md
    ├─ world/
    ├─ characters/
    ├─ plot/
    ├─ foreshadowing/
    ├─ rules/
    ├─ chapters/
    │  ├─ ch-001/
    │  │  ├─ outline.md
    │  │  ├─ plan.md
    │  │  ├─ draft.md
    │  │  ├─ summary.md
    │  │  ├─ review.md
    │  │  └─ requests.md
    │  └─ ...
    ├─ reviews/
    └─ output/
       └─ novel.md

## 7. IDと章順序

章IDと表示順を分離する。
例：ch-001、ch-002、ch-003。

chapters-order.mdをCanonicalな順序マニフェストとする。

    chapters:
      - ch-001
      - ch-002
      - ch-003

章の挿入・並び替えは章IDの変更ではなくマニフェストを変更する。

## 8. 設定データ

### 8.1 キャラクター

キャラクターIDを必須とする。
ファイル名もIDに統一する。

    characters/C001.md
    characters/C002.md

最低限：
id、name、personality、values、behavior、speech、abilities、background、knowledge、secrets、relationships、growth。

### 8.2 伏線

伏線の正本はforeshadowing/registry.mdのみとする。
active.mdやresolved.mdはCanonicalにはしない。必要ならregistryから生成するビューとする。

例：

    id: F003
    name: 王家の紋章
    introduced_chapter: ch-001
    planned_resolution_chapter: ch-010
    status: active
    importance: major

status：
planned / active / resolved / cancelled

## 9. 章データ

outline.md：Humanが章目的、出来事、登場人物、伏線、次章接続等を定義する。

plan.md：Claudeがシーン構成、行動、設定、伏線、禁止事項、文体、視点、文章量、前後章接続を定義する。

draft.md：AGYが実際に執筆するCanonical本文。

summary.md：章承認時にClaudeが生成する承認済み状態。
最低限、出来事、登場人物、各人物の知識、位置、所持品、負傷・状態、関係変化、世界設定への影響、伏線状態、次章開始時点の状態を含む。

review.md：Validator結果を機械可読形式で保存する。

requests.md：設定変更や追加判断が必要な場合の要求を保存する。
本文にSETTING_REQUIREDを埋め込んではならない。

## 10. 章状態

章状態はGit上の固定メタデータとして保存し、Controllerメモリだけには置かない。

通常遷移：

OUTLINED → PLANNED → PLAN_APPROVED → DRAFTED → AI_VALIDATED → HUMAN_APPROVED → FINAL

異常・中断：

STOPPED / WAITING_HUMAN / REJECTED

Job状態とは分離する。

## 11. Job状態

Controllerが管理する実行状態：

IDLE / QUEUED / PLANNING / WAITING_APPROVAL / WRITING / VALIDATING / COMPLETED / FAILED / STOPPED / WAITING_HUMAN

JobがCOMPLETEDでも章がFINALとは限らない。

例：
PLAN_APPROVED
→ Job QUEUED
→ WRITING
→ VALIDATING
→ chapter DRAFTED
→ chapter AI_VALIDATED
→ Job COMPLETED

## 12. WARNING / STOP

PASS：自動継続可能。

WARNING：Human確認が必要。HumanはCONTINUE、REQUEST_FIX、STOPを選択する。ControllerはWARNINGを自動的にPASSへ変換しない。

STOP：自動継続不可。STOP後はWAITING_HUMANとし、Human判断なしに再開しない。

STOP対象：
- 世界観矛盾
- 重大なキャラクター逸脱
- 時系列矛盾
- 伏線破壊
- 未定義設定の発明
- 計画違反
- 許可範囲外変更
- CLI異常
- timeout
- Validator判断不能

## 13. 執筆監視方式

本文全文をリアルタイムでClaudeへ逐次送信する方式はv0.4では採用しない。

AGY実行中はControllerが機械的に以下を監視する。
- process状態
- timeout
- stdout/stderr異常
- 書込み先path
- 変更ファイル数
- allowlist外変更
- 明確な形式異常

AGY完了後に変更範囲検査を行い、その後Claude Validatorを実行する。

    AGY
    ↓
    変更範囲検査
    ↓
    Claude Validator
    ↓
    PASS / WARNING / STOP

将来シーン単位検証を追加する余地は残すが、v0.4の必須機能にはしない。

## 14. AGY作業領域

AGYは対象章専用branchまたはworktreeで実行する。

通常の本文執筆で許可される変更は、

    chapters/<chapter-id>/draft.md

のみ。

章作成時は対象章ディレクトリ内の必要ファイル作成を許可する。

world、characters、plot、foreshadowing、rules、他章への変更はRejectする。
設定変更要求だけはrequests.mdへの出力を許可する。

Controllerはgit diff --name-only相当で検査し、allowlist外変更が1つでもあればジョブ全体をRejectする。

## 15. 章完了後の状態更新

章本文の検証後、Claudeは章によって変化したCanonical状態を分析する。

対象：
- 伏線status
- timeline
- キャラクター知識
- キャラクター位置
- 所持品
- 負傷・状態
- 人間関係
- 世界設定
- 用語

処理：

章本文
→ Claudeによる状態更新提案
→ 影響範囲分析
→ Human承認
→ Controllerによる設定反映
→ Validator
→ Git diff確認

Claudeは提案できるが、Human承認なしにCanonical設定へ反映しない。

## 16. 設定変更の影響範囲

設定変更承認時には、既存章・summary・timeline・伏線への影響を分析する。

影響対象がある場合、Humanへ一覧を提示する。

設定変更によって過去章の整合性が崩れる可能性がある場合、即時FINAL化せずWAITING_HUMANとする。

## 17. 過去章コンテキスト

毎回全作品本文をAIへ投入しない。

優先順：
1. 直前章summary.md
2. 必要な過去章summary.md
3. 必要な場合のみ過去章draft.md
4. characters/
5. world/
6. plot/
7. foreshadowing/registry.md

summaryには人物の知識状態、位置、所持品、負傷、関係等を記録する。
これにより30章目でも1～29章の全文を常時投入せずに状態検証できる。

## 18. 執筆フロー

Human
→ outline.md
→ Claude
→ plan.md
→ Human承認
→ PLAN_APPROVED
→ AGY
→ draft.md
→ Controller変更範囲検査
→ Claude Validator
→ AI_VALIDATED
→ Claude状態更新提案
→ Human承認
→ summary.md確定
→ FINAL

## 19. 未定義設定

AGYが判断不能な場合は本文へ要求文を挿入せず、requests.mdへ機械可読要求を出す。

例：

    type: SETTING_REQUIRED
    target: 主人公の魔法適性
    reason: 既存設定から判定不能
    requested_by: AGY
    status: pending

ControllerはWAITING_HUMANへ遷移する。

## 20. 選択範囲編集

UI選択範囲は行番号ではなく一意な文字列anchorで特定する。

要求にはchapter-id、anchor text、surrounding context、requested changeを含める。

一致数が0または2以上の場合はERROR。
1件だけ一致した場合のみ編集する。

AIに推測で別箇所を編集させてはならない。

## 21. Human直接編集

HumanはUIからdraft.mdを直接編集できる。
Human直接編集はAGYを経由する必要がない。

直接編集後はGit diffを生成し、必要に応じてValidatorを実行する。
直接編集だけを理由にAI_VALIDATEDにはしない。

## 22. Git / branch / worktree

Canonical branchはmain。

AGY変更は専用branch/worktreeで行う。

例：
ai/ch-004/job-123

標準反映フロー：

AGY変更
→ 変更範囲検査
→ Validator
→ git diff
→ Human承認
→ commit
→ mainへmerge
→ 必要に応じてpush

AIが直接mainへ書き込む運用は禁止する。

## 23. Git競合

Job開始時に対象branchの基準commitを記録する。

push/merge前にmainの更新を確認する。
mainが進んでいた場合はPUSH_BLOCKED → WAITING_HUMANとし、自動mergeしない。

## 24. 並行実行

v0.4では同一プロジェクトにつきAI書込みJobを同時に1つだけ許可する。

読み取り専用Claude分析は並行可能。
AGY書込みJobはproject lockを取得する。
ロック中の別書込みJobはQUEUEDとする。

## 25. Retry

自動Retryは有限とする。

- CLI一時エラー：最大2回
- timeout：最大1回
- Validator STOP：Retryしない
- 設定不明：Retryしない
- allowlist外変更：Retryしない

上限到達後はFAILEDまたはWAITING_HUMAN。

## 26. Validator

ValidatorはClaudeを使用する。

検証対象：
- character
- world
- timeline
- plot
- foreshadowing
- style
- plan compliance
- file scope
- undefined setting

結果は機械可読形式で保存する。

例：

    status: PASS
    checks:
      character: PASS
      world: PASS
      timeline: PASS
      foreshadowing: PASS
      style: WARNING
      plan_compliance: PASS
    issues: []

WARNING/STOPの理由を必ず保存する。

## 27. Validatorの誤検出

HumanはWARNING/STOPに対しCONTINUE、REQUEST_FIX、CANCEL、OVERRIDEを選択できる。
OVERRIDEには理由を必須とする。

ただし、allowlist外変更、認証、プロセス安全、Git安全制約はValidatorのOVERRIDEだけでは解除できない。

Controllerの機械的安全制約をAI判定より上位とする。

## 28. Claude利用不能時

Claudeのレート制限、認証失敗、利用不能等が発生してもAGYだけで検証を代替しない。

WAITING_HUMANとし、Humanが再試行または明示的な検証省略を選択する。

## 29. AGY対話確認

Controller経由のAGY実行では、事前にHuman承認を取得したうえで非対話実行できる構成を基本とする。

AGYが予期せず追加確認を要求した場合はWAITING_APPROVALへ遷移する。
無期限待機はせずControllerのtimeoutを適用する。

## 30. UI

左：作品ツリー
中央：本文・選択範囲
右：Claude / AGY / Validator状態

表示：
- chapter state
- job state
- Git state
- Validator結果
- WARNING / STOP
- 設定変更要求
- diff
- lock状態

スマートフォンはタブ形式に対応する。

## 31. UI認証・ネットワーク

Controllerはシェル実行権限を持つため、公開インターネットへ直接公開しない。

Android
→ Tailscale
→ PC
→ Controller

Controller/Web UIはTailscale経由を基本とし、Tailscale ACLで許可端末を限定する。
外部からCLIやGitへ直接接続させない。

## 32. Git安全制御

AIがmainへ無条件commit/pushすることは禁止。

AI変更
→ diff
→ validation
→ Human approval
→ commit
→ push/merge

設定ファイル変更は本文変更より厳格に扱う。

## 33. 完成原稿

chapters-order.mdの順番でchapters/<id>/draft.mdを結合しoutput/novel.mdを生成する。

output/novel.mdは直接編集しない。

## 34. 最終監査

全章完成後Claudeが以下を監査する。

World：設定矛盾、設定追加、設定変更、用語。
Characters：性格、口調、行動、能力、知識、人間関係。
Story：時系列、因果関係、章間接続、矛盾。
Foreshadowing：提示、維持、回収、時期、状態変化。
Prose：視点、文体、重複、不自然な表現、誤字。

結果をreviews/へ保存する。

## 35. 最終承認

AI_VALIDATED
→ HUMAN_APPROVED
→ FINAL

AI検証だけではFINALにしない。
FINAL化時にsummary.mdを確定する。

## 36. Git管理対象

管理対象：
project.md
chapters-order.md
world/
characters/
plot/
foreshadowing/
rules/
chapters/
reviews/

原則管理外：
output/novel.md
.env
credentials
API keys
CLI sessions
cache
temporary files
large execution logs

## 37. 禁止事項

1. AGYが独断でCanonical設定を変更する
2. AGYが対象外ファイルを変更する
3. AIが未定義設定を発明する
4. ClaudeがCanonical本文を直接編集する
5. output/novel.mdを原本として編集する
6. 全作品本文を毎回AIへ投入する
7. AIがmainへ無条件commitする
8. AIが無条件pushする
9. 認証情報をGitへ保存する
10. Validator未実施のAI変更を自動FINALにする
11. Human承認なしに設定変更をCanonicalへ反映する
12. Validatorの結果だけで機械的安全制約を解除する

## 38. 実装フェーズと受入条件

### Phase 0：Repository / Git基盤

対象：
- Git Repository
- project構造
- .gitignore
- branch/worktree
- chapters-order
- 状態メタデータ

受入：
- Canonical構造を作成できる
- AI作業branchをmainから分離できる
- diffで変更範囲を検出できる

### Phase 1：Controller基盤

対象：
- Job状態
- project lock
- timeout
- CLI process管理
- STOP
- path allowlist

受入：
- AGYを対象worktreeで起動できる
- timeout/STOPできる
- 許可外変更をrejectできる

### Phase 2：Claude / AGY連携

対象：
- Claude planning
- AGY writing
- Claude validation
- requests.md
- summary生成

受入：
- outline→plan→draft→validationが実行できる
- 不明設定がWAITING_HUMANになる
- 章承認後summaryを生成できる

### Phase 3：状態管理 / Git反映

対象：
- chapter state
- job state
- diff
- Human approval
- commit
- push/merge
- 競合検出

受入：
- Human承認なしにmainへ反映できない
- chapter/job stateが分離される
- 競合時に自動mergeしない

### Phase 4：Web UI

対象：
- 章選択
- 本文表示
- 選択範囲編集
- AI状態
- diff
- approval
- STOP
- requests
- Git状態
- Tailscale経由アクセス

受入：
- UIからControllerを安全に操作できる
- Tailscale経由で利用できる
- 直接Git/CLI操作を外部へ公開しない

### Phase 5：全体監査 / 完成原稿

対象：
- 全章監査
- summary整合性
- output生成
- 最終承認

受入：
- FINAL章を正式原稿として生成できる
- 全体監査結果を保存できる
- outputを再生成できる

## 39. 変更履歴

### v0.4

- 章完了後の状態更新フローを追加
- summary.mdを追加
- 過去章コンテキスト方式を追加
- AGYの変更パスをControllerで強制
- worktree/branch方式を追加
- 執筆中監視を機械監視＋章完了後Validatorへ整理
- requests.mdを追加
- 伏線registryを単一正本化
- キャラクターIDを必須化
- 章IDと章順序を分離
- 選択範囲anchor方式を追加
- Human直接編集を明記
- 設定変更の影響範囲分析を追加
- chapter stateとjob stateを分離
- 並行実行、lock、retryを追加
- ValidatorのWARNING/STOP/OVERRIDEを定義
- Claude利用不能時の挙動を定義
- Tailscale/ACLによるUIアクセス制御を追加
- Git基盤をPhase 0へ移動
- output/novel.mdをCanonical管理から除外
- フェーズごとの受入条件を追加

## 40. 設計上の核心

Human
→ Web UI
→ Controller
→ Claude（設計・計画・監督・検証）
→ AGY（実装・本文書込み）
→ Validator
→ Human Approval
→ Local Git/main
→ GitHub Private Repository

Claudeは「何をどうするか」を決める。
AGYは「実際に書く」。
Controllerはその境界を機械的に強制する。
Humanは作品内容、設定変更、Git反映、最終版を承認する。

小説本文についても、本システム自身のソースコードについても、この責務分離を基本原則とする。
