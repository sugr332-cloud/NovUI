# AGYへの作業指示：Phase 1-D1 作業ブランチ・Git 反映・lock・schema の v0.5.2 対応

## あなたの役割

あなたは Controller の**実装担当**です。本指示の範囲のコードとテストを書き、テストを実行し、phase1/controller branch に commit・push します。
設計の判断はしません。本指示と仕様書が矛盾する場合、または本指示で決まっていない判断が必要になった場合は、作業を止めて報告してください。

本段階では podman・agy・claude は使いません。git の操作はすべて pytest の `tmp_path` に作った試験用リポジトリに対して行います。

## 参照する文書（作業前に必ず読む。変更しない）

* `docs/novel-system-spec-v0.5.2.md` の §6.4、§6.5.1、§7.1、§15.2、§15.5、§17、§18.1
* `docs/phase1/phase1-plan.md`（特に §4 の 8、15）
* 1-A〜1-C で作成した `src/novui/` と `tests/`

## 書込みの許可範囲

```text
許可：
  src/novui/workrepo.py、src/novui/locks.py（新規）
  src/novui/protected.py（is_protected の追加のみ）
  src/novui/config.py（本指示の追加のみ）
  src/novui/semantics.py（本指示の追加のみ）
  schemas/common.schema.json、schemas/plan.schema.json、schemas/summary.schema.json（本指示の変更のみ）
  schemas/character.schema.json、schemas/registry.schema.json（新規）
  tests/**（本指示の変更・追加）
  .venv/**（Git 管理外）
禁止：上記以外すべて（docs/、spike/、tools/、.git/、.gitignore、pyproject.toml、src/novui のその他のファイルを含む）
```

## 実行してよいコマンド

```text
.venv/bin/python -m pytest（オプションは自由）
git status / git diff / git log
git add <許可範囲のファイル> / git commit / git push origin phase1/controller
```

1-A〜1-C の禁止事項はすべて引き続き有効です。テストが作る git リポジトリは必ず `tmp_path` の中に作り、NovUI リポジトリ自身に git の書込み操作をしないこと。テスト内で commit する場合も、git config を変更せず `-c user.name=... -c user.email=...` で渡すこと。

## 作業 1：config.py への追加

* Settings に `git_name: str` と `git_email: str` を追加する。環境変数 `NOVUI_GIT_NAME`（既定 `NovUI Controller`）、`NOVUI_GIT_EMAIL`（既定 `novui@localhost`）。空文字なら ValueError。
* 既存のテストが壊れないようにする。

## 作業 2：protected.py への追加

```python
def is_protected(path: str) -> bool
```

* path が PROTECTED_PATHS のどれかと同じか、その配下（`<p>/` で始まる）なら True。
* path が `is_safe_relpath` でなければ ValueError。
* 例：`world/w.md` → True、`worldmap.md` → False、`foreshadowing/registry.yaml` → True、`foreshadowing/notes.md` → False、`.novui/approvals/A-0001.yaml` → True、`chapters/ch-001/draft.md` → False。

## 作業 3：src/novui/workrepo.py（新規）

作品リポジトリに対する Controller の git 操作。すべて `gitinspect.run_git` を通すこと。commit を作る操作では `run_git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", ...)` のように名前とメールを `-c` で渡す（git config は変更しない）。

```python
TRAILER_KEYS: frozenset[str]   # "NovUI-Job", "NovUI-Edit", "Approval-Id"

class WorkRepoError(Exception)
class MergeConflict(WorkRepoError)   # 属性 paths: tuple[str, ...]
class CommitGuardError(WorkRepoError)  # 属性 paths: tuple[str, ...]

@dataclass(frozen=True)
class JobWorktree:
    branch: str          # ai/<chapter_id>/<job_id>
    path: Path           # <worktree_root>/<work_key>/<job_id>
    base_commit: str     # 作成時の main の commit（40桁）

@dataclass(frozen=True)
class MergePlan:
    kind: str            # "fast_forward" | "no_overlap" | "overlap"
    base_commit: str
    main_head: str
    main_changed: frozenset[str]
    branch_changed: frozenset[str]
    overlap: frozenset[str]

def format_message(subject: str, trailers: Sequence[tuple[str, str]]) -> str
def parse_trailers(message: str) -> dict[str, list[str]]
def check_commit_guard(paths: Iterable[str], trailers: Sequence[tuple[str, str]]) -> CheckResult
def head_commit(repo: Path, ref: str = "main") -> str
def create_job_worktree(repo: Path, worktree_root: Path, work_key: str, chapter_id: str, job_id: str) -> JobWorktree
def remove_job_worktree(repo: Path, jw: JobWorktree, *, delete_branch: bool) -> None
def commit_all(worktree: Path, subject: str, trailers: Sequence[tuple[str, str]], *, name: str, email: str) -> str | None
def changed_paths(repo: Path, a: str, b: str) -> frozenset[str]
def plan_merge(repo: Path, jw: JobWorktree) -> MergePlan
def merge_job_branch(repo: Path, jw: JobWorktree, subject: str, trailers: Sequence[tuple[str, str]],
                     *, name: str, email: str, extra_writes: Mapping[str, bytes] | None = None) -> str
def chapter_branches(repo: Path, chapter_id: str) -> list[str]
```

