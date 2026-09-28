# 小説作成支援システム 仕様書 v0.3

**ステータス：READ-ONLY / 実装開始前**

## 1. システム概要

Claude CLI と AGY CLI を組み合わせ、長編小説の企画・設定・執筆・監視・検証・修正を一元管理する。

基本フロー：

```
人間
 ↓
Claude
「何を書くべきか決める」
 ↓
AGY
「実際に本文を書く」
 ↓
Claude
「方針・設定から逸脱していないか監視」
 ↓
人間
「採用・修正を判断」
```

作品データは Git Repository を正本とし、各章の本文を独立した Markdown ファイルとして管理する。

## 2. 目的

- 世界観の一元管理
- キャラクター設定の一元管理
- ストーリー構造の管理
- 伏線の管理
- 各章の概要管理
- 各章の執筆方針生成
- AI による本文執筆
- AI による執筆中監視
- AI による設定逸脱検出
- 人間による部分修正
- 修正指示の AI による生成
- AGY による実際の本文編集
- 完成後の整合性監査
- Markdown による完成原稿出力
- Git による変更履歴・復元・差分管理

## 3. 基本原則

### 3.1 Git Repository を作品の正本とする

作品に関する重要データは Git Repository で管理する。

Claude や AGY の CLI セッション、Controller のメモリ、Web UI の状態を作品の正本とはしない。

### 3.2 本文は章単位で分離する

全作品を 1 つの本文ファイルで管理しない。

```
chapters/
├─ 001/
│  └─ draft.md
├─ 002/
│  └─ draft.md
├─ 003/
│  └─ draft.md
└─ ...
```

各章の `draft.md` を本文原本とする。

### 3.3 output/novel.md は生成物

全章を結合した `output/novel.md` は完成原稿の生成物とする。

直接編集せず、修正は該当章の `draft.md` に対して行い、再生成する。

## 4. システム開発自体の AI 役割分担

本システムそのものの設計・実装・検証についても、小説作成時と同じ責務分離を適用する。

### 4.1 Claude CLI：設計・監督・検証

Claude は本システムの開発において、設計者・監督者・レビュアーとして扱う。

担当：

- 要件分析
- アーキテクチャ設計
- 実装計画作成
- 変更範囲の定義
- 実装方針の決定
- AGY への実装指示作成
- 実装結果のコードレビュー
- テスト計画
- テスト結果の評価
- 仕様逸脱の検出
- セキュリティ・安全性の確認
- Git 差分の確認
- 修正要求の作成
- 最終的な実装検証

Claude は原則として本システムのコードを直接書き込まない。

### 4.2 AGY CLI：実装・書込み

AGY は本システムの実装担当とする。

担当：

- ソースコード作成
- ソースコード変更
- 設定ファイル変更
- テストコード作成
- 必要なテスト実行
- Claude が承認した実装計画に基づくファイル操作

AGY は独自判断でアーキテクチャ、仕様、設計方針を変更しない。

### 4.3 システム開発フロー

基本フロー：

```
人間
 ↓
要件・変更要求
 ↓
Claude
 ↓
READ-ONLY 分析
 ↓
Claude
 ↓
実装計画
 ↓
人間確認
 ↓
AGY
 ↓
コード実装・書込み
 ↓
テスト
 ↓
Claude
 ↓
コードレビュー・仕様照合
 ↓
PASS / 修正要求
 ↓
AGY
```

### 4.4 システム開発時の禁止事項

- AGY が設計を独断で変更する
- AGY が仕様にない機能を追加する
- AGY がレビューなしに広範囲のリファクタリングを行う
- Claude が実装計画なしに直接コードを書き換える
- 検証なしに実装を完成扱いにする
- AI が無条件に commit / push する

## 5. AI の役割分担

### Claude CLI

Claude は主として編集者・構成担当・監督・検証担当とする。

担当：

- 世界観分析
- キャラクター分析
- ストーリー分析
- 伏線分析
- 章構成
- 執筆方針作成
- AGY への執筆指示生成
- AGY の監視
- 設定逸脱検出
- 完成後の整合性検証
- 修正内容の検証

