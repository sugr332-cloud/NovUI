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

※ 設定ファイルの保護と承認記録は§43（追補）を参照。

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

※ 詳細は§42（追補）を参照。

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

### v0.4 追補（2026-09-28）

- §41 人間の指示入口と振り分けを追加（指示バー、scope、instruction_routing、Job種別と書込み許可、FINAL章への指示）
- §42 Human直接編集と添削を追加（本文モード、直接編集できるファイル、mainへのcommitとロック、表記修正／内容変更、赤入れ）
- §43 設定ファイルの保護を追加（保護対象、3つの変更経路、承認記録、機械的な強制、UI）
- §44 新規作品の作成を追加（1作品1リポジトリ、作品ごとの制作フロー、Controllerによるリポジトリ作成、作品の分離）

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

## 41. 追補：人間の指示入口と振り分け

**ステータス：v0.4追補（2026-09-28）。v0.5で正式化する。Phase 0スパイクの対象外。**

§20（選択範囲編集）と§21（Human直接編集）に加え、選択範囲より大きい単位への人間の指示を定義する。

### 41.1 原則

- 人間が文章で指示する相手はClaudeだけとする。AGYへ直接指示する入口は設けない。
- Claudeは指示を実行せず、Jobへ振り分けた案を返す。Humanが承認した案だけをControllerが実行する。
- Human直接編集（§21）は本節の対象外であり、従来どおり指示を経由せずに行える。

### 41.2 指示バー

UIに指示の入口を1つだけ置く（指示バー）。
指示の対象（scope）は画面の状態から自動で決め、Humanが手動で変更できる。

| scope | 自動で選ばれる条件 | 対象 |
|---|---|---|
| range | draft.mdで本文を選択している | 選択範囲（§20のanchor方式） |
| chapter | 章を開いていて選択がない | 章全体 |
| plan | outline.md / plan.mdを表示している | plan.md（outline.mdはHumanが直接編集する） |
| setting | 手動選択、または設定ファイルを表示している | world / characters / plot / foreshadowing / rules |
| fix | WARNING / STOPの判断中にREQUEST_FIXを選んだ | 検証結果の指摘箇所 |

REQUEST_FIXには理由（指示文）を必須とする。理由のないREQUEST_FIXは受け付けない。

### 41.3 Claudeによる振り分け

Claudeは指示をstdoutへ構造化データとして返し、Controllerがschema検証する（§5・§26と同じ経路）。

    type: instruction_routing
    instruction_id: INS-0012
    scope: chapter
    chapter_id: ch-004
    instruction: "全体的にテンポが遅い。門の場面までを短くしたい。"
    scope_mismatch: false
    proposed_jobs:
      - job_type: plan_revision
        summary: "S1を短縮し、S2の門の場面を早める"
      - job_type: chapter_rewrite
        summary: "改訂planに基づきS1〜S2を再執筆する"
        depends_on: plan_revision
    impact: []
    questions: []

- 指示が曖昧でJobを決められない場合、Claudeは `questions` を返し、Jobを提案しない。Controllerは `WAITING_HUMAN` とする。
- UI上のscopeと実際に必要な変更の単位が異なる場合（例：選択範囲への指示だが設定変更を伴う）、Claudeは `scope_mismatch: true` とし、理由を示す。scopeを広げるかどうかはHumanが判断する。Claudeが自動でscopeを広げてはならない。
- 設定変更を含む案では、`impact` に§16の影響範囲を列挙する。

### 41.4 Job種別と書込み許可

| job_type | 実行 | 書込み許可 | 章状態への影響 |
|---|---|---|---|
| range_edit | AGY | 対象章の `draft.md`（prefix/suffix検査付き）、`requests.md`（追記のみ） | FINAL / AI_VALIDATEDの章はDRAFTEDへ戻る |
| chapter_rewrite | AGY | 対象章の `draft.md`（全体）、`requests.md`（追記のみ） | 実行後DRAFTED |
| plan_revision | Claude（stdout）→ Controller | 対象章の `plan.md` | PLANNEDへ戻る（再承認が必要） |
| setting_change | Claude（State Patch）→ Controller | Patchの `target` のみ（§15の手順） | 影響章に要確認フラグを付ける |
| resolve_request | Controller | `requests.md`（追記のみ） | なし |

