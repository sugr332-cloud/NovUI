# AGYへの作業指示：Phase 1-C 仕上げ（v0.5.1 の入出力方式への移行）

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを変更・作成し、テストと結合試験を実行し、phase1/controller branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。
結合試験で想定と違う結果が出た場合は、コードや試験を変えて辻褄を合わせず、その時点で止めて報告してください。

## 背景

Phase 1-C の確認（docs/phase1/results/1c-*.yaml）の結果、仕様を v0.5.1 に改めました。要点：

* AGY はファイルを読まず書かない。必要な情報はプロンプトに埋め込み、本文はテキストで受け取る。Controller が書き込む。
* AGY のコンテナには Job用HOME だけをマウントする。
* AGY のプロンプトは 120,000 バイト以下。
* AGY の設定不足は本文中の `【要確認：…】` で示し、Controller が requests.yaml に移す。
* Claude は `--safe-mode --restricted --strict-mcp-config --output-format json --json-schema` で起動し、結果は JSON の封筒の `structured_output` から取る。

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/novel-system-spec-v0.5.1.md` の §4.2、§5.1、§5.3、§5.5、§5.7、§5.8、§8.3、§10、§13.5、§16、§26
* `docs/phase1/phase1-plan.md`
* `docs/phase1/agy-instruction-1c.md`（元の指示。本指示と異なる部分は本指示が優先）
* `docs/phase1/results/` の4つの YAML

## 書込みの許可範囲

```text
許可：
  src/novui/container.py、src/novui/claude_cli.py、src/novui/claude_output.py（本指示の変更のみ）
  src/novui/protected.py、src/novui/agy_output.py（新規）
  schemas/job_record.schema.json（本指示の変更のみ）
  tests/**（本指示の変更・追加）
  tools/**、docs/phase1/results/**（前回までの作業で作成済みのもの。変更しない。commit に含める）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。試験の終了時に削除する）
禁止：上記以外すべて（docs/ の既存ファイル、spike/、.git/、.gitignore、pyproject.toml、src/novui のその他のファイルを含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由）
ls -la ~/.local/share/novui-itest
podman ps -a --filter name=novui-itest --format '{{.Names}}'
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase1/controller
```

1-A〜1-C の禁止事項はすべて引き続き有効です（トークンの内容を出力しないこと、`~/.gemini/` を自分で読まないこと、`--dangerously-skip-permissions` を使わないことを含む）。

## 作業 0：procrun のテストの修正（tests/test_procrun.py）

`test_grandchildren_cleaned_up` は、kill した `sleep` を `os.kill(pid, 0)` で確認しています。親に回収されない環境ではプロセスがゾンビとして残り、kill に成功していても失敗と判定されます（Claude の環境で再現）。確認方法を次に改めてください（src/novui/procrun.py は変更しない）。

* 最大2秒、0.1秒ごとに確認し、`/proc/<pid>/stat` が存在しない、または状態（3番目のフィールド）が `Z` になれば成功とする。2秒経っても生きていれば失敗。

## 作業 1：src/novui/protected.py（新規）

* container.py の `PROTECTED_PATHS` をそのまま移す（値と順番は変えない）。
* container.py からは削除する。テストの import も直す。

## 作業 2：src/novui/container.py の変更

* `build_mounts` を削除し、代わりに次を追加する。

  ```python
  def build_agy_mounts(*, job_home: Path, jobhome_root: Path) -> list[Mount]
  ```

  - 戻り値は `[Mount(source=job_home, target="/home/agy", mode="rw")]` の1件だけ。
  - job_home が `ensure_within(jobhome_root, job_home)` を通らない、jobhome_root そのもの、パス文字列に `,` または `:` を含む、のいずれかなら ContainerError。

* `agy_command` に次を加える。

  ```python
  MAX_PROMPT_BYTES: int = 120_000

  class PromptTooLarge(ValueError)   # 属性 size: int（プロンプトの UTF-8 バイト数）、limit: int
  ```

  - プロンプトの UTF-8 のバイト数が MAX_PROMPT_BYTES を超えたら PromptTooLarge。
  - プロンプトに NUL 文字（`\x00`）が含まれていたら ValueError。
  - それ以外（引数のリストの形）は変えない。

* `run_container` の後始末を次のとおりにする。
  - 今は `remove_container` の失敗を握りつぶしている。これを改め、run_process が**正常に戻った場合に** remove_container が失敗したら ContainerError を送出する（コンテナが残った可能性を隠さないため）。
  - run_process が例外を送出した場合は、remove_container を呼んだうえで元の例外を送出する（remove_container の失敗はその場合だけ無視してよい）。

* `build_run_args` は変更しない。

## 作業 3：src/novui/agy_output.py（新規）

```python
MARKER_RE: re.Pattern[str]   # r"【要確認[：:]\s*([^】]*?)\s*】"

class AgyOutputError(Exception):   # 属性 kind: str（"decode" | "empty" | "code_fence"）

@dataclass(frozen=True)
class Marker:
    message: str     # 括弧内の文字列（前後の空白を除く）。空なら "（記載なし）"
    start: int       # text 内の開始位置（文字単位）
    end: int         # text 内の終了位置（文字単位）

@dataclass(frozen=True)
class AgyText:
    text: str
    markers: tuple[Marker, ...]

def parse_agy_text(stdout: bytes, *, trailing_newline: bool = True) -> AgyText
def markers_to_requests(markers: Sequence[Marker], *, job_id: str, chapter_id: str) -> list[dict]
def splice_range(prefix: bytes, original_range: bytes, replacement: str, suffix: bytes) -> bytes
```

* `parse_agy_text`：
  1. UTF-8（strict）で復号。失敗は kind=`decode`。
  2. 末尾の空白（`rstrip()`）を除く。結果が空（空白のみを含む）なら kind=`empty`。
  3. 先頭の空白を除いた文字列が ```` ``` ```` で始まれば kind=`code_fence`。
  4. trailing_newline が True なら末尾に `\n` を1つ付ける。
  5. MARKER_RE のすべての一致を Marker にする（位置は手順4の後の text での位置）。
* `markers_to_requests`：各 Marker を `{"type": "request", "job_id": job_id, "chapter_id": chapter_id, "kind": "undefined_setting", "target": None, "message": marker.message}` にし、リスト全体を schema `requests` と semantics で検証してから返す（エラーは SchemaError）。
* `splice_range`（range_edit で Controller が使う。§5.8、§13.5）：
  - original_range を UTF-8 で復号し、末尾の空白文字列（正規表現 `\s*$` に一致する部分）を取り出す。
  - `replacement.rstrip()` にその末尾の空白を付けたものを UTF-8 にし、`prefix + それ + suffix` を返す。
  - original_range が UTF-8 で復号できなければ ValueError。

## 作業 4：src/novui/claude_cli.py の変更

* `claude_args` を次に改める（output_format 引数は削除）。

  ```python
  def claude_args(*, model: str, json_schema: str) -> list[str]
  ```

  戻り値（この順で完全一致）：

  ```text
  ["claude", "-p", "--tools", "Read", "--permission-prompts", "none", "--no-session-persistence",
   "--safe-mode", "--restricted", "--strict-mcp-config",
   "--model", model, "--output-format", "json", "--json-schema", json_schema]
  ```

  model・json_schema が空なら ValueError。

* `run_claude` の引数から output_format を削除し、json_schema を必須にする。それ以外は変えない。

## 作業 5：src/novui/claude_output.py の変更

Claude の出力は JSON の封筒になりました。`parse_claude_output` を、封筒を受け取る形に改めます。

* `ClaudeOutputError.kind` に `"envelope"` を追加する。
* `parse_claude_output(stdout: bytes, expected_type: str) -> dict` の手順：
  1. expected_type の確認（変更なし）
  2. UTF-8（strict）で復号。失敗は `decode`。
  3. 前後の空白を除いた全体を json.loads。失敗は `json`（コードフェンスの除去はしない。変更なし）。
  4. 封筒の確認：dict でない、`is_error` が true、`structured_output` がない、または dict でない場合は `envelope`。
  5. `structured_output` について、従来の手順（`type` の確認 → schema → semantics）を行う。kind は従来どおり `type`・`schema`・`semantic`。
  6. `structured_output` を返す。
* 追加：

  ```python
  @dataclass(frozen=True)
  class ClaudeRunMeta:
      is_error: bool | None
      model_usage_keys: tuple[str, ...]
      total_cost_usd: float | None
      permission_denials_count: int

  def parse_claude_meta(stdout: bytes) -> ClaudeRunMeta | None
  ```

  封筒から各値を取り出す（`modelUsage` のキー、`total_cost_usd`、`permission_denials` の件数）。stdout が封筒として読めなければ None。例外は送出しない。
* `run_with_output_retry` は変更しない（parse_claude_output の変更に従う）。

## 作業 6：schemas/job_record.schema.json の変更

* cli のオブジェクトに `actual_model`（string \| null。必須）を追加する。Claude は `modelUsage` のキーから得たモデル名、AGY は null を入れる（§5.7）。
* 既存の fixture（tests/fixtures/valid/job_record.yaml）は cli が null なので変更不要。cli が null でない正例を test_schema.py に1件追加する。

## 作業 7：単体テストの変更・追加

* test_container.py：
  - build_mounts のテストを削除し、build_agy_mounts のテスト（正常、jobhome_root の外、jobhome_root そのもの、`,`・`:` を含むパス）に置き換える。
  - agy_command：120,000 バイトちょうどは成功、120,001 バイトで PromptTooLarge（size と limit が正しい）、NUL を含むと ValueError。日本語（1文字3バイト）で境界を確かめるテストを1件含める。
  - run_container：run_process が正常に戻り remove_container が失敗した場合に ContainerError。run_process が例外を送出し remove_container も失敗した場合に、元の例外が伝わる。
* test_protected.py（新規）：PROTECTED_PATHS の値と順番が spec v0.5.1 §15.2 と一致する（期待値はテストにリテラルで書く）。
* test_agy_output.py（新規）：
  - 末尾の空白の除去と改行の付加、trailing_newline=False。
  - decode・empty（空、空白と改行だけ）・code_fence（先頭の空白の後の ```` ``` ```` を含む）の各失敗。
  - マーカー：全角コロン・半角コロン・前後の空白・複数・空（"（記載なし）" になる）・位置（text[start:end] がマーカー全体と一致）。マーカーのない本文で markers が空。
  - markers_to_requests が schema に適合する dict を返す。不正な job_id（`job-x`）で SchemaError。
  - splice_range：元の範囲の末尾が `\n` の場合と `\n` なしの場合、置き換えが日本語の場合。結果に対し checks.check_prefix_suffix が PASS になる。
* test_claude_cli.py：claude_args の全体を完全一致で比較する。model・json_schema が空で ValueError。
* test_claude_output.py：
  - 既存のテストを、正例を封筒 `{"type": "result", "is_error": false, "result": <JSON文字列>, "structured_output": <正例>, "modelUsage": {"claude-opus-5-5": {}}, "total_cost_usd": 0.01, "permission_denials": []}` で包む形に直す。
  - 新しい失敗：封筒が配列、`is_error` が true、`structured_output` がない、`structured_output` が文字列 → それぞれ `envelope`。
  - parse_claude_meta：正しい封筒から各値、壊れた入力で None。

## 作業 8：結合試験の書き換え（tests/integration/test_container_integration.py）

共通の fixture は今のものを使う（試験用リポジトリと worktree は I4 で使う）。test_i4 に追加した診断出力の print は残す。

1. **I1 マウント検査**：mounts = build_agy_mounts(job_home=<偽トークンの Job用HOME>, jobhome_root=...)。`cat /proc/self/mountinfo` を find_unexpected_mounts（allowed=`["/home/agy"]`）で検査し、0件。
2. **I2 コンテナから見えるもの**（I1 と同じマウント）：`sh -c` のスクリプトで次を出力し、期待と一致することを確認する。
   - `/home/agy/x` の作成 → `HOME_WRITE=OK`
   - `stat -c %a /home/agy/.gemini/antigravity-cli/antigravity-oauth-token` → `TOKEN_MODE=600`
   - `ls -A /workspace | wc -l` → `WORKSPACE_ENTRIES=0`
3. **I3 timeout と後始末**：変更なし（マウントは build_agy_mounts）。
4. **I4 AGY の本文生成と Controller による書込み**（実際のトークン、モデルは環境変数 NOVUI_AGY_MODEL）：
   - プロンプト（worktree の draft.md の内容を Controller 役のテストが埋め込む）：

     ```text
     あなたはシェルコマンドを実行できず、ファイルを読むこともできません。ツールは一切使わないでください。必要な情報はすべて以下にあります。
     設定に書かれていないこと（人物の名前、地名、出来事など）を、推測で決めてはいけません。必要なのに設定にない場合は、その箇所に【要確認：何が不明か】と書いてください。
     【これまでの本文】
     <draft.md の内容>
     【指示】
     これまでの本文の続きとして、「結合試験」という1行だけを書いてください。前置き・説明・コードブロックは不要です。本文だけを出力してください。
     ```

   - mounts = build_agy_mounts、agy_command で実行（timeout 300 秒）。
   - 期待：exit_code 0、check_cli_output が PASS、parse_agy_text が成功し text に「結合試験」を含み markers が空。
   - Controller 役として、元の draft.md の内容 + text を worktree の draft.md に書き込み、get_changes の結果が `chapters/ch-001/draft.md` だけで、check_allowed_paths（allowed=`["chapters/ch-001/draft.md"]`）が PASS。
   - Job用HOME が残っていない。トークンの漏洩がない（stdout、stderr、ログファイル、worktree 内の全ファイル。内容は出力しない）。
   - 使ったモデル名とイメージ ID を print で出力する。
5. **I5 設定不足の印**（I4 と同じ条件）：プロンプトは docs/phase1/results/1c-agy-context-probe.yaml の P1 と同じ文面。期待：parse_agy_text が成功し markers が1件以上、markers_to_requests（job_id="job-1"、chapter_id="ch-001"）が SchemaError を出さない。

## 作業の手順

1. 本指示と参照文書を読み、これから変更・作成・削除するファイル、関数、テスト、実行するコマンドの一覧を説明する。
2. **Human の許可を得る。**
3. 作業0〜7を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする（結合試験は skip される）。テストを通すために本指示の仕様を変えてはならない。
4. 作業8を行い、次で結合試験を実行する。失敗したら直さずに止めて報告する。
   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=gemini-3.8-flash-high .venv/bin/python -m pytest -s -m integration tests/integration
   ```
5. `ls -la ~/.local/share/novui-itest` と `podman ps -a --filter name=novui-itest --format '{{.Names}}'` で、一時領域とコンテナが残っていないことを確認する。
6. 許可範囲のファイル（未 commit の tools/、docs/phase1/results/、tests/integration/ を含む）だけを `git add` し、次のメッセージで commit する。
   ```text
   phase1: 1-C finish - text I/O for AGY, Claude JSON envelope, integration tests and probe results
   ```
7. `git push origin phase1/controller`
8. 報告して止まる。

## 報告の内容

1. 変更・作成・削除したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力（単体）
3. 結合試験の出力（`-s` の print を含む全文）
4. 手順5の結果
5. 本指示どおりにできなかった点、判断に迷った点（なければ「なし」）
6. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。Claude がレビューするまで、次の段階（1-D）に進まないでください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