### 書式と検査

* `format_message`：`subject`、空行、trailer を1行ずつ `Key: value` の形で並べた文字列（trailer がなければ subject だけ）。subject が空・改行を含む場合は ValueError。trailer のキーが TRAILER_KEYS 以外、値が次に合わない場合は ValueError：`NovUI-Job` は job_id の pattern（`^job-[0-9]+$`）、`NovUI-Edit` は `human-typo` か `human-content`、`Approval-Id` は approval_id の pattern（`^A-[0-9]{4,}$`）。
* `parse_trailers`：メッセージの最後の段落（最後の空行より後）の各行のうち、`Key: value` の形で Key が TRAILER_KEYS に含まれるものを集める。
* `check_commit_guard`（§15.5 の 3）：name は `commit_guard`。paths のうち `is_protected` が True のものがあり、trailers に `NovUI-Edit` も `Approval-Id` もなければ FAIL（detail に該当パスを昇順）。それ以外は PASS。

### worktree

* `create_job_worktree`：
  - work_key は `^[a-z0-9][a-z0-9-]{0,63}$`、chapter_id・job_id は common の pattern に合わなければ ValueError。
  - repo は main を checkout しているリポジトリ（main worktree）とする。branch が `main` でなければ WorkRepoError。
  - path が既に存在する、または branch が既に存在する場合は WorkRepoError。
  - `<worktree_root>/<work_key>/` を（なければ）作り、`worktree add -b <branch> <path> main` を実行する。base_commit は実行直前の `main` の commit。
* `remove_job_worktree`：`worktree remove --force <path>`。delete_branch が True なら `branch -D <branch>`。
* `chapter_branches`：`for-each-ref --format=%(refname:short) refs/heads/ai/<chapter_id>/` の結果を昇順のリストで返す（§18.1 の章 lock の判定に使う。git の状態から判定するため、Controller の再起動後も正しい）。

### commit

* `commit_all`：worktree で `add -A` し、`diff --cached --name-only -z --no-renames` で変更されるパスを取り、check_commit_guard が FAIL なら `reset -q` で add を取り消して CommitGuardError。変更がなければ None を返す（commit しない）。あれば format_message のメッセージで commit し、新しい commit（40桁）を返す。

### merge（§17.2）

* `changed_paths(repo, a, b)`：`diff --name-only -z --no-renames a b` のパスの集合。
* `plan_merge`：
  - main_head が base_commit と同じ → kind `fast_forward`
  - そうでなければ main_changed = changed_paths(base_commit, main_head)、branch_changed = changed_paths(base_commit, jw.branch)、overlap = 両者の共通部分。overlap が空なら `no_overlap`、あれば `overlap`。
  - `fast_forward` のときも branch_changed は計算する（main_changed と overlap は空）。
* `merge_job_branch`：
  1. repo の現在の branch が main で、`status --porcelain --untracked-files=all` が空であること（違えば WorkRepoError）。
  2. plan_merge を行い、kind が `overlap` なら MergeConflict（paths=overlap）を送出し、何もしない（§17.2：自動 merge しない）。
  3. `merge --no-ff --no-commit <branch>` を実行する。失敗したら `merge --abort` を実行してから MergeConflict（paths は `diff --name-only --diff-filter=U` の結果）。
  4. extra_writes の各パス（`is_safe_relpath` で、repo の外に出ないこと）にバイト列を書き込み、`add` する（§7.1：章状態の変更を merge と同じ commit にするため）。
  5. 変更されるパス（`diff --cached --name-only -z --no-renames HEAD`）に check_commit_guard を行い、FAIL なら `merge --abort` してから CommitGuardError。
  6. format_message のメッセージで commit し、新しい commit を返す。
  - 途中で例外が起きた場合は、`merge --abort` を試みてから再送出する（repo を merge 途中の状態で残さない）。