- `chapter_rewrite` は、承認済みのplan（`PLAN_APPROVED`）を前提とする。`plan_revision` を伴う場合は、改訂planの承認後にのみ実行する。
- 文章レビュー（Writing Review）の指摘をHumanが採用した場合は、`range_edit` に変換する。
- 上記以外のファイルへの変更は§14と同様にRejectする。

### 41.5 FINAL章への指示

- FINAL章に `range_edit` または `chapter_rewrite` を実行した場合、章状態はDRAFTEDに戻り、Validatorと承認を再度経る。
- その章の `summary.md` と、後続章の `summary.md` に要確認フラグを付け、Humanへ一覧を提示する。
- 要確認フラグが残っている章は、FINALへ再遷移させない。

### 41.6 承認画面

Humanは振り分け案に対し「この案で実行」「破棄」を選ぶ。画面には最低限以下を表示する。

- 各Jobの種別、実行者、書込み対象
- 章状態の変化（戻り先）
- 影響範囲（setting_change、FINAL章への指示）
- `scope_mismatch` の有無と理由

### 41.7 指示の記録

- 採用された指示は、`instruction_id` と指示文をJob記録に残す。
- 破棄された指示と、Claudeとの対話の途中経過は保存しない。
- 保存する場合も、作品の正本（Canonical）には含めない。

### 41.8 v0.5での確定事項

- instruction_routingのschema
- chapter_rewriteの範囲指定（章全体か、シーン単位か）
- 要確認フラグの保存場所と解除条件
- 同一章への指示が連続した場合のJobの扱い（キュー、統合、却下）

## 42. 追補：Human直接編集と添削

**ステータス：v0.4追補（2026-09-28）。v0.5で正式化する。§21を詳細化する。**

### 42.1 本文モード

draft.mdの表示には3つのモードを設ける。

| モード | 内容 | AIの関与 |
|---|---|---|
| 閲覧 | 本文を読む。選択して指示バー（§41）から指示できる | 指示した場合のみ |
| 直接編集 | Humanがdraft.mdを直接書き換える（追記・削除・修正） | なし |
| 添削 | 本文に赤入れ（範囲＋コメント）を付け、まとめてClaudeへ渡す | Claudeが範囲編集Jobへ振り分ける |

### 42.2 直接編集できるファイル

- Humanは最終決定権を持つため、Canonicalファイルはすべて直接編集できる。
  対象：`draft.md`、`outline.md`、`plan.md`、`world/`、`characters/`、`plot/`、`foreshadowing/registry`、`rules/`、`chapters-order`。
- 次のファイルはControllerまたはAIの出力を正本とするため、直接編集の対象外とする。
  対象：`review.md`（Validator結果）、`requests.md`（追記専用）、`summary.md`（Human承認済みの状態更新から生成する）。
  summaryの誤りは、状態更新の手順（§15）で修正する。
- `output/novel.md` は生成物であり、編集しない（§3.3）。

### 42.3 反映先とロック

- Humanの直接編集は、作業branchを経由せず `main` へcommitする。commitメッセージに `human-edit` と種別（42.4）を付ける。
- 対象章に未mergeのAI作業branchがある間（Jobの状態を問わない）、その章の直接編集と添削はできない。先にJobをSTOPするか、mergeを完了させる。
- 設定ファイルの直接編集中にAI書込みJobがある場合、そのJobのmerge時に§23の競合検出が働く。
- Web UIとローカルのエディタで同時に編集した場合の競合は、§23と同様に自動mergeしない。

### 42.4 保存時の種別

直接編集の保存時に、Humanが種別を選ぶ。