Claude は原則として本文の正本を直接編集しない。

### AGY CLI

AGY は本文作成・本文編集担当とする。

担当：

- 新規章の本文執筆
- 指定範囲の文章変更
- 指定範囲の文章拡張
- Claude が作成した執筆方針に基づく本文生成

AGY は独自判断で作品設定を変更しない。

### Controller

Claude と AGY を管理する中間層。

担当：

- CLI 起動
- CLI 入力
- CLI 出力取得
- タイムアウト
- 状態管理
- ファイルロック
- 作業対象制御
- Claude / AGY 間の受け渡し
- 監視
- STOP 処理
- 差分取得
- Git 操作
- Web UI への状態通知

## 6. プロジェクト構造

```
novel-project/
│
├─ project.md
│
├─ world/
│   ├─ world.md
│   ├─ geography.md
│   ├─ history.md
│   ├─ technology.md
│   └─ terminology.md
│
├─ characters/
│   ├─ protagonist.md
│   ├─ heroine.md
│   ├─ character-001.md
│   └─ relationships.md
│
├─ plot/
│   ├─ main-story.md
│   ├─ arcs.md
│   └─ timeline.md
│
├─ foreshadowing/
│   ├─ registry.md
│   ├─ active.md
│   └─ resolved.md
│
├─ rules/
│   ├─ writing-policy.md
│   ├─ style.md
│   └─ prohibited.md
│
├─ chapters/
│   ├─ 001/
│   │   ├─ outline.md
│   │   ├─ plan.md
│   │   ├─ draft.md
│   │   └─ review.md
│   │
│   ├─ 002/
│   │   ├─ outline.md
│   │   ├─ plan.md
│   │   ├─ draft.md
│   │   └─ review.md
│   │
│   └─ ...
│
├─ reviews/
│   ├─ world.md
│   ├─ characters.md
│   ├─ story.md
│   └─ foreshadowing.md
│
└─ output/
    └─ novel.md
```

## 7. 作品設定

### 6.1 world/

世界観を管理する。

管理対象：

- 世界の構造
- 地理
- 国家
- 歴史
- 技術
- 魔法
- 社会制度
- 固有用語

### 6.2 characters/

管理対象：

- 性格
- 価値観
- 行動原理
- 口調
- 能力
- 経歴
- 知識
- 秘密
- 人間関係
- 成長予定

キャラクターには必要に応じて一意の ID を付与する。

例：

```
C001 = 主人公
C002 = ヒロイン
```

### 6.3 plot/

```
plot/
├─ main-story.md
├─ arcs.md
└─ timeline.md
```

管理対象：

- 全体ストーリー
- ストーリーアーク
- 時系列
- 重要イベント
- 因果関係
- 各章との関連

### 6.4 foreshadowing/

伏線には一意の ID を付与する。

例：

```
F001
F002
F003
```

例：

```yaml
id: F003
name: 王家の紋章
introduced: 001
planned_resolution: 010
status: active
importance: major
```

状態：

- planned
- active
- resolved
- cancelled

## 8. 章管理

1 章につき 1 ディレクトリを作成する。

例：

```
chapters/004/
```

内部：

```
outline.md
plan.md
draft.md
review.md
```

### 7.1 outline.md

人間が決める章概要。

内容：

- 章の目的
- 主な出来事
- 必須イベント
- 登場人物
- 使用する伏線
- 回収予定
- 次章への接続

### 7.2 plan.md

Claude が作成する執筆方針。

内容：

- シーン構成
- キャラクター行動
- 必須設定
- 伏線
- 禁止事項
- 文体
- 視点
- 目標文章量
- 前章との接続
- 次章への接続

### 7.3 draft.md

AGY が実際に執筆する本文。

**各章の本文原本はこのファイルとする。**

### 7.4 review.md

Claude による章単位の監査結果。

例：

```
Character: PASS
World: PASS
Timeline: PASS
Foreshadowing: PASS
Style: WARNING
```

## 9. 執筆フロー

```
人間
 ↓
outline.md
 ↓
Claude
 ↓
plan.md
 ↓
人間確認
 ↓
AGY
 ↓
draft.md
 ↓
Claude Validator
 ↓
PASS / WARNING / STOP
```

