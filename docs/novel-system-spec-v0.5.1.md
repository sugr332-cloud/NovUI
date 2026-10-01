# 小説作成支援システム 仕様書 v0.5.1

**ステータス：Phase 1 実装基準（v0.5 は Human 確認済み。v0.5.1 は Phase 1-C の実測による改訂）**
**前版：v0.5（docs/novel-system-spec-v0.5.md）**
**根拠：Phase 0 技術スパイク結果（docs/spike/results/phase0-summary.md）、Phase 1-C の実測（docs/phase1/results/1c-*.yaml）**

v0.5 は、v0.4 本体と追補 §41〜§44 を1本に統合し、Phase 0 の実測結果を反映したもの。v0.5.1 は、Phase 1-C の実測により AGY と Claude の入出力の方式を改めたもの（変更点は §26）。
本書を Phase 1（Controller 実装）の実装基準とする。

---

## 1. 文書の位置付け

* 本書は NovUI における小説の作成・管理・AI執筆パイプラインの仕様である。
* 上位仕様（SPECIFICATION.md）の所在は確認できていない。確認できるまでは本書を NovUI の実装基準とし、上位仕様が見つかった場合は差分を整理して本書に統合する。
* 目的は、Controller の実装時に、AI の役割・状態・変更権限について追加の仕様判断が発生しないことである。
* 実行方法に関する記述（§5）は、Phase 0 で実測した事実に基づく。CLI の更新で前提が崩れた場合は §5 を改訂する。

## 2. 用語

| 用語 | 定義 |
|---|---|
| Human | 作品内容、設定変更、採用判断、最終承認を行う人間 |
| Claude | 設計、計画、分析、監督、検証を担当する CLI（Claude Code） |
| AGY | 本文・コードの書込みを担当する CLI（Antigravity CLI） |
| Controller | CLI の実行、実行隔離、安全制御、状態管理、Git 操作を担当する中間層 |
| Validator | Claude を用いた作品内容・変更内容の検証 |
| Canonical | Git 上で正本として扱うデータ |
| Job | Controller が実行する1単位の処理（執筆、範囲編集、検証など） |
| 作業ブランチ | AI の書込み Job 専用の branch と worktree |
| Job用HOME | AGY を実行するために Job ごとに作る使い捨ての HOME ディレクトリ |
| 保護対象 | Human の行為なしに変更できない設定ファイル群（§15） |
| 承認記録 | 保護対象への変更を Human が承認したことを示す記録（§15.4） |
| 要確認フラグ | 前提が変わったため Human の確認が必要な章に付ける印（§7.3） |
| PASS / WARNING / STOP | 検証結果。§11.4 |

## 3. 基本原則

1. **Git を正本とする。** 作品データの正本はローカルの Git リポジトリとし、GitHub の private リポジトリはバックアップ・履歴・復旧先とする。CLI のセッション、Controller のメモリ、Web UI の状態は正本ではない。
2. **本文は章単位で分離する。** 本文の原本は chapters/<chapter-id>/draft.md とする。
3. **output は生成物とする。** output/novel.md は章本文から生成し、直接編集しない。通常の Git 管理対象外とする。
4. **AI の変更は検証を経て正式化する。** Controller の機械検査、Validator の内容検証、Human の承認を経て main に反映する。
5. **不明な情報を発明しない。** 既存データから判断できない設定・人物・時系列・伏線を、AI が確定してはならない。
6. **機械的強制を優先する。** Controller は AI の自主的なルール遵守を前提にしない。禁止事項は、実行隔離・権限設定・変更検査で強制する。

## 4. 役割分担

### 4.1 Claude（設計・計画・監督・検証）

* 世界観・キャラクター・ストーリー・伏線の分析、章構成と執筆計画、AGY への指示の作成
* AGY の変更結果の検証、設定変更・状態更新の提案、Human の指示の振り分け、全体監査
* Claude は読み取り専用で起動し（§5.5）、ファイルを直接書き込まない。出力は標準出力で返し、Controller が検証してファイルに書く（§10）。

### 4.2 AGY（本文の生成）

* 本文の執筆・範囲編集・再執筆
* AGY はファイルを読まず、書かない。必要な情報は Controller がプロンプトに埋め込み（§16）、AGY は本文をテキストで返す。ファイルへの書込みは Controller が行う（§5.8）。
* 設定にない情報が必要な場合、AGY は推測せず、本文中に `【要確認：<不明な内容>】` を書く。Controller がこれを requests.yaml に移す（§5.8）。
* AGY は Job ごとにコンテナ内で実行し、作品のファイルはマウントしない（§5.3）。

### 4.3 Human

* outline の作成、plan の承認、設定変更の承認、WARNING / STOP の判断、指示、直接編集、最終承認

### 4.4 Controller

* CLI の起動と入出力、実行隔離、作業ブランチと worktree の管理、変更検査、状態管理、timeout、lock、Validator の起動、Git 操作、Web UI への通知
* Controller は AI の出力を schema 検証してから書き込み、保護対象には承認記録なしに書き込まない。

### 4.5 システム自身の開発

* 本システムの開発にも同じ責務分離を適用する。Claude が設計・レビューし、AGY がコードを書き、Human が承認する。
* AGY が作業ブランチで commit・push し、Claude が差分を確認する。Claude が問題なしと判断した場合、Human を経由せず AGY に次の作業（commit・push を含む）を指示してよい。main への反映は Claude の確認を経て行う。

---

## 5. 実行プロファイル（Phase 0 で確定）

### 5.1 AGY の起動