| 種別 | 例 | 章状態 | Validator | summary |
|---|---|---|---|---|
| 表記修正 | 誤字、句読点、言い回し、表記ゆれ | 維持 | 任意 | 変更しない |
| 内容変更 | 出来事、台詞の意味、人物の行動・知識・位置の変化 | DRAFTEDへ戻す | 推奨 | この章と後続章に要確認 |

- 内容変更後の章は、Validatorを実行してAI_VALIDATEDとする。Validatorを省略する場合は、`validation_skipped` をJob記録に残し、Human承認でHUMAN_APPROVEDへ進めてよい（§28と同じ扱い）。
- 種別の判定はHumanが行う。ControllerとClaudeは種別を変更しない。
- 表記修正として保存された変更について、Claudeが内容変更の疑いを検出した場合は、WARNINGとして提示するにとどめる。状態の変更はHumanが判断する。
- 設定ファイルの直接編集は、種別にかかわらず保存時に影響範囲分析（§16）を実行するかをHumanが選ぶ。既定は実行する。

### 42.5 添削（赤入れ）

- 赤入れは、範囲（§20のanchor方式）とコメントの組とする。
- 赤入れは、送信するまで作業中データとして保持し、Canonicalには含めない。
- 「まとめてClaudeへ送る」で、赤入れ全体を1つの指示として§41.3の振り分けに渡す。scopeは `redline` とする。
- Claudeは原則、赤入れ1件につき `range_edit` 1件を提案する。複数の赤入れが同じ範囲に重なる場合は1件にまとめてよい。赤入れの内容が設定変更や章の再執筆を要する場合は `scope_mismatch` を返す。
- 生成された複数の `range_edit` は、同じ章についてはproject lockにより順番に実行する。前のJobで本文が変わるため、後続Jobは実行直前にanchorを再照合し、一致しなければERRORとする（§20）。
- 採用されなかった赤入れは保存しない（§41.7）。

### 42.6 v0.5での確定事項

- 赤入れの作業中データの保存場所と保持期間
- 範囲編集Jobを連続実行する際のanchor再照合の方式
- 要確認フラグ（§41.5と共通）の保存場所と解除条件

## 43. 追補：設定ファイルの保護

**ステータス：v0.4追補（2026-09-28）。v0.5で正式化する。§15・§41・§42を横断する規則。**

### 43.1 目的

Humanは設定ファイルを閲覧・編集・変更指示できる。
一方で、設定ファイルはHumanの行為（直接編集・指示・承認）なしには一切変更されないことを、Controllerが機械的に保証する。

### 43.2 保護対象

- `project.md`
- `chapters-order.md`
- `world/`
- `characters/`
- `plot/`
- `foreshadowing/registry`
- `rules/`

### 43.3 変更経路

保護対象を変更できる経路は次の3つに限る。いずれもHumanの行為を起点または必須条件とする。

| 経路 | 起点 | 書込み主体 | 記録 |
|---|---|---|---|
| Human直接編集（§42） | Human | Human（UI経由でmainへcommit） | commitに `human-edit` |
| Humanの指示（§41 `setting_change`） | Human | Controller（承認済みState Patchを適用） | 承認記録（43.4） |
| Claudeの提案の承認（§15） | Claude（提案のみ） | Controller（承認済みState Patchを適用） | 承認記録（43.4） |

- Claudeの提案はHumanが承認するまで反映しない。未承認の提案は作業中データとし、Canonicalに含めない。
- Claudeが提案を出すのは、章完了後の状態更新（§15）と、Humanの指示への振り分け（§41）の時だけとする。それ以外の時点でClaudeが設定変更を提案することはない。
- AGYはいかなる経路でも保護対象へ書き込まない。

### 43.4 承認記録

Controllerが保護対象へ書き込むには、承認記録が必要である。

    approval_id: A-0032
    source: instruction        # instruction | proposal
    instruction_id: INS-0012   # sourceがinstructionの場合
    job_id: job-123            # sourceがproposalの場合
    patch_sha256: ...
    targets:
      - characters/C002.yaml
    approved_by: human
    approved_at: 2026-09-28T20:10:00+09:00