## 作業 4：src/novui/locks.py（新規）

```python
class LockError(Exception)

class RunLock:
    """作品ごとの実行 lock（§18.1）。同時に実行できる AGY の Job は作品ごとに1つ。"""
    def acquire(self, work_key: str, job_id: str) -> bool     # 取得できれば True。既に他の job が持っていれば False
    def release(self, work_key: str, job_id: str) -> None     # 持ち主以外が release したら LockError
    def holder(self, work_key: str) -> str | None

def can_accept_write_job(repo: Path, chapter_id: str) -> bool   # chapter_branches が空なら True（§18.1 の章 lock）
```

* RunLock はプロセス内のメモリに持つ（Controller の再起動時、実行中の Job は STOPPED になるため、永続化しない）。複数スレッドから呼ばれても正しく動くよう、内部で `threading.Lock` を使う。

## 作業 5：schema の v0.5.2 対応（§6.4、§6.5.1）

### common.schema.json に追加

| 名前 | 定義 |
|---|---|
| foreshadow_id | string、pattern `^F[0-9]{3,}$` |
| scene_id | string、pattern `^S[0-9]+$` |
| knowledge_id | string、pattern `^K[0-9]{3,}$` |
| position | object：chapter（chapter_id）、scene（scene_id） |
| change | object：value（nonempty_string）、from（position）、reason（nonempty_string） |

### plan.schema.json・summary.schema.json の変更

* plan の scenes[].id を `$ref: scene_id` に、scenes[].foreshadowing の要素を `$ref: foreshadow_id` にする。
* summary の foreshadowing[].id を `$ref: foreshadow_id` にする。
* tests/fixtures/valid/plan.yaml・summary.yaml の該当値を `F001` 形式に直す。

### character.schema.json（新規。characters/<ID>.yaml）

**本 schema だけは、必須を id と name の2つに限る**（§6.4.1：構造化項目は任意）。それ以外の規則（additionalProperties false など）は 1-B と同じ。

| プロパティ | 型 |
|---|---|
| id | character_id（必須） |
| name | nonempty_string（必須） |
| personality | array of nonempty_string |
| behavior | array of nonempty_string |
| speech | object（すべて任意）：first_person（nonempty_string）、formality（enum `casual`、`polite`、`mixed`）、endings・habits・forbidden（array of nonempty_string） |
| address | object：default（nonempty_string、任意）。それ以外のキーは `^C[0-9]{3,}$` に一致するものだけ（patternProperties）で、値は nonempty_string、または object：default（nonempty_string、必須）、changes（array of change、任意） |
| relationships | array of object：with（character_id、必須）、state（nonempty_string、必須）、changes（array of change、任意） |
| exceptions | array of object：rule（nonempty_string）、from（position）、to（position）、reason（nonempty_string）。すべて必須 |
| knowledge | array of object：id（knowledge_id）、fact（nonempty_string）、source_chapter（chapter_id）。すべて必須 |
| notes | string |

### registry.schema.json（新規。foreshadowing/registry.yaml。トップレベルは配列）

各要素は object で、すべて必須：id（foreshadow_id）、name（nonempty_string）、status（enum `planned`、`active`、`resolved`、`cancelled`）、importance（enum `minor`、`normal`、`major`）、introduced・hints・developments（array of position）、planned_resolution（position \| null）、resolved（position \| null）、notes（string）。

### semantics.py に追加

| 関数 | 検査 |
|---|---|
| `check_character(doc) -> list[str]` | address のキー（default 以外）と relationships[].with が自分の id と異なる。knowledge の id が重複しない |
| `check_registry(doc) -> list[str]` | id が重複しない。status が resolved なら resolved が null でない。status が resolved 以外なら resolved が null |

SEMANTIC_CHECKS に `character`・`registry` を追加する。章・場面の順序に依存する検査（introduced より前の hints など）は Phase 2 で行うので、今回は実装しない。

