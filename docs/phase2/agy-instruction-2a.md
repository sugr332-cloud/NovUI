# AGYへの作業指示：Phase 2-A 作品の初期化・CLI・Claude の Job 実行器・plan Job

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストと結合試験を実行し、phase2/chapter-flow branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。
結合試験で想定と違う結果が出た場合は、コードや試験を変えて辻褄を合わせず、その時点で止めて報告してください。
**本指示と異なる実装をした場合は、理由とともに必ず報告の「指示どおりにできなかった点」に書いてください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase2/phase2-plan.md`（特に §2 の表）
* `docs/novel-system-spec-v0.5.2.md` の §6、§7.1、§10、§16、§19
* `prompts/claude/preamble.md`、`prompts/claude/plan.md`（Claude へのプロンプトの文面。読み込んで使う。変更しない）
* Phase 1 で作成した `src/novui/`、`schemas/`、`tests/`

## 書込みの許可範囲

```text
許可：
  src/novui/ の新規ファイル：works.py、workinit.py、chapters.py、claudejob.py、planjob.py、ids.py、cli.py、__main__.py
  src/novui/config.py（claude_model の追加のみ）
  src/novui/prompt.py（build_claude_prompt と load_prompt_template の追加のみ。既存の関数の動作は変えない）
  src/novui/schema.py（bundle_schema の追加のみ）
  src/novui/semantics.py（新しい schema の SEMANTIC_CHECKS 登録のみ）
  schemas/ の新規ファイル：project.schema.json、chapters_order.schema.json、prohibited.schema.json、works_registry.schema.json
  templates/novel/v1/**（新規）
  tests/**（追加と、既存テストの必要最小限の修正）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。終了時に削除する）
禁止：上記以外すべて（docs/、prompts/、spike/、tools/、container/、.git/、.gitignore、pyproject.toml、src/novui のその他のファイルを含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由）
.venv/bin/python -m novui（動作確認。一時領域の中でのみ）
claude --version
ls -la ~/.local/share/novui-itest
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase2/chapter-flow
```

Phase 1 の禁止事項はすべて引き続き有効です（`~/.gemini/` を自分で読まない、トークンの内容を出力しない、main への push・force push・reset・rebase・merge・checkout・tag をしない、git config を変更しない、など）。テストの git リポジトリは tmp_path か結合試験の一時領域の中に作ること。

## 作業 1：テンプレートと schema

### templates/novel/v1/（作品の初期構造。物語の内容は含めない。§19.4）

| ファイル | 内容 |
|---|---|
| project.yaml | `format_version: 1`、`work_key: ""`、`title: ""`（init_work が書き換える） |
| chapters-order.yaml | `[]` |
| world/README.md | `# 世界観` と、空行、`世界観の設定を Markdown で書く。` |
| characters/.keep | 空 |
| plot/timeline.yaml | `[]` |
| foreshadowing/registry.yaml | `[]` |
| rules/style.md | `# 文体` と、空行、`作品の文体の方針を書く。` |
| rules/prohibited.yaml | `phrases: []` と `repeated_ending_threshold: 3` |
| .novui/approvals/.keep | 空 |
| .gitignore | `output/` |

### 新しい schema（1-B の規則に従う。additionalProperties false、指定のないものは必須）

| schema | 内容 |
|---|---|
| project | object：format_version（const 1）、work_key（string、pattern `^[a-z0-9][a-z0-9-]{0,63}$`）、title（nonempty_string） |
| chapters_order | array of chapter_id、uniqueItems true |
| prohibited | object：phrases（array of nonempty_string）、repeated_ending_threshold（integer、minimum 2） |
| works_registry | object：works（array of object：work_key（上と同じ pattern）、path（nonempty_string）、format_version（integer、minimum 1）、state（enum `active`、`archived`）） |

semantics：works_registry は work_key が重複しないこと、path が絶対パスであること。他の3つは空のリストを返す関数を登録する。tests/fixtures/valid/ に4つの正例を加え、test_schema.py の対象（19件）に加える。

## 作業 2：config.py

* Settings に `claude_model: str` を追加する。環境変数 `NOVUI_CLAUDE_MODEL`、既定 `opus`。空文字は ValueError。

## 作業 3：src/novui/works.py（作品登録簿。§19.5）

```python
@dataclass(frozen=True)
class WorkInfo:
    work_key: str
    path: Path
    format_version: int
    state: str

def registry_path(settings: Settings) -> Path        # <data_dir>/registry.yaml
def list_works(settings: Settings) -> list[WorkInfo]   # ファイルがなければ空
def get_work(settings: Settings, work_key: str) -> WorkInfo   # なければ KeyError
def register_work(settings: Settings, work_key: str, path: Path, format_version: int) -> WorkInfo
```

* register_work：path は絶対パス。同じ work_key が既にあれば ValueError。state は `active`。schema と semantics で検証して write_yaml_atomic で保存する。

## 作業 4：src/novui/workinit.py

```python
TEMPLATE_DIR: Path    # Path(__file__).resolve().parents[2] / "templates" / "novel" / "v1"

def init_work(settings: Settings, path: Path, work_key: str, title: str) -> WorkInfo
```

1. path が既に存在すれば FileExistsError。path の親ディレクトリが存在しなければ FileNotFoundError。work_key の pattern、title が空でないことを確認する（ValueError）。get_work で同じ work_key が登録済みなら ValueError。
2. path を作り、`git init -b main`（run_git）。
3. TEMPLATE_DIR の中身をすべて path にコピーする。
4. project.yaml を `{"format_version": 1, "work_key": work_key, "title": title}` で write_yaml_atomic し、schema `project` で検証する。
5. `commit_all(path, f"init work {work_key}", [("NovUI-Edit", "human-content")], name=settings.git_name, email=settings.git_email)`（保護対象を含むため）。
6. register_work して WorkInfo を返す。

## 作業 5：src/novui/chapters.py

```python
class ChapterError(Exception)

def read_chapters_order(root: Path) -> list[str]
def read_chapter_meta(root: Path, chapter_id: str) -> dict | None
def ensure_main_ready(repo: Path) -> None
def add_chapter(settings: Settings, work: WorkInfo, chapter_id: str, title: str, outline_text: str) -> str
def transition_on_main(settings: Settings, work: WorkInfo, chapter_id: str, event: ChapterEvent, *,
                       subject: str, trailers: Sequence[tuple[str, str]] = (),
                       extra_files: Mapping[str, bytes] | None = None, last_job: str | None = None) -> str
```

* `read_chapters_order`：root/chapters-order.yaml を読み、schema `chapters_order` で検証して返す。
* `read_chapter_meta`：root/chapters/<id>/chapter.yaml がなければ None、あれば schema `chapter` と semantics で検証して返す。
* `ensure_main_ready`：repo の現在の branch が main で、`status --porcelain --untracked-files=all` が空であること。違えば ChapterError。
* `add_chapter`：
  1. ensure_main_ready。chapter_id の pattern、title と outline_text が空でないこと（ValueError）。chapter_id が既に chapters-order.yaml にある、または chapters/<id>/ が存在すれば ChapterError。
  2. `next_chapter_state(None, ChapterEvent.OUTLINE_CREATED)` で状態を求める。
  3. chapters/<id>/outline.md（outline_text。末尾に改行がなければ付ける）、chapters/<id>/chapter.yaml（`{id, title, state, review_required: false, review_reasons: [], validation_skipped: false, last_job: null}`。schema で検証）を書き、chapters-order.yaml の末尾に chapter_id を加える。
  4. `commit_all(repo, f"add chapter {chapter_id}", [("NovUI-Edit", "human-content")], ...)` の commit を返す。
* `transition_on_main`：
  1. ensure_main_ready。`can_accept_write_job(repo, chapter_id)` が False なら ChapterError（作業ブランチがある章の状態を main で変えない）。
  2. read_chapter_meta（None なら ChapterError）、`next_chapter_state(現状態, event, review_required=meta["review_required"])` で次状態（InvalidTransition はそのまま伝える）。
  3. state を更新し、last_job が与えられれば更新する。schema と semantics で検証して chapter.yaml を書く。extra_files の各パス（is_safe_relpath、repo の外に出ない）にバイト列を書く。
  4. `commit_all(repo, subject, trailers, ...)` の commit を返す。

## 作業 6：src/novui/schema.py に bundle_schema を追加

Claude の `--json-schema` には1つの文字列で schema を渡すため、`$ref` を解決した自己完結の schema を作る。

```python
def bundle_schema(schema_name: str, *, schema_dir: Path = SCHEMA_DIR) -> dict
```

* 対象の schema をコピーし、`$id` を除く（`$schema` は残す）。
* 対象の中、および取り込んだ schema の中の `$ref` を、すべて同じ文書の中の参照（`#/$defs/...`）に書き換え、参照先を対象の `$defs` に取り込む。
  - `<stem>.schema.json#/$defs/<name>` → `#/$defs/<stem>__<name>`（取り込む定義の中の `$ref` も同じ規則で再帰的に書き換える）
  - `<stem>.schema.json`（ファイル全体）→ `#/$defs/<stem>`（取り込むときに `$id`・`$schema` を除き、その中の `$defs` も `<stem>__<name>` として最上位の `$defs` に移す）
  - 取り込んだ schema の中の `#/$defs/<name>` → `#/$defs/<stem>__<name>`
  - 対象 schema 自身の `#/$defs/<name>` は変えない
  - 上記以外の形の `$ref`（`#/properties/...` など）は ValueError
* 存在しない schema_name は ValueError。

テスト：19の schema すべてについて、bundle_schema の結果が `Draft202012Validator.check_schema` を通る、`#/` で始まらない `$ref` を含まない、tests/fixtures/valid/ の正例が bundle 後の schema（registry なし）で検証エラー0件、1-B の負例のうち各 schema 1つ以上がエラーになる。

## 作業 7：src/novui/prompt.py に追加

```python
PROMPTS_DIR: Path   # Path(__file__).resolve().parents[2] / "prompts"

def load_prompt_template(name: str, **values: str) -> str
def build_claude_prompt(root: Path, context_paths: Sequence[str], task_text: str) -> BuiltPrompt
```

* `load_prompt_template`：`PROMPTS_DIR/claude/<name>.md` を読み、`{{key}}` を values で置き換える。置き換えた後に `{{` が残れば ValueError。name は `^[a-z0-9_]+$`。
* `build_claude_prompt`：build_agy_prompt と同じ構成・検査で、次の2点だけが異なる。先頭の文は PREAMBLE ではなく `load_prompt_template("preamble")` の内容。最後は `【指示】` の行と instruction ではなく、task_text をそのまま置く（task_text は `【作業】` で始まる文面で、見出しを含んでいるため）。共通部分は内部関数にまとめてよいが、build_agy_prompt の出力は変えないこと（既存テストが通ること）。

## 作業 8：src/novui/ids.py

```python
def next_job_id(settings: Settings, work_key: str) -> str
```

* jobs_dir の `job-<数字>.yaml` の最大の数字 + 1（なければ 1）で `job-<数字>` を返す。

## 作業 9：src/novui/claudejob.py（Claude の Job 実行器）

```python
ClaudeRunner = Callable[..., ProcResult]   # claude_cli.run_claude と同じキーワード引数

class ClaudeTimedOut(Exception)

@dataclass(frozen=True)
class ClaudeJobRequest:
    work_key: str
    job_id: str
    job_type: str
    chapter_id: str | None
    root: Path                      # 資料を読むディレクトリ（作品の main worktree、または作業ブランチの worktree）
    context_paths: tuple[str, ...]
    task_text: str
    expected_type: str              # claude_output.CLAUDE_OUTPUT_TYPES のいずれか
    model: str

def claude_version() -> str
def run_claude_job(settings: Settings, req: ClaudeJobRequest, *, claude_runner: ClaudeRunner = run_claude,
                   max_retries: int = 2) -> tuple[dict, dict | None]
```

* `claude_version`：`claude --version` の出力の1行目（前後の空白を除く）。失敗したら `unknown`。プロセス内で1回だけ実行して結果を保持する。
* `run_claude_job`：Job 記録は状態が変わるたびに jobrunner.jobs_dir に保存する。
  1. `new_job_record(job_id, job_type, chapter_id=...)` を保存（QUEUED）。
  2. `build_claude_prompt(root, context_paths, task_text)`。ValueError なら記録を QUEUED のまま残して送出する。context を記録する。
  3. LOCK_ACQUIRED → RUNNING（Claude の Job は実行 lock を使わない。§18.1）。
  4. schema の文字列：`json.dumps(bundle_schema(expected_type), ensure_ascii=False, separators=(",", ":"))`。
  5. 作業ディレクトリ `<data_dir>/claude-cwd/<job_id>` を新規に作る（既にあれば FileExistsError）。関数を抜けるとき（例外を含む）に必ず削除する。log_dir は `<jobs_dir>/<job_id>-logs`。
  6. `run_with_output_retry(call, expected_type, max_retries=max_retries)`。call(attempt) は `claude_runner(prompt=..., cwd=..., model=..., json_schema=..., timeout_seconds=settings.timeouts["claude"], log_dir=..., name=f"{job_id}-a{attempt}")` を呼び、最後の ProcResult を保持し、timed_out なら ClaudeTimedOut を送出し、そうでなければ stdout を返す。
  7. 記録：cli = `{name: "claude", version: claude_version(), model: req.model, actual_model: <最後の stdout の parse_claude_meta の model_usage_keys の先頭、なければ None>}`、container = null、run = 最後の ProcResult から（timeout_seconds は settings.timeouts["claude"]）、attempt = 試行回数。
  8. 結果による遷移：
     - ClaudeTimedOut：TIMED_OUT → FAILED。`(record, None)` を返す。
     - OutputRetryExhausted：CLI_EXITED → CHECKING、checks に `{"name": "claude_output", "status": "FAIL", "details": ["attempt <n>: <kind>: <最初のエラー>", ...]}`、CHECK_FAILED → FAILED。`(record, None)` を返す。
     - 成功：CLI_EXITED → CHECKING、checks に claude_output の PASS を加え、**CHECKING のまま** `(record, data)` を返す（呼び出し側が追加の検査をして遷移させる）。
  9. その他の例外は、RUNNING なら CLI_EXITED → CHECK_FAILED で FAILED にして保存し、再送出する。

## 作業 10：src/novui/planjob.py

```python
def collect_plan_context(repo: Path, chapter_id: str) -> list[str]
def run_plan_job(settings: Settings, work: WorkInfo, chapter_id: str, *, job_id: str | None = None,
                 claude_runner: ClaudeRunner = run_claude) -> dict
def approve_plan(settings: Settings, work: WorkInfo, chapter_id: str) -> str
def reject_plan(settings: Settings, work: WorkInfo, chapter_id: str) -> str
```

* `collect_plan_context`：存在するファイルだけを、次の順で並べる。project.yaml、rules/style.md、rules/prohibited.yaml、chapters-order.yaml、world/ 以下の全ファイル（昇順）、characters/ の `*.yaml`（昇順）、foreshadowing/registry.yaml、plot/timeline.yaml、chapters-order.yaml で対象の章の直前にある章の summary.yaml（あれば）、最後に chapters/<id>/outline.md。
* `run_plan_job`：
  1. ensure_main_ready、can_accept_write_job（False なら ChapterError）。章の状態が OUTLINED でなければ ChapterError。job_id が None なら next_job_id。
  2. run_claude_job（job_type `plan`、root=repo、context_paths=collect_plan_context、task_text=`load_prompt_template("plan", chapter_id=chapter_id)`、expected_type `plan`、model=settings.claude_model）。data が None なら記録を返す。
  3. 追加の検査（name `plan_refs`。違反を detail に列挙し、1つでもあれば FAIL）：
     - data の chapter_id が対象と一致
     - scenes の id が S1 から連番
     - scenes の characters の各 ID について characters/<ID>.yaml が存在
     - scenes の foreshadowing の各 ID が foreshadowing/registry.yaml にある
     - context.settings の各パスが context_paths に含まれる
     - context.past_summaries・past_drafts の各章が chapters-order.yaml で対象の章より前にある
  4. FAIL なら CHECK_FAILED → FAILED で返す（main は変えない）。
  5. PASS なら `transition_on_main(..., ChapterEvent.PLAN_WRITTEN, subject=f"plan {chapter_id} by {job_id}", trailers=[("NovUI-Job", job_id)], extra_files={f"chapters/{chapter_id}/plan.yaml": dumps_yaml(data).encode("utf-8")}, last_job=job_id)`、続けて CHECK_PASSED（needs_validation=False）→ COMPLETED。記録を返す。
* `approve_plan`・`reject_plan`：transition_on_main で PLAN_APPROVED・PLAN_REJECTED（subject `approve plan <id>`・`reject plan <id>`、trailer なし）。

## 作業 11：CLI（src/novui/cli.py、src/novui/__main__.py）

`python -m novui <サブコマンド>`。argparse を使う。設定は load_settings()。

| サブコマンド | 引数 | 動作 |
|---|---|---|
| init-work | `<path> --key <work_key> --title <title>` | init_work |
| add-chapter | `--work <key> --id <chapter_id> --title <title> --outline-file <path>` | outline を UTF-8 で読み add_chapter |
| plan | `--work <key> --chapter <id>` | run_plan_job。結果の state と checks を表示 |
| approve-plan / reject-plan | `--work <key> --chapter <id>` | approve_plan / reject_plan |
| show | `--work <key> [--chapter <id>]` | 章の一覧（id、title、state。main の chapters-order の順）。--chapter があればその章の chapter.yaml と、その章の Job 記録（job_id、job_type、state、最後の history）を表示 |
| models fetch | なし | fetch_agy_models と save_catalog。新しいモデルがあれば表示 |
| models select | `--role <role> --model <id> [--work <key>]` | select_model |

* 正常終了は終了コード 0。例外は stderr にメッセージを出して終了コード 1。Job が FAILED で終わった場合は checks を表示して終了コード 2、WAITING_HUMAN は 3。
* トークンの内容を表示しないこと。

## 作業 12：テスト

### 単体テスト（claude・podman は使わない）

claude_runner には、ProcResult を返す偽物を渡す。偽物は、正しい封筒（1-C の test_claude_output.py の形）に plan の正例を入れた stdout を返す。

* test_works.py、test_workinit.py：登録・重複・取得。init_work でテンプレートのファイルがすべて作られ、project.yaml が正しく、commit の trailer が `NovUI-Edit: human-content`、registry に登録される。既存パス・不正な work_key で失敗。
* test_chapters.py：add_chapter（ファイル・chapters-order・commit・trailer）、重複・汚れた main で ChapterError。transition_on_main（PLAN_WRITTEN の extra_files、遷移表にない遷移で InvalidTransition、作業ブランチがある章で ChapterError）。
* test_schema.py：bundle_schema（作業6のテスト）と新しい4つの schema。
* test_prompt.py：load_prompt_template（置き換え、残った `{{` で ValueError、不正な name）、build_claude_prompt の構成（先頭が preamble.md、最後が task_text）。build_agy_prompt の既存テストが変わらず通る。
* test_ids.py：next_job_id。
* test_claudejob.py：成功（CHECKING で返る、actual_model が記録される、作業ディレクトリが削除される）、1回目に壊れた出力・2回目に成功（attempt 2）、3回とも失敗（FAILED、details 3件）、timeout（FAILED、TIMED_OUT）、偽物が RuntimeError（FAILED、再送出、作業ディレクトリが削除される）。偽物が受け取った json_schema が bundle_schema(expected_type) の JSON と一致すること、cwd が空のディレクトリであることも確認する。
* test_planjob.py：collect_plan_context の順序。成功（main に plan.yaml と chapter.yaml PLANNED の commit、trailer NovUI-Job、記録 COMPLETED）。plan_refs の各違反（存在しない C999、存在しない F999、S1 からの連番でない、context.settings に資料にないパス、未来の章を past_summaries に指定）で FAILED、main が変わらない。OUTLINED 以外で ChapterError。approve・reject の遷移。
* test_cli.py：各サブコマンドを関数として呼び（subprocess ではなく `cli.main(argv)`）、終了コードと出力を確認する（plan は偽の claude_runner を差し込めるようにしてよい。その場合の方法を報告に書く）。

### 結合試験（tests/integration/test_planjob_integration.py、`@pytest.mark.integration`）

実際の Claude を使う。一時領域 `~/.local/share/novui-itest/<ランダム16進8桁>/` に data_dir と作品を作り、終了時に削除する。

1. **K1 plan Job の通し**：
   - init_work（work_key `itest-<ランダム>`、title `結合試験`）。
   - 作品に次を書き、`commit_all(..., [("NovUI-Edit", "human-content")])` で commit する：
     - world/setting.md：`# 世界観` 空行 `舞台は架空の港町ミナト。王家の紋章を持つ者は港の門を自由に通れる。`
     - characters/C001.yaml：`{id: C001, name: カイ, speech: {first_person: 俺}}`
     - foreshadowing/registry.yaml：F001（name `王家の紋章`、status planned、importance major、introduced・hints・developments は空、planned_resolution null、resolved null、notes ""）
   - add_chapter（ch-001、title `港へ`、outline `カイが港町ミナトに着く。門番がカイの持つ紋章に反応する（F001 の初出）。カイは理由が分からないまま門を通る。`）
   - run_plan_job（実際の Claude。モデルは settings.claude_model）。
   - 期待：COMPLETED、main の chapters/ch-001/plan.yaml が schema に適合、chapter.yaml が PLANNED、scenes に C001 と F001 が含まれる、actual_model が null でない。plan の scenes の数、actual_model、Job 記録の run.elapsed_seconds を print する。
   - approve_plan の後、chapter.yaml が PLAN_APPROVED。

## 作業の手順

1. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る。**
3. 作業1〜12（結合試験を除く）を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする。テストを通すために本指示の仕様を変えてはならない。
4. 結合試験を実行する（Phase 1 の分も含む）。失敗したら直さずに止めて報告する。
   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=gemini-3.8-flash-high .venv/bin/python -m pytest -s -m integration tests/integration
   ```
5. `ls -la ~/.local/share/novui-itest` と `podman ps -a --filter name=novui-itest --format '{{.Names}}'` で残存がないことを確認する。
6. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。
   ```text
   phase2: 2-A work init, CLI, Claude job runner, plan job
   ```
7. `git push origin phase2/chapter-flow`。「fetch first」等で拒否された場合に限り、`git pull --rebase origin phase2/chapter-flow` を1回だけ実行してから再度 push してよい（競合が出たら解決せずに止めて報告する）。
8. 報告して止まる。

## 報告の内容

1. 変更・作成したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力（単体）
3. 結合試験の出力の全文（print を含む）
4. 残存確認の結果
5. 指示どおりにできなかった点、指示と異なる実装をした点、判断に迷った点（なければ「なし」）
6. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
