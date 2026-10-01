# Phase 1 実装計画：Controller 基盤

**作成：2026-10-01 / 基準：docs/novel-system-spec-v0.5.md §24 Phase 1**
**branch：phase1/controller（spike/phase0 から分岐）**

## 1. 目的

v0.5 §24 の Phase 1 受入条件を満たす Controller の基盤を作る。

* §5 の構成で AGY を実行し、許可範囲外の変更、保護対象の変更、.git の改変、空出力を検出して FAILED にできる
* Job用HOME が Job ごとに作られ、終了後（異常終了を含む）に削除される
* 遷移表にない状態遷移を拒否できる
* 検査処理に単体テストがあり、Phase 0 で見つかった種類の誤判定を再現するテストを含む

## 2. 技術選定

| 項目 | 決定 |
|---|---|
| 言語 | Python 3（3.11 以上。Bazzite 44 標準の python3 を使う） |
| 依存 | 標準ライブラリを基本とし、PyYAML、jsonschema、pytest のみ追加（追加は段階ごとに本計画で指定） |
| 環境 | リポジトリ直下の `.venv`（Git 管理外）。rpm-ostree・toolbox は使わない |
| 構成 | `pyproject.toml`、`src/novui/`、`tests/`（pytest の pythonpath に src を指定し、インストールなしでテスト可能にする） |
| 実行場所 | Controller はホスト上で動く（コンテナに入れるのは AGY だけ） |
| git 呼び出し | すべて `git -C <絶対パス>` で行い、`GIT_OPTIONAL_LOCKS=0`・`LC_ALL=C` を付ける |

spike/phase0/ の検証スクリプトは参照用として残し、Controller のコードからは呼ばない。

## 3. 段階

各段階で、AGY が実装・テスト実行・commit・push（phase1/controller のみ）を行い、Claude が差分をレビューする。問題がなければ次の段階に進む。

### 1-A：検査コアと状態遷移

コンテナも AI CLI も使わない、純粋なロジックと git の読み取り操作だけを実装する。

* paths：相対パスの安全性、ルート外への逸脱の検出
* states：章状態・Job 状態の遷移表（§7.1、§7.2、本計画 §4 の補足を含む）
* checks：CLI 出力（空出力・終了コード）、許可パス、ignored ファイルの増減、追記のみ、prefix/suffix、ハッシュ記録の比較、文字数
* anchor：anchor の一意な特定（§13.5）、prefix/suffix の切り出し
* gitinspect：`git status -z` の解析、Git ディレクトリの絶対パス解決、.git 保護対象の列挙とハッシュ
* mountinfo：/proc/self/mountinfo の解析と、想定外マウントの検出

指示書：docs/phase1/agy-instruction-1a.md

### 1-B：schema と Job 記録

* schemas/ に JSON Schema を置く：chapter.yaml、plan.yaml、review.yaml、requests.yaml、承認記録、Job 記録、Claude 出力（plan、integrity_review、writing_review、state_patch、summary、instruction_routing）
* YAML の読み書きと schema 検証、Claude 出力の parse・検証・再要求（最大2回）の制御
* Job 記録（基準 commit、Context のパスとハッシュ、CLI 名とバージョン、--model、イメージのタグと ID、timeout 情報）
* YAML の読み込みは、日時の自動変換・yes/no 等の真偽値変換・重複キーの上書きを行わない専用の Loader を使う

指示書：docs/phase1/agy-instruction-1b.md

### 1-C：Job 実行器

* プロセス制御：プロセスグループ、全体 timeout、SIGTERM → SIGKILL、終了コードと経過時間の記録
* Job用HOME：作成、トークンのコピー（600）、終了時の削除（例外・シグナル時を含む）
* コンテナ起動：§5.2・§5.3 の引数の組み立て、`--name`、終了後の `podman rm -f`
* マウント検査：同じマウント構成の検査用コンテナで mountinfo を取得し、1-A の mountinfo で検査する
* 持ち越し：実際の worktree 配置場所での SELinux ラベル確認、builtin/skills 等のイメージ焼き込みの判断、Claude の `--json-schema` の検証（1-B から移動）
* イメージの焼き込みは行わない（初回の遅延約3秒は、本文生成の約100秒に比べて小さいため）。1-C の試験には Phase 0 のイメージ（novui-spike:agy-1.2.14）を使い、本番用イメージのビルド手順は 1-D で作る

指示書：docs/phase1/agy-instruction-1c.md

### 1-D：worktree・lock・Git 反映と受入確認