### テスト

* tests/fixtures/valid/character.yaml・registry.yaml を追加する。character.yaml は §6.4.1 の例（address の文字列と object の両方、changes、exceptions を含む）を使う。
* test_schema.py の対象 schema の一覧に character・registry を加える（13件）。
* 「必須プロパティを1つ消すとエラー」のテストは、schema の `required` に挙がっているプロパティを1つ消す方法に改める（character は id か name を消す。配列の schema は要素の必須プロパティを消す）。
* 負例：plan の foreshadowing に `王家の紋章`、character の address に `X001` キー、character の speech.formality に `rude`、registry の status `done`、registry の resolved が null で status resolved（semantics）、character の address に自分の id（semantics）。

## 作業 6：テスト

上記の各作業に対応するテストを書く。特に次を必ず含める（すべて tmp_path の試験用リポジトリで行う）。

* test_protected.py：is_protected の例（作業2）と、不正なパスで ValueError。
* test_workrepo.py：
  - format_message・parse_trailers の往復。不正なキー・値・subject で ValueError。
  - check_commit_guard：保護対象の変更で trailer なし → FAIL、`NovUI-Edit: human-content` あり → PASS、`Approval-Id` あり → PASS、`NovUI-Job` だけ → FAIL、保護対象なし → PASS。
  - create_job_worktree：branch 名・path・base_commit が正しい。同じ job_id で2回目は WorkRepoError。main 以外を checkout したリポジトリで WorkRepoError。chapter_branches に現れ、can_accept_write_job が False になる。remove_job_worktree（delete_branch=True）の後は True に戻る。
  - commit_all：draft.md の変更で commit され、trailer が parse_trailers で読める。変更なしで None。`world/w.md` の変更を NovUI-Job だけで commit しようとすると CommitGuardError で、index が元に戻っている（`diff --cached` が空）。
  - plan_merge・merge_job_branch：
    - main が進んでいない → fast_forward、merge 後の main に draft.md の変更があり、merge commit に NovUI-Job の trailer がある。
    - main で別の章のファイルを変更 → no_overlap、merge が成功し両方の変更が main にある。
    - main で同じ draft.md を変更 → overlap、MergeConflict（paths に draft.md）、main の HEAD と作業ツリーが変わっていない。
    - extra_writes で `chapters/ch-001/chapter.yaml` を渡すと、merge commit にそのファイルが含まれる。
    - extra_writes で `world/w.md` を NovUI-Job だけで渡すと CommitGuardError で、main の HEAD が変わらず、merge 途中の状態（`.git/MERGE_HEAD`）が残っていない。
    - main の作業ツリーが汚れている場合に WorkRepoError。
* test_locks.py：RunLock の取得・競合・release・持ち主違いの LockError、作品が違えば独立。複数スレッドから同時に acquire して1つだけが True になる。
* test_config.py：git_name・git_email の既定値と上書き、空文字の ValueError。

## 作業の手順

1. 本指示と参照文書を読み、これから変更・作成するファイル、関数、テスト、実行するコマンドの一覧を具体的に説明する。
2. **Human の許可を得る。**
3. 作業1〜6を行い、`.venv/bin/python -m pytest -q` を全件 PASS にする（結合試験は skip される）。テストを通すために本指示の仕様を変えてはならない。仕様どおりに実装してテストが通らない場合は、止めて報告する。
4. `git status` で、変更が許可範囲だけであることを確認する。
5. 許可範囲のファイルだけを `git add` し、次のメッセージで commit する。
   ```text
   phase1: 1-D1 work repo git ops, merge rules, locks, v0.5.2 schemas
   ```
6. `git push origin phase1/controller`。「fetch first」等で拒否された場合に限り、`git pull --rebase origin phase1/controller` を1回だけ実行してから再度 push してよい（競合が出たら解決せずに止めて報告する）。
7. 報告して止まる。

## 報告の内容

1. 変更・作成したファイルの一覧と行数（`wc -l`）
2. `.venv/bin/python -m pytest -q` の最終出力
3. 本指示どおりにできなかった点、判断に迷った点（なければ「なし」）
4. `git status --short` と `git log -3 --oneline` の結果

報告のあと、作業を止めて次の指示を待ってください。

これから何をするかを説明し、Humanの許可を得てから作業を開始すること。
