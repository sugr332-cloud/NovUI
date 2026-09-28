# Phase 0 技術スパイク実施手順書

**ステータス：READ-ONLY / 本体実装前**
**目的：v0.5仕様を確定する前に、Controller設計を左右する技術的未知点を実測する。**

## 1. 位置付け

本スパイクはNovUI本体のController実装ではない。AGY CLI、Claude CLI、実行隔離、構造化出力、Context再現性、文章品質比較を小規模に検証し、結果を次期仕様へ反映する。

本スパイクでは本体Controller、Web UI、章管理機能などを実装しない。

## 2. 実行責務

Human → Claude READ-ONLY分析・手順レビュー → Human承認 → AGYが検証用スクリプト/設定を実行 → 実測結果保存 → Claudeレビュー → v0.5へ反映。

AGYに本体Controllerの実装をさせない。AGYが変更できるのはスパイク専用ディレクトリと検証用成果物に限定する。

## 3. 実行環境

- Bazzite
- Podman
- NovUI Git repository
- AGY CLI
- Claude CLI

Bazziteではdistroboxを使用しない。まず素のPodmanで検証する。distroboxは既定でHomeをマウントするため、今回の隔離検証には適さない。

原則として、AI作業用worktreeだけをRW bind mountし、本体Git管理領域はmountしない。必要な場合でもROとする。Homeはmountしない。

## 4. Spike-00：事前確認

実験開始前の環境を固定する。

記録対象：
- OS / kernel
- Podman version
- Git version
- AGY CLI version
- Claude CLI version
- repository HEAD
- 実験用branch/worktree
- CLIから取得可能なmodel情報
- 実験日時

正確な内部model versionをCLIから取得できない場合は、取得可能な範囲だけ記録する。少なくともCLI名、CLI version、reported model、Job実行日時を記録する。

## 5. Spike-01：AGY非対話実行

### 検証項目
- 非対話起動
- stdin入力
- stdout取得
- stderr取得
- 正常終了コード取得
- 異常終了コード取得
- timeout
- 作業ディレクトリ固定
- 予期しないinteractive promptの検出
- CLI/model情報取得

### PASS基準

Human入力なしで開始でき、stdout/stderrと終了コードを取得でき、外部からtimeoutを検出でき、指定worktreeを作業ディレクトリとして実行でき、予期しない対話待ちを検出できること。

### FAIL時

AGY CLIの別非対話モード、bounded PTY/PTY監視、限定的なinteractive confirmation処理を順に検討する。無期限のinteractive待機は採用しない。

## 6. Spike-02：AGY作業領域隔離

PodmanでAGYを実行し、必要なworktreeだけをRW bind mountする。

意図的に以下への書込みを試す：
1. worktree内
2. worktreeの親ディレクトリ
3. Home
4. /tmp等の想定外領域
5. 本体Git管理領域
6. worktreeの.git参照先
7. .git/config
8. .git/hooks/

### PASS基準

worktree内の許可対象には書ける一方、worktree外、Home、本体Git管理領域への書込みが失敗すること。

Git差分検査は実行隔離の代替ではない。worktree外への書込みはGitでは完全に検出できないため、実行隔離を一次防御、Git/.git hash検査を二次防御とする。

### FAIL時の代替案

AGY専用OSユーザー、専用HOME、filesystem permissionによるworktree限定、別実行環境の順に検証する。

## 7. Spike-03：.git保護

worktree内の.gitは実体ではない。Git common directoryを確認し、本体Git管理領域を検査対象とする。

確認コマンド：

    git rev-parse --git-common-dir

検査対象：
- 本体 .git/config
- 本体 .git/hooks/
- .git/worktrees/<name>/

AGY実行前後で対象ファイル一覧とSHA-256を記録し比較する。

### PASS基準

.git/config、.git/hooks/、.git/worktrees/<name>/に変更がなく、containerから直接書込みを試みても拒否されること。

## 8. Spike-04：requests.md append-only

既存requests.mdを作り、AGYに新規要求を追加させる。既存要求の変更・削除も意図的に試す。

### PASS基準

新規要求のappendは許可し、既存要求の変更・削除はrejectすること。

## 9. Spike-05：Claude Read-only構造化出力

想定フロー：

    Claude CLI
       | stdout
       v
    Controller相当
       | schema validation
       +-- valid   -> file write
       +-- invalid -> retry / FAILED

plan、state update proposal、summary、integrity validation、writing reviewを対象とする。

意図的に正常JSON、前置き付きJSON、後置き付きJSON、壊れたJSON、必須項目欠落、型違い、未知フィールド、不正Patchを用意する。

### PASS基準

schema準拠出力だけがファイル化され、schema不一致はcanonical fileへ書かれず、再要求またはFAILEDになること。Claude自身がcanonical fileを直接変更しないこと。

## 10. Spike-06：JSON Patch / State Patch

State PatchはJSON Patch RFC 6902形式を基本とする。

最低限：

    type: state_patch
    target: ...
    base_hash: ...
    operations: ...

