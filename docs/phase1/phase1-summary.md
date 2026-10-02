# Phase 1 結果まとめ：Controller 基盤

**作成：2026-10-03 / branch：phase1/controller（最終確認 commit 49c2826）**
**基準：docs/novel-system-spec-v0.5.2.md §24 Phase 1、docs/phase1/phase1-plan.md**

## 1. 結論

Phase 1 の受入条件（§24）をすべて満たした。単体テスト 200件、結合試験 8件（実機の Podman・AGY・Claude を使用）がすべて PASS。

| 受入条件 | 確認したテスト |
|---|---|
| §5 の構成で AGY を実行し、許可範囲外の変更・保護対象の変更・.git の改変・空出力を検出して FAILED にできる | tests/test_jobrunner.py（14通りの経路）、結合試験 J1・I1〜I5 |
| Job用HOME が Job ごとに作られ、終了後（異常終了を含む）に削除される | tests/test_jobhome.py、tests/test_jobrunner.py（例外の経路）、結合試験 J1 |
| 遷移表にない状態遷移を拒否できる | tests/test_states.py（全状態 × 全イベント。期待表は仕様からの書き写し） |
| 検査処理の単体テストと、Phase 0 の誤判定の再現 | 見出し行の差分：tests/test_checks.py、overlay の誤検出：tests/test_mountinfo.py（Phase 0 の実データ）、相対パス：tests/test_gitinspect.py |

## 2. 実装したもの（src/novui/）

| モジュール | 内容 |
|---|---|
| paths、protected | 相対パスの安全性、ルート外への逸脱の検出、保護対象の判定 |
| states | 章状態・Job 状態の遷移表 |
| checks、anchor、gitinspect、mountinfo | 変更検査（許可パス、ignored、追記のみ、prefix/suffix、ハッシュ、文字数）、anchor、git status の解析、.git 保護対象、mountinfo |
| yamlio、schema、semantics | YAML の安全な読み書き（日時・yes/no・60進数・8進数の暗黙変換と重複キーを禁止）、JSON Schema 15種、schema で表せない検査 |
| claude_output、claude_cli | Claude の起動（設定・MCP を読み込まない）、JSON の封筒の検証と再要求 |
| procrun、jobhome、container、agy_output、prompt | プロセス制御と timeout、Job用HOME、コンテナ起動、AGY の出力の取り込みと【要確認】の抽出、プロンプトの組み立て |
| workrepo、locks | 作業ブランチと worktree、commit と trailer、保護対象の commit の検査、merge の条件、実行 lock・章 lock |
| jobrecord、jobrunner、recovery、models | Job 記録、draft Job の一連の実行、起動時の回復、モデル一覧の取得と選択 |
| config | 設定（データディレクトリ、イメージ、トークンの場所、timeout、git の名前） |

## 3. Phase 1 で確定した方式（仕様 v0.5.1・v0.5.2 に反映済み）

* AGY はファイルを読まず書かない。必要な情報は Controller がプロンプトに埋め込み、AGY は本文をテキストで返す。AGY のコンテナには Job用HOME だけをマウントする。
* AGY のプロンプトは 120,000 バイト以下（Linux の引数の上限 128KB の実測による）。
* AGY の設定不足は本文中の `【要確認：…】` で示し、Controller が requests.yaml に移して WAITING_HUMAN にする。
* AGY の `--json-schema` は使わない（1行に80〜127秒、トークン消費が大きい）。テキスト出力は約7〜12秒。
* Claude は `--safe-mode --restricted --strict-mcp-config --output-format json --json-schema` で起動する（付けないとホストの設定・コネクタの影響を受けた）。実際のモデル名は modelUsage から取得できる。
* 作品の章 lock は、作業ブランチ（`ai/<章>/<Job>`）の有無で判定する（再起動後も正しい）。
* 設定ファイルの変更を含む commit は、`NovUI-Edit` か `Approval-Id` の trailer がなければ作らない。
* モデルは `agy models` で取得し、用途ごとに Human が選ぶ。一覧から消えたら止まり、自動で切り替えない。

## 4. Phase 2 以降への持ち越し

| 項目 | 時期 |
|---|---|
| 本番用イメージのビルド：完了（2026-10-03、localhost/novui-agy:1.2.14、image id ce3e93ec402f。結合試験8件 PASS）。既定のイメージを切り替え済み | 完了 |
| Validator（Claude）の Job への組み込み、plan・validate・state_update・summary・instruction_routing の各 Job | Phase 2 |
| range_edit・chapter_rewrite の Job（splice_range は実装済み） | Phase 2 |
| キャラクター一貫性・伏線の検査、場面の区切りの機械検査（v0.5.2 §6.5.1、§11.7、§11.8） | Phase 2 |
| 章・場面の順序に依存する検査（伏線の時系列、changes・exceptions の適用位置） | Phase 2 |
| Job のキュー（今は実行 lock が取れなければ RunLockBusy を返す） | Phase 2 |
| 作業ブランチが残っている章への修正 Job（REQUEST_FIX）の扱い（今は章 lock で拒否する） | Phase 2 で設計 |
| 120,000 バイトを超える Context の受け渡し（AGY の stream-json 入力など） | 必要になった時点 |
| トークン期限切れ時の挙動 | 運用中に確認 |
| Spike-09 の Human ブラインド評価、Spike-12（GitHub リポジトリ作成の権限） | Phase 2 の執筆品質確認時、Phase 5 の前 |
| systemd の常駐、Web UI、Tailscale | Phase 4 |
| 結合試験 I4 の診断出力（print）の整理 | 任意 |

## 5. 進め方についての記録

* 実機での確認（1-C）で、AGY のツール実行の自動拒否、`--json-schema` の制約と遅さ、引数長の上限、Claude へのホスト設定の混入が見つかり、方式を改めた。机上の仕様だけで実装を進めていれば、Phase 2 で手戻りになっていた。
* 1-D2 の初回の報告で、AGY は結合試験を指示書と異なる内容で実装しながら「判断に迷った点なし」と報告した。Claude のレビューで差分を確認して修正させた。今後も、AGY の報告ではなく差分そのものを確認する。

## 6. 次の作業

1. Human の承認を得て、phase1/controller を main に統合する（main は phase1/controller の祖先のため fast-forward になる）。
2. 本番用イメージをビルドする。
3. Phase 2（Claude / AGY 連携）の計画を立てる。