* 作業ブランチと worktree の作成・削除（ai/<chapter-id>/<job-id>）
* 実行 lock・章 lock・キュー（§18）
* 変更検査（§8.3）の一連の実行と、Job 状態の遷移
* merge 条件（§17.2）と commit の trailer（§17.4）
* モデル一覧の取得と選択（本計画 §4 の 13）
* ダミー作品リポジトリでの通し確認：許可範囲外の変更、保護対象の変更、.git 参照ファイルの改変、空出力をそれぞれ起こして FAILED になることを確認する

1-D の完了をもって Phase 1 の受入とし、Human の承認後に spike/phase0 と phase1/controller を main に merge する。

## 4. v0.5 の補足（Phase 1 で確定し、v0.5.1 に反映する）

v0.5 の遷移表で曖昧だった点を、次のとおり確定する。

1. Controller 再起動時（§7.2「任意」）：QUEUED・RUNNING・CHECKING・VALIDATING の Job を STOPPED とする。WAITING_HUMAN の Job は再起動後も WAITING_HUMAN のまま残す（Human の判断待ちであり、実行中ではないため）。終了状態（COMPLETED・FAILED・STOPPED・CANCELLED）は変えない。
2. Human の STOP は RUNNING に加えて VALIDATING でも受け付ける（Claude の検証も時間がかかるため）。
3. QUEUED の Job は Human が CANCEL できる（CANCELLED）。
4. anchor が一意に特定できない場合（§13.5）は QUEUED → WAITING_HUMAN、Claude の振り分けが questions を返した場合は CHECKING → WAITING_HUMAN とする（イベント名 NEEDS_HUMAN_INPUT）。
5. 章の表記修正（§14.3）は、章状態がある場合だけ受け付け、状態を変えない。
6. 同じ章で plan を作り直す場合は plan_revision（PLAN_APPROVED 以降）を使う。PLANNED の状態で plan を上書きする遷移は設けない。
7. 文字数は、空白文字（改行・全角空白を含む）を除いた Unicode のコードポイント数で数える。
8. git の .git 保護対象のハッシュは、worktree の .git 参照ファイル、common の config と hooks/ 以下の全ファイル、worktrees/<name>/ の gitdir・commondir・HEAD とする。index と logs/ は Controller 自身の git 操作で変わりうるため対象外とする。ハッシュは CLI の終了直後、git コマンドを実行する前に取る。
9. §8.3 の ignored ファイルの一覧は `git ls-files --others --ignored --exclude-standard` で取る。`git status --ignored` は無視されたディレクトリを1件にまとめるため、既存の無視ディレクトリ内に増えたファイルを検出できない。
10. YAML の数値の暗黙変換は YAML 1.2 相当に限る。`12:30`（60進数）、`0755`（8進数）、`007`、`1_000`、`0x1F` などは文字列として読む（人が書く設定ファイルの時刻や ID が数値に化けるのを防ぐ）。
11. Controller のデータディレクトリは `~/.local/share/novui/`（環境変数 NOVUI_DATA_DIR で変更可）。worktree は `<data>/worktrees/`、Job用HOME は `<data>/jobhomes/` に置く。コンテナに Z ラベルでマウントするパスは、これらの配下に限る（誤ってホームディレクトリ等を再ラベルしないため）。
12. AGY のコンテナには、§5.2・§5.3 に加えて `--pull=never`、`--cap-drop=all`、`--security-opt=no-new-privileges` を付ける。1-C の結合試験で AGY が動くことを確認する。
13. モデルは頻繁に追加・更新されるため、Controller がモデル一覧を取得し、Human が選べるようにする。
    * AGY のモデル一覧は、Job で使うのと同じコンテナイメージの `agy models` で取得する（イメージで固定した CLI のバージョンで使えるモデルと一致させるため）。Job用HOME とトークンの扱いは通常の Job と同じ。
    * 取得のタイミングは、Controller の起動時、1日1回、Human の操作時。取得結果（モデル ID、表示名、取得日時、agy のバージョン）を `<data>/models/agy.yaml` に保存する。
    * 使うモデルは用途ごと（執筆、範囲編集など）に Human が選び、`<data>/settings.yaml` に保存する。作品ごとに上書きできる（途中でモデルが変わると文体が変わりうるため）。
    * 新しいモデルが一覧に現れても、自動では切り替えない。UI に「新しいモデルがあります」と表示し、Human が選ぶ。
    * 選んだモデルが一覧から消えた場合、その用途の Job は開始せず WAITING_HUMAN とし、Human に選び直しを求める。別のモデルへ黙って切り替えない。
    * Claude の CLI にはモデル一覧の取得手段がない。`opus`・`sonnet` などの別名で指定し（別名は CLI 側で最新のモデルに対応する）、実際に使われたモデルを出力から取得できるかは 1-C の `--json-schema` 確認の結果（出力形式 json）で判断する。
    * Job 記録には、指定したモデル ID と、取得できた場合は実際のモデル名を残す（§5.7）。
