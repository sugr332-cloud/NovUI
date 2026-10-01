# Phase 0 技術スパイク 結果まとめ

**作成：2026-10-01 / 対象branch：spike/phase0（最終確認commit 6cd8581）**
**基準：docs/spike/phase0-spike-plan.md、docs/novel-system-spec-v0.4.md（§41〜§44 追補を含む）**

## 1. 実行環境

| 項目 | 値 |
|---|---|
| ホスト | LivingPC（Bazzite 44 / Kinoite、kernel 7.2.4） |
| SELinux | Enforcing |
| Podman | 5.8.4（rootless） |
| Git | 2.55.0 |
| AGY CLI | 1.2.14（単体ELFバイナリ、~/.local/bin/agy） |
| Claude Code | 2.1.286 |
| コンテナイメージ | novui-spike:agy-1.2.14（Fedora 44 ベース、AGY バイナリを COPY） |

AGY は自動更新される。2日間で 1.2.12 → 1.2.13 → 1.2.14 と更新された。

## 2. 結果一覧

| Spike | 内容 | 結果 | 備考 |
|---|---|---|---|
| 00 | 事前確認 | PASS | 各 CLI の --help を取得 |
| 01 | AGY 非対話実行 | PASS（再実行） | 初回は --print の引数解釈で FAIL |
| 11 | AGY 認証の受け渡し | PASS | 方式A・B 成功。C・D は AGY が APIキー認証に非対応のため対象外 |
| 02 | Podman 実行隔離 | PASS（再実行） | 初回 FAIL は mountinfo 検査の誤判定 |
| 03 | .git 保護 | 実質 PASS | 判定 FAIL はハッシュ記録ファイルの見出し行の差分による誤判定。実ファイルのハッシュは一致 |
| 05 | Claude CLI 出力挙動 | PASS | 読み取り専用・構造化出力とも合格 |
| 10 | モデル情報 | PASS | CLI からモデル名は取得不可（取得可否が確定） |
| 09 | 文章ブラインド比較 | 生成 PASS | Human 評価は未実施 |
| 12 | GitHub リポジトリ作成権限 | 未実施 | §44 の実装まで延期 |

## 3. 判明した事実と v0.5 への反映事項

### 3.1 AGY の起動方法（Spike-00・01）

* 非対話の起動は `agy --mode accept-edits --print=<プロンプト>` とする。
* `--print` は直後の引数をプロンプトとして解釈する。他のフラグは `--print` より前に置き、プロンプトは `--print=` に結合して1引数で渡す。
* 標準入力からプロンプトを渡す方法には対応していない（`flag needs an argument: -print`）。
* 不正な引数では終了コード 2 を返す。終了後にプロセスグループは残らない。

### 3.2 accept-edits モードの権限（Spike-02）

* `--print --mode accept-edits` の AGY は、ファイル編集は自動承認され、コマンド実行（Bash 等）は自動で拒否される。
* 執筆 Job に必要なのは編集だけであり、このモードを AGY 実行の標準とする。
* 拒否時、AGY は「標準出力が空・終了コード 0」で終了した。**Controller は空出力を失敗として扱う**こと。
* `--dangerously-skip-permissions` は使用しない。

### 3.3 実行隔離（Spike-02）

* rootless Podman + `--userns=keep-id` で、コンテナから見えるホストのパスは、明示的にマウントした作業ツリーだけだった。
* 作業ツリーの外（親ディレクトリ、Home、Git common directory）への書込みは失敗した。コンテナ内 /tmp への書込みはホストに届かない。
* 作業ツリーを rw でマウントし、設定ファイル（world/、characters/、plot/、foreshadowing/、rules/、project.md、chapters-order.md）を ro で重ねる構成で、設定への書込みはすべて拒否され、draft.md には書き込めた。**§43 の設定保護は実行隔離で強制できる。**
* コンテナで作成したファイルの所有者は、keep-id の有無にかかわらずホストのユーザー（1000:1000）になった。コンテナ内のプロセスもホストと同じ UID で動く keep-id ありを標準とする。

### 3.4 SELinux ラベル（Spike-02）

* .work 配下の試験ディレクトリでは、ラベルなし・z・Z のすべてで書込みに成功した。
* 試験ディレクトリが以前の試験のラベルを引き継いでいた可能性があり、「ラベルなしで書ける」とは結論できない。
* AI 用の作業ツリーには Z（そのコンテナ専用のラベル）を使う方針を維持する。実際の作業ツリーの配置場所での確認は Phase 1 で行う。

### 3.5 .git の扱い（Spike-03）

* Git common directory をマウントしない構成では、コンテナ内の git は動かない。ro でマウントする構成では、config・hooks・worktrees への書込みは拒否され、git status は動いた。
* AGY は accept-edits でコマンドを実行できず、git を必要としない。**Git common directory はマウントしない構成を標準とする。** git の操作はすべてホスト側の Controller が行う。
* 作業ツリー内の .git 参照ファイルは、作業ツリーが rw のため書き換え可能だった。**.git 参照ファイルを ro で重ねてマウントし、Job 終了後に Controller がハッシュで確認する。**

### 3.6 AGY の認証（Spike-11）