- ControllerはPatchを適用する直前に、Patchのhashが承認記録の `patch_sha256` と一致し、Patchの `target` が承認記録の `targets` に含まれることを確認する。一致しなければ適用しない。
- Patchは§15と同じく `base_hash` を持ち、承認後に対象ファイルが変わっていた場合は適用しない（再提案とする）。
- 承認記録を使ったcommitには `Approval-Id: A-0032` を付ける。
- 1つの承認記録は1回だけ使える。

### 43.5 機械的な強制

1. **AGYの実行環境**：保護対象はマウントしないか、読み取り専用とする（Phase 0 Spike-02で方式を確認する）。
2. **AI作業branch**：保護対象への変更が1つでもあればJob全体をRejectする（§14）。
3. **mainへのcommit**：Controllerが作成するcommitで保護対象に変更がある場合、`human-edit` または有効な `Approval-Id` がなければcommitしない。
4. **外部での変更**：HumanがUIを経由せずにローカルで保護対象を変更したcommitは許可する（Humanの行為であるため）。ただし、Controllerは次回同期時にこれを検出し、「外部で変更」としてUIに表示し、影響範囲分析（§16）の実行を促す。
5. **Claude**：読み取り専用で実行し（§5）、保護対象への書込み手段を持たない。

### 43.6 UI

- 作品ツリーの設定ファイルには保護表示を付ける。
- 設定ファイルを開くと、内容・変更履歴（経路と承認記録）・未反映の提案を表示する。
- 設定ファイルを開いた状態では、指示バーのscopeは `setting` に固定する。
- 直接編集の保存時に、影響範囲分析を実行するかを選ぶ（既定は実行、§42.4）。
- 提案は「承認して反映」「却下」を選ぶ。却下した提案は保存しない。

### 43.7 取り消し

- 反映済みの設定変更の取り消しは、Humanの行為（直接編集、または取り消しの指示）として行う。
- 取り消しも保護対象の変更であり、43.3の経路と43.4の記録に従う。

### 43.8 v0.5での確定事項

- 承認記録の保存場所（Git上か、Controllerのデータか）と保持期間
- 外部での変更を検出する方式
- 未反映の提案の保持期間と、同じ対象への提案が重複した場合の扱い

## 44. 追補：新規作品の作成

**ステータス：v0.4追補（2026-09-28）。v0.5で正式化する。**

本節は、NovUIを使って新しい小説を始める手順（作品ごとの制作フロー）を定義する。
NovUI本体の開発フェーズ（§38 Phase 0〜5）とは別物である。

### 44.1 原則

- 1作品につき1リポジトリとする。作品同士でリポジトリ・設定・履歴を共有しない。
- リポジトリの作成はControllerが行う。Claude・AGYは関与しない。
- 作成を開始するのはHumanだけである。

### 44.2 作品ごとの制作フロー

```text
① Human：新規作品の作成を指示（作品名、リポジトリ名、テンプレート）
② Controller：ローカルにリポジトリを作成
③ Controller：テンプレートから初期構造を作成し、mainへ初期commit
④ Controller：GitHubにprivateリポジトリを作成し、push
⑤ Controller：作品の登録簿へ追加
⑥ Human：世界観・キャラクター・伏線などの初期設定を入力
   （直接編集。指示バーでClaudeに下書きを依頼し、承認して反映することも可）
⑦ Claude：設定を分析し、章構成（chapters-order・plot）を提案
⑧ Human：章構成を承認（§43の保護対象のため、承認するまで反映されない）
⑨ Human：各章のoutline.mdを作成
⑩ 以降は§18の執筆フロー（plan → 承認 → AGY執筆 → 検証 → 状態更新 → FINAL）
```

### 44.3 リポジトリ作成の手順と失敗時の扱い

