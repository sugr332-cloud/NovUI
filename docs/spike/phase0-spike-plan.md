# Phase 0 技術スパイク実施手順書（改訂版）

**ステータス：READ-ONLY / 本体実装前**
**基準仕様：novel-system-spec-v0.4（commit 5b85f2b）**
**目的：v0.5仕様を確定する前に、Controller設計を左右する「実行しないと分からない技術的未知点」だけを実測する。**

---

## 1. 位置付けとスコープ

本スパイクはNovUI本体のController実装ではない。
検証対象は、CLIの実挙動、実行環境、隔離、認証、モデル情報、文章品質など、**実際に動かさなければ結果が分からない項目**に限定する。

### 1.1 スパイクで扱うもの

| ID | 内容 |
|---|---|
| Spike-00 | 事前確認・環境固定 |
| Spike-01 | AGY非対話実行 |
| Spike-11 | AGY認証情報の受け渡し（Spike-02に先行） |
| Spike-02 | Podman実行隔離（Spike-11で確定した認証方式を使用） |
| Spike-03 | .git保護 |
| Spike-05 | Claude CLI出力挙動 |
| Spike-10 | モデル情報の取得可否 |
| Spike-09 | Claude / AGY 文章ブラインド比較（独立・並行可） |

### 1.2 スパイクで扱わないもの（Phase 1へ移管）

以下は環境やCLI挙動に依存しない、Controller内部の決定的ロジックである。
スパイクで検証すると実質的にController実装になるため、**Phase 1で単体テスト込みで実装する仕様**として扱う。

* requests.md append-only検査
* JSON Patch（RFC 6902）/ State Patch適用
* base_hash検証
* prefix/suffix（ファイル全体）による範囲外差分検査
* Context内容版（commit/hash）の記録と再現
* Claude出力のschema検証器そのもの

スパイクでは上記の実装・検査コードを作成しない。

---

## 2. 実行責務

```text
Human
 ↓
Claude：本手順書のREAD-ONLYレビュー
 ↓
Human承認
 ↓
AGY：検証スクリプトの作成のみ（実行しない）
 ↓
Human：スクリプト内容の確認
 ↓
Human：スクリプト実行
 ↓
結果保存
 ↓
Claude：結果のREAD-ONLYレビュー
 ↓
v0.5へ反映
```

原則：

* AGYは**検証スクリプトを書くまで**とする。podman、AGY CLI、Claude CLI、隔離試験をAGY自身に実行させない。
* 隔離が未検証の段階で、非隔離のAGYにホスト上の隔離環境構築・実行を行わせない。
* 隔離試験はAGYに「外へ書こうとさせる」方式ではなく、**同一コンテナ設定で通常のshell scriptから書込みを試行する**方式で行う。隔離はコンテナ設定の性質であり、AGYの振る舞いに依存させない。
* Claudeは結果をレビューするが、Spike-09の文章優劣を最終判定しない。

---

## 3. 成果物の配置と書込み許可

### 3.1 配置

| 種別 | 配置 | Git管理 |
|---|---|---|
| 検証スクリプト | `spike/phase0/` | 対象 |
| スパイク用ignore設定 | `spike/phase0/.gitignore` | 対象 |
| 生ログ | `spike/phase0/.logs/` | 対象外 |
| 一時作業領域 | `spike/phase0/.work/` | 対象外 |
| Spike-09 ラベル対応表 | `spike/phase0/.work/spike09-mapping.*` | 対象外 |
| 実行結果 | `docs/spike/results/` | 対象 |

`spike/phase0/.gitignore` で `.logs/` と `.work/` を除外する。リポジトリルートの `.gitignore` は変更しない。

### 3.2 AGYの書込みallowlist（スクリプト作成Job）

```text
許可：
  spike/phase0/**

禁止：
  上記以外すべて
  （docs/、SPECIFICATION類、ルート.gitignore、.git、本体ソースを含む）
```

AGY作業後、Humanが以下で確認する。

```text
git status --porcelain --untracked-files=all
git status --porcelain --ignored
```

allowlist外の変更が1つでもあれば、作業全体をrejectする。

`docs/spike/results/` への書込みは、Humanが実行したスクリプトが行う。

### 3.3 生ログの扱い

* 生ログは `spike/phase0/.logs/` に保存し、Git管理外とする。
* 結果ファイルにstdout/stderrの要約は置かない（`stdout_summary` は廃止）。
* 結果ファイルには、生ログのファイル名とhashのみ記録する。
* 生ログ・結果ファイルに認証情報・APIキー・トークン本体を出力しない。

---

## 4. 実行環境

* Bazzite
* Podman（rootless）
* Git
* NovUI Git repository
* AGY CLI
* Claude CLI