## 10. Claude による執筆方針作成

Claude は必要なファイルのみ読み込む。

基本的には、

```
project.md
rules/
world/
characters/
plot/
foreshadowing/
chapters/対象章/outline.md
```

を対象とする。

過去章は必要に応じて参照する。

**毎回全作品を Claude へ投入しない。**

## 11. AGY による執筆

AGY には、

```
plan.md
+
必要な設定
+
必要なキャラクター
+
必要な伏線
+
必要な前章情報
```

を与える。

AGY は `draft.md` を作成・更新する。

## 12. 未定義設定

AGY が判断できない設定に遭遇した場合、独自に設定を作らない。

例：

```
[SETTING_REQUIRED]

対象：
主人公の魔法適性

理由：
既存設定から判定不能。
```

Controller は作業を停止し、人間または Claude による判断を要求する。

## 13. 執筆監視

Claude Validator が AGY の出力を監視する。

### キャラクター

- 性格
- 価値観
- 行動原理
- 口調
- 能力
- 知識

### 世界観

- 世界設定
- 魔法
- 技術
- 地理
- 歴史
- 用語

### ストーリー

- 時系列
- 因果関係
- 章構成

### 伏線

- 提示
- 維持
- 回収
- 状態変更

## 14. STOP 条件

以下を検出した場合、Controller は AGY の作業を停止できる。

- 世界観矛盾
- キャラクター逸脱
- 時系列矛盾
- 伏線破壊
- 設定の勝手な追加
- 執筆方針違反
- 出力形式異常
- CLI 異常
- タイムアウト

## 15. 人間による文章修正

UI 上で本文の一部を選択できる。

例：

> もっと緊張感を出して、300 字くらいに膨らませて。

処理：

```
選択範囲
 ↓
Claude
 ↓
AGY 向け修正指示
 ↓
AGY
 ↓
draft.md 変更
 ↓
Claude Validator
```

Claude は原則として修正文そのものを正本へ書き込まず、AGY が実編集を行う。

## 16. 差分管理

AGY による変更前後を Git 差分として確認できる。

Controller は変更されたファイルを確認する。

本文変更と設定変更を区別して扱う。

## 17. Git / GitHub 管理

### 16.1 GitHub Private Repository

作品データは原則として **GitHub Private Repository** で管理する。

GitHub は以下に利用する。

- 作品の正本
- バックアップ
- バージョン管理
- 差分管理
- 復元
- 作業履歴

実際の AI 処理はローカル Git Repository に対して行う。

```
Local Git Repository
        │
        │ push / pull
        ▼
GitHub Private Repository
```

### 16.2 Git 管理対象

基本的に以下を管理対象とする。

```
project.md
world/
characters/
plot/
foreshadowing/
rules/
chapters/
reviews/
output/
```

### 16.3 Git 管理対象外

以下は GitHub に保存しない。

```
.env
認証情報
API キー
CLI セッション情報
一時ファイル
キャッシュ
大量の実行ログ
```

`.gitignore` で管理する。

## 18. Git 操作の安全制御

初期仕様では、AI による無条件の自動 push を禁止する。

基本フロー：

```
AI変更
 ↓
Controller
 ↓
git diff
 ↓
検証
 ↓
人間確認
 ↓
commit
 ↓
push
```

特に、

```
world/
characters/
plot/
foreshadowing/
rules/
```

などの設定ファイル変更は本文変更より厳格に扱う。

## 19. AI による設定ファイル変更

AGY が以下を変更する場合は通常の本文編集とは別扱いとする。

```
world/
characters/
plot/
foreshadowing/
rules/
```

原則：

```
AGY
 ↓
設定変更要求
 ↓
Controller
 ↓
人間承認
 ↓
変更
```

## 20. Git による復元

誤った AI 編集が発生した場合、Git 差分から変更を確認し、変更前へ復元できること。

章単位・ファイル単位で過去の commit から復元できる構造とする。

## 21. Tailscale による外部アクセス

Web UI は PC 側で稼働させる。