* 起動形式：`agy --mode accept-edits --model <モデル> --print=<プロンプト>`
* `--print` は直後の引数をプロンプトとして解釈するため、他のフラグはすべて `--print` より前に置き、プロンプトは `--print=` に結合して1引数で渡す。
* 標準入力でプロンプトを渡す方法は使わない（AGY が対応していない）。
* `--mode accept-edits` では、ファイル編集は自動承認され、コマンド実行は自動で拒否される。AGY の Job はこのモードで実行する。
* `--dangerously-skip-permissions` は使用しない。
* `--model` は Job ごとに明示する（§5.7）。
* `--output-format` は既定（text）とし、`--json-schema` は使わない。Phase 1-C の実測で、`--json-schema`（`--output-format json` が必須）は1行の出力に80〜127秒かかり、トークン消費も大きかった。テキスト出力は約7〜12秒だった。
* AGY はツール（シェルコマンド、ファイルの読込み）を使おうとすると、権限の自動拒否により何も出力せずに終了することがある。プロンプトには「ツールを使わないこと」を明記し、必要な情報はすべてプロンプトに含める。
* プロンプトは1つの引数で渡すため、Linux の引数1つあたりの上限（128KB）を受ける。実測で 124,954 バイトは成功、139,954 バイトは `Argument list too long` で失敗した。Controller はプロンプトを **120,000 バイト（UTF-8）以下** に制限する（§16.1）。

### 5.2 コンテナ

* AGY は rootless Podman のコンテナ内で実行する。
* イメージは Fedora ベースで、AGY のバイナリを COPY して作る。タグに AGY のバージョンを含める（例：novui-agy:1.2.14）。
* ホストの AGY は自動更新されるため、コンテナのバージョンはイメージで固定する。更新の取り込みはイメージの再ビルドで行い、再ビルドは Human の承認を要する。
* `--userns=keep-id` を付け、コンテナ内のプロセスをホストと同じ UID で実行する。
* コンテナには `--name` を付け、終了後（timeout を含む）に必ず `podman rm -f` で削除する。
* `-t` は付けない。

### 5.3 マウント構成

AGY のコンテナには、Job用HOME だけをマウントする。作品の worktree、Git のディレクトリ、その他のホストのパスはマウントしない。

| マウント元 | マウント先 | モード |
|---|---|---|
| Job用HOME | /home/agy | rw、SELinux ラベル Z |

* コンテナには `--pull=never`、`--cap-drop=all`、`--security-opt=no-new-privileges` を付ける。
* Z ラベルでマウントするパスは、Controller のデータディレクトリ配下の Job用HOME に限る（誤ってホームディレクトリ等を再ラベルしないため）。
* Phase 1-C の結合試験で、Controller のデータディレクトリ（~/.local/share/novui）配下での Z ラベルのマウント、想定外マウントがないこと（mountinfo 検査）、timeout 時のコンテナの後始末を確認した。

### 5.4 Job用HOME と認証

* AGY は Job ごとに空の Job用HOME で実行する。ホストの HOME（~/.gemini を含む）はマウントしない。
* Job用HOME には、ホストのトークンファイル（~/.gemini/antigravity-cli/antigravity-oauth-token）のコピーだけを置く（chmod 600）。履歴・記憶・設定・Gemini CLI の認証情報はコピーしない。
* コンテナ内の HOME は `-e HOME=/home/agy` で明示する。
* Job 終了後、Job用HOME は丸ごと削除する。異常終了時も trap 等で必ず削除する。
* AGY は起動のたびに Job用HOME に会話の記録・記憶（brain/、conversations/、implicit/ など）を書き込む。Job用HOME を Job 間・作品間で共有してはならない。
* 作品の内容を含まない補助ファイル（builtin/、bin/ 等）は、初回の展開に約3秒かかる。必要ならイメージに焼き込む（Phase 1 で判断）。
* 認証情報は Controller 側だけが扱い、ログ・結果ファイル・Git 管理対象に出力しない。

### 5.5 Claude の起動

* 起動形式：`claude -p --tools Read --permission-prompts none --no-session-persistence --safe-mode --restricted --strict-mcp-config --model <モデル> --output-format json --json-schema <schema>`
* `--safe-mode --restricted --strict-mcp-config` により、ホストの Claude Code の設定、CLAUDE.md、プラグイン、MCP サーバー（claude.ai のコネクタを含む）を読み込まない。Phase 1-C の実測で、これらを付けない場合は出力にコネクタの案内文が混入した。
* プロンプトは標準入力で渡す。必要な情報はすべてプロンプトに含め（§16）、Claude は Job ごとの空の作業ディレクトリで起動する。
* 出力は JSON の封筒で返る。結果は `structured_output`、エラーの有無は `is_error`、実際に使われたモデルは `modelUsage` のキーから取得する（§10）。
* `--json-schema` を付けない場合、プロンプトの指示だけでは JSON がコードブロックで囲まれ、前後に文章が付くことがあった。`--json-schema` は必須とする。

### 5.6 timeout

* print モードの CLI は最後にまとめて出力するため、生成中は無出力が続く。無出力 timeout は使用せず、全体 timeout で制御する。
* 全体 timeout の初期値：AGY 執筆 900秒、AGY 範囲編集 300秒、Claude 300秒。Job 種別ごとに設定できるようにする。
* timeout 時はプロセスグループに SIGTERM、続いて SIGKILL を送り、コンテナを削除する。種別・経過時間・シグナルを Job 記録に残す。

### 5.7 モデルとバージョンの記録

* Job ごとに `--model` を明示し、その値を記録する。Claude は `modelUsage` から実際のモデル名も記録する。AGY の出力からはモデル名を取得できない。
* Job 記録には、CLI 名、CLI バージョン、指定したモデル、コンテナイメージのタグ、イメージ ID を残す。
* モデルの自己申告は参考値としてだけ扱う。

### 5.8 AGY の出力の取り込み