distroboxは使用しない。distroboxは既定でHomeをマウントするため、隔離検証に適さない。

コンテナ設定の原則：

* AI作業用worktreeのみRW bind mountする。
* Git common directory（本体 `.git`）はmountしない。必要な場合のみRO（Spike-03で比較）。
* Homeはmountしない。
* 認証情報はSpike-11で決定する最小限の方法で渡す。

---

## 5. timeout値（スパイク初期値）

| 対象 | 値 |
|---|---|
| AGY 1回の実行（全体） | 900秒 |
| AGY 出力停止（stdout/stderrが無出力） | 180秒 |
| Claude 1回の実行（全体） | 300秒 |
| Claude 出力停止 | 120秒 |
| 隔離試験スクリプト 1本 | 60秒 |

値はスパイク用の初期値であり、実測結果を踏まえてv0.5で確定する。
timeout発生時は、発生した種別（全体/出力停止）、経過時間、終了させた方法（SIGTERM/SIGKILL）を記録する。

---

## 6. 実行順序

```text
Spike-00
   ↓
Spike-01
   ↓
Spike-11
   ↓
Spike-02
   ↓
Spike-03
   ↓
Spike-05
   ↓
Spike-10

Spike-09：上記と独立して並行実施可能
```

Spike-02はpodman内でAGYを動かすため認証が必要である。先にSpike-11で認証方式を確定し、Spike-02ではその方式を使用する。

---

## 7. Spike-00：事前確認

実験開始前の環境を固定し、記録する。

記録対象：

* OS / kernel
* SELinuxの状態（enforcing / permissive）
* Podman version
* rootless / rootful の別
* Git version
* AGY CLI version
* Claude CLI version
* repository HEAD
* 実験用branch / worktree のパス
* Git common directory（`git rev-parse --git-common-dir`）
* 実験日時
* AGY CLI・Claude CLIの `--help` 出力（生ログとして保存）

`--help` 出力は、以降のSpikeで使用するフラグの根拠とする。
存在を確認していないフラグをスクリプトに前提として書かない。

### PASS基準

上記がすべて記録されていること。

---

## 8. Spike-01：AGY非対話実行（ホスト上）

隔離前に、AGY CLIが非対話で制御可能かを確認する。
作業対象は使い捨てのworktree（`spike/phase0/.work/` 配下のテスト用ディレクトリでも可）とする。

### 検証項目

* Human入力なしで開始できるか
* stdin経由で指示を渡せるか
* stdout / stderr を分離して取得できるか
* 正常終了時の終了コード
* 異常終了時の終了コード（不正な引数、認証なし等）
* 外部からのtimeout（全体・出力停止）と強制終了
* 作業ディレクトリの固定
* 予期しない対話プロンプトの発生と、その検出可否
* 実行中の子プロセスが、強制終了後に残らないか

### PASS基準

* Human入力なしで開始・終了できる
* stdout / stderr / 終了コードを取得できる
* 5章の値でtimeoutを検出し、子プロセスを含めて終了できる
* 指定ディレクトリを作業ディレクトリとして実行できる
* 対話待ちが発生した場合、出力停止timeoutで検出できる

### FAIL時の代替案

1. AGY CLIの別の非対話モード
2. PTY経由の監視（出力パターンと出力停止による検出）
3. 限定的な対話確認処理（既知のプロンプトのみ）

無期限の対話待機は採用しない。

---

## 9. Spike-11 → Spike-02：認証と実行隔離

### 9.1 Spike-11：AGY認証情報

AGYは認証情報を使用する当事者であるため、**AGY自身が認証情報を読めないようにすることは要件としない**。
検証するのは「漏洩しないこと」と「必要最小限の渡し方で動くこと」である。

比較対象：

* 環境変数
* Podman secret
* 認証ファイル・ディレクトリの最小限RO mount

原則：

* 認証情報をGitへ保存しない
* worktreeへ保存しない
* container imageへ焼き込まない
* Home全体をmountしない

#### 検証項目

* 各方式でAGYが認証に成功するか
* RO mountの場合、トークン更新時に書込みが必要か（更新失敗の有無）。トークン期限切れを再現できない場合は、その旨を記録する
* 実行後、worktreeの差分・未追跡ファイル・ignoredファイルに認証情報の文字列が含まれないか
* コンテナ削除後に、認証情報がコンテナ・イメージに残らないか（`podman inspect` 等で確認）

認証情報の文字列照合は、スクリプトが「一致あり / なし」のみを出力し、認証情報そのものをログに出さない方式とする。

#### PASS基準

* AGYが必要な認証のみで動作する
* worktreeおよびGit管理対象に認証情報が残らない
* コンテナ終了後に認証情報が残らない
* 選択した方式でトークン更新に問題がない、または問題の有無が記録されている