* 認証は OAuth。トークンは `~/.gemini/antigravity-cli/antigravity-oauth-token`。
* 空の HOME（/home/agy）にトークンのコピーだけを置く構成で、コンテナ内の認証に成功した（書込み可・読み取り専用の両方）。
* 今回の実行ではトークンは更新されなかった。期限切れ時に読み取り専用で更新に失敗するかは未確認。
* **標準は方式A：Job ごとにトークンのコピーを作り、書込み可能で渡し、終了後に HOME ごと破棄する。** ホストの元のトークンには触れない。
* ログ・結果ファイル・Git 管理対象のいずれにもトークン文字列の漏洩はなかった。

### 3.7 AGY の記憶と作品の分離（Spike-11）

* AGY は1回の起動ごとに、HOME 配下の brain/、conversations/、implicit/、annotations/ などに会話の記録を書き込む。
* ホストの HOME のまま実行すると、別の作品や別のプロジェクトの記憶が文脈に混ざりうる。
* **§44.5 の作品分離のため、AGY は Job ごとに空の HOME で実行することを必須とする。**

### 3.8 起動時間（Spike-11）

| 条件 | 所要時間 |
|---|---|
| コンテナ・空の HOME・1回目 | 12秒 |
| コンテナ・同じ HOME・2回目 | 9秒 |
| ホスト上で直接起動 | 9秒 |

* コンテナ化と空の HOME による遅延は、初回の約3秒だけだった。
* 初回には builtin/skills、bin/ などが展開される。必要ならイメージに焼き込んで初回の遅延をなくせる（Phase 1 で判断）。

### 3.9 自動更新（Spike-11）

* コンテナ内では更新の試行は見られず、バージョンは変わらなかった。
* ホストの AGY は頻繁に自動更新されるため、**コンテナイメージのタグに AGY のバージョンを入れて固定する**。更新の取り込みはイメージの再ビルドで行う。

### 3.10 Claude CLI（Spike-05）

* 読み取り専用の起動は `claude -p --tools Read --permission-prompts none --no-session-persistence`。ファイル作成・変更を明示的に指示しても、何も作成・変更されなかった。
* プロンプトの指示だけで、構造化出力（JSON）は10回中10回、そのまま parse でき schema に適合した。コードフェンスや前置き・後置きは0回。
* それでも Controller 側の schema 検証と、不適合時の再要求は残す。
* `--json-schema` による構造化出力の強制は未検証（Phase 1 で検証）。

### 3.11 モデル情報（Spike-10）

* AGY・Claude とも、CLI のメタデータからモデル名は取得できなかった。
* 自己申告は AGY が「Gemini 3.8 Flash」、Claude が「Opus 5.5」。参考値としてだけ扱う。
* **Job 記録には CLI のバージョンと、明示した --model の値を残す。** モデルを確実に記録するため、Job ごとに --model を明示する。

### 3.12 timeout（Spike-01・09）

* print モードは最後にまとめて出力するため、生成中は無出力になる。章の本文の生成では、Claude 約73秒、AGY 約104秒、出力なしの時間が続いた。
* 無出力 timeout は print モードと相性が悪い。**print モードでは全体 timeout を主とし、無出力 timeout は使わないか、全体と同じ値にする。** 進捗を見たい場合は stream-json 出力を検討する。

### 3.13 文字数の遵守（Spike-09）

* 目標 4,000〜6,000字に対し、一方の出力は約10,800字（約2倍）、もう一方は約5,500字だった。
* **文字数は Controller の機械検査（AI 的定型表現の検査と同じ枠）で確認し、範囲外は WARNING とする。**

## 4. 検証スクリプトで見つかった不具合（記録）

* Spike-03：初版は試験用リポジトリの Git common directory を相対パスで取得し、実リポジトリの .git/hooks に書き込む不具合があった（修正3で修正、実行前に発見）。
* Spike-03：ハッシュ比較で記録ファイルの見出し行まで比較しており、誤って FAIL と判定した（未修正。実ファイルのハッシュは一致）。
* Spike-02：mountinfo 検査がコンテナルートの overlay オプションを誤検出した（修正済み）。
* Spike-00：Git common directory を相対パスで表示している（表示のみ。未修正）。

これらは Phase 1 の Controller 実装でも起こりうる種類の誤りである。Controller の検査処理には単体テストを必須とする。

## 5. 未完了・持ち越し

* Spike-09 の Human ブラインド評価（評価シートは docs/spike/results/spike09_blind_evaluation_sheet_run_20261001_221555.md）
* Spike-12（GitHub リポジトリ作成の権限）：§44 の実装時に実施
* トークン期限切れ時の読み取り専用マウントでの挙動
* 実際の作業ツリーの配置場所での SELinux ラベルの確認
* Claude の --json-schema による構造化出力
* builtin/skills 等のイメージへの焼き込みによる初回遅延の解消

## 6. 総合判断

Controller 設計の前提だった次の4点が、実測で成立した。

1. AGY を非対話で起動し、終了コード・timeout・プロセスの後始末を制御できる。
2. AGY を実行隔離し、作業ツリー以外と設定ファイルへの書込みを防げる。
3. AGY を作品ごと・Job ごとに分離した HOME で、ホストの認証情報を壊さずに認証できる。
4. Claude を読み取り専用で起動し、構造化出力を得られる。

Phase 0 の目的は達成した。次は本まとめを反映した v0.5 仕様案の作成に進む。
