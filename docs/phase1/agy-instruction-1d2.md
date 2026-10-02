# AGYへの作業指示：Phase 1-D2 Job の一連の実行・モデル一覧・受入確認

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストと結合試験を実行し、phase1/controller branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。
結合試験で想定と違う結果が出た場合は、コードや試験を変えて辻褄を合わせず、その時点で止めて報告してください。

本段階は Phase 1 の最後です。Phase 1 の受入条件（spec v0.5.2 §24）を満たすことを、単体テストと結合試験で示します。

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/novel-system-spec-v0.5.2.md` の §5、§7.2、§8.3、§16、§17.4、§18、§21、§24 の Phase 1
* `docs/phase1/phase1-plan.md`（特に §3 の 1-D、§4 の 13）
* 1-A〜1-D1 で作成した `src/novui/`、`schemas/`、`tests/`

## 書込みの許可範囲

```text
許可：
  src/novui/prompt.py、src/novui/jobrunner.py、src/novui/models.py、src/novui/recovery.py（新規）
  src/novui/container.py（image_label の追加のみ）
  src/novui/semantics.py（本指示の追加のみ）
  schemas/model_catalog.schema.json、schemas/controller_settings.schema.json（新規）
  container/Containerfile、container/build-agy-image.sh（新規。作成のみで実行しない）
  tests/**（本指示の追加）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。試験の終了時に削除する）
禁止：上記以外すべて（docs/、spike/、tools/、.git/、.gitignore、pyproject.toml、src/novui のその他のファイルを含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションと環境変数の指定は自由）
ls -la ~/.local/share/novui-itest
podman ps -a --filter name=novui-itest --format '{{.Names}}'
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase1/controller
```

`container/build-agy-image.sh` は作成するだけで、実行しないこと。1-A〜1-D1 の禁止事項はすべて引き続き有効です。

## 作業 1：src/novui/container.py への追加

```python
def image_label(image: str, key: str) -> str | None
```

* `podman image inspect --format '{{json .Labels}}' <image>` の出力を json として読み、key の値を返す（なければ None）。podman の失敗は ContainerError。

## 作業 2：src/novui/prompt.py（新規）

AGY に渡すプロンプトを組み立てる（spec §4.2、§5.1、§16）。

```python
PREAMBLE: str     # 下記の固定文

@dataclass(frozen=True)
class ContextEntry:
    path: str        # 作品リポジトリ内の相対パス
    sha256: str      # "sha256:<16進>"

@dataclass(frozen=True)
class BuiltPrompt:
    text: str
    context: tuple[ContextEntry, ...]
    size_bytes: int

def build_agy_prompt(worktree: Path, context_paths: Sequence[str], instruction: str) -> BuiltPrompt
```

* PREAMBLE（この文面をそのまま使う）：

  ```text
  あなたはシェルコマンドを実行できず、ファイルを読むこともできません。ツールは一切使わないでください。必要な情報はすべて以下にあります。
  設定に書かれていないこと（人物の名前、地名、出来事など）を、推測で決めてはいけません。必要なのに設定にない場合は、その箇所に【要確認：何が不明か】と書いてください。
  出力は本文だけにしてください。前置き・説明・コードブロックは不要です。
  ```

* text の構成：PREAMBLE、空行、context_paths の順に各ファイルを `【ファイル：<path>】` の行と内容（UTF-8）、空行、`【指示】` の行、instruction。
* context_paths の各パスは `is_safe_relpath` で、`ensure_within(worktree, worktree / path)` を通り、通常のファイルであること（違反は ValueError）。ファイルを UTF-8 で復号できなければ ValueError。
* context には各ファイルの path と sha256（読み込んだバイト列から計算）を、context_paths の順で入れる（§16.2）。
* size_bytes は text の UTF-8 のバイト数。上限の確認は agy_command（PromptTooLarge）に任せる。

## 作業 3：src/novui/jobrunner.py（新規）

draft Job の一連の実行（spec §9 の Controller の部分、§8.3、§7.2）。Phase 1 では Validator（Claude）は呼ばない。

```python
class JobRejected(Exception)      # 章 lock により受け付けない（§18.1）
class RunLockBusy(Exception)      # 実行 lock を取得できない

ContainerRunner = Callable[..., ProcResult]   # container.run_container と同じキーワード引数を受け取る

@dataclass(frozen=True)
class DraftJobRequest:
    work_key: str
    repo: Path                     # 作品リポジトリ（main を checkout した main worktree）
    chapter_id: str
    job_id: str
    model: str
    context_paths: tuple[str, ...]
    instruction: str
    target_chars: tuple[int, int] | None = None
    needs_validation: bool = False

def jobs_dir(settings: Settings, work_key: str) -> Path      # <data_dir>/works/<work_key>/jobs
def run_draft_job(settings: Settings, req: DraftJobRequest, run_lock: RunLock,
                  *, container_runner: ContainerRunner = run_container) -> dict
```

`run_draft_job` は次の順で処理し、最終的な Job 記録（dict）を返す。Job 記録は**状態が変わるたびに** `save_job_record(jobs_dir(...), record)` で保存する。

1. `can_accept_write_job(repo, chapter_id)` が False なら JobRejected（Job 記録は作らない）。
2. `new_job_record(job_id, "draft", chapter_id=...)` を作り保存する（QUEUED）。
3. `run_lock.acquire(work_key, job_id)` が False なら RunLockBusy（記録は QUEUED のまま残す）。
4. 以降、関数を抜けるときは（例外を含め）必ず `run_lock.release` する。
5. `create_job_worktree(repo, settings.worktree_root, work_key, chapter_id, job_id)`。branch、worktree（パスの文字列）、base_commit を記録する。
6. `build_agy_prompt(worktree, context_paths, instruction)` で組み立て、context を記録する。続けて `agy_command(model=..., prompt=...)` を作る。PromptTooLarge の場合は、NEEDS_HUMAN_INPUT（reason は `prompt_too_large: <size> > <limit>`）で WAITING_HUMAN にし、worktree と branch を削除して（`remove_job_worktree(..., delete_branch=True)`）記録を返す。
7. LOCK_ACQUIRED → RUNNING。
8. 実行前の状態を取る：`hash_targets(git_protection_targets(worktree))` と `get_ignored(worktree)`。
9. `job_home(settings.jobhome_root, job_id, settings.agy_token_path)` の中で、`container_runner(name=f"novui-{work_key}-{job_id}", image=settings.agy_image, mounts=build_agy_mounts(...), command=<6のコマンド>, timeout_seconds=settings.timeouts["agy_draft"], log_dir=<jobs_dir>/<job_id>-logs)` を実行する。コンテナ名は小文字・数字・ハイフンだけになるため、work_key と job_id の規則で満たされる。
10. 記録：cli = `{name: "agy", version: image_label(image, "org.novui.agy.version") または "unknown", model: model, actual_model: None}`、container = `{image_tag: settings.agy_image, image_id: image_id(image)}`、run = ProcResult から。
11. ProcResult.timed_out なら TIMED_OUT → FAILED で返す。
12. CLI_EXITED → CHECKING。直ちに（git コマンドより前に）`hash_targets(git_protection_targets(worktree))` を取り直す（phase1-plan §4 の 8）。
13. 検査（すべて CheckResult として record["checks"] に順に追加する。dataclass は dict に変換する）：
    1. `check_cli_output(exit_code, stdout)`
    2. `compare_hash_records(実行前, 実行後)`
    3. `parse_agy_text(stdout)` の成否（name `agy_output`、失敗は FAIL で detail に kind）
    4. 1〜3 がすべて PASS の場合だけ、Controller が書き込む：
       - `chapters/<chapter_id>/draft.md` に text（UTF-8）を書く（draft Job は全体を置き換える）。
       - markers があれば、`markers_to_requests` の結果を `chapters/<chapter_id>/requests.yaml` に追記する。既存の内容（なければ空）の末尾に、`dumps_yaml(新しい要素のリスト)` を連結する（既存の内容が改行で終わらない場合は間に改行を1つ入れる）。書いた後の requests.yaml を load_yaml で読み、schema `requests` と semantics で検証し、`check_append_only(前, 後)` を追加する。
    5. `check_allowed_paths(get_changes(worktree), {draft.md, requests.yaml のパス})`
    6. `check_ignored_unchanged(実行前, get_ignored(worktree))`
    7. target_chars があれば `check_char_range(text, min, max)`（WARNING は FAIL ではない）
14. FAIL が1つでもあれば CHECK_FAILED → FAILED。worktree と branch は調査のため残す（§7.2）。
15. FAIL がなければ `commit_all(worktree, f"draft {chapter_id} by {job_id}", [("NovUI-Job", job_id)], name=settings.git_name, email=settings.git_email)`。
16. markers が1件以上あれば NEEDS_HUMAN_INPUT（reason `undefined_settings: <件数>`）→ WAITING_HUMAN。なければ CHECK_PASSED（needs_validation）→ VALIDATING または COMPLETED。
17. 予期しない例外（ContainerError、GitError、OSError など）が起きた場合は、その時点の状態から遷移できるなら FAILED にして保存し（RUNNING なら TIMED_OUT ではなく CLI_EXITED → CHECK_FAILED の順で FAILED にする。QUEUED なら記録を QUEUED のまま残す）、例外を再送出する。

## 作業 4：src/novui/recovery.py（新規）

Controller 起動時の処理（§7.2、§21、phase1-plan §4 の 1）。

```python
def recover_on_startup(settings: Settings) -> list[str]
```

* `<data_dir>/works/*/jobs/*.yaml` の Job 記録をすべて読み、state が QUEUED・RUNNING・CHECKING・VALIDATING のものに CONTROLLER_RESTARTED を適用して保存する。WAITING_HUMAN と終了状態は変えない。STOPPED にした job_id のリストを返す。
* `cleanup_stale_job_homes(settings.jobhome_root)` を呼ぶ。

## 作業 5：src/novui/models.py（新規）と schema

phase1-plan §4 の 13。

### schemas/model_catalog.schema.json（`<data_dir>/models/agy.yaml`）

object、すべて必須：fetched_at（datetime）、agy_image（nonempty_string）、image_id（nonempty_string）、agy_version（nonempty_string）、models（array of object：id（nonempty_string）、label（string））。

### schemas/controller_settings.schema.json（`<data_dir>/settings.yaml`）

object、すべて必須：models（object：キーは `draft`・`range_edit`・`chapter_rewrite` のうち任意、値は nonempty_string）、works（object：キーは work_key の pattern `^[a-z0-9][a-z0-9-]{0,63}$`、値は object：models（上と同じ形））。

### semantics.py

SEMANTIC_CHECKS に `model_catalog`（models の id が重複しない）と `controller_settings`（空のリストを返す）を追加する。

### models.py

```python
ROLES: frozenset[str]   # "draft", "range_edit", "chapter_rewrite"

class ModelUnavailable(Exception)   # 属性 role、model_id

def parse_agy_models(text: str) -> list[dict]
def fetch_agy_models(settings: Settings, *, container_runner: ContainerRunner = run_container) -> dict
def save_catalog(settings: Settings, catalog: dict) -> Path
def load_catalog(settings: Settings) -> dict | None
def new_model_ids(old: dict | None, new: dict) -> list[str]
def load_controller_settings(settings: Settings) -> dict
def select_model(settings: Settings, role: str, model_id: str, *, work_key: str | None = None) -> None
def resolve_model(settings: Settings, role: str, *, work_key: str | None = None) -> str
```

* `parse_agy_models`：空行を除く各行を、最初の空白の並びで2つに分け、`{"id": 前, "label": 後（前後の空白を除く。なければ ""）}` にする。id は `^[A-Za-z0-9][A-Za-z0-9._-]*$` に一致する行だけを採る（見出しや警告の行を除くため）。
* `fetch_agy_models`：job_home（job_id は `job-` + 現在の UNIX 時刻の秒（整数）。job_home の job_id の pattern に合わせるため）の中で、`agy models` をコンテナで実行する（コンテナ名 `novui-models-<秒>`、timeout 120 秒、log_dir は `<data_dir>/models/logs`）。exit_code が 0 でない、または1件も得られなければ RuntimeError。結果を model_catalog の形の dict にして返す（agy_version は image_label、なければ "unknown"）。
* `save_catalog`：schema と semantics で検証して `<data_dir>/models/agy.yaml` に write_yaml_atomic。`load_catalog`：なければ None、あれば検証して返す。
* `new_model_ids`：new にあって old にない id を、new の順で返す（old が None なら new の全件）。UI で「新しいモデルがあります」と表示するために使う。自動では切り替えない。
* `load_controller_settings`：`<data_dir>/settings.yaml` がなければ `{"models": {}, "works": {}}`、あれば検証して返す。
* `select_model`：role が ROLES にない場合 ValueError。最新のカタログ（load_catalog）がない、または model_id がカタログにない場合 ModelUnavailable。work_key があれば works[work_key].models[role] に、なければ models[role] に保存する（write_yaml_atomic、保存前に検証）。
* `resolve_model`：work_key の設定があればそれ、なければ全体の設定を使う。設定がない場合、または最新のカタログにその id がない場合は ModelUnavailable（別のモデルに黙って切り替えない）。

## 作業 6：container/Containerfile と container/build-agy-image.sh（作成のみ。実行しない）

本番用イメージ `localhost/novui-agy:<agy のバージョン>` を Human が作るための手順（spec §5.2）。

* Containerfile：spike/phase0/Containerfile と同じ内容を基本とし、ベースイメージを `registry.fedoraproject.org/fedora:44` に固定し、`LABEL org.novui.agy.version` を引数から設定する。
* build-agy-image.sh：
  - `set -euo pipefail`。ホストの agy の場所は `command -v agy`。`agy --version` の出力をバージョンとする（`^[0-9]+\.[0-9]+\.[0-9]+$` に合わなければ中止）。
  - 一時ディレクトリ（`mktemp -d`）に agy と Containerfile をコピーし、`podman build --build-arg AGY_VERSION=<ver> -t localhost/novui-agy:<ver> -f Containerfile <一時ディレクトリ>` を実行する。終了時（trap）に一時ディレクトリを削除する。
  - 同じタグのイメージが既にある場合は中止する（上書きしない）。
  - ~/.gemini 配下のものを一切コピーしない。
  - 実行方法と、ビルド後に `NOVUI_AGY_IMAGE=localhost/novui-agy:<ver>` を設定することを、ファイル先頭のコメントに書く。

## 作業 7：単体テスト（podman・agy は使わない）

container_runner に偽物を渡して、run_draft_job の各経路を確かめる。偽物は `ProcResult` を返し、必要に応じて worktree に違反を書き込む（AGY が書き込める状態になった場合や、Controller の誤りを想定した検出の試験）。試験用の作品リポジトリと settings（data_dir は tmp_path）、偽のトークンファイルを fixture で用意する。image_label と image_id は monkeypatch で差し替える。

**Phase 1 の受入条件（§24）に対応する必須のテスト：**

| テスト | 偽物の動き | 期待 |
|---|---|---|
| 正常 | stdout に本文、何も書かない | COMPLETED。main は変わらず、作業ブランチに draft.md の commit（NovUI-Job の trailer）。checks がすべて PASS |
| 設定不足 | stdout に `【要確認：名前】` を含む本文 | WAITING_HUMAN。requests.yaml に1件、schema に適合。作業ブランチに commit |
| 空出力 | exit 0、stdout 空 | FAILED（cli_output が FAIL）。draft.md を書いていない |
| 許可範囲外の変更 | worktree に `chapters/ch-002/draft.md` を作る | FAILED（allowed_paths が FAIL）。commit していない |
| 保護対象の変更 | worktree の `world/w.md` を書き換える | FAILED（allowed_paths が FAIL）。commit していない |
| .git の改変 | worktree の `.git` 参照ファイルに追記する | FAILED（hash_compare が FAIL） |
| hooks の改変 | Git common directory の hooks にファイルを作る | FAILED（hash_compare が FAIL） |
| ignored の追加 | .gitignore で無視されたパスにファイルを作る | FAILED（ignored_files が FAIL） |
| timeout | timed_out=True の ProcResult | FAILED（history の最後の event が TIMED_OUT） |
| コード片 | stdout が ```` ``` ```` で始まる | FAILED（agy_output が FAIL） |
| プロンプト超過 | instruction を 120,001 バイトにする | WAITING_HUMAN、worktree と branch が削除されている |
| 章 lock | 同じ章で先に作業ブランチを作っておく | JobRejected、Job 記録が作られない |
| 実行 lock | run_lock を別の job が持っている | RunLockBusy、記録は QUEUED |
| 例外 | 偽物が ContainerError を送出 | 記録が FAILED、例外が伝わる、run_lock が解放されている |

上記すべてで、終了後に run_lock が解放されていること、Job 用HOME が残っていないこと、保存された Job 記録が load_job_record で読めることも確認する。

**その他：**

* test_prompt.py：構成と順序、context の sha256、不正なパス（`../x`、存在しない、ディレクトリ、worktree 外へのシンボリックリンク）、UTF-8 でないファイルで ValueError。
* test_recovery.py：各状態の記録を置き、QUEUED・RUNNING・CHECKING・VALIDATING だけが STOPPED になり、WAITING_HUMAN と終了状態は変わらない。古い Job用HOME が削除される。
* test_models.py：parse_agy_models（1-C で報告された `agy models` の出力をそのまま使う。見出し・空行・警告行を含む例も）。fetch_agy_models（偽の container_runner）。save・load、new_model_ids、select_model・resolve_model（全体と作品ごと、カタログにない id で ModelUnavailable、カタログから消えたら resolve_model が ModelUnavailable）。
* test_container.py：image_label（subprocess を monkeypatch で差し替える）。
* test_schema.py：model_catalog・controller_settings の正例と負例（fixture を追加し、対象一覧を15件にする）。

## 作業 8：結合試験（tests/integration/test_jobrunner_integration.py。`@pytest.mark.integration`）

1-C の結合試験と同じ一時領域・試験用リポジトリ（ただし data_dir・worktree_root は一時領域の中）で、実際の AGY（イメージは既定の `localhost/novui-spike:agy-1.2.14`、モデルは環境変数 NOVUI_AGY_MODEL）を使う。

work_key は `itest-` で始まる名前（例 `itest-<ランダム16進8桁>`）にする（コンテナ名が `novui-itest-...` になり、残存確認の filter に掛かるようにするため）。

1. **J1 draft Job の通し**：context_paths に `world/w.md` と `chapters/ch-001/draft.md`、instruction に `これまでの本文の続きとして、「結合試験」という1行だけを書いてください。` を与えて run_draft_job を実行する。期待：COMPLETED、作業ブランチの draft.md に「結合試験」を含む、checks に FAIL がない、Job用HOME が残っていない、コンテナが残っていない、トークンの内容が Job 記録・ログ・worktree に含まれない（内容は出力しない）。
2. **J2 モデル一覧の取得**：fetch_agy_models を実行し、`gemini-3.8-flash-high` を含むこと。save_catalog の後、select_model(settings, "draft", "gemini-3.8-flash-high") と resolve_model(settings, "draft") が一致すること。

## 作業の手順

1. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る。**
3. 作業1〜7を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする（結合試験は skip される）。テストを通すために本指示の仕様を変えてはならない。
4. 作業8を行い、次で結合試験（1-C の分を含む全件）を実行する。失敗したら直さずに止めて報告する。
   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=gemini-3.8-flash-high .venv/bin/python -m pytest -s -m integration tests/integration
   ```
5. `ls -la ~/.local/share/novui-itest` と `podman ps -a --filter name=novui-itest --format '{{.Names}}'` で残存がないことを確認する。
6. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。
   ```text
   phase1: 1-D2 draft job runner, recovery, model catalog, image build script, acceptance tests
   ```
7. `git push origin phase1/controller`。「fetch first」等で拒否された場合に限り、`git pull --rebase origin phase1/controller` を1回だけ実行してから再度 push してよい（競合が出たら解決せずに止めて報告する）。
8. 報告して止まる。

## 報告の内容

1. 変更・作成したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力（単体）
3. 結合試験の出力（`-s` の print を含む全文）
4. 手順5の結果
5. 本指示どおりにできなかった点、判断に迷った点（なければ「なし」）
6. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