検証対象：add、replace、remove、base_hash一致、不一致、不正path、存在しないtarget、schema違反。

### PASS基準

base_hash一致時だけ適用し、不一致はrejectする。knowledge等の蓄積データを全体replaceしない。

## 11. Spike-07：選択範囲編集の境界検査

prefixは編集範囲の開始位置より前に存在する**ファイル全体**、suffixは編集範囲の終了位置以降に存在する**ファイル全体**と定義する。anchor前後の数十文字ではない。

編集前：

    FILE = PREFIX + TARGET + SUFFIX

編集後：

    FILE' = PREFIX + TARGET' + SUFFIX

### PASS基準

PREFIXとSUFFIXがバイト単位で完全一致し、TARGETだけが変更されていること。いずれか不一致ならrejectする。anchor自体が置換されても検査できること。

## 12. Spike-08：Context再現性

Job開始時にcontextをパスだけでなく内容の版として記録する。

最低限：
- Job基準commit
- 各context fileのpath
- 各context fileのhash
- 実際にAIへ渡した内容、またはcommit/hashから再現可能な参照

実験ではJob開始後にcontext fileを意図的に変更し、Validatorが現在の最新版ではなく、AGYが見た版と同一内容を検証できることを確認する。

## 13. Spike-09：文章品質比較

同一のoutline、plan、world、characters、foreshadowing、writing constraintsをClaudeとAGYへ渡し、双方の章を作成する。

モデル名を伏せてHumanが比較する。

評価項目：
- キャラクターの声
- 日本語の自然さ
- 情景描写
- 心情表現
- plan準拠度
- 設定整合性
- 冗長さ
- 文体一貫性
- 読みやすさ

1〜2章では統計的な優劣を判断せず、今回のNovUIにおける初期判断材料とする。Claude自身にClaudeとAGYの優劣を最終判定させない。

## 14. Spike-10：モデル情報

Jobごとに可能な範囲でprovider、CLI name、CLI version、reported model name、reported model version、timestamp、prompt/context hashを保存する。

CLIが正確なmodel versionを返さない場合は、その事実自体を記録する。

## 15. Spike-11：AGY認証情報

Podman隔離下でAGYを実行するための認証情報受け渡し方法を確認する。

原則：
- credentialsをGitへ保存しない
- worktreeへ保存しない
- container imageへ焼き込まない
- Home全体をmountしない

環境変数、Podman secret、必要最小限のread-only mount等を比較する。

### PASS基準

AGYが必要な認証だけを取得でき、worktreeからcredentialsを読み取れず、Git管理対象へcredentialsが残らず、container終了後にsecretが残らないこと。

## 16. Job記録

各Spike実行について、spike_id、job_id、timestamp、host、OS、kernel、podman_version、git_version、cli_name、cli_version、reported_model、repository_head、worktree、context_hashes、command、exit_code、stdout_summary、stderr_summary、result、failure_reason、alternative_usedを記録する。

secret、API key、credential本体は保存しない。

## 17. 総合PASS条件

以下が成立すること：
1. AGY非対話実行
2. timeoutと終了コード取得
3. worktree外書込みの隔離
4. 本体Git領域への書込み防止
5. .git/config、.git/hooks等のhash検査
6. requests.md append-only検査
7. Claude構造化出力のschema検証
8. State Patchのbase_hash検証
9. 選択範囲外差分検査
10. AGY/Validatorのcontext再現
11. 認証情報の安全な受け渡し
12. 文章比較用サンプル作成
13. CLI/model metadata記録

## 18. 不合格時

FAILについて、原因、再現条件、代替案、代替案の検証結果、v0.5への影響を記録する。

実行隔離が成立しない場合は、Controller設計そのものを再検討する。

## 19. スパイク完了後

実測結果をClaudeがREAD-ONLYでレビューし、v0.4との差分整理 → v0.5仕様案 → Humanレビュー → Human承認 → v0.5確定 → Phase 1 Controller実装計画の順に進める。

Phase 0の結果を反映せずにController本体を実装しない。

## 20. 現時点の設計仮説

- Claude：設計・計画・監督・整合性検証・文章品質レビュー
- AGY：本文生成・本文編集
- Controller：境界強制・プロセス管理・schema検証・diff検査
- Human：作品内容と採用判断

ClaudeとAGYの本文生成品質についてはSpike-09のブラインド比較結果を優先し、事前の優劣判断を仕様へ固定しない。

## 21. v0.5へ反映予定

- Job種別ごとの固定allowlist
- worktree外実行隔離
- .git実体の保護方式
- Claude stdout → Controller → fileの出力経路
- schema定義
- JSON Patch形式
- State Patch base_hash
- 選択範囲のprefix/suffix定義
- context内容版の記録方式
- Validator入力の再現方法
- Claude整合性Gateと文章品質Reviewの分離
- Review指摘のanchor付き構造化形式
- Review→Rewriteループ上限
- requests.md append-only
- Jobごとのmodel/CLI metadata
- AI-like patternのController検査
- AGY認証情報の受け渡し方式
- Git競合・Human直接編集の最終仕様