```
Android
   │
   │ Tailscale
   ▼
PC
   │
   ▼
Novel Controller
   │
   ▼
Web UI
```

外部から直接 Git Repository を編集するのではなく、UI から Controller を操作する。

## 22. UI

基本 3 ペイン構成。

```
┌──────────┬────────────────┬──────────┐
│ 作品管理  │   本文エディタ  │ AI状態    │
│          │                │          │
│ 世界観    │ 第4章           │ Claude   │
│ キャラ    │                │ ●監視中   │
│ ストーリー│ 本文……         │          │
│ 伏線      │                │ AGY      │
│ 章一覧    │                │ ●執筆中   │
│          │                │          │
│          │                │ F001 ✓    │
│          │                │ F002 ✓    │
│          │                │ F003 ⚠    │
└──────────┴────────────────┴──────────┘
```

スマートフォンではタブ形式に対応する。

## 23. UI から可能な操作

- 章選択
- 本文閲覧
- 本文選択
- 修正指示
- 文章量増加
- 表現変更
- Claude による指示生成
- AGY による編集開始
- 執筆停止
- 再開
- Claude 監査
- 差分確認
- Git 状態確認
- commit
- push
- 過去版確認
- 章単位の復元

## 24. Controller 状態

基本状態：

```
IDLE
QUEUED
PLANNING
WAITING_APPROVAL
WRITING
MONITORING
VALIDATING
COMPLETED
```

異常状態：

```
FAILED
STOPPED
WAITING_HUMAN
```

## 25. 最終監査

全章完成後、Claude による全体監査を行う。

### 世界観

- 設定矛盾
- 新規設定
- 設定変更
- 用語

### キャラクター

- 性格
- 口調
- 行動原理
- 能力
- 人間関係

### ストーリー

- 時系列
- 因果関係
- 章間接続
- ストーリー上の矛盾

### 伏線

- 未回収
- 誤回収
- 回収時期
- 伏線内容の変化

### 文章

- 視点
- 文体
- 重複
- 不自然な表現
- 誤字

## 26. 最終監査結果

```
reviews/
├─ world.md
├─ characters.md
├─ story.md
└─ foreshadowing.md
```

へ保存する。

## 27. 完成原稿

各章の、

```
chapters/*/draft.md
```

を順番に結合する。

```
chapters/001/draft.md
chapters/002/draft.md
chapters/003/draft.md
...
```

↓

```
output/novel.md
```

## 28. 完成条件

AI による検証だけでは完成扱いにしない。

```
AI_VALIDATED
       ↓
HUMAN_APPROVED
       ↓
FINAL
```

最終承認は人間が行う。

## 29. 重要な禁止事項

1. AGY が独断で設定を変更する
2. AI が不明な設定を勝手に補完する
3. AI が伏線を勝手に変更する
4. Claude が本文正本を直接編集する
5. `output/novel.md` を原本として編集する
6. 全作品を毎回 AI へ投入する
7. AI が無条件に GitHub へ push する
8. 認証情報を Git に保存する
9. 検証なしに AI 変更を正式版とする

## 30. 最終アーキテクチャ

```
                       Human
                         │
                         ▼
                  ┌─────────────┐
                  │   Web UI    │
                  └──────┬──────┘
                         │
                  Tailscale VPN
                         │
                  ┌──────▼──────┐
                  │ Controller  │
                  └──────┬──────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
       Claude           AGY          Validator
        CLI              CLI          Claude
          │              │              │
          ▼              ▼              ▼
      Planning         Writing      Validation
          │              │              │
          └──────────────┼──────────────┘
                         │
                         ▼
                Local Git Repository
                         │
                         │ push / pull
                         ▼
                GitHub Private Repo
                         │
                         ▼
                    Backup / History
```

## 31. 設計上の核心

- **GitHub**：作品の正本・履歴
- **Local Git Repository**：AI が操作する作業領域
- **Controller**：Claude / AGY の実行管理・安全装置
- **Claude**：設計・指示・監視・検証
- **AGY**：本文の実編集
- **人間**：作品内容・設定変更・Git 反映・最終版の承認
- **章別 Markdown**：本文の原本
- **output/novel.md**：最終生成物