### 9.2 Spike-02：実行隔離

9.1で確定した認証方式を使用する。

#### 試験方法

AGYの実行と**同一のコンテナ設定**（mount、ユーザー、SELinuxラベル、userns）で、通常のshell scriptを実行し、書込みを試行する。

試行対象：

1. worktree内（許可対象）
2. worktreeの親ディレクトリ
3. Home
4. `/tmp`
5. Git common directory（本体 `.git`）
6. worktree内の `.git` 参照ファイル
7. `.git/config`
8. `.git/hooks/`

加えて、コンテナ内の `/proc/self/mountinfo` を記録し、**意図したmount以外にホストのパスが存在しない**ことを確認する。

`/tmp` 等のコンテナ内ローカル領域への書込みは、ホストのパスに反映されない限り許容する。判定基準は「ホスト上の、worktree外のパスが変化しないこと」とする。

#### Bazzite固有の確認項目

* **SELinux**：bind mountにラベル指定なし / `:z` / `:Z` の3通りで、アクセス可否とホスト側への影響を記録する。
* **UID対応付け**：`--userns=keep-id` の有無で、コンテナ内で作成したファイルのホスト上の所有者を記録する。Humanがホスト上でそのファイルを編集・commitできることを確認する。
* **.git参照ファイル**：Git common directoryをmountしない場合、worktree内の `.git` 参照ファイルの指す先がコンテナ内に存在しない。この状態でAGYを実行し、AGYがgitコマンドの失敗に対して何をするか（`git init`、`.git` の削除・作成など）を記録する。

#### PASS基準

* worktree内の許可対象には書き込める
* worktree外、Home、Git common directoryへの書込みが失敗する、またはコンテナ内に存在しない
* mountinfoに意図しないホストパスがない
* コンテナで作成したファイルを、ホスト上のHumanが通常どおり編集・commitできる

Git差分検査は実行隔離の代替ではない。実行隔離を一次防御、Git・hash検査（Spike-03）を二次防御とする。

#### FAIL時の代替案

1. AGY専用OSユーザー＋専用HOME
2. filesystem permissionによるworktree限定
3. 別実行環境（VM等）

実行隔離がいずれでも成立しない場合、Controller設計そのものを再検討する。

---

## 10. Spike-03：.git保護

worktree内の `.git` は実体ではない。実体はGit common directoryにある。

```text
git rev-parse --git-common-dir
```

### 検査対象

* 本体 `.git/config`
* 本体 `.git/hooks/` 配下
* `.git/worktrees/<name>/` 配下
* worktree内の `.git` 参照ファイル

AGY実行前後で、対象ファイル一覧とSHA-256をホスト側で記録し比較する。

### 比較する構成

1. Git common directoryをmountしない
2. Git common directoryをRO mount

構成2では、`git status` 等がindexのロック・更新で失敗・警告するかも記録する。

### PASS基準

* 検査対象にAGY実行前後で変更がない
* コンテナ内からの直接書込みが拒否される、またはパスが存在しない
* どちらの構成を採用するかを判断できる材料が揃っている

---

## 11. Spike-05：Claude CLI出力挙動

確認するのは**Claude CLIが実際にどのような出力を返すか**であり、Controllerのschema検証器ではない。

### 11.1 読み取り専用実行

* Claude CLIのツール制限・権限設定（Spike-00の `--help` で確認したもの）を用いて、Write / Edit / Bash 等を禁止した状態で起動する。
* ファイルの作成・変更を明示的に指示するプロンプトを与え、実際にファイルが変更されないことを確認する。
* 実行前後で作業ディレクトリの `git status` とファイルhashを比較する。

### 11.2 構造化出力

* 固定のJSON Schema（plan用の簡易schema 1種）を用意し、schemaに従ったJSONのみを出力するよう指示する。
* CLIが提供する構造化出力オプションがあれば、有無の両方で実行する。
* 同一プロンプトを10回実行し、以下を集計する。

集計項目：

* そのままJSONとしてparseできた回数
* コードフェンスで囲まれていた回数
* 前置き・後置きの文章が付いていた回数
* schemaに適合した回数（既存のJSON Schemaライブラリで判定）
* 必須項目の欠落・型違い・未知フィールドの回数

schema適合の判定には既存ライブラリを使い、独自の検証ロジックは作らない。

### PASS基準

* 読み取り専用設定でファイルが変更されない
* 構造化出力の適合率と、不適合の典型パターンが記録されている

適合率そのものに合否の閾値は設けない。結果はv0.5のretry方針・出力形式の決定に使う。

---

## 12. Spike-10：モデル情報

各CLIから、Job単位で取得できる情報を確認する。

記録対象：