- ローカルでの作成と初期commit（②③）を先に行う。正本はローカル（§3.1）であり、この時点で執筆準備は完了する。
- GitHubへの作成・push（④）が失敗しても作品は使用できる。Controllerは「未push」として表示し、Humanの操作で再試行する。
- 同じ名前のリポジトリがGitHub上に既にある場合は作成せず、Humanに確認する。既存リポジトリへの自動pushは行わない。
- 公開範囲はprivate固定とする。Controllerは公開範囲の変更、リポジトリの削除、名前の変更、所有者の移転を行わない。これらはHumanがGitHub上で直接行う。

### 44.4 初期構造

- 初期構造は、NovUIが版管理するテンプレートから作成する。
- `project.md` に `format_version` を記録する。将来の形式変更時の移行判断に使う。
- テンプレートが作成するもの：

      project.md            （作品名、format_version、作成日）
      chapters-order.md     （空）
      world/ characters/ plot/ foreshadowing/registry  （空のひな形）
      rules/                （style.md、prohibited.md のひな形）
      chapters/ reviews/
      .gitignore            （output/、認証情報、cache、実行ログを除外）

- テンプレートは内容（世界観・人物）を含まない。初期設定はHumanが入力する（⑥）。

### 44.5 作品の分離

リポジトリを分けるだけでは作品間の混入を防げないため、以下をControllerで強制する。

- **作品登録簿**：Controllerは作品ごとに、ローカルパス、リモートURL、format_version、状態を記録する。
- **作品単位の管理**：lock（§24）、Job、承認記録（§43.4）、要確認フラグは作品ごとに独立させる。ある作品の承認記録を別の作品で使うことはできない。
- **AGY**：実行環境には、対象作品の作業worktreeだけをマウントする（§14、Phase 0 Spike-02）。
- **Claude**：対象作品のディレクトリ内で実行し、他作品のファイルを読み取れないようにする。
- **Context**：AIへ渡すcontext（§17）には、対象作品のファイルだけを含める。Controllerは、contextに他作品のパスが含まれる場合、Jobを開始しない。

### 44.6 認証情報

- ControllerがGitHubでリポジトリを作成・pushするための認証情報は、必要最小限の権限とする。
- 認証情報はController側だけに置き、AGY・Claudeの実行環境には渡さない。
- 使用するトークンの形式と、リポジトリ作成に必要な権限は、Phase 0 Spike-12で確認する。

### 44.7 v0.5での確定事項

- 作品登録簿の保存場所と形式
- テンプレートの配置場所と版管理の方法
- format_versionが異なる作品を開いたときの扱い（移行、読み取り専用など）
- 作品の保管終了（アーカイブ）時の扱い


## 45. 追補：プロジェクト全体の伏線管理

**ステータス：v0.4追補（2026-10-02）。v0.5で正式化する。**

### 45.1 目的

§8.2の伏線管理を章単位だけでなく、作品全体を横断して追跡できるものとする。

伏線は章内のメモではなく、作品全体で一意なIDを持つCanonicalデータとして管理する。

### 45.2 伏線ID

伏線には必ず一意なIDを付ける。

例：

    F001
    F002
    F003

同一伏線を複数章・複数Sceneで扱う場合も同一IDを使用する。

### 45.3 registry

`foreshadowing/registry.md`を作品全体の伏線レジストリとする。

最低限、以下を管理する。

    id: F001
    name: 王家の紋章
    status: active
    importance: major
    introduced:
      - chapter: ch-001
        scene: S003
    hints:
      - chapter: ch-004
        scene: S002
      - chapter: ch-008
        scene: S001
    developments:
      - chapter: ch-010
        scene: S004
    planned_resolution:
      chapter: ch-015
      scene: S003
    resolved:
      chapter: null
      scene: null

status：

    planned
    active
    resolved
    cancelled

importance：

    minor
    normal
    major

### 45.4 章・Sceneからの参照