* Controller は AGY の標準出力を UTF-8 として復号し、末尾の空白を除いて末尾に改行を1つ付けたものを本文とする。空、復号できない、先頭がコードブロック（```）の場合は失敗とする。
* draft・chapter_rewrite では、本文を作業ブランチの draft.md に Controller が書き込む。
* range_edit では、AGY は範囲の置き換え後の文章だけを返し、Controller が保存しておいた prefix と suffix の間に差し込む。範囲外は Controller が変更しないため、prefix/suffix の一致は構造上保証される（変更検査でも確認する）。
* 本文中の `【要確認：…】`（コロンは全角・半角とも可）を抽出し、requests.yaml に kind=undefined_setting の要求として追記する。要求が1件以上ある Job は WAITING_HUMAN とする。

---

## 6. リポジトリとデータ形式

### 6.1 作品リポジトリの構造

    novel-project/
    ├─ project.yaml
    ├─ chapters-order.yaml
    ├─ world/
    ├─ characters/
    │  ├─ C001.yaml
    │  └─ ...
    ├─ plot/
    │  └─ timeline.yaml
    ├─ foreshadowing/
    │  └─ registry.yaml
    ├─ rules/
    │  ├─ style.md
    │  └─ prohibited.yaml
    ├─ chapters/
    │  └─ ch-001/
    │     ├─ chapter.yaml
    │     ├─ outline.md
    │     ├─ plan.yaml
    │     ├─ draft.md
    │     ├─ summary.yaml
    │     ├─ review.yaml
    │     └─ requests.yaml
    ├─ reviews/
    ├─ .novui/
    │  └─ approvals/
    └─ output/          （Git 管理外）

### 6.2 ファイル形式

* 機械が読み書きするファイルは YAML とする（.yaml）。散文は Markdown とする（.md）。
* v0.4 の chapters-order.md、registry.md、characters/*.md などは、v0.5 で .yaml に改める。
* 各 YAML のスキーマは NovUI リポジトリの schemas/ に JSON Schema として置き、Controller は読み書きのたびに検証する。
* project.yaml に format_version（整数）を持つ。

### 6.3 章メタデータ（chapter.yaml）

章の状態と要確認フラグは chapters/<id>/chapter.yaml に保存する。

    id: ch-004
    title: 王都へ
    state: DRAFTED
    review_required: false
    review_reasons: []
    validation_skipped: false
    last_job: job-123

* chapter.yaml の state と review_* は Controller だけが書き込む。Human が直接編集しない。
* review_required の扱いは §7.3。

### 6.4 キャラクター・伏線・timeline

* キャラクターは characters/<ID>.yaml。ID は必須。knowledge・items などの蓄積データは、項目ごとに id と source_chapter を持つリストとする。

      id: C003
      name: カイ
      knowledge:
        - id: K014
          fact: 門番が紋章に反応したことを知っている
          source_chapter: ch-004

* 伏線の正本は foreshadowing/registry.yaml のみ。status は planned / active / resolved / cancelled。
* timeline は plot/timeline.yaml。

### 6.5 章ファイル

| ファイル | 書く者 | 内容 |
|---|---|---|
| outline.md | Human | 章の目的、出来事、登場人物、伏線、次章への接続 |
| plan.yaml | Claude（Controller が書込み） | シーン構成、行動、使用する設定・伏線、禁止事項、視点、文体、目標文字数、前後の接続 |
| draft.md | AGY の出力を Controller が書込み / Human | 本文（Canonical） |
| summary.yaml | Claude（Controller が書込み） | 章終了時点の状態（§12.2） |
| review.yaml | Controller | Validator と機械検査の結果 |
| requests.yaml | Controller（追記のみ。AGY の `【要確認：…】` から作成） | 設定の判断要求 |

### 6.6 .novui/

* .novui/approvals/ に承認記録（§15.4）を保存する。
* .novui/ は保護対象と同じ扱いとし、AGY のコンテナには ro で重ねるかマウントから除外する。

---

## 7. 状態

### 7.1 章状態と遷移

章状態：OUTLINED / PLANNED / PLAN_APPROVED / DRAFTED / AI_VALIDATED / HUMAN_APPROVED / FINAL

| 現状態 | イベント | 次状態 |
|---|---|---|
| （なし） | Human が outline を作成 | OUTLINED |
| OUTLINED | Claude の plan を Controller が書込み | PLANNED |
| PLANNED | Human が plan を承認 | PLAN_APPROVED |
| PLANNED | Human が plan を却下 | OUTLINED |
| PLAN_APPROVED | 執筆 Job が merge 可能な状態で完了 | DRAFTED |
| DRAFTED | 整合性ゲート PASS、または WARNING を Human が CONTINUE / OVERRIDE | AI_VALIDATED |
| DRAFTED | Human が検証を省略（§11.6） | AI_VALIDATED（validation_skipped: true） |
| AI_VALIDATED | 状態更新 Patch を Human が承認し、summary.yaml を書込み | HUMAN_APPROVED |
| HUMAN_APPROVED | Human が最終承認し、main に merge | FINAL |
| PLAN_APPROVED 以降 | plan_revision Job を実行 | PLANNED |
| DRAFTED 以降 | Human の内容変更（§14）、range_edit / chapter_rewrite Job の完了 | DRAFTED |

* 表記修正（§14.3）は章状態を変えない。
* FINAL は、review_required が true の間は付与しない。
* 作業ブランチ上の変更は、main に merge するまで章状態を確定しない。Controller は章状態の変更を main への merge と同じ commit で行う。

### 7.2 Job 状態と遷移

Job 状態：QUEUED / RUNNING / CHECKING / VALIDATING / WAITING_HUMAN / COMPLETED / FAILED / STOPPED / CANCELLED

| 現状態 | イベント | 次状態 |
|---|---|---|
| QUEUED | 実行 lock を取得 | RUNNING |
| RUNNING | CLI が終了 | CHECKING |
| RUNNING | timeout | FAILED（Retry 規則 §18.3） |
| RUNNING | Human が STOP | STOPPED |
| CHECKING | 機械検査 PASS | VALIDATING（検証が必要な Job）／COMPLETED |
| CHECKING | 許可範囲外の変更・空出力・schema 不適合 | FAILED |
| VALIDATING | PASS | COMPLETED |
| VALIDATING | WARNING / STOP / 判断不能 | WAITING_HUMAN |
| WAITING_HUMAN | Human が CONTINUE / OVERRIDE | COMPLETED |
| WAITING_HUMAN | Human が REQUEST_FIX | COMPLETED（修正 Job を新規に QUEUED） |
| WAITING_HUMAN | Human が CANCEL | CANCELLED |
| 任意 | Controller の再起動を検知 | STOPPED（自動再開しない） |

* Job が COMPLETED でも章が FINAL とは限らない。
* STOPPED・FAILED・CANCELLED の Job の作業ブランチは main に merge しない。調査のため一定期間残し、Human の操作で削除する。

### 7.3 要確認フラグ

* 次の場合、Controller は該当章の chapter.yaml の review_required を true にし、review_reasons に理由を追記する。
  - 前の章の内容変更・再執筆・FINAL 章への編集（後続章すべて）
  - 設定変更の影響範囲分析で影響ありとされた章
  - UI を経由しない保護対象の変更（外部変更、§17.6）の影響範囲分析で影響ありとされた章
* review_required は、Human が UI で該当章を確認し「確認済み」とした時点で false に戻す。必要なら Human は再検証を依頼できる。
* review_required の変更は Controller が main に commit する。

---

## 8. Job 種別と書込み許可

### 8.1 Job 種別

| Job 種別 | 実行 | 書込み許可 | 実行後の章状態 |
|---|---|---|---|
| plan | Claude → Controller | 対象章の plan.yaml | PLANNED |
| draft | AGY → Controller | 対象章の draft.md、requests.yaml（追記のみ） | DRAFTED |
| range_edit | AGY → Controller | 対象章の draft.md（範囲内のみ）、requests.yaml（追記のみ） | DRAFTED |
| chapter_rewrite | AGY → Controller | 対象章の draft.md、requests.yaml（追記のみ） | DRAFTED |
| plan_revision | Claude → Controller | 対象章の plan.yaml | PLANNED |
| validate | Claude → Controller | 対象章の review.yaml | （§7.1） |
| state_update | Claude → Controller（承認後） | Patch の target、対象章の summary.yaml | HUMAN_APPROVED |
| setting_change | Claude → Controller（承認後） | Patch の target | 影響章に要確認 |
| resolve_request | Controller | requests.yaml（追記のみ） | 変化なし |
| routing | Claude → Controller | なし（Controller のデータ） | 変化なし |

* 書込み許可は、Controller が AI の出力を書き込める範囲を表す。AI 自身はどのファイルにも書き込まない。

### 8.2 実行前の条件

* draft・chapter_rewrite は、章が PLAN_APPROVED 以降であることを条件とする。
* plan_revision を伴う chapter_rewrite は、改訂した plan の承認後にだけ実行する。
* range_edit は、実行直前に anchor を再照合する（§13.5）。

### 8.3 変更検査

AGY の Job の終了後、Controller は次を検査し、1つでも満たさなければ Job を FAILED とし、作業ブランチを merge しない。

1. AGY の終了コードが 0 で、標準出力が空でないこと（§5.8 の取り込みに成功すること）
2. Controller が出力を書き込んだ後、`git status --porcelain --untracked-files=all` と ignored ファイルの一覧（`git ls-files --others --ignored --exclude-standard`）で、変更が書込み許可の範囲だけであること
3. requests.yaml は追記のみであること
4. range_edit では、範囲より前（prefix）と後（suffix）がバイト単位で一致すること
5. worktree の .git 参照ファイルと、Git common directory の config・hooks・worktrees/<name>/ の gitdir・commondir・HEAD のハッシュが Job の前後で一致すること
6. draft.md の文字数が plan の目標文字数の範囲内であること（範囲外は WARNING として Validator に渡す）

AGY は作品のファイルをマウントしないため、2〜5 は AGY による改変ではなく、Controller 自身の誤りと外部からの変更を検出するための検査である。

---

## 9. 執筆の標準フロー

    Human：outline.md を作成（OUTLINED）
    → Claude：plan を作成 → Controller：schema 検証して plan.yaml を書込み（PLANNED）
    → Human：plan を承認（PLAN_APPROVED）
    → Controller：作業ブランチと worktree を作成、Context を組み立てて記録
    → AGY：コンテナ内で本文を生成し、テキストで返す
    → Controller：出力を draft.md に書込み、【要確認】を requests.yaml に移し、変更検査（§8.3）
    → Claude：整合性ゲート（同じ版の Context で検証）と文章レビュー
    → Human：WARNING 等の判断（必要時）（AI_VALIDATED）
    → Claude：状態更新 Patch を提案 → Human：承認
    → Controller：Patch を適用、summary.yaml を書込み（HUMAN_APPROVED）
    → Human：最終承認 → Controller：main に merge（FINAL）

---

## 10. Claude の出力経路

* Claude は `--output-format json --json-schema` で起動し（§5.5）、JSON の封筒を返す。Controller は封筒を parse し、`is_error` が false で `structured_output` があることを確認してから、`structured_output` を対応する schema で検証し、所定のファイルに書き込む。
* schema に適合しない場合はファイルに書き込まず、同じ入力で最大2回まで再要求する。それでも不適合なら Job を FAILED とする。
* `--json-schema` により形式は CLI 側でも強制されるが、Controller の検証と再要求は省略しない。

Claude の出力の種類と schema：

| type | 用途 | 書込み先 |
|---|---|---|
| plan | 執筆計画 | plan.yaml |
| integrity_review | 整合性ゲートの結果 | review.yaml |
| writing_review | 文章レビューの指摘 | review.yaml |
| state_patch | 状態更新・設定変更の提案 | Controller のデータ（承認後に適用） |
| summary | 章終了時点の状態 | summary.yaml |
| instruction_routing | Human の指示の振り分け | Controller のデータ |

各 schema は NovUI リポジトリの schemas/ に置く。

---

## 11. Validator

### 11.1 入力

* Validator には、AGY に渡したのと同じ版の Context を渡す。Controller は Job 開始時に基準 commit と、Context に含めた各ファイルのパスとハッシュを Job 記録に残し、Validator にはその版を渡す。

### 11.2 整合性ゲートと文章レビュー

* 整合性ゲート（integrity_review）：character、world、timeline、plot、foreshadowing、plan_compliance、undefined_setting を検査し、PASS / WARNING / STOP を返す。Job を止める権限を持つ。
* 文章レビュー（writing_review）：不自然な表現、冗長さ、説明過多、文末の単調さ、定型表現などを指摘する。Job を止めない。指摘は anchor 付きで返し、Human が採用したものだけ range_edit Job に変換する。

### 11.3 機械検査

* Controller は Claude を呼ばずに次を検査し、結果を review.yaml に記録する。
  - 文字数（plan の目標範囲）
  - rules/prohibited.yaml に登録した定型表現
  - 同一文末の連続（閾値は rules で設定）
* 機械検査の結果は WARNING として扱い、STOP にはしない。

### 11.4 PASS / WARNING / STOP

* PASS：自動で次に進む。
* WARNING：Human が CONTINUE / REQUEST_FIX / CANCEL / OVERRIDE を選ぶ。Controller は WARNING を自動で PASS にしない。
* STOP：自動では進まない。STOP の対象は、世界観の矛盾、重大なキャラクター逸脱、時系列の矛盾、伏線の破壊、未定義設定の発明、plan 違反、Validator の判断不能。
* REQUEST_FIX には理由（指示文）を必須とする。
* OVERRIDE には理由を必須とし、Job 記録に残す。
* 許可範囲外の変更、.git の改変、認証情報の漏洩、timeout などの機械的な安全違反は、OVERRIDE で解除できない。

### 11.5 Review → Rewrite の上限

* 文章レビューから range_edit への変換は、同じ章について連続2回までとする。それ以上は Human が明示的に再開する。

### 11.6 Claude を利用できない場合

* Claude のレート制限・認証失敗などで検証できない場合、AGY で代替しない。WAITING_HUMAN とし、Human が再試行か検証の省略を選ぶ。省略した場合は chapter.yaml の validation_skipped を true にする。

---

## 12. 状態更新と State Patch

### 12.1 State Patch

* 状態更新と設定変更は、JSON Patch（RFC 6902）形式の State Patch で表す。

      type: state_patch
      target: characters/C003.yaml
      base_hash: sha256:...
      operations:
        - op: add
          path: /knowledge/-
          value:
            id: K014
            fact: 門番が紋章に反応したことを知っている
            source_chapter: ch-004
      reason: 第4章で門番の反応を目撃した

* base_hash が適用時のファイルと一致しない場合は適用しない（再提案とする）。
* knowledge・items などの蓄積データは add を基本とし、リスト全体の replace は使わない。

### 12.2 summary.yaml

* summary.yaml は状態更新の承認時に Controller が書き込む（HUMAN_APPROVED への遷移と同時）。
* 最低限、出来事、登場人物ごとの知識・位置・所持品・負傷や状態・関係の変化、世界設定への影響、伏線の状態、次章開始時点の状態を含む。
* summary.yaml の誤りは Human が直接編集せず、状態更新の手順で修正する。

---

## 13. Human の指示

### 13.1 原則

* Human が文章で指示する相手は Claude だけとする。AGY へ直接指示する入口は設けない。
* Claude は指示を実行せず、Job への振り分け案（instruction_routing）を返す。Human が承認した案だけを Controller が実行する。

### 13.2 指示バー

UI に指示の入口を1つだけ置く。対象（scope）は画面の状態から自動で決め、Human が変更できる。

| scope | 自動で選ばれる条件 | 対象 |
|---|---|---|
| range | draft.md で本文を選択している | 選択範囲 |
| chapter | 章を開いていて選択がない | 章全体 |
| plan | outline.md / plan を表示している | plan |
| setting | 設定ファイルを表示している | 保護対象の設定 |
| fix | WARNING / STOP の判断中に REQUEST_FIX を選んだ | 検証結果の指摘箇所 |
| redline | 添削モードで「まとめて送る」を押した | 赤入れ全体 |

### 13.3 instruction_routing

    type: instruction_routing
    instruction_id: INS-0012
    scope: chapter
    chapter_id: ch-004
    instruction: 全体的にテンポが遅い。門の場面までを短くしたい。
    scope_mismatch: false
    scope_mismatch_reason: null
    proposed_jobs:
      - job_type: plan_revision
        summary: S1を短縮し、S2の門の場面を早める
      - job_type: chapter_rewrite
        summary: 改訂planに基づきS1〜S2を再執筆する
        depends_on: 0
    impact: []
    questions: []

* 指示が曖昧な場合、Claude は questions を返し、proposed_jobs を空にする。Controller は WAITING_HUMAN とする。
* 必要な変更の単位が scope と異なる場合、Claude は scope_mismatch を true にする。scope を広げるかは Human が判断する。Claude が自動で広げてはならない。
* 設定変更を含む案では、impact に影響範囲を列挙する。
* 採用した指示だけを instruction_id とともに Job 記録に残す。破棄した指示と対話の途中経過は保存しない。

### 13.4 連続する指示

* 同じ章への Job は、実行 lock（§18.1）により順番に実行する。
* 後続の Job は、実行直前に基準 commit を確認する。前の Job で対象が変わっている場合、range_edit は anchor を再照合し、その他の Job は Claude に振り分けを再要求する。

### 13.5 範囲の特定（anchor）

* 選択範囲は、行番号ではなく、anchor の文字列と前後の文脈で特定する。
* 一致が0件または2件以上の場合は ERROR とし、Job を WAITING_HUMAN にする。
* 編集の前に、範囲より前のファイル全体（prefix）と後のファイル全体（suffix）を保存する。AGY には範囲の文章と前後の文脈をプロンプトで渡し、置き換え後の文章だけを返させる。Controller が prefix・出力・suffix を連結して draft.md に書き込む（§5.8）。

---

## 14. Human の直接編集と添削

### 14.1 本文モード

draft.md の表示には、閲覧・直接編集・添削の3つのモードを設ける。

### 14.2 直接編集できるファイル

* Canonical ファイルは Human が直接編集できる。対象は draft.md、outline.md、plan.yaml、および保護対象（§15.2）。
* review.yaml、requests.yaml、summary.yaml、chapter.yaml、.novui/ は直接編集の対象外とする。

### 14.3 保存時の種別

| 種別 | 例 | 章状態 | Validator | 要確認フラグ |
|---|---|---|---|---|
| 表記修正 | 誤字、句読点、言い回し | 維持 | 任意 | 付けない |
| 内容変更 | 出来事、台詞の意味、人物の行動・知識 | DRAFTED | 推奨（省略時は validation_skipped） | この章と後続章 |

* 種別は Human が判定する。Claude が表記修正の中に内容変更の疑いを見つけた場合は WARNING として示すだけにする。
* 保護対象の直接編集では、保存時に影響範囲分析を実行するかを選ぶ（既定は実行）。

### 14.4 反映先とロック

* Human の直接編集は main に commit する。commit に `NovUI-Edit: human-typo` または `NovUI-Edit: human-content` を付ける。
* 対象章に未 merge の作業ブランチがある間は、その章の直接編集と添削はできない。

### 14.5 添削（赤入れ）

* 赤入れは範囲（anchor）とコメントの組とする。送信するまでは Controller の作品別データとして保持し、Canonical には含めない。章が FINAL になった時点、または Human が破棄した時点で削除する。
* 送信すると scope=redline の指示として振り分ける。原則、赤入れ1件につき range_edit 1件とし、順番に実行する。

---

## 15. 設定ファイルの保護

### 15.1 目的

Human は設定ファイルを閲覧・編集・変更指示できる。設定ファイルは Human の行為（直接編集・指示・承認）なしには変更されないことを、Controller が機械的に保証する。

### 15.2 保護対象

project.yaml、chapters-order.yaml、world/、characters/、plot/、foreshadowing/registry.yaml、rules/、.novui/

### 15.3 変更経路

| 経路 | 起点 | 書込み主体 | 記録 |
|---|---|---|---|
| Human の直接編集 | Human | Human（UI 経由で main に commit） | `NovUI-Edit: human-*` |
| Human の指示（setting_change） | Human | Controller | 承認記録 |
| Claude の提案の承認（state_update） | Claude（提案のみ） | Controller | 承認記録 |

* Claude が提案を出すのは、章完了後の状態更新と、Human の指示の振り分けの時だけとする。
* 未承認の提案は Controller の作品別データとし、Canonical に含めない。同じ target への新しい提案が出た場合、古い提案は superseded とする。base_hash が合わなくなった提案は無効とする。
* AGY は、いかなる経路でも保護対象に書き込まない。

### 15.4 承認記録

    approval_id: A-0032
    source: instruction        # instruction | proposal
    instruction_id: INS-0012
    job_id: null
    patch_sha256: ...
    targets:
      - characters/C002.yaml
    approved_by: human
    approved_at: 2026-10-01T20:10:00+09:00

* 承認記録は作品リポジトリの .novui/approvals/<approval_id>.yaml に保存し、Patch の適用と同じ commit に含める。commit には `Approval-Id: <approval_id>` を付ける。
* Controller は、Patch のハッシュが patch_sha256 と一致し、target が targets に含まれる場合だけ適用する。
* 1つの承認記録は1回だけ使える。

### 15.5 機械的な強制

1. AGY のコンテナでは、保護対象を ro で重ねる（§5.3）。Phase 0 で書込みが拒否されることを確認済み。
2. 作業ブランチに保護対象の変更が1つでもあれば、Job を FAILED とする（§8.3）。
3. Controller が main に作る commit で保護対象に変更がある場合、`NovUI-Edit: human-*` または有効な `Approval-Id` がなければ commit しない。
4. Claude は読み取り専用で起動し、書込み手段を持たない。

---

## 16. Context

### 16.1 Context の構成

AI に渡す Context は、次の優先順で必要なものだけを選ぶ。全作品の本文を毎回渡さない。

1. 直前章の summary.yaml
2. 必要な過去章の summary.yaml
3. 必要な場合のみ過去章の draft.md
4. 関係する characters、world、plot、foreshadowing/registry.yaml、rules
5. 対象章の outline.md と plan.yaml

* どの過去章が必要かは、plan の作成時に Claude が提案し、Controller が plan.yaml に記録する。執筆・検証の Job は plan.yaml の記録に従う。
* Context は Controller がプロンプトに埋め込む。AI にファイルを読ませない。
* AGY のプロンプトは 120,000 バイト以下とする（§5.1）。超える場合、Controller は優先順の低いものから外す（3 の過去章の draft.md を外し、要約で代える）。それでも超える場合は Job を開始せず WAITING_HUMAN とする。

### 16.2 Context の記録

* Job 開始時に、基準 commit と、Context に含めた各ファイルのパスと SHA-256 を Job 記録に残す。
* Context に対象作品以外のパスが含まれる場合、Controller は Job を開始しない。

---

## 17. Git 運用

### 17.1 ブランチ

* Canonical の branch は main。
* AI の書込み Job は作業ブランチ（ai/<chapter-id>/<job-id>）と専用の worktree で行う。
* AI が main に直接書き込むことは禁止する。

### 17.2 merge

* 作業ブランチは、変更検査と検証を通過し、Human の承認を得てから Controller が main に merge する。
* Job 開始時の基準 commit から main が進んでいる場合：
  - main 側の変更が作業ブランチの変更したパスと重ならなければ、Controller は作業ブランチを main に追従させて merge してよい。
  - 重なる場合は WAITING_HUMAN とし、自動 merge しない。

### 17.3 push

* GitHub への push は main だけとする。作業ブランチは push しない。
* push に失敗しても作品の作業は継続できる。Controller は「未 push」として表示し、Human の操作で再試行する。
* origin/main が進んでいる場合は WAITING_HUMAN とし、自動 merge しない。

### 17.4 commit の規則

* Controller が作る commit には、Job の場合は `NovUI-Job: <job-id>`、Human の直接編集の場合は `NovUI-Edit: human-*`、承認記録を伴う場合は `Approval-Id:` を付ける。

### 17.5 Git 管理対象

* 管理対象：project.yaml、chapters-order.yaml、world/、characters/、plot/、foreshadowing/、rules/、chapters/、reviews/、.novui/
* 管理対象外：output/、認証情報、CLI のセッション、キャッシュ、一時ファイル、実行ログ

### 17.6 外部変更の検出

* Human が UI を経由せずにローカルで commit した変更は許可する。
* Controller は同期時に、前回確認した commit 以降の commit を調べ、保護対象の変更に `NovUI-Edit` も `Approval-Id` もないものを「外部変更」として表示し、影響範囲分析の実行を促す。

---

## 18. 並行実行・lock・Retry

### 18.1 lock

* 実行 lock：作品ごとに、同時に実行できる AGY の Job は1つとする。
* 章 lock：未 merge の作業ブランチがある章には、新しい書込み Job・直接編集・添削を受け付けない。
* WAITING_HUMAN の Job は実行 lock を解放し、章 lock は保持する。他の章の Job は進められる。
* 読み取り専用の Claude の分析は並行してよい。

### 18.2 キュー

* lock を取得できない Job は QUEUED とする。

### 18.3 Retry

* CLI の一時エラー：最大2回
* timeout：最大1回
* schema 不適合：最大2回（§10）
* 許可範囲外の変更、STOP、設定不明（requests.yaml への要求）：Retry しない

---

## 19. 新規作品の作成

### 19.1 原則

* 1作品につき1リポジトリとする。
* リポジトリの作成は Human が開始し、Controller が行う。Claude・AGY は関与しない。

### 19.2 手順

1. Human が作品名、リポジトリ名、テンプレートを指定する。
2. Controller がローカルにリポジトリを作成し、テンプレートから初期構造を作り、main に初期 commit する。
3. Controller が GitHub に private リポジトリを作成し、push する（Spike-12 で認証方式を確認してから実装）。
4. Controller が作品登録簿に追加する。
5. Human が初期設定を入力する（直接編集、または指示バーで Claude に下書きを依頼し承認）。
6. Claude が章構成を提案し、Human が承認する。
7. Human が各章の outline.md を作成し、§9 の執筆フローに入る。

### 19.3 リポジトリの扱い

* 同じ名前のリポジトリが GitHub 上にある場合は作成せず、Human に確認する。
* 公開範囲は private に固定する。Controller は公開範囲の変更、削除、名前の変更、所有者の移転を行わない。

### 19.4 テンプレートと format_version

* テンプレートは NovUI リポジトリの templates/novel/v<format_version>/ に置き、NovUI と一緒に版管理する。
* テンプレートは作品の内容（世界観・人物）を含まない。
* NovUI が対応していない format_version の作品は、読み取り専用で開き、Human の承認を得て移行する。

### 19.5 作品の分離

* 作品登録簿は Controller のデータディレクトリ（例：~/.local/share/novui/registry.yaml）に置く。作品ごとにローカルパス、リモート URL、format_version、状態（active / archived）を記録する。
* lock、Job、承認記録、要確認フラグ、添削データは作品ごとに独立させる。
* AGY の Job用HOME は Job ごとに新規作成する（§5.4）。
* archived の作品は読み取り専用とし、AI の Job を実行しない。

---

## 20. UI

* 3ペイン構成（作品ツリー・本文・AI状態）。スマートフォンではタブ形式。
* 表示：章状態、Job 状態、Git 状態（未 merge・未 push・外部変更）、Validator 結果、要確認フラグ、requests、承認待ちの提案、lock。
* 本文モード（閲覧・直接編集・添削）、指示バー、設定ファイルの保護表示と変更履歴、新規作品の作成画面。
* 画面イメージは Phase 0 で作成したモック（Design 成果物）を基準とする。

## 21. ネットワークと認証

* Controller はシェル実行権限を持つため、公開インターネットに直接公開しない。
* Web UI は Tailscale 経由でのみ利用し、Tailscale の ACL で許可する端末を限定する。
* Controller はホスト上で systemd のユーザーサービスとして常駐させる（`loginctl enable-linger`）。
* ホストの再起動後、実行中だった Job は STOPPED とし、自動で再開しない。

## 22. 完成原稿と最終監査

* chapters-order.yaml の順に FINAL の章の draft.md を結合して output/novel.md を生成する。
* 全章完成後、Claude が世界観・キャラクター・ストーリー・伏線・文章を監査し、結果を reviews/ に保存する。

## 23. 禁止事項

1. AGY が保護対象を変更すること（v0.5.1 以降、AGY は作品のファイルをマウントしない）
2. AGY が許可範囲外のファイルを変更すること
3. AI が未定義の設定を発明すること
4. Claude が Canonical ファイルを直接書き込むこと
5. output/novel.md を原本として編集すること
6. 全作品の本文を毎回 AI に渡すこと
7. AI が main に直接 commit すること、無条件に push すること
8. 認証情報を Git・ログ・結果ファイルに残すこと
9. 検証を経ない AI の変更を FINAL にすること
10. 承認記録なしに保護対象を Controller が変更すること
11. Validator の OVERRIDE で機械的な安全違反を解除すること
12. AGY をホストの HOME で実行すること、Job用HOME を Job 間・作品間で共有すること
13. AGY を `--dangerously-skip-permissions` で実行すること、AGY にシェルコマンドの実行を許可すること
14. Claude を、ホストの設定・CLAUDE.md・MCP を読み込む状態で実行すること（§5.5）

## 24. 実装フェーズと受入条件

### Phase 0：技術スパイク（完了）

* 結果：docs/spike/results/phase0-summary.md

### Phase 1：Controller 基盤

対象：
* Job 実行器（§5 の実行プロファイル：コンテナ、マウント構成、Job用HOME、timeout、後始末）
* 作業ブランチと worktree の管理、変更検査（§8.3）
* 章状態・Job 状態の遷移（§7）と chapter.yaml
* lock とキュー（§18）
* schema 検証（§10）と schemas/
* Job 記録（Context、モデル、バージョン）
* Git 運用（§17）の merge・commit 規則

受入：
* §5 の構成で AGY を実行し、許可範囲外の変更、保護対象の変更、.git の改変、空出力を検出して FAILED にできる
* Job用HOME が Job ごとに作られ、終了後（異常終了を含む）に削除される
* 遷移表にない状態遷移を拒否できる
* 検査処理（変更検査、prefix/suffix、追記のみ、ハッシュ比較、mountinfo）に単体テストがあり、Phase 0 で見つかった種類の誤判定（見出し行の差分、overlay オプションの誤検出、相対パス）を再現するテストを含む

### Phase 2：Claude / AGY 連携

対象：plan、draft、validate、state_update、summary、requests、instruction_routing
受入：outline から FINAL まで（§9）を1章分実行できる。不明な設定で WAITING_HUMAN になる。

### Phase 3：状態管理と Git 反映

対象：承認記録、設定の保護（§15）、要確認フラグ、外部変更の検出、merge・push の規則
受入：承認記録なしに保護対象を変更できない。競合時に自動 merge しない。

### Phase 4：Web UI

対象：§20 の画面、指示バー、直接編集・添削、Tailscale 経由のアクセス
受入：UI から Controller を安全に操作でき、外部から CLI・Git を直接操作できない。

### Phase 5：新規作品・全体監査・完成原稿

対象：§19（Spike-12 の実施を含む）、§22
受入：テンプレートから作品を作成し、GitHub に private で push できる。FINAL の章から原稿を生成できる。

## 25. 持ち越し事項

* Spike-09 の Human ブラインド評価（AGY を執筆担当とする仮説の確認）
* Spike-12（GitHub リポジトリ作成の権限）：Phase 5 の前に実施
* トークン期限切れ時の挙動（標準は書込み可能なコピーのため必須ではない）
* 120,000 バイトを超える Context が必要になった場合の受け渡し（AGY の `--input-format stream-json` による標準入力など）
* 完了（Phase 1-C）：実際の配置場所での SELinux ラベル、Claude の `--json-schema`、イメージの焼き込みの判断（焼き込まない）

## 26. 変更履歴

### v0.5.1

* Phase 1-C の実測（docs/phase1/results/1c-*.yaml）を反映
* AGY はファイルを読まず書かず、本文をテキストで返す方式に変更。Controller が draft.md と requests.yaml に書き込む（§4.2、§5.8、§6.5、§8.1）
* AGY のコンテナのマウントを Job用HOME だけに変更（§5.3）
* AGY の設定不足は本文中の `【要確認：…】` で示し、Controller が requests.yaml に移す
* AGY のプロンプトを 120,000 バイト以下に制限（§5.1、§16.1）
* AGY の `--json-schema` は使わない（遅く、トークン消費が大きいため）
* Claude の起動に `--safe-mode --restricted --strict-mcp-config --output-format json --json-schema` を追加し、出力は封筒の `structured_output` から取得（§5.5、§10）
* 変更検査の位置付けを、Controller 自身の誤りと外部変更の検出に改めた（§8.3）

### v0.5

* v0.4 本体と追補 §41〜§44 を統合
* Phase 0 の結果から実行プロファイル（§5）を新設：AGY の起動形式、accept-edits、コンテナとマウント構成、Job用HOME と認証、Claude の読み取り専用起動、timeout、モデル記録
* .git 本体をマウントしない構成を標準化し、.git 参照ファイルを ro で重ねる
* 機械可読ファイルを YAML 化（project.yaml、chapters-order.yaml、characters/*.yaml、registry.yaml、plan.yaml、summary.yaml、review.yaml、requests.yaml）
* 章メタデータ chapter.yaml と要確認フラグの保存場所・解除条件を確定
* 章状態・Job 状態の遷移表を確定し、Job 状態を整理（RUNNING、CHECKING、CANCELLED を追加）
* Job 種別と書込み許可を一覧化し、変更検査に空出力・文字数を追加
* 承認記録の保存場所を .novui/approvals/ に確定
* 赤入れの作業データ、未承認の提案、作品登録簿、テンプレートの置き場所を確定
* merge の条件（パスが重ならない場合の追従）と push の規則を確定
* lock を実行 lock と章 lock に分け、WAITING_HUMAN 中の扱いを確定
* 禁止事項に Job用HOME と `--dangerously-skip-permissions` を追加
* Phase 0 を完了とし、Phase 1 以降の受入条件を改訂
