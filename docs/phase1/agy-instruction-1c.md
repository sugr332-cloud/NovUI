# AGYへの作業指示：Phase 1-C Job 実行器

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストと試験を実行し、phase1/controller branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。

本段階では、初めて podman・agy・claude を Controller のコードから実行します。**結合試験（作業 3〜5）で想定と違う結果が出た場合は、コードや試験を変えて辻褄を合わせず、その時点で止めて、結果をそのまま報告してください。**

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase1/phase1-plan.md`（特に §3 の 1-C、§4 の 10〜12）
* `docs/novel-system-spec-v0.5.md` の §5、§15.2、§16.2、§23
* `docs/spike/results/phase0-summary.md` の §3
* 1-A・1-B で作成した `src/novui/` と `tests/`

## 書込みの許可範囲

```text
許可：
  src/novui/yamlio.py（作業0の変更のみ）
  src/novui/**（新規ファイルの追加）
  tests/**（新規ファイルの追加と、tests/test_yamlio.py への追記）
  tools/**（新規）
  docs/phase1/results/**（新規。作業5の結果のみ）
  pyproject.toml（[tool.pytest.ini_options] への markers の追加のみ）
  .venv/**（Git 管理外）
  ~/.local/share/novui-itest/**（結合試験の一時領域。試験の終了時に削除する）
禁止：上記以外すべて（docs/ の既存ファイル、spike/、.git/、.gitignore を含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションは自由。環境変数の指定を含む）
.venv/bin/python tools/claude_json_schema_probe.py
agy models
agy --version
claude --version
podman image exists <image>
podman image inspect --format '{{.Id}}' <image>
podman ps -a --filter name=novui-itest --format '{{.Names}}'
ls -la ~/.local/share/novui-itest（存在確認のみ）
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase1/controller
```

上記以外のコマンドは実行しないでください。コードの中から podman・agy・claude を実行するのは、本指示で定めた関数と試験の中だけとします。

1-A・1-B の禁止事項（main への push、force push、reset・rebase・merge・checkout・tag、branch の切り替え、git config の変更、sudo、rpm-ostree、toolbox）はすべて引き続き有効です。加えて次を禁止します。

* `~/.gemini/` の中身を、あなた自身が読む・表示する・コピーすること。トークンファイルを扱うのは本指示の `jobhome.py` のコードだけで、内容をログ・標準出力・ファイル名・例外メッセージ・テストの出力に出してはならない。
* `~/.local/bin/agy` を読むこと、イメージをビルド・削除すること、`podman rm` を手で実行すること。
* `--dangerously-skip-permissions` を使うこと。

## 共通の規則

* 1-A・1-B の共通の規則（型ヒント、git は run_git 経由、pathlib）を守る。新しい依存は追加しない。
* 外部コマンドは `shell=True` を使わず、引数のリストで実行する。
* 結合試験は `@pytest.mark.integration` を付け、環境変数 `NOVUI_INTEGRATION=1` がない場合は skip する。pyproject.toml の `[tool.pytest.ini_options]` に `markers = ["integration: requires podman, agy and claude on the host"]` を追加する。
* 単体テスト（integration 以外）は、podman・agy・claude がなくても通ること。

## 作業 0：YAML の数値の暗黙変換を YAML 1.2 相当にする（src/novui/yamlio.py）

`12:30` が 750 に、`0755` が 493 に、`007` が 7 に化けることを確認しました。phase1-plan.md §4 の 10 のとおり、NovuiYamlLoader の int と float の暗黙 resolver を次の正規表現に置き換えてください（yaml モジュール全体の SafeLoader は変更しない。既存の bool・timestamp の処理は変えない）。

```text
int:   ^[-+]?(0|[1-9][0-9]*)$
float: ^[-+]?(\.[0-9]+|[0-9]+\.[0-9]*)([eE][-+]?[0-9]+)?$
       ^[-+]?\.(inf|Inf|INF)$
       ^\.(nan|NaN|NAN)$
```

* resolver は先頭文字ごとに登録されるため、各正規表現が一致しうる先頭文字（`-`、`+`、`0`〜`9`、`.`）すべてに登録すること。旧 resolver は全先頭文字から除くこと。
* `tests/test_yamlio.py` に次を追記する：`12:30`、`1:20:30.5`、`0755`、`007`、`1_000`、`0x1F`、`0o17`、`0b101` がすべて文字列。`0`、`42`、`-7`、`+3` が int。`1.5`、`-0.5`、`.5`、`1.0e3`、`.inf`、`-.inf` が float、`.nan` が nan。`dumps_yaml` で書いた `{"t": "12:30", "n": 42}` を読み戻すと型が保たれる。

## 作成するファイル

```text
src/novui/config.py
src/novui/procrun.py
src/novui/jobhome.py
src/novui/container.py
src/novui/claude_cli.py
tools/claude_json_schema_probe.py
tests/test_config.py
tests/test_procrun.py
tests/test_jobhome.py
tests/test_container.py
tests/test_claude_cli.py
tests/integration/test_container_integration.py
docs/phase1/results/1c-claude-json-schema.yaml（作業5で probe が書き出す）
```

### src/novui/config.py

```python
@dataclass(frozen=True)
class Settings:
    data_dir: Path
    worktree_root: Path       # data_dir / "worktrees"
    jobhome_root: Path        # data_dir / "jobhomes"
    agy_image: str
    agy_token_path: Path
    timeouts: Mapping[str, int]

DEFAULT_TIMEOUTS: Mapping[str, int]   # {"agy_draft": 900, "agy_range_edit": 300, "claude": 300}

def load_settings(env: Mapping[str, str] | None = None) -> Settings
```

* env を省略したら `os.environ`。
* `NOVUI_DATA_DIR`（既定 `~/.local/share/novui`）、`NOVUI_AGY_IMAGE`（既定 `localhost/novui-spike:agy-1.2.14`）、`NOVUI_AGY_TOKEN`（既定 `~/.gemini/antigravity-cli/antigravity-oauth-token`）。`~` は展開し、パスは絶対パスにする（相対パスが与えられたら `ValueError`）。
* load_settings はファイルを作らず、トークンファイルを開かない。

### src/novui/procrun.py

```python
@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None      # Popen.returncode（シグナルで終了した場合は負の値）。待てなかった場合 None
    elapsed_seconds: float
    timed_out: bool
    signal_sent: str | None    # None | "SIGTERM" | "SIGKILL"（最後に送ったもの）
    group_remaining: bool      # 後始末の後もプロセスグループが残っていたか
    stdout: bytes
    stderr: bytes

def run_process(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None,
                stdin_data: bytes | None = None, timeout_seconds: float,
                kill_grace_seconds: float = 10.0,
                stdout_path: Path, stderr_path: Path) -> ProcResult
```

* `subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True, stdin=PIPE または DEVNULL, stdout=ファイル, stderr=ファイル)` で起動する。stdout・stderr は stdout_path・stderr_path に直接書き、終了後に読み戻して ProcResult に入れる。
* stdin_data があれば書き込んで閉じる。なければ DEVNULL。
* 全体 timeout（timeout_seconds）を過ぎたら、`os.killpg(pgid, SIGTERM)`、kill_grace_seconds 待っても終わらなければ `os.killpg(pgid, SIGKILL)`。無出力 timeout は実装しない（§5.6）。
* 終了後（timeout の有無にかかわらず）、プロセスグループに `os.killpg(pgid, 0)` で生存確認し、残っていれば SIGKILL を送って group_remaining=True とする（子が残っていた事実を記録する）。
* argv が空、timeout_seconds ≤ 0、cwd が絶対パスでない場合は `ValueError`。
* 例外（KeyboardInterrupt を含む）で抜ける場合も、プロセスグループに SIGKILL を送ってから再送出する。

### src/novui/jobhome.py

```python
TOKEN_RELPATH: str   # ".gemini/antigravity-cli/antigravity-oauth-token"

class JobHomeError(Exception)

@contextmanager
def job_home(jobhome_root: Path, job_id: str, token_path: Path) -> Iterator[Path]

def cleanup_stale_job_homes(jobhome_root: Path) -> list[Path]
```

* `job_home`：
  - jobhome_root を（なければ）mode 700 で作る。job_id は common の job_id の pattern（`^job-[0-9]+$`）か、結合試験用の `^itest-[0-9a-f]{8,}$` に一致しなければ `ValueError`。
  - `<jobhome_root>/<job_id>-<ランダム16進8桁>` を mode 700 で新規作成する（既にあれば JobHomeError）。
  - token_path が通常ファイルでなければ（存在しない、ディレクトリ、シンボリックリンク）JobHomeError。メッセージにパスは入れてよいが、内容は入れない。
  - トークンを `<home>/.gemini/antigravity-cli/antigravity-oauth-token` にバイト単位でコピーし、mode 600 にする（途中のディレクトリは 700）。トークン以外は何もコピーしない。
  - with ブロックを抜けるとき（例外・KeyboardInterrupt を含む）、home を `shutil.rmtree` で削除する。削除に失敗したら、元の例外がなければ JobHomeError を送出する。
* `cleanup_stale_job_homes`：jobhome_root 直下の、名前が `job-` または `itest-` で始まるディレクトリをすべて削除し、削除したパスのリストを返す（Controller 起動時に、異常終了で残った Job用HOME を消すため）。jobhome_root がなければ空のリスト。それ以外の名前のものは触らない。
* トークンの内容を、ログ・例外メッセージ・戻り値に含めてはならない。

### src/novui/container.py

```python
PROTECTED_PATHS: tuple[str, ...]   # v0.5 §15.2 の順：
    # "project.yaml", "chapters-order.yaml", "world", "characters", "plot",
    # "foreshadowing/registry.yaml", "rules", ".novui"

class ContainerError(Exception)

@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    mode: str          # "rw" | "ro"

def build_mounts(*, worktree: Path, job_home: Path, worktree_root: Path, jobhome_root: Path) -> list[Mount]
def mount_arg(m: Mount) -> str
def build_run_args(*, name: str, image: str, mounts: Sequence[Mount], command: Sequence[str]) -> list[str]
def agy_command(*, model: str, prompt: str) -> list[str]
def run_container(*, name: str, image: str, mounts: Sequence[Mount], command: Sequence[str],
                  timeout_seconds: float, log_dir: Path, kill_grace_seconds: float = 10.0) -> ProcResult
def remove_container(name: str) -> None
def image_id(image: str) -> str
```

* `build_mounts`：§5.3 の構成を作る。順番は次のとおり。
  1. worktree → `/workspace`、rw
  2. PROTECTED_PATHS のうち worktree 内に**存在するもの**を、この順で → `/workspace/<path>`、ro（存在しないものは重ねない。作られた場合は変更検査で検出する）
  3. `<worktree>/.git` → `/workspace/.git`、ro（.git がファイルでなければ ContainerError）
  4. job_home → `/home/agy`、rw
  - worktree は `paths.ensure_within(worktree_root, worktree)` を通り、かつ worktree_root そのものではないこと。job_home も同様に jobhome_root の配下であること。違反は ContainerError（phase1-plan.md §4 の 11。誤ってホームディレクトリ等を再ラベルしないため）。
  - source のパス文字列に `,` または `:` を含む場合は ContainerError（podman の -v の区切り文字のため）。
* `mount_arg`：`f"{source}:{target}:{mode},Z"`。
* `build_run_args`：次の順で返す。name は `^novui-[a-z0-9-]+$` に一致しなければ ValueError。

  ```text
  podman run --rm --name <name> --pull=never --userns=keep-id
    --cap-drop=all --security-opt=no-new-privileges
    -e HOME=/home/agy -w /workspace
    -v <mount_arg> ...（mounts の順）
    <image> <command...>
  ```

* `agy_command`：`["agy", "--mode", "accept-edits", "--model", model, f"--print={prompt}"]`。model・prompt が空なら ValueError。`--dangerously-skip-permissions` を含めないこと。
* `run_container`：`procrun.run_process` で build_run_args を実行する（cwd は log_dir、stdout・stderr は `<log_dir>/<name>.stdout.log`・`.stderr.log`）。**成功・失敗・例外にかかわらず**、最後に `remove_container(name)` を呼ぶ。
* `remove_container`：`podman rm -f --ignore --time 0 <name>` を実行する。終了コードが 0 以外なら ContainerError。
* `image_id`：`podman image inspect --format {{.Id}} <image>` の出力（前後の空白を除く）。失敗は ContainerError。

### src/novui/claude_cli.py

```python
def claude_args(*, model: str, output_format: str = "text", json_schema: str | None = None) -> list[str]
def run_claude(*, prompt: str, cwd: Path, model: str, timeout_seconds: float, log_dir: Path, name: str,
               output_format: str = "text", json_schema: str | None = None) -> ProcResult
```

* `claude_args`：`["claude", "-p", "--tools", "Read", "--permission-prompts", "none", "--no-session-persistence", "--model", model, "--output-format", output_format]`、json_schema があれば末尾に `["--json-schema", json_schema]`。output_format は `text` か `json` のみ（それ以外は ValueError）。`--dangerously-skip-permissions` と `--permission-mode` を含めないこと。
* `run_claude`：プロンプトは stdin で渡す（§5.5）。stdout・stderr は `<log_dir>/<name>.stdout.log`・`.stderr.log`。

### tools/claude_json_schema_probe.py

Claude の `--json-schema` の挙動を記録する使い捨ての確認スクリプト（phase1-plan.md の持ち越し事項）。

* 作業ディレクトリは `~/.local/share/novui-itest/probe-<ランダム16進8桁>/`（終了時に削除）。log_dir も同じ場所。
* モデルは `opus`。timeout は 300 秒。
* プロンプト（stdin）：`次の質問に答えてください。質問：日本の首都はどこですか。type には "probe" を入れてください。`
* schema（1行の JSON 文字列）：`{"type":"object","properties":{"type":{"const":"probe"},"answer":{"type":"string"}},"required":["type","answer"],"additionalProperties":false}`
* 次の3通りを各1回実行する。
  - A：output_format=text、json_schema なし
  - B：output_format=text、json_schema あり
  - C：output_format=json、json_schema あり
* 各実行について次を記録する。
  - exit_code、elapsed_seconds、timed_out
  - stdout_bytes（長さ）、stdout_head（stdout の先頭 1000 文字。UTF-8 で復号できなければ `"<not utf-8>"`）、stderr_head（同じく先頭 1000 文字）
  - stdout_is_json（stdout 全体が json.loads できるか）
  - top_level_keys（JSON の object なら、そのキーの一覧。そうでなければ null）
  - schema_match_paths（JSON 全体、またはトップレベルの値のうち、`{"type": "probe", "answer": <文字列>}` の形（追加キーなし）に一致するものの場所。全体なら `"$"`、トップレベルのキーなら `"$.<key>"`。値が文字列の場合は、その文字列を json.loads した結果も調べ、一致すれば `"$.<key>(string)"`）
* 結果を `{"claude_version": <claude --version の出力>, "model": "opus", "runs": {"A": {...}, "B": {...}, "C": {...}}}` として `docs/phase1/results/1c-claude-json-schema.yaml` に dumps_yaml で書く。

## 必須のテスト（単体）

### test_config.py
* 既定値、環境変数による上書き、`~` の展開、相対パスの ValueError。load_settings がファイルを作らない。

### test_procrun.py（`sh`、`sleep`、`printf` を使う。各テストは数秒以内に終わるよう timeout と grace を短くする）
* 正常終了：exit_code 0、stdout・stderr がファイルと戻り値の両方に入る、timed_out False、signal_sent None。
* 終了コード 3。
* stdin_data が子に渡る（`sh -c 'cat'`）。
* timeout：`sleep 30` を timeout 1 秒で止め、timed_out True、signal_sent "SIGTERM"、exit_code が負。
* SIGTERM を無視する子：`sh -c 'trap "" TERM; sleep 30'` を timeout 1 秒・grace 1 秒で止め、signal_sent "SIGKILL"。
* 孫プロセス：`sh -c 'sleep 30 & echo started'` が即座に終了しても、残った `sleep` がプロセスグループの後始末で殺され、group_remaining True。テストの最後に、その sleep の PID（`sh -c 'sleep 30 & echo $!'` で取得）が存在しないことを確認する。
* 引数の ValueError（空の argv、timeout 0、相対 cwd）。

### test_jobhome.py（トークンは tmp_path に作った偽物 `FAKE-TOKEN-<ランダム>` を使う）
* with の中：home が mode 700、トークンのコピーが mode 600 で内容一致、home の中にトークン以外のファイルがない。
* with を抜けると home が消える。with の中で例外を送出しても消え、例外はそのまま伝わる。
* トークンがない・ディレクトリ・シンボリックリンクで JobHomeError、例外メッセージに偽トークンの内容が含まれない。
* 不正な job_id で ValueError。
* cleanup_stale_job_homes：`job-1-xxxxxxxx`・`itest-...` のディレクトリは消し、`other` は残す。

### test_container.py（podman は実行しない）
* build_mounts：保護対象を一部だけ作った worktree で、存在するものだけがこの順に ro で並ぶ。最初が worktree の rw、最後が job_home の rw、その直前が .git の ro。
* worktree が worktree_root の外、worktree_root そのもの、job_home が jobhome_root の外、パスに `,` または `:` を含む、.git がディレクトリ、の各場合に ContainerError。
* build_run_args の全体を期待するリストと完全一致で比較する。不正な name で ValueError。
* agy_command：`--print=` が最後の要素で、プロンプトに空白・改行・日本語・`--mode` という文字列を含んでも1要素のまま。`--dangerously-skip-permissions` を含まない。
* run_container が、procrun.run_process が例外を送出した場合でも remove_container を呼ぶ（monkeypatch で両方を差し替えて確認）。

### test_claude_cli.py
* claude_args の全体を期待するリストと完全一致で比較する（json_schema なし・あり）。不正な output_format で ValueError。

## 結合試験（tests/integration/test_container_integration.py）

すべて `@pytest.mark.integration`。共通の fixture：

* 試験の一時領域 `~/.local/share/novui-itest/<ランダム16進8桁>/` を作り、その中に data_dir を置く（worktree_root、jobhome_root はその配下）。試験の終了時に一時領域を丸ごと削除する。
* 試験用の作品リポジトリを data_dir の外（同じ一時領域の `repo/`）に作り、`project.yaml`、`world/w.md`、`characters/C001.yaml`、`.novui/approvals/.keep`、`chapters/ch-001/draft.md` を置いて commit する。commit は `-c user.name=test -c user.email=test@example.invalid` で行う。
* `git worktree add` で `<worktree_root>/itest-<ランダム>` に linked worktree を作る（run_git 経由）。
* イメージは `load_settings().agy_image`。`podman image exists` が失敗したら pytest.fail で止める（skip にしない）。
* コンテナ名は `novui-itest-<ランダム16進8桁>`。
* 各試験の後、`podman ps -a --filter name=novui-itest` に該当がないことを確認する。

試験：

1. **I1 マウント検査**：build_mounts のマウントで、command `["cat", "/proc/self/mountinfo"]` を run_container で実行し、stdout を mountinfo.parse_mountinfo → find_unexpected_mounts（allowed=`["/workspace", "/home/agy"]`）で検査して、想定外のマウントが0件。Job用HOME には偽トークンを使う。
2. **I2 書込みの可否（実際の配置場所での SELinux の確認）**：同じマウントで、command `["sh", "-c", <スクリプト>]` を実行する。スクリプトは次をそれぞれ試し、成否を `<対象>=OK` / `<対象>=DENIED` の行で出力する：`/workspace/chapters/ch-001/draft.md` への追記、`/workspace/world/w.md` への追記、`/workspace/project.yaml` への追記、`/workspace/.novui/approvals/x` の作成、`/workspace/.git` への追記、`/home/agy/x` の作成。期待：draft.md と /home/agy は OK、それ以外はすべて DENIED。さらに試験後、ホスト側で run_git による `get_changes` が成功し、変更が `chapters/ch-001/draft.md` だけであること。
3. **I3 timeout とコンテナの後始末**：command `["sleep", "60"]` を timeout 3 秒・grace 3 秒で実行し、timed_out True、試験後にコンテナが残っていない。
4. **I4 AGY の実行**（実際のトークンを使う。環境変数 `NOVUI_AGY_MODEL` が未設定なら pytest.fail）：
   - job_home(jobhome_root, "itest-<ランダム>", settings.agy_token_path) の中で、agy_command(model=NOVUI_AGY_MODEL、prompt=`chapters/ch-001/draft.md の末尾に「結合試験」という1行を追記してください。それ以外のファイルは変更しないでください。完了したら「完了」とだけ出力してください。`) を timeout 300 秒で実行する。
   - 期待：exit_code 0、checks.check_cli_output が PASS、get_changes の変更が `chapters/ch-001/draft.md` だけ、draft.md に「結合試験」を含む。
   - with を抜けた後、Job用HOME が残っていない。
   - トークンの漏洩がない：トークンファイルの内容（bytes）が、stdout、stderr、ログファイル、worktree 内の全ファイルのいずれにも含まれない（テスト内で比較するだけで、内容を出力しない。失敗時の assert メッセージにも含めない）。
   - 実行に使ったモデル名、イメージの ID（image_id）、イメージ内の agy のバージョン（同じマウントで command `["agy", "--version"]` を実行した stdout）を、`print` で出力する（pytest は `-s` で実行して報告に含める）。

## 作業の手順

1. 本指示と参照文書を読み、作業0を含め、これから作る・変更するファイル、関数、テスト、実行するコマンドの一覧を説明する。
2. **Human の許可を得る。**
3. 作業0を行い、`.venv/bin/python -m pytest -q tests/test_yamlio.py` を PASS させる。
4. 単体の実装とテストを書き、`.venv/bin/python -m pytest -q` を全件 PASS にする（結合試験は skip される）。ここで一度、許可範囲のファイルを `git add` して commit・push する。
   ```text
   phase1: 1-C job runner units (procrun, jobhome, container, claude cli)
   ```
5. 前提を確認する：`agy --version`、`claude --version`、`podman image exists <既定のイメージ>`、`agy models`。`agy models` の一覧から、名前に `3.8` と `flash` の両方を含む（大文字小文字を区別しない）ものがちょうど1つあれば、それをモデル名とする。0件または2件以上なら止めて、一覧をそのまま報告する。
6. 結合試験を実行する：
   ```text
   NOVUI_INTEGRATION=1 NOVUI_AGY_MODEL=<5で決めたモデル名> .venv/bin/python -m pytest -s -m integration tests/integration
   ```
   失敗した試験があれば、直さずに止めて、出力をそのまま報告する。
7. `.venv/bin/python tools/claude_json_schema_probe.py` を実行し、docs/phase1/results/1c-claude-json-schema.yaml ができたことを確認する。
8. `ls -la ~/.local/share/novui-itest` と `podman ps -a --filter name=novui-itest --format '{{.Names}}'` で、一時領域とコンテナが残っていないことを確認する（一時領域のディレクトリ自体が空で残るのは可）。
9. 許可範囲のファイルだけを `git add` し、次のメッセージで commit・push する。
   ```text
   phase1: 1-C integration tests and Claude --json-schema probe result
   ```
10. 報告して止まる。

## 報告の内容

1. 作成・変更したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力（単体）
3. 手順5の結果：agy・claude のバージョン、`agy models` の一覧、選んだモデル名
4. 手順6の結合試験の出力（`-s` の print を含む全文）
5. docs/phase1/results/1c-claude-json-schema.yaml の全文
6. 手順8の結果
7. 本指示どおりにできなかった点、判断に迷った点（なければ「なし」）
8. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。Claude がレビューするまで、次の段階（1-D）に進まないでください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