`outline.md`、`plan.md`、`draft.md`、`summary.md`では、該当する伏線IDを明示的に参照できる。

例：

    foreshadowing: [F001, F007]

これにより、本文上の表現だけに依存せず、同一伏線の全章における登場履歴を追跡できる。

### 45.5 Context Engine

執筆・検証時の伏線Contextは、最低限次の順で構成する。

1. 現在Sceneで参照される伏線
2. 現在Chapterでactiveな伏線
3. 過去Chapterから継続しているactiveな伏線
4. 作品全体の未回収major伏線
5. 今後の回収予定
6. 直近の伏線状態変更

全作品本文を毎回AIへ投入する方式にはしない。

### 45.6 Validator

Validatorは、少なくとも以下を検査する。

- 未回収伏線の状態
- 伏線IDの存在
- 存在しない伏線IDの参照
- introducedより前のhintになっていないか
- resolved後に未説明のhintが追加されていないか
- planned_resolutionを過ぎても未回収の場合のWARNING
- 重要伏線が章構成から消失していないか
- 伏線の状態変更とsummaryの整合性
- 伏線の回収内容が設定・plotと矛盾していないか

Validatorは「回収すべき」と独断で確定してはならない。予定変更や意図的な未回収はHumanが判断する。

### 45.7 伏線の状態変更

章完了後の状態更新（§15）で、伏線の

- introduced
- hints
- developments
- planned_resolution
- resolved
- cancelled

への変更を提案できる。

Canonicalへの反映は§43の承認経路に従う。

### 45.8 UI

UIでは、現在章だけでなく作品全体の伏線状態を確認できる。

最低限：

- active伏線一覧
- major伏線一覧
- 未回収伏線
- 回収予定章
- 回収済み伏線
- 現在Sceneとの関連
- WARNING対象

を表示できる。

### 45.9 受入条件

- 1つの伏線を複数章から同一IDで参照できる
- 未回収伏線を章をまたいで検索できる
- 回収予定と実際の回収を区別できる
- 伏線の状態変更をHuman承認付きでCanonicalへ反映できる
- Validatorが伏線ID、時系列、未回収、回収状態を検査できる


## 46. 追補：キャラクター一貫性監視

**ステータス：v0.4追補（2026-10-02）。v0.5で正式化する。**

### 46.1 目的

characters/ の設定を単にAIへContextとして渡すだけではなく、執筆された本文がキャラクター設定と一致しているかをValidatorが監視する。

対象は、性格だけではなく、話し方、一人称、二人称、人物ごとの呼称、人間関係、知識、行動傾向を含む。

### 46.2 キャラクター設定

characters/<character-id>.mdには、可能な限り機械的に検証可能な形式で以下を定義する。

    id: C001
    name: 山田太郎

    personality:
      - 冷静
      - 弱みを見せない

    speech:
      first_person: 俺
      sentence_style:
        - 短い
        - 断定的
      forbidden:
        - 僕
        - 基本的な敬語

    address:
      C002: 美咲
      C003: 先生
      default: お前

    behavior:
      - 危険時に仲間を優先する

    knowledge:
      - K001
      - K004

自由記述の設定を禁止するものではないが、監視対象にしたい項目は構造化する。

### 46.3 監視対象

Validatorは最低限、以下を検査する。

#### A. 一人称

設定：

    first_person: 俺

本文：

    「僕はそう思わない。」

この場合WARNINGを生成する。

#### B. 二人称

相手ごとの二人称設定と一致しているかを確認する。

#### C. 呼称

人物間の呼び方を監視する。

例：

    C001 → C002 = 「美咲」

本文：

    「美咲ちゃん、待ってくれ。」

設定上の変更が存在しなければWARNINGとする。

#### D. 口調

- 敬語／タメ口
- 語尾
- 文の長さ
- 断定／婉曲
- 特徴的な口癖
- 禁止語

などを検査対象とする。

#### E. 性格

性格設定と本文中の行動・発言に明確な不整合がないかを検査する。

