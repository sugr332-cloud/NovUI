# AGYへの作業指示：Phase 1-A 検査コアと状態遷移

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストを実行し、phase1/controller branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/phase1/phase1-plan.md`（特に §2 技術選定、§4 v0.5 の補足）
* `docs/novel-system-spec-v0.5.md` の §5.3、§7.1、§7.2、§8.3、§13.5
* `docs/spike/results/phase0-summary.md` の §4（Phase 0 で見つかった誤判定）

## 書込みの許可範囲

```text
許可：
  pyproject.toml（新規）
  .gitignore（リポジトリ直下。新規）
  src/novui/**（新規）
  tests/**（新規）
  .venv/**（Git 管理外。python3 -m venv と pip が作るもの）
禁止：上記以外すべて（docs/、spike/、.git/ を含む）
```

## 実行してよいコマンド

```text
python3 --version
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest（オプションは自由）
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase1/controller
```

上記以外のコマンドは実行しないでください。特に次は禁止です。

* main への push、force push、`git reset` / `rebase` / `merge` / `checkout` / `tag`、phase1/controller 以外への `git switch`
* `git config`（--global を含む）の変更
* sudo、rpm-ostree、toolbox、distrobox、podman
* `~/.gemini/` の中身や `~/.local/bin/agy` を読むこと
* python3 の venv や pip が失敗した場合に別の方法で環境を作ること（失敗したら止めて報告）

テストが作る git リポジトリは、必ず pytest の `tmp_path` の中に作ってください。NovUI リポジトリ自身に対して git の書込み操作をするテストは禁止です。テスト内の commit は、`git -c user.name=test -c user.email=test@example.invalid commit ...` のように `-c` で名前を渡してください。

## 共通の規則

* Python 3.11 以上。標準ライブラリと pytest だけを使う（PyYAML・jsonschema は 1-B で追加するので、今回は使わない）。
* すべての公開関数に型ヒントを付ける。
* git の呼び出しは `src/novui/gitinspect.py` の `run_git` だけを通す。`run_git` は `["git", "-C", str(<絶対パス>), ...]` の形で実行し、環境変数に `GIT_OPTIONAL_LOCKS=0` と `LC_ALL=C` を加え（既存の環境変数は引き継ぐ）、`shell=True` は使わない。終了コードが 0 以外なら `GitError`（stderr を含む）を送出する。`-C` に渡すパスが絶対パスでない場合は `ValueError`。
* パスは `pathlib.Path` で扱う。git から得たパスは POSIX 形式の相対パス文字列（`str`）のまま扱う。

## 作成するファイル

```text
pyproject.toml
.gitignore
src/novui/__init__.py
src/novui/paths.py
src/novui/states.py
src/novui/checks.py
src/novui/anchor.py
src/novui/gitinspect.py
src/novui/mountinfo.py
tests/conftest.py（必要なら）
tests/test_paths.py
tests/test_states.py
tests/test_checks.py
tests/test_anchor.py
tests/test_gitinspect.py
tests/test_mountinfo.py
tests/fixtures/mountinfo_spike02.txt
```

### pyproject.toml

* `[project]`：name = "novui"、version = "0.1.0"、requires-python = ">=3.11"、dependencies = []
* `[project.optional-dependencies]`：dev = ["pytest>=8"]
* `[tool.pytest.ini_options]`：pythonpath = ["src"]、testpaths = ["tests"]
* build-system は書かなくてよい（インストールしないため）。

### .gitignore（リポジトリ直下）

```text
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

### src/novui/paths.py

* `class PathError(Exception)`
* `is_safe_relpath(p: str) -> bool`
  - 空文字、NUL を含む、`/` で始まる、`\` を含む、空の要素を含む（`//`、末尾の `/`）、要素が `.` または `..` のいずれかなら False。それ以外は True。
* `ensure_within(root: Path, target: Path) -> Path`
  - `root` と `target` をそれぞれ `resolve(strict=False)` し、target が root と同じか root の配下ならその resolve 後のパスを返す。そうでなければ `PathError`。
  - root が絶対パスでなければ `ValueError`。

### src/novui/states.py

```python
class InvalidTransition(Exception)

class ChapterState(Enum): OUTLINED, PLANNED, PLAN_APPROVED, DRAFTED, AI_VALIDATED, HUMAN_APPROVED, FINAL
class ChapterEvent(Enum): OUTLINE_CREATED, PLAN_WRITTEN, PLAN_APPROVED, PLAN_REJECTED, DRAFT_COMPLETED,
                          VALIDATION_ACCEPTED, VALIDATION_SKIPPED, STATE_PATCH_APPROVED, FINAL_APPROVED,
                          PLAN_REVISED, CONTENT_CHANGED, TYPO_FIXED

def next_chapter_state(current: ChapterState | None, event: ChapterEvent, *, review_required: bool = False) -> ChapterState
```

章の遷移表（これ以外の組合せはすべて `InvalidTransition`）：

| 現状態 | イベント | 次状態 |
|---|---|---|
| None | OUTLINE_CREATED | OUTLINED |
| OUTLINED | PLAN_WRITTEN | PLANNED |
| PLANNED | PLAN_APPROVED | PLAN_APPROVED |
| PLANNED | PLAN_REJECTED | OUTLINED |
| PLAN_APPROVED | DRAFT_COMPLETED | DRAFTED |
| DRAFTED | VALIDATION_ACCEPTED | AI_VALIDATED |
| DRAFTED | VALIDATION_SKIPPED | AI_VALIDATED |
| AI_VALIDATED | STATE_PATCH_APPROVED | HUMAN_APPROVED |
| HUMAN_APPROVED | FINAL_APPROVED | FINAL（review_required が True なら InvalidTransition） |
| PLAN_APPROVED, DRAFTED, AI_VALIDATED, HUMAN_APPROVED, FINAL | PLAN_REVISED | PLANNED |
| DRAFTED, AI_VALIDATED, HUMAN_APPROVED, FINAL | CONTENT_CHANGED | DRAFTED |
| None 以外のすべての状態 | TYPO_FIXED | 現状態のまま |

```python
class JobState(Enum): QUEUED, RUNNING, CHECKING, VALIDATING, WAITING_HUMAN, COMPLETED, FAILED, STOPPED, CANCELLED
class JobEvent(Enum): LOCK_ACQUIRED, CLI_EXITED, TIMED_OUT, HUMAN_STOP, CHECK_PASSED, CHECK_FAILED,
                      VALIDATION_PASSED, VALIDATION_NEEDS_HUMAN, NEEDS_HUMAN_INPUT, HUMAN_CONTINUE,
                      HUMAN_OVERRIDE, HUMAN_REQUEST_FIX, HUMAN_CANCEL, CONTROLLER_RESTARTED

TERMINAL_JOB_STATES: frozenset[JobState]  # COMPLETED, FAILED, STOPPED, CANCELLED

def next_job_state(current: JobState, event: JobEvent, *, needs_validation: bool | None = None, reason: str | None = None) -> JobState
```

Job の遷移表（これ以外の組合せはすべて `InvalidTransition`。終了状態からはどのイベントも `InvalidTransition`）：

| 現状態 | イベント | 次状態 |
|---|---|---|
| QUEUED | LOCK_ACQUIRED | RUNNING |
| QUEUED | NEEDS_HUMAN_INPUT | WAITING_HUMAN |
| QUEUED | HUMAN_CANCEL | CANCELLED |
| QUEUED | CONTROLLER_RESTARTED | STOPPED |
| RUNNING | CLI_EXITED | CHECKING |
| RUNNING | TIMED_OUT | FAILED |
| RUNNING | HUMAN_STOP | STOPPED |
| RUNNING | CONTROLLER_RESTARTED | STOPPED |
| CHECKING | CHECK_PASSED | needs_validation が True なら VALIDATING、False なら COMPLETED |
| CHECKING | CHECK_FAILED | FAILED |
| CHECKING | NEEDS_HUMAN_INPUT | WAITING_HUMAN |
| CHECKING | CONTROLLER_RESTARTED | STOPPED |
| VALIDATING | VALIDATION_PASSED | COMPLETED |
| VALIDATING | VALIDATION_NEEDS_HUMAN | WAITING_HUMAN |
| VALIDATING | HUMAN_STOP | STOPPED |
| VALIDATING | CONTROLLER_RESTARTED | STOPPED |
| WAITING_HUMAN | HUMAN_CONTINUE | COMPLETED |
| WAITING_HUMAN | HUMAN_OVERRIDE | COMPLETED（reason 必須） |
| WAITING_HUMAN | HUMAN_REQUEST_FIX | COMPLETED（reason 必須） |
| WAITING_HUMAN | HUMAN_CANCEL | CANCELLED |

* CHECK_PASSED で needs_validation が None なら `ValueError`。
* HUMAN_OVERRIDE・HUMAN_REQUEST_FIX で reason が None または空白のみなら `ValueError`。
* WAITING_HUMAN に CONTROLLER_RESTARTED は `InvalidTransition`（呼び出し側は WAITING_HUMAN をそのまま残す）。
* 遷移表は、テストから全組合せを列挙できるよう、モジュール内のデータ（dict 等）として定義し、関数はそのデータを引くだけにしてください。

### src/novui/checks.py

```python
@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str            # "PASS" | "WARNING" | "FAIL" のいずれか
    details: tuple[str, ...] = ()
```

| 関数 | name | 判定 |
|---|---|---|
| `check_cli_output(exit_code: int, stdout: bytes) -> CheckResult` | cli_output | exit_code ≠ 0 なら detail `exit_code=<N>`、`stdout.strip()` が空なら detail `empty_stdout`。どちらかがあれば FAIL、なければ PASS |
| `check_allowed_paths(changes: Sequence[PathChange], allowed: Collection[str]) -> CheckResult` | allowed_paths | 各 change の path と orig_path（ある場合）がすべて allowed に完全一致で含まれれば PASS。含まれないパスを昇順で detail に列挙して FAIL。allowed に `is_safe_relpath` が False のものがあれば `ValueError` |
| `check_ignored_unchanged(before: Set[str], after: Set[str]) -> CheckResult` | ignored_files | 増えたもの `added: <path>`、減ったもの `removed: <path>` を昇順で detail に入れ、どちらかがあれば FAIL |
| `check_append_only(before: bytes \| None, after: bytes \| None) -> CheckResult` | append_only | 下記 |
| `check_prefix_suffix(after: bytes, prefix: bytes, suffix: bytes) -> CheckResult` | prefix_suffix | `len(after) >= len(prefix) + len(suffix)` かつ after が prefix で始まり suffix で終わるなら PASS、それ以外 FAIL |
| `compare_hash_records(before: Mapping[str, str], after: Mapping[str, str]) -> CheckResult` | hash_compare | 値が違う `changed: <key>`、after にない `missing_after: <key>`、after にだけある `added_after: <key>` を昇順で detail に入れ、どれかがあれば FAIL |
| `count_chars(text: str) -> int` | － | `ch.isspace()` が True の文字を除いたコードポイント数 |
| `check_char_range(text: str, min_chars: int, max_chars: int) -> CheckResult` | char_count | 範囲内（両端を含む）なら PASS、範囲外なら WARNING。detail は常に `count=<N> range=<min>-<max>`。min_chars < 0 または min_chars > max_chars なら `ValueError` |

check_append_only の判定：

* before が None、after が None → PASS
* before が None、after あり → PASS（新規作成）
* before あり、after が None → FAIL（detail `deleted`）
* after == before → PASS
* after が before で始まり、かつ（before が空、または before が `\n` で終わる）→ PASS
* after が before で始まるが before が `\n` で終わらない → FAIL（detail `last_line_modified`。既存の最終行への追記は既存行の変更とみなす）
* それ以外 → FAIL（detail `existing_content_modified`）

PathChange は gitinspect.py で定義し、checks.py から import してください。

### src/novui/anchor.py

* `class AnchorError(Exception)`：属性 `count: int`（一致件数）を持つ。
* `find_anchor(text: str, anchor: str, before: str = "", after: str = "") -> tuple[int, int]`
  - anchor が空なら `ValueError`。
  - `before + anchor + after` を text 内で検索し、重なりも数える（見つかった位置の次の文字から再検索する）。
  - 一致がちょうど1件なら、その中の anchor 部分の範囲を **UTF-8 のバイトオフセット** `(start, end)` で返す。
  - 0件または2件以上なら `AnchorError`（count に件数）。
* `split_by_range(data: bytes, start: int, end: int) -> tuple[bytes, bytes]`
  - `0 <= start <= end <= len(data)` でなければ `ValueError`。
  - `(data[:start], data[end:])` を返す。

### src/novui/gitinspect.py

```python
class GitError(Exception)

@dataclass(frozen=True)
class PathChange:
    status: str               # porcelain の XY 2文字（"??"、"!!" を含む）
    path: str
    orig_path: str | None     # rename/copy のときの元のパス

@dataclass(frozen=True)
class GitDirs:
    git_dir: Path
    common_dir: Path

def run_git(repo: Path, *args: str) -> bytes
def parse_porcelain_z(data: bytes) -> list[PathChange]
def get_changes(worktree: Path) -> list[PathChange]
def get_ignored(worktree: Path) -> set[str]
def resolve_git_dirs(worktree: Path) -> GitDirs
def git_protection_targets(worktree: Path) -> dict[str, Path]
def hash_targets(targets: Mapping[str, Path]) -> dict[str, str]
```

* `parse_porcelain_z`：`git status --porcelain=v1 -z` の出力を解析する。各エントリは `XY<空白><path>\0`。X または Y が `R` か `C` の場合、直後の `\0` 区切りの1フィールドが元のパス（orig_path）。パスは UTF-8（`errors="surrogateescape"`）で復号する。形式が壊れていれば `ValueError`。
* `get_changes`：`status --porcelain=v1 -z --untracked-files=all` を実行して解析した結果から、status が `!!` のものを除いて返す。
* `get_ignored`：`ls-files -z --others --ignored --exclude-standard` を実行し、`\0` で区切ったファイルパスの集合を返す（UTF-8、`errors="surrogateescape"`）。`git status --ignored` は無視されたディレクトリを `dir/` の1件にまとめてしまい、既存の無視ディレクトリ内に増えたファイルを検出できないため使わない。
* `resolve_git_dirs`：`rev-parse --path-format=absolute --git-dir --git-common-dir` を実行し、2行をそれぞれ Path にする。どちらかが絶対パスでなければ `GitError`。
* `git_protection_targets`：worktree の保護対象を、ラベルから絶対パスへの dict で返す（phase1-plan.md §4 の 8）。
  - worktree の `.git` がファイルでない（ディレクトリ、つまり linked worktree ではない）場合は `ValueError`。
  - `"worktree:.git"` → `<worktree>/.git`
  - `"common:config"` → `<common_dir>/config`
  - `"common:hooks/<相対パス>"` → `<common_dir>/hooks/` 以下の全ファイル（再帰。hooks がなければ0件）
  - `"gitdir:gitdir"`、`"gitdir:commondir"`、`"gitdir:HEAD"` → `<git_dir>/` の各ファイル
* `hash_targets`：各パスのファイル内容の SHA-256 を `"sha256:<小文字16進>"` で返す。ファイルがなければ `"MISSING"`。git は呼ばない。

### src/novui/mountinfo.py

```python
@dataclass(frozen=True)
class MountEntry:
    mount_id: int
    parent_id: int
    root: str
    mount_point: str
    options: str
    fstype: str
    source: str
    super_options: str

STANDARD_EXACT: frozenset[str]     # "/", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf", "/run/.containerenv", "/run/secrets"
STANDARD_TREES: tuple[str, ...]    # "/proc", "/sys", "/dev"

def parse_mountinfo(text: str) -> list[MountEntry]
def find_unexpected_mounts(entries: Sequence[MountEntry], allowed: Collection[str]) -> list[MountEntry]
```

* `parse_mountinfo`：1行を `" - "` で前後に分ける。前半の第1〜6フィールドが mount_id、parent_id、major:minor（保持しない）、root、mount_point、options。第7フィールド以降の optional fields は捨てる。後半は fstype、source、super_options。root と mount_point の8進エスケープ（`\040`、`\011`、`\012`、`\134`）を復元する。空行は無視する。形式が壊れていれば `ValueError`。
* `find_unexpected_mounts`：次のどれにも当たらない entry を、入力順で返す。
  - mount_point が STANDARD_EXACT に含まれる
  - mount_point が STANDARD_TREES のどれかと同じか、その配下（`<tree>/` で始まる）
  - mount_point が allowed のどれかと同じか、その配下
* 判定には mount_point だけを使う。root、options、super_options（overlay の lowerdir 等）は判定に使わない。

### tests/fixtures/mountinfo_spike02.txt

`docs/spike/results/spike02_run_20261001_220536_mountinfo.log` の内容をそのままコピーしてください（docs/ 側のファイルは変更しない）。

## 必須のテスト

以下を必ず含めてください。これ以外のテストを追加するのは自由です。

### test_paths.py
* is_safe_relpath：`chapters/ch-001/draft.md` が True。`""`、`/etc/passwd`、`../x`、`a/../b`、`a//b`、`a/`、`./a`、`a\\b`、`a\x00b` が False。
* ensure_within：配下は通る。`root/../outside` は PathError。root 内に置いた root 外へのシンボリックリンクを通したパスは PathError。

### test_states.py
* 章・Job の遷移表の全行が期待どおりの次状態になる。
* 全状態 × 全イベントの組合せを列挙し、表にないものがすべて InvalidTransition になる（章は current=None も含める）。
* FINAL_APPROVED が review_required=True で InvalidTransition。
* CHECK_PASSED の needs_validation=None が ValueError。
* HUMAN_OVERRIDE・HUMAN_REQUEST_FIX の reason が None と `"  "` で ValueError。
* FAILED から HUMAN_OVERRIDE が InvalidTransition（機械的な安全違反は OVERRIDE で解除できない）。
* WAITING_HUMAN から CONTROLLER_RESTARTED が InvalidTransition。

### test_checks.py
* check_cli_output：exit 0 かつ空 stdout が FAIL（Phase 0 で accept-edits の拒否時に起きた形）。stdout が空白と改行だけでも FAIL。exit 2 で FAIL。exit 0 かつ内容ありで PASS。
* check_allowed_paths：許可内だけで PASS。許可外を含むと FAIL で detail にそのパス。rename の orig_path が許可外なら FAIL。
* check_ignored_unchanged：増加・減少それぞれ FAIL。
* check_append_only：本指示の判定表の全行。
* check_prefix_suffix：本文の途中だけ変えた場合 PASS。prefix 側の1バイト変更、suffix 側の1バイト変更、after が短すぎる場合がそれぞれ FAIL。
* compare_hash_records：**Phase 0 の見出し行の誤判定の再現**として、同じファイル群を「Before」「After」と異なる見出し・ラベル付きで記録しても、記録同士の比較（dict の比較）では PASS になることを示すテスト。値の変更、キーの欠落、キーの追加がそれぞれ FAIL。
* count_chars：`"あい う\n　え\t"` が 4。
* check_char_range：範囲内 PASS、境界値（min、max ちょうど）PASS、範囲外 WARNING（FAIL ではない）。

### test_anchor.py
* 日本語の本文で一意に見つかった場合、返したバイト範囲を切り出して復号すると anchor と一致する。
* 同じ anchor が2か所にあると AnchorError（count=2）、before/after の文脈を付けると一意になって成功する。
* 0件で AnchorError（count=0）。
* 重なりの数え方：text `"ああああ"`、anchor `"ああ"` は3件で AnchorError（count=3）。
* split_by_range の範囲外で ValueError。split_by_range で切った prefix・suffix に対し、anchor 部分だけを置き換えた after が check_prefix_suffix で PASS になる（組合せのテスト）。

### test_gitinspect.py
すべて tmp_path 内に作った試験用リポジトリで行う。
* parse_porcelain_z：rename（`R ` と元のパス）、`??`、`!!`、日本語のファイル名（`chapters/第一章.md`）を含む bytes を正しく解析する。壊れた入力で ValueError。
* get_changes：日本語のファイル名の未追跡ファイルが、引用符やエスケープなしの `chapters/第一章.md` として返る。
* get_ignored：.gitignore で無視したディレクトリ（例 `output/`）に、**既にファイルが1つある状態で**別のファイルを追加すると、get_ignored の結果にその新しいファイルのパスが増える（check_ignored_unchanged が FAIL になる）。サブディレクトリ内のファイルと日本語のファイル名も個別のパスとして返る。
* resolve_git_dirs：**Phase 0 の相対パスの誤りの再現**として、`monkeypatch.chdir` で無関係なディレクトリに移動した状態で linked worktree（`git worktree add`）に対して呼び、git_dir と common_dir が絶対パスで、それぞれ `<本体>/.git/worktrees/<name>` と `<本体>/.git` に一致する（resolve 後に比較）。
* git_protection_targets：linked worktree で期待するラベルがそろう。本体（.git がディレクトリ）に対しては ValueError。
* hash_targets と compare_hash_records の組合せ：ハッシュを取ったあと get_changes と get_ignored を実行しても、再度取ったハッシュとの比較が PASS（Controller 自身の git status で保護対象が変わらないこと）。hooks に1ファイル追加し、git_protection_targets と hash_targets を取り直して比較すると FAIL（added_after）。worktree の .git 参照ファイルを書き換えると FAIL（changed）。
* run_git：相対パスを渡すと ValueError。

### test_mountinfo.py
* **Phase 0 の overlay オプションの誤検出の再現**として、fixtures/mountinfo_spike02.txt を解析し、allowed=["/workspace"] で find_unexpected_mounts が空になる（/ の overlay の lowerdir に /var/home/... が含まれていても検出しない）。
* 同じ内容に、ホストのホームディレクトリを `/mnt/host` にマウントした行を1行加えると、その1件だけが返る。
* allowed=["/workspace"] のとき `/workspace/world`（重ねマウント）は検出しない。`/workspace2` は検出する。
* mount_point に `\040` を含む行が空白に復元される。
* 区切り `" - "` のない行で ValueError。

## 作業の手順

1. 本指示と参照文書を読み、これから作るファイル・関数・テストの一覧と、実行するコマンドの一覧を説明する。
2. **Human の許可を得る。**
3. `python3 --version` を確認する（3.11 未満なら止めて報告）。`python3 -m venv .venv`、`.venv/bin/pip install pytest` を実行する。
4. 実装とテストを書く。
5. `.venv/bin/python -m pytest -q` を実行し、全件 PASS にする。テストを通すために本指示の仕様を変えてはならない。仕様どおりに実装してテストが通らない場合は、止めて報告する。
6. `git status` で、変更が許可範囲だけであること、`.venv/` と `__pycache__/` が Git 管理外になっていることを確認する。
7. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。

   ```text
   phase1: 1-A inspection core and state transitions
   ```

8. `git push origin phase1/controller`
9. 報告して止まる。

## 報告の内容

1. `python3 --version` と、インストールされた pytest のバージョン
2. 作成したファイルの一覧と行数（`wc -l`）
3. `.venv/bin/python -m pytest -q` の最終出力（件数のサマリー）
4. 本指示どおりにできなかった点、判断に迷った点（なければ「なし」）
5. `git status --short` の結果（commit 後）
6. `git log -2 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。Claude がレビューするまで、次の段階（1-B）に進まないでください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