* provider
* CLI name
* CLI version
* reported model name
* reported model version
* 取得方法（フラグ、出力中のフィールド、設定ファイル等）
* timestamp

CLIが正確なモデルバージョンを返さない場合は、**その事実自体を記録する**。

### PASS基準

各CLIについて、取得できる項目と取得できない項目が確定していること。

---

## 13. Spike-09：Claude / AGY 文章ブラインド比較（独立）

### 入力

同一の入力パッケージを両方に渡す。

* outline
* plan
* world
* characters
* foreshadowing
* writing constraints（視点、文体、目標文字数）

目標文字数は両者同一とする（例：4,000〜6,000字）。

### 生成

* 1〜2章分を生成する。
* ClaudeもAGYも、本文は標準出力で受け取り、スクリプトがファイル化する。

### 匿名化

* 生成物からモデル名・前置き・後置き・見出し形式の差を除去し、形式を揃える。
* スクリプトがランダムにA / Bのラベルを付ける。
* ラベルとモデルの対応表は `spike/phase0/.work/` に保存し、評価完了までHumanは開かない。

### 評価

Humanが以下を5段階で評価し、所感を記録する。

* キャラクターの声
* 日本語の自然さ
* 情景描写
* 心情表現
* plan準拠度
* 設定整合性
* 冗長さ
* 文体の一貫性
* 読みやすさ

評価完了後に対応表を開き、結果を `docs/spike/results/` に記録する。

### 注意

* 1〜2章では統計的な優劣は判断できない。今回のNovUIにおける初期判断材料として扱う。
* Claudeに優劣の最終判定をさせない。

---

## 14. 実行記録

各Spikeの実行ごとに、以下を `docs/spike/results/` に記録する。

* spike_id
* run_id
* timestamp
* host
* OS / kernel
* SELinux状態
* podman_version
* git_version
* cli_name
* cli_version
* reported_model
* repository_head
* worktree
* container設定（mount一覧、ラベル、userns）
* command（認証情報を含まない形）
* exit_code
* 生ログのファイル名とhash
* result（PASS / FAIL）
* failure_reason
* alternative_used

認証情報・APIキー・トークン本体は記録しない。

---

## 15. 総合PASS条件

1. 環境情報が記録されている（Spike-00）
2. AGYを非対話で起動・終了でき、timeoutと終了コードを取得できる（Spike-01）
3. 認証情報が漏洩せず、最小限の方法でAGYが動作する（Spike-11）
4. worktree外への書込みが実行隔離で防止される（Spike-02）
5. Git common directoryと `.git` 参照ファイルが保護され、構成を選択できる（Spike-03）
6. Claude CLIを読み取り専用で実行でき、構造化出力の挙動が記録されている（Spike-05）
7. 各CLIのモデル情報の取得可否が確定している（Spike-10）
8. 文章比較のサンプルとHuman評価が記録されている（Spike-09）

---

## 16. 不合格時

FAILごとに以下を記録する。

* 原因
* 再現条件
* 試した代替案
* 代替案の結果
* v0.5への影響

実行隔離（Spike-02）が成立しない場合は、v0.5の前にController設計を再検討する。

---

## 17. スパイク完了後

```text
実測結果
 ↓
Claude READ-ONLYレビュー
 ↓
v0.4との差分整理
 ↓
v0.5仕様案
 ↓
Humanレビュー・承認
 ↓
v0.5確定
 ↓
Phase 1 Controller実装計画
```

Phase 0の結果を反映せずにController本体を実装しない。

---

## 18. Phase 1へ移管する仕様

以下はv0.5で仕様を確定し、Phase 1で単体テスト込みで実装する。

* Job種別ごとの固定allowlist
* requests.md append-only検査
* JSON Patch（RFC 6902）/ State Patch
* base_hash検証
* prefix/suffix（ファイル全体）による範囲外差分検査
* Context内容版（Job基準commit、各ファイルhash）の記録とValidatorへの同一版投入
* Claude stdout → schema検証 → Controller → file の出力経路
* Claude整合性Gateと文章品質Reviewの分離
* Review指摘のanchor付き構造化形式とRewriteループ上限
* AI的定型表現のController側機械検査
* Jobごとのmodel / CLI metadata記録
* Git競合・Human直接編集の最終仕様
* 人間の指示入口と振り分け（v0.4 §41：instruction_routing、Job種別 range_edit / chapter_rewrite / plan_revision / setting_change）

---

## 19. 現時点の設計仮説

* Claude：設計・計画・監督・整合性検証・文章品質レビュー
* AGY：本文生成・本文編集
* Controller：境界強制・プロセス管理・schema検証・diff検査
* Human：作品内容と採用判断

ClaudeとAGYの本文生成品質については、Spike-09の結果を優先し、事前の優劣判断を仕様に固定しない。