性格判定は完全な機械判定を前提とせず、Claude ValidatorによるWARNING/STOP判定を基本とする。

#### F. 行動傾向

設定された価値観・行動原則と矛盾する行動を検出する。

#### G. 知識

キャラクターがまだ知らない情報を発言・認識していないかを検査する。

これは§15のsummaryに保存された「各人物の知識」と連携する。

#### H. 人間関係

relationshipの状態と、本文中の呼称・態度・行動が矛盾していないかを検査する。

### 46.4 時系列によるルール変更

キャラクター設定は物語途中で変化することがある。

そのため、ルール変更には適用開始地点を指定できる。

例：

    address:
      C002:
        default: お前
        changes:
          - value: 君
            from:
              chapter: ch-018
              scene: S004
            reason: 関係性の変化

この場合、ch-018/S004以前は「お前」、それ以降は「君」を標準とする。

一時的な例外も指定できる。

    exceptions:
      - rule: speech.formality
        from:
          chapter: ch-010
          scene: S002
        to:
          chapter: ch-010
          scene: S003
        reason: 公的な場面

### 46.5 設定変更との連携

キャラクターの性格、口調、呼称、人間関係等を変更する場合は§43の設定保護を適用する。

AIが本文中の変化だけを見てCanonical設定を自動変更してはならない。

物語上の変化が発生した場合：

    本文
    ↓
    Claudeによる状態変化提案
    ↓
    Human承認
    ↓
    Character設定Patch
    ↓
    Validator
    ↓
    Git反映

とする。

### 46.6 Validator出力

キャラクター監視結果は既存のreview.mdへ保存する。

例：

    status: WARNING
    checks:
      character: WARNING
    character_issues:
      - character_id: C001
        rule: speech.first_person
        expected: 俺
        actual: 僕
        chapter: ch-012
        scene: S003
        severity: warning

別の例：

    - character_id: C001
      rule: address.C002
      expected: 美咲
      actual: 美咲ちゃん
      chapter: ch-012
      scene: S004
      severity: warning

### 46.7 自動修正の扱い

Validatorは原則として問題を検出するだけとする。

- 明確な表記違反：HumanがREQUEST_FIXを選択した場合、range_editとして修正可能
- 性格・人間関係・知識の矛盾：自動修正しない
- 設定変更を伴う場合：setting_changeへ振り分ける
- 意図的な例外：Humanが承認してルール変更または例外を登録する

AIが「設定に合わせるため」に物語上の意味を勝手に変更してはならない。

### 46.8 Context Engineとの連携

現在Sceneの登場人物については、Context Engineが優先的に以下を渡す。

1. キャラクター基本設定
2. 現在の性格・状態
3. 一人称・二人称
4. 登場人物ごとの呼称
5. 現在の人間関係
6. 現在の知識
7. 適用中の例外
8. 直近の設定変更

全キャラクターの全文を毎回投入するのではなく、現在Sceneに必要な人物を優先する。

### 46.9 受入条件

- キャラクターごとに一人称を検査できる
- 相手ごとの呼称を検査できる
- 口調ルールを検査できる
- 性格・行動傾向をValidatorで検査できる
- 知識状態をsummaryと照合できる
- 物語途中の設定変更を適用開始Scene付きで管理できる
- 意図的な例外を登録できる
- 設定変更なしにAIがCanonicalキャラクター設定を書き換えられない
- 検出結果をreview.mdへ機械可読形式で保存できる

### 46.10 全体監査への追加

§34のCharacters監査を以下まで拡張する。

Characters：

- 性格
- 口調
- 一人称
- 二人称
- 人物ごとの呼称
- 語彙・口癖
- 行動傾向
- 能力
- 知識
- 人間関係
- 時系列による設定変更
- 例外ルール

Foreshadowing：

- 作品全体の伏線ID
- 初出
- ヒント
- 発展
- 回収予定
- 実際の回収
- 未回収
- cancelled
- 時系列
- 他設定との整合性
