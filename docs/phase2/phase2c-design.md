# Phase 2-C 設計判断記録：状態更新と最終承認

**作成：2026-10-07 / 基準：docs/novel-system-spec-v0.5.2.md §7・§8・§12・§15・§17、docs/phase2/phase2-plan.md §2・§3**
**対象コード：origin/phase2/chapter-flow 7046baa（2-B1／2-B2 完了、rebase 済み）**
**段階：2-C0（設計・指示書・Claude プロンプトの確定）。この文書はコードを含まない。既存の仕様書・コード・schema・テストは変更しない。**

実装担当（AGY）への作業指示は `docs/phase2/agy-instruction-2c.md`。この文書は「なぜそうするか」の記録で、指示書は「何を作るか」の記録である。両者が食い違う場合は、作業を止めて Human に報告する。

---

## 0. 結論の要約

AI_VALIDATED の章について、次の流れを Controller の操作だけで FINAL まで通す。

```text
AI_VALIDATED（作業ブランチ）
  → state-update    ：Claude が summary と state_patch を提案（Controller のデータに保存）
                      Job は WAITING_HUMAN（＝承認待ち。提案が正常に出来た場合の通常の終わり方）
  → approve-state   ：Human が承認。Controller が1つの commit で次を書く
                        承認記録、Patch を適用した設定ファイル、summary.yaml、chapter.yaml(HUMAN_APPROVED)
                      trailer：NovUI-Job、Approval-Id（STATE_PATCH_APPROVED）
  → final-approve   ：Human が最終承認。Controller が作業ブランチを main に merge
                      merge commit に chapter.yaml(FINAL)、trailer：NovUI-Job（全 Job）、Approval-Id（全承認）
                      作業ブランチと worktree を削除（FINAL_APPROVED）
```

既存コードは、次の最小の追加だけで足りる（既存ファイルの書き換えは cli.py のサブコマンド追加と、新しい schema を使う場合の semantics.py への登録だけ）。

* 新規モジュール：`statepatch.py`、`approvals.py`、`statecheck.py`、`proposals.py`、`stateupdate.py`、`finalize.py`、`requestflow.py`
* 新規 schema（Human の許可が要る）：`schemas/proposal.schema.json`
* 新規プロンプト：`prompts/claude/state_update.md`、`prompts/claude/state_patch.md`

---

## 1. 現状との差分（調査で分かった事実）

2-C の設計に影響する、2-B 実装と 2-B 指示書・仕様とのずれを先に記録する。**これらは 2-B を変更せずに 2-C 側で吸収する。**

| # | 事実 | 2-C への影響 |
|---|---|---|
| F1 | 2-B 指示書は `cycle.py`・`draftflow.py` を挙げているが、実装は `draftjob.py`（`run_chapter_draft_job`）、`validatejob.py`（`find_chapter_branch`、`_check_draft_consistency`、`_write_and_commit`）である | 2-C の指示書は実際の関数名に合わせる。`cycle.py` は作らない |
| F2 | `load_prompt_template` の引数名は `group`（`kind` ではない）。プロンプトは `prompts/<group>/<name>.md` | 指示書は `group` を使う |
| F3 | draft Job が【要確認】で WAITING_HUMAN になった場合、**chapter.yaml は DRAFTED にならない**（作業ブランチ上は PLAN_APPROVED のまま）。draft.md には【要確認：…】の文字がそのまま残る。requests.yaml には request だけが追記される | resolve_request の設計（§7）の前提 |
| F4 | validate は `_check_draft_consistency` で draft Job が COMPLETED であることを要求する。したがって WAITING_HUMAN の draft Job のままでは validate に進めない（2-B 指示書の `unresolved_requests` による ChapterError は実装されていない。実装は別の経路で同じ結果になる） | 2-C で draft Job を COMPLETED にする経路が必要 |
| F5 | CLI に `discard` が無い（2-B 指示書には有る）。【要確認】で止まった draft は、作業ブランチ・worktree が残り、chapter lock（`can_accept_write_job`）で draft の再実行もできない行き止まりになっている | 2-C3 で `discard` を追加する（§7） |
| F6 | validate の「判断」（CONTINUE 等）は chapter.yaml を作業ブランチの AI_VALIDATED にするところまで。state_update 以降は未実装 | 2-C の起点 |
| F7 | `check_commit_guard` は「保護対象を含む commit に `NovUI-Edit` か `Approval-Id` の trailer が1つでもあるか」しか見ない。承認記録との対応や、どのパスが承認されたかは見ない | §4 で、2-C 側に監査を追加する |
| F8 | `commit_all(worktree, …)` はすべての commit にこの guard をかける。`merge_job_branch` も merge commit に同じ guard をかける | 承認 commit・merge commit は既存 guard をそのまま通る |
| F9 | `jobrecord` の `approval_id` は1件（`A-NNNN` か null）。`job_type` の enum に `state_update`・`resolve_request` は既にある | 承認は「提案1件につき承認記録1件」とする（§3） |
| F10 | `state_patch` schema は `operations` が1件以上で、1つの Patch は target を1つしか持たない。「変更なし」を表せない。`summary` schema の `characters[].location` などは characters/ の schema に対応する項目が無い | 提案は「summary 1つ ＋ target ごとの Patch 複数」とし、変更なしは `test` のみの Patch で表す（§1.1） |
| F11 | `planjob.collect_plan_context` が読む前章の summary は main 上の `chapters/<前章>/summary.yaml`。main に入るのは FINAL の merge のとき | 次章の plan は前章が FINAL になってからでないと要約を読めない（§5.4、§8 の Human 判断事項） |
| F12 | `write_yaml_atomic` は `yaml.safe_dump` で書き直すため、YAML のコメント・書式は Patch 適用の際に失われる（読み込みは `NovuiYamlLoader`） | 受け入れる。base_hash は元のファイルのバイト列で取る（§2.3）。Human 判断事項に記載 |

### 1.1 F10 の扱い

* Claude の出力は1回に1つの JSON オブジェクト（1つの schema）だけである。よって state_update は、次の呼び出しを順に行う。
  1. `summary` を1回
  2. Controller が summary から決めた **Patch の対象ファイルごとに**、`state_patch` を1回ずつ
* 対象ファイルは Claude に決めさせず、Controller が summary から決める（§2.1）。Claude は与えられた target の Patch だけを返す。
* 対象に変更が不要・表せない場合、Claude は `operations` を `test` 1件だけにする。Controller は `test` だけの Patch を提案から除く。
* summary の `location`・`items_*`・`condition` は characters/ の schema に項目が無いので、**summary.yaml にだけ残り、設定ファイルには反映しない**。次章は summary.yaml から読む（§5.4）。

---

## 2. state_update Job

### 2.1 入力と Context

* 前提：作業ブランチが1本あり（`validatejob.find_chapter_branch`）、その chapter.yaml が AI_VALIDATED、worktree に未 commit の変更がなく、draft Job の記録が COMPLETED で Context の SHA-256 が worktree と一致する（`validatejob._check_draft_consistency` をそのまま使う）。
* Context は validate と同じ版にする（§9 の「同じ版の Context」と同じ考え方）。draft Job の記録の `context` に並ぶパスと、`chapters/<id>/draft.md`。順序も validate と同じ（`ctx_paths = [e["path"] for e in draft["context"]]` ＋ draft.md が無ければ追加）。review.yaml は Context に入れない（検証結果に引きずられず、本文だけから状態を書かせるため）。
* Claude は読み取り専用（§15.5 の4）。ファイルを読み書きさせず、Controller がプロンプトに埋め込む（`build_claude_prompt`）。
* 呼び出しの回数と順序：
  1. **summary**（プロンプト `prompts/claude/state_update.md`）
  2. **state_patch**（プロンプト `prompts/claude/state_patch.md`）を target ごとに。target は昇順。`characters/<ID>.yaml` を先、`foreshadowing/registry.yaml` を後にする。
* Patch の target（Controller が summary から決める）：
  * `characters/<ID>.yaml`：summary の `characters[]` のうち、`knowledge_added` か `relationship_changes` が空でない人物
  * `foreshadowing/registry.yaml`：summary の `foreshadowing[]` が1件以上ある場合
  * それ以外（`world_impacts`、`location`、`items_*`、`condition`）は Patch を作らない。世界設定の変更は `setting_change`（Human の指示）の領分で、2-C の範囲外である。
  * target のファイルが Context にない（draft の Context に入っていない人物など）場合は、その人物の Patch を作らず、summary の参照検査（§2.4）で止める。
* **ID の割り当て**：knowledge の ID は Controller が決める開始番号（作品の全 `characters/*.yaml` の K 番号の最大値＋1。直前の Patch で使った分は加算）をプロンプトで渡し、Claude はその番号から連番で使う。
* **pointer の指定**：registry.yaml は配列、characters/ の relationships も配列なので、Claude に添字を数えさせない。Controller が `F001 → /0` のような対応表をプロンプトに渡す。それでも `test` を先頭に置かせ、添字のずれは dry-run（§2.4）で必ず検出する。

### 2.2 Claude 出力の検証

出力ごとに既存の経路をそのまま使う。

* `run_claude_call`（`expected_type` は `summary`／`state_patch`、`max_retries=2`）。封筒・type・schema・semantics の検証と再要求（§10）は `claude_output.parse_claude_output` が行う。
* 1つの state_update Job の中で、同じ `job_id` を使う（validate が integrity と writing で同じ `actual_job_id` を使うのと同じ）。ログは `log_name=f"{job_id}-summary"`、`f"{job_id}-patch-{n}"`。
* Job 記録の `cli.actual_model` は最後の呼び出しの値（validate と同じ作り）。`checks` に呼び出しごとの結果（`claude_summary`、`claude_state_patch_<n>`）を追加する。

### 2.3 hash の定義

* `base_hash`：Patch の target ファイルの、作業ブランチ worktree 上の**生のバイト列**の SHA-256。形式は `sha256:<16進64桁>`。Context の `ContextEntry.sha256`（`prompt._collect_context`）と同じ計算で、Context に入れた版と一致する。
* `patch_sha256`（個々の Patch）：Patch の dict（`type`、`target`、`base_hash`、`operations`、`reason`）を正規 JSON にした UTF-8 のバイト列の SHA-256。正規 JSON は `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`。`operations` の配列の順序は変えない（順序に意味があるため）。
* `patch_set_sha256`（承認記録の `patch_sha256` に入れる値）：提案に含まれるすべての Patch の dict を **target の昇順に並べた配列**を、同じ正規 JSON にして SHA-256 を取る。Patch が0件の提案には作らない。
* Claude が返した `base_hash` は信用しない。Controller が計算した値と比べ、**Claude の値と違えば Controller の値で置き換え、`checks` に `base_hash_corrected`（WARNING）を残す**（hash の転記は機械的な作業で、置き換えても Claude が見た版の hash と一致するため）。

### 2.4 Controller の検査（Claude の出力を受けた後）

すべて純粋な関数として `statecheck.py` に置く（2-C1）。1つでも失敗すれば Job は FAILED（再要求はしない。§18.3 の「schema 不適合」の再要求は `run_claude_call` の中で済んでいる）。

1. **summary_refs**：`summary.chapter_id` が対象の章と一致する。`characters[].id` は重複せず、すべて Context に `characters/<id>.yaml` がある人物。`foreshadowing[].id` はすべて `foreshadowing/registry.yaml` にある。文字列に `{{` か `}}` を含まない（Patch 用プロンプトに埋め込むため）。
2. **patch_target**：Patch の target が Controller の決めた対象と一致し、重複がない。
3. **patch_policy**：Patch の operations が許可された形（§3.4 の表）に収まる。
4. **patch_apply**：dry-run。target ファイルを読み込み、Patch を適用し（失敗なら FAILED）、結果を target の schema（`character`／`registry`）と semantics（`check_character`／`check_registry`）で検証する。適用結果が不正なら FAILED。
5. **summary_patch_consistency**（WARNING。Job は止めず、Human への表示に使う）：summary の `knowledge_added` の各文が、同じ人物の Patch の `add /knowledge/-` の `fact` と一致するか。一致しないものを details に列挙する。

### 2.5 提案の保存場所と形式

* 保存先は作品リポジトリの**外**（Controller のデータ）。`<data_dir>/works/<work_key>/proposals/<chapter_id>/<job_id>.yaml`。保護対象でも Canonical でもない（§15.3 の「未承認の提案は Controller の作品別データ」）。`jobs_dir` と同じ階層に置く。
* 形式（`schemas/proposal.schema.json`。**新規 schema。Human の許可が必要。許可されない場合は、提案ファイルの検証を summary・state_patch の既存 schema の個別検証に限る**）：

      type: state_proposal
      job_id: job-12                 # state_update Job
      chapter_id: ch-001
      branch: ai/ch-001/job-3
      branch_head: <40桁>            # 提案時点の作業ブランチの HEAD
      status: pending                # pending | approved | rejected | superseded | invalid
      status_reason: null
      created_at: ...
      decided_at: null
      approval_id: null
      summary: {…}                   # summary schema に適合
      patches:                       # target の昇順
        - patch: {…}                 # state_patch schema に適合
          patch_sha256: sha256:...
      patch_set_sha256: sha256:...   # patches が空なら null

### 2.6 Job の状態と遷移

既存の `states.py` の遷移だけを使う（変更しない）。

| 場面 | 遷移 |
|---|---|
| 開始 | QUEUED → LOCK_ACQUIRED → RUNNING（実行 lock は取らない。Claude の Job は対象外：§18.1。validate と同じ） |
| Claude 呼び出し終了 | RUNNING → CLI_EXITED → CHECKING |
| timeout | RUNNING → TIMED_OUT → FAILED |
| Claude の出力が再要求を尽くしても不適合／Claude が利用不能／§2.4 の検査に失敗 | CHECKING → CHECK_FAILED → FAILED |
| 提案が正常に出来た | CHECKING → **NEEDS_HUMAN_INPUT** → **WAITING_HUMAN**（reason：`awaiting approval of state update proposal`） |
| Human が承認（approve-state） | WAITING_HUMAN → HUMAN_CONTINUE → COMPLETED（`approval_id` を記録） |
| Human が却下（reject-state） | WAITING_HUMAN → HUMAN_CANCEL → CANCELLED |
| 提案が無効になった（§2.7） | WAITING_HUMAN → HUMAN_CANCEL → CANCELLED（reason に無効の理由） |

* `CHECK_PASSED`（→ VALIDATING）は使わない。承認待ちは「検証の WARNING」ではなく、この Job の通常の終わり方だからである。
* Claude が利用できない場合に検証を省略する経路（§11.6）は state_update には無い。summary が無いと次章の Context が作れないため、Job は FAILED とし、Human は再実行する。
* 実行時の前提が満たされない場合（章が AI_VALIDATED でない、作業ブランチが無い等）は、Job 記録を作らず `ChapterError` を送出する（validate・draft と同じ）。

### 2.7 WAITING_HUMAN 条件・再提案条件・superseded

* WAITING_HUMAN になるのは、§2.6 の「提案が正常に出来た」場合だけ。
* **提案が invalid になる条件**（approve-state の時点で検査し、1つでも当てはまれば、提案を `invalid`、Job を CANCELLED にして終了する。Human は再提案を実行する）：
  * 作業ブランチの HEAD が `branch_head` と違う
  * target ファイルの現在の SHA-256 が Patch の `base_hash` と違う（§12.1：適用しない）
  * §2.4 の 3・4（policy・dry-run）を再実行して失敗する
  * 章の状態が AI_VALIDATED でない、worktree に未 commit の変更がある
* **superseded**：同じ章に pending の提案がある状態で `state-update --replace` を実行した場合、新しい Job が提案を保存すると同時に、古い提案を `superseded`、古い Job を HUMAN_CANCEL（reason：`superseded by <新 job_id>`）で CANCELLED にする。`--replace` なしで pending の提案がある場合は ChapterError（validate の「先に CANCEL する」と同じ運用）。
* 却下（reject-state）された提案は `rejected`。再提案は新しい state-update で行う。Human のコメントを次の提案に渡す経路は 2-C には無い（Human 判断事項）。

---

## 3. JSON Patch・承認

### 3.1 JSON Patch の実装方針（jsonpatch 依存は追加しない）

* 仕様が要求するのは add・remove・replace・test の4つだけ（§12.1、`state_patch.schema.json` の enum もこの4つ）。`jsonpatch` ライブラリは move・copy も受け付けるため、仕様で禁じる操作を別に弾く層が要り、依存を増やす利点が小さい。現在の依存は PyYAML と jsonschema だけで、プロジェクトは依存を絞っている。
* 自前の実装（`statepatch.py`）で、RFC 6901（JSON Pointer）・RFC 6902 のうち次の範囲を実装する。pyproject.toml は変更しない。
  * Pointer：`~0`・`~1` のエスケープ、空の pointer（文書全体）、配列の `-`、配列の添字は先頭ゼロなしの10進数
  * add：オブジェクトの既存キーは置換、配列は挿入（`-` は末尾）、範囲外はエラー
  * remove：存在しないパスはエラー
  * replace：存在しないパスはエラー
  * test：値の等価は JSON の等価（型を区別。`true` と `1` は等しくない）、不一致はエラー
  * 適用は**全か無か**：入力を変更せず、深いコピーに適用し、1つでも失敗すれば何も返さない
* 配列の replace（リスト全体）の禁止などの運用規則は、適用器ではなく state_update の policy（§3.4）で行う。適用器は RFC の4操作を汎用に実装する。
* これは Human への確認事項として報告する（§8 の 1）。

### 3.2 承認記録（A-NNNN）

* **単位**：1つの提案につき承認記録1件。Patch が複数あっても承認は1つで、Human は提案を丸ごと承認する（一部だけの承認は無い）。Patch が0件の提案（設定ファイルの変更が無い章）には承認記録を作らない。保護対象に変更が無い commit は `Approval-Id` を要しないためである。
* **採番**：`A-` ＋ 4桁以上のゼロ詰め連番（`approval.schema.json` の `^A-[0-9]{4,}$`）。作品ごとに Controller のデータ `<data_dir>/works/<work_key>/approvals.next`（次に使う番号）で管理し、`max(カウンタ, main と作業ブランチの .novui/approvals/ にある最大番号 ＋ 1)` を使って、カウンタを進めてから書く。**番号は再利用しない**（commit に失敗しても欠番にする）。章ごとの作業ブランチが並行して存在しても、番号は作品内で重ならない（重なると merge が `.novui/approvals/` の add/add で overlap になるため）。
* **記録の内容**（`approval.schema.json` と `semantics.check_approval` に適合）：

      approval_id: A-0001
      source: proposal
      instruction_id: null
      job_id: job-12                 # state_update Job
      patch_sha256: sha256:...       # patch_set_sha256
      targets: [characters/C001.yaml, foreshadowing/registry.yaml]   # 昇順
      approved_by: human
      approved_at: 2026-10-07T12:00:00+09:00

* base_hash は承認記録には入れない（schema を変えない）。Patch の hash に base_hash が含まれるため、承認は「この base の、この Patch」に対するものになる。
* **適用の条件**（§15.4）：Controller は、提案の `patch_set_sha256` を再計算して承認記録の `patch_sha256` と一致すること、各 Patch の target が `targets` に含まれることを確認してから適用する。
* **1回限りの使用**：承認記録 `A-NNNN` は次のいずれかの場合は「使用済み」とし、再利用を拒否する。(a) main または作業ブランチに `.novui/approvals/A-NNNN.yaml` が既にある、(b) 提案の `status` が pending でない、(c) 番号がカウンタ以下で既に払い出し済み（払い出しは approve-state の途中でしか起きず、途中で失敗した番号は再利用しない）。

### 3.3 承認済み Patch の commit（approve-state）

作業ブランチの worktree で、Controller が次を**1つの commit**にする。

1. 前提検査（§2.7 の invalid 条件）
2. 各 target に Patch を適用（dry-run と同じ関数）して `write_yaml_atomic`
3. `.novui/approvals/<approval_id>.yaml` を書く（Patch が1件以上のとき）
4. `chapters/<id>/summary.yaml` を書く（§5）
5. `chapters/<id>/chapter.yaml` を `STATE_PATCH_APPROVED` で HUMAN_APPROVED にし、`last_job` を state_update の job_id にする
6. `check_allowed_paths(get_changes(worktree), 許可集合)` で、変更が「承認記録・Patch の target・summary.yaml・chapter.yaml」だけであることを確認する（許可集合は提案から計算する。それ以外が1つでも変わっていれば commit せず、書いたファイルを元に戻す。`validatejob._restore_paths` 相当）
7. `commit_all(worktree, subject, trailers, name=…, email=…)`。subject は `state update approved {chapter_id} by {job_id}`、trailers は `[("NovUI-Job", job_id)]` ＋ Patch が1件以上なら `("Approval-Id", approval_id)`
8. Job 記録を HUMAN_CONTINUE → COMPLETED、`approval_id` を記録。提案を `approved`（`approval_id`・`decided_at`）にする。

* 1〜7 のどこかで失敗した場合、作業ブランチは元の HEAD のまま、worktree は未 commit の変更なし、提案は pending のまま（カウンタだけ進んでいる）。
* 7 と 8 の間で落ちた場合（commit はあるが Job 記録・提案が更新されていない）の復旧：次の approve-state または show が、作業ブランチの HEAD の commit の trailer（`parse_trailers`）に、この Job の `NovUI-Job` と `Approval-Id` があるのを見つけたら、8 だけを完了させる。

### 3.4 state_update の Patch の policy（`statecheck.check_patch_policy`）

Claude の提案が書いてよい範囲を限る。**これは state_update だけの規則であり、`statepatch.py` の適用器は4操作を汎用に扱う。**

共通：

* target は `characters/<ID>.yaml`（ID は `C[0-9]{3,}`）か `foreshadowing/registry.yaml` に限る。
* op は `add`・`replace`・`test` に限る。**remove は使えない**（設定の削除は Human の `setting_change` の領分）。
* 操作の value に `null` 以外の空文字列・空配列で既存のリストを置き換える `replace` は使えない（§12.1：リスト全体の replace 禁止）。

`characters/<ID>.yaml`：

| op | path | 条件 |
|---|---|---|
| add | `/knowledge/-` | value は `{id, fact, source_chapter}`。`source_chapter` は対象の章。id は Controller が割り当てた番号の範囲で、同じ Patch の中で重複しない |
| add | `/relationships/<i>/changes/-` | 直前までの操作に `test /relationships/<i>/with` がある。value は `common.change`（value・from・reason）。`from.chapter` は対象の章、`from.scene` は plan の場面 |
| add | `/relationships/-` | value は `{with, state}`。`with` は summary の `relationship_changes` の相手 |
| add | `/address/<ID2>/changes/-` | `address.<ID2>` が object 形式であること（dry-run で検査）。value は `change` |
| replace | `/address/<ID2>` | 元の値が文字列のときだけ。直前に `test /address/<ID2>` がある。value は `{default: <元の文字列>, changes: [...]}` |
| add | `/address/<ID2>` | `address.<ID2>` が無いときだけ。value は文字列 |
| test | 上記に付随する pointer | 常に可 |

* 性格・行動・口調（personality・behavior・speech）・exceptions・name・id・notes は、Claude の提案では変更できない（§6.4.1：AI が本文の変化を見て設定を書き換えてはならない。Human の指示（setting_change）で行う）。

`foreshadowing/registry.yaml`：

* すべての Patch は、先頭の操作が `test /<i>/id`（値は対象の伏線 ID）であること。以降の操作の pointer は同じ `/<i>/` で始まること（1つの Patch で複数の伏線を扱う場合は、伏線ごとに先頭に `test` を置く）。
* 許可：`add /<i>/introduced/-`、`add /<i>/hints/-`、`add /<i>/developments/-`（value は `position`。`chapter` は対象の章、`scene` は plan の場面）、`replace /<i>/status`、`replace /<i>/resolved`。
* `status` を `resolved` にする場合は、同じ Patch で `resolved` を位置に `replace` すること（`check_registry` が検査する）。`cancelled` にする Patch は書けない（取りやめは Human が決める）。
* `planned_resolution` の変更は書けない（「予定の変更や意図的な未回収かどうかは Human が判断する」：prompts/claude/integrity_review.md の方針と同じ。仕様 §12.2 は変更の提案を許すので、これは Human 判断事項に挙げる）。

「変更なし」：`operations` が `test` だけの Patch。Controller は提案から除く。

---

## 4. 保護対象ファイル：§15.5 の2 と「承認した Patch を作業ブランチに commit する」の関係

### 4.1 矛盾の整理

* §15.5 の2：「作業ブランチに保護対象の変更が1つでもあれば、Job を FAILED とする（§8.3）」
* §8.3：AGY の Job の終了後の変更検査。目的は「AI による改変を検出する」こと（§8.3 末尾：「Controller 自身の誤りと外部からの変更を検出するための検査」）。
* §15.3：Human の承認を経た提案（state_update）の反映は Controller の書込みで、承認記録が記録になる。§8.1：state_update の書込み許可は「Patch の target、対象章の summary.yaml」。

つまり仕様自身が、state_update の承認後に Controller が保護対象を書くことを許している。矛盾に見えるのは、§15.5 の2 が「Job の変更検査」と「Controller が Human の承認を受けて書く commit」を区別していないためである。

### 4.2 区別の方法（仕様を変えず、既存の commit guard を再利用する）

**書込みの主体と段階で区別する。**

| 段階 | 主体 | 作業ブランチの保護対象 | 検査 |
|---|---|---|---|
| AGY／Claude の出力の取り込み（draft、validate、state_update の提案） | AI の出力を Controller が書く | **変更があれば FAILED**（既存の `check_allowed_paths`。変更なし。state_update の提案段階は作業ブランチに何も書かないので、worktree の変更は0件でなければならない） | §8.3 のとおり（既存） |
| 承認の適用（approve-state） | Human の承認を受けた Controller | 変更してよい。ただし承認記録・Patch の target に限る | ①許可集合との照合（§3.3 の6）、②既存の commit guard（`Approval-Id` trailer が必要） |
| 最終承認の merge | Human の承認を受けた Controller | 作業ブランチ全体を監査してから merge | ③保護対象変更の監査（§4.3）、④merge commit に既存の commit guard |

* §15.5 の2 は、**「AI が書いた内容が保護対象に及んでいないか」の検査**と読み、上表の第1段に適用する。承認後の commit は第2段で、Job の出力ではなく Controller の操作である。この読み方は仕様書の文言を変えるものではないが、曖昧さが残るので v0.6 で明文化する（§8 の 5）。
* 第1段の検査は、**「Job の実行前の作業ブランチ HEAD」と「実行後の worktree」の差**で行う。承認 commit が既にある作業ブランチ（HUMAN_APPROVED 以降）に対して、再度 AI の Job を実行する場合（2-D の再 validate など）、承認 commit は「実行前の HEAD」に含まれるので FAILED の対象にならない。

### 4.3 既存 commit guard の弱さを補う監査（`finalize.audit_protected_changes`、2-C3）

`check_commit_guard` は trailer の有無しか見ない（F7）。最終承認の前に、Controller が作業ブランチの `base_commit..branch` の commit を1つずつ調べる。

1. その commit が保護対象のパスを変更しているなら、`Approval-Id` trailer が1件以上あること（`NovUI-Edit` だけでは通さない）。
2. trailer の各 `Approval-Id` について、`.novui/approvals/<id>.yaml` が**同じ commit で追加**されていること。
3. 記録を `approval` schema と semantics で検証でき、`approval_id` が trailer と一致すること。
4. その commit が変更した保護対象のパス（`.novui/approvals/` 以下を除く）がすべて、同じ commit の承認記録の `targets` に含まれること。
5. 承認記録の `approval_id` が、main の `.novui/approvals/` に既にないこと（使用済みでない）。
6. 保護対象を変更しない commit に `Approval-Id` が付いていてもよい（成否に影響しない）が、承認記録がないものは違反とする。

違反が1つでもあれば、merge しない（`FinalizeError`。chapter・Job の状態は変えない）。

### 4.4 変更しないもの

* `check_commit_guard`、`commit_all`、`merge_job_branch`、`plan_merge`、`remove_job_worktree`、`format_message`、`parse_trailers`、`TRAILER_KEYS`、`protected.py` は変更しない。
* `jobrunner` の変更検査（`check_allowed_paths`）の許可集合は変更しない。

---

## 5. summary.yaml

### 5.1 Claude の提案との関係

* summary.yaml の中身は、Claude が提案した summary（`summary` schema に適合）に**そのまま**する。Controller は項目を加えず、書き換えもしない（`base_hash` のような Controller の補正は Patch だけ）。
* Human は summary を編集できない（§12.2）。誤りがある場合は、reject-state（または `--replace`）で再提案させる。
* Human が承認する対象は、画面上は「summary ＋ Patch 一式」の1組である。

### 5.2 検証と保存のタイミング

* 検証：state_update Job の中で `summary` schema（`parse_claude_output`）と §2.4 の summary_refs。approve-state の時点でもう一度 schema を検証してから書く。
* 保存：approve-state の commit（§3.3）で作業ブランチに書く（`chapters/<id>/summary.yaml`）。**承認前は、作業ブランチにも main にも書かない**（提案は Controller のデータだけ）。
* summary.yaml は保護対象ではない（`chapters/` は §15.2 に無い）ので、guard の対象外である。

### 5.3 commit の対象

* approve-state の1つの commit に、承認記録・Patch を適用した設定ファイル・summary.yaml・chapter.yaml を入れる（§3.3）。
* 提案から FINAL までの間、summary.yaml は作業ブランチにあるだけで、main に入るのは最終承認の merge のとき。

### 5.4 次章の Context への利用

* `planjob.collect_plan_context` は、main の `chapters/<直前章>/summary.yaml` を読む（既存）。`draftjob.collect_draft_context` も plan の `context.past_summaries` の各章の summary.yaml を読む。
* したがって、**次章の plan・draft が前章の要約を読めるのは、前章が FINAL で main に merge された後**である（F11）。2-C は planjob・draftjob を変更しない。前章が FINAL でない状態で次章の plan を作ることを禁止するかどうかは Human 判断事項（§8 の 7）。
* 設定ファイル（characters/・registry.yaml）の Patch の結果も、main に入るのは FINAL の merge のとき。

---

## 6. 最終承認（final-approve）

### 6.1 HUMAN_APPROVED → FINAL の条件

すべてを満たす場合だけ実行する。

1. 作業ブランチが1本で、その chapter.yaml の state が HUMAN_APPROVED。
2. main の chapter.yaml と作業ブランチの chapter.yaml の `review_required` がどちらも false（§7.1「FINAL は review_required が true の間は付与しない」。`states.next_chapter_state(HUMAN_APPROVED, FINAL_APPROVED, review_required=…)` が InvalidTransition を送出する）。`review_required` を false に戻す操作（「確認済み」。§7.3）は2-C の範囲外で、2-C の流れでは true にならない（Human 判断事項 §8 の 6）。
3. worktree に未 commit の変更がなく、作業ブランチの HEAD の commit が approve-state のもの（chapter.yaml が HUMAN_APPROVED）。
4. 保護対象変更の監査（§4.3）に通る。
5. main が `main` ブランチで、working tree が clean（`merge_job_branch` の前提。`chapters.ensure_main_ready`）。

### 6.2 main が進んでいる場合・conflict の場合

`workrepo.plan_merge` と `merge_job_branch` の既存の動作をそのまま使う。

| `plan_merge.kind` | 動作 |
|---|---|
| fast_forward（main が基準 commit のまま） | `merge --no-ff` で merge commit を作る |
| no_overlap（main が進んだが、作業ブランチの変更パスと重ならない） | 同様に merge（§17.2：main に追従させて merge してよい） |
| overlap（重なる） | `MergeConflict`。**自動 merge しない**。main・作業ブランチ・chapter.yaml は変えない |

* merge の途中で conflict が出た場合も `merge --abort` で main を元に戻す（`merge_job_branch` が行う）。
* **WAITING_HUMAN の扱い**：final-approve は Job ではないので Job 記録を持たない。overlap・conflict・commit guard の失敗は、CLI が**終了コード 3（Human の判断待ち）**と重なったパス・理由を表示して終わる。章は HUMAN_APPROVED、作業ブランチ・worktree は残る。Human は main 側の原因を取り除くか、`discard` で作業ブランチを捨てる。Job の WAITING_HUMAN と同じ位置づけの「章の待ち」である（`job_type` に最終承認用の種類が無く、schema を変えないため。Human 判断事項 §8 の 3）。
* 作品に `origin` があっても push はしない（§17.3 の push は Phase 3 以降。2-C の範囲外）。

### 6.3 merge commit

* subject：`final approve {chapter_id}`
* 作業ブランチの `base_commit..branch` の全 commit の message を `parse_trailers` で読み、次を commit の古い順に、重複を除いて集める。
  * `NovUI-Job`：すべて（draft、validate、state_update など、この章のサイクルで commit した Job。plan の Job は main に commit されているので含まれない）
  * `Approval-Id`：すべて
* merge commit の trailers：集めた `NovUI-Job` をすべて、続けて `Approval-Id` をすべて。`format_message` が各値を検証する。
* `extra_writes`：`chapters/<id>/chapter.yaml` を FINAL にしたもの（`next_chapter_state(HUMAN_APPROVED, FINAL_APPROVED, review_required=False)`、`last_job` は作業ブランチの値のまま。schema・semantics を検証してから `dumps_yaml` のバイト列で渡す）。これは `merge_job_branch` の `extra_writes` の既存の使い方である。
* commit guard：merge commit は保護対象（characters/・registry.yaml・.novui/）の変更を含むことがあるが、`Approval-Id` trailer があるので既存の guard を通る。承認が無い章（Patch が0件）は保護対象の変更が無いので通る。

### 6.4 後始末

* merge の成功後に `remove_job_worktree(repo, jw, delete_branch=True)`。`jw` は draft Job の記録から作る（`JobWorktree(branch, Path(worktree), base_commit)`。`validatejob._check_draft_consistency` が読むのと同じ記録）。
* merge は成功したが worktree・branch の削除に失敗した場合：main は FINAL で確定している。再度 final-approve を実行すると、作業ブランチの先端が main の祖先で、main の chapter.yaml が FINAL なら、後始末だけをやり直して成功とする（作業ブランチが残ると chapter lock が解けず、次の書込み Job が受け付けられないため）。
* Controller のデータ（Job 記録、提案、承認カウンタ）は消さない。

---

## 7. resolve_request（【要確認】）

### 7.1 現状（F3〜F5）

【要確認】が出た draft Job は WAITING_HUMAN で止まり、chapter.yaml は DRAFTED にならず、draft.md に【要確認：…】の文字が残る。validate は draft Job が COMPLETED でないと実行できず、`discard` も無い。resolve_request が無いと先へ進めない。

### 7.2 request・resolution の形式

既存の `requests.schema.json` をそのまま使う。

* request：`{type: request, job_id, chapter_id, kind: undefined_setting|conflict|question, target: str|null, message}`。draft Job が【要確認】から作る（kind は `undefined_setting`、target は null）。
* resolution：`{type: resolution, request_index, decision, resolved_at}`。`request_index` は **requests.yaml 全体の配列の添字**（0始まり）で、type が request の要素を指す。`decision` は Human の判断の文（空でない）。
* 解決済みの request：その添字を指す resolution が1つ以上ある request。未解決：ないもの。

### 7.3 resolve_request の責任範囲

* job_type `resolve_request` の Job（§8.1：Controller が requests.yaml に**追記のみ**）。AI は使わない。
* 作業ブランチの `chapters/<id>/requests.yaml` に resolution を1件追記し、`NovUI-Job` trailer で commit する。変更を許すのは requests.yaml だけで、追記以外（既存の要素の変更・削除）を検出したら commit しない。章の状態は変えない。
* 指定された添字が request でない、範囲外、既に解決済みの場合は ChapterError。
* Job の状態：QUEUED → LOCK_ACQUIRED → RUNNING → CLI_EXITED → CHECKING → CHECK_PASSED(needs_validation=False) → COMPLETED。`history` の reason に `request <添字>: <decision>` を残す（resolution の監査記録）。
* **resolve_request は、設定ファイルも本文も変更しない。** resolution は「Human がこの要求をどう判断したか」の記録である。undefined_setting の判断を設定として残すのは、Human の直接編集（`NovUI-Edit: human-content`、main への commit）の役割で、2-C の範囲外である。

### 7.4 解決した後にどの Job を再開するか

* draft.md に【要確認：…】の文字が残っているので、**そのままでは validate・state_update に進めない**。本文の書換えは範囲編集（range_edit。2-D）の領分で、2-C では行わない。
* したがって、全 request が解決された後の道は、**draft の再実行**である。`redraft`（2-C3）：
  1. 全 request が解決済みであること、作業ブランチが1本あること、draft Job が WAITING_HUMAN で、その chapter.yaml が PLAN_APPROVED（draft Job が COMPLETED でないこと）を確認する。
  2. 作業ブランチと worktree を削除する（`discard` と同じ。`remove_job_worktree(delete_branch=True)`）。
  3. 古い draft Job を HUMAN_CANCEL（reason：`redrafted by <新 job_id>`）で CANCELLED にする。
  4. 新しい draft Job を実行する。AGY の instruction は `draftjob.build_draft_instruction` の戻り値に、Human の判断の節（各 request の message と decision）を**追記した**ものにする。追記の文言は §7.5。`draftjob.py` は変更せず、`run_chapter_draft_job` と同じ手順を `requestflow.py` に実装する（`collect_draft_context`・`build_draft_instruction`・`run_draft_job` を呼ぶ）。
* Human が先に設定ファイルを main に追加した場合は、再実行の Context に自動的に入るので、判断の節は冗長だが無害である。設定に残さず判断の節だけで再実行した場合、その判断は**この章の本文にだけ効く**（次章以降には引き継がれない）。この注意を CLI の出力に表示する。
* 新しい draft がまた【要確認】を出した場合は、同じ手順を繰り返す。古い requests.yaml は作業ブランチとともに消える（Controller のデータには残らない。判断の記録は resolve_request の Job 記録の `history` にある）。
* `draft` コマンド（既存）との関係：`draft` は作業ブランチが無い PLAN_APPROVED の章でだけ動く（既存）。`redraft` は、作業ブランチを捨てて、そのうえで判断の節つきで draft を実行するための別コマンドである。`discard` を単独で実行して `draft` をやり直すこともできる（判断の節なし）。

### 7.5 判断の節の文言（Claude が書く文面ではなく、Controller が AGY の instruction に追記する定型）

    ---
    【Human の判断】
    前回の本文には次の確認事項がありました。Human は次のとおり判断しました。この判断に従って書いてください。判断にないことは、これまでどおり推測で決めず【要確認：…】と書いてください。

    * 確認事項：<message>
      判断：<decision>

### 7.6 discard

* `discard`（2-C3）：作業ブランチと worktree を削除する。main は変えず、章は main 上の状態（PLAN_APPROVED）に戻る。HUMAN_APPROVED 以降の作業ブランチの discard は、承認済みの設定変更を捨てることになるので、**Human が `--force` を付けた場合だけ**許可する。
* discard の対象の Job 記録（WAITING_HUMAN の draft・validate・state_update）は、HUMAN_CANCEL で CANCELLED にする。
* §7.2 の「STOPPED・FAILED・CANCELLED の Job の作業ブランチは調査のため一定期間残し、Human の操作で削除する」に従い、discard は Human の操作のときだけ実行する（自動では消さない）。

---

## 8. Human の判断が必要な事項（2-C1 の開始までに）

| # | 事項 | 推奨 |
|---|---|---|
| 1 | JSON Patch を自前実装し、`jsonpatch` 依存を追加しない（§3.1）ことの承認 | 自前実装（依存を増やさない） |
| 2 | `schemas/proposal.schema.json` の新規追加（既存 schema は変更しない）の許可 | 許可（Controller の読み書きは検証する：§6.2 の方針） |
| 3 | final-approve の待ち（overlap・conflict）が Job 記録を持たず終了コード 3 で表すことの承認（`job_type` を増やさない） | 承認 |
| 4 | 3つ目のプロンプト `prompts/claude/state_patch.md` の追加（要求は state_update.md の1つだが、Claude の出力は1回に1つの schema のため、summary 用と state_patch 用の2つが要る） | 承認 |
| 5 | §15.5 の2 の読み方（§4.2）の承認と、v0.6 での明文化 | 承認 |
| 6 | `review_required` を「確認済み」にする操作（§7.3）を 2-C に含めるか。含めない場合、FINAL の guard だけを実装する | 含めない（review_required が true になる経路自体が Phase 3 以降） |
| 7 | 前章が FINAL でない状態で次章の plan を作ることを禁止するか（F11） | 2-C では禁止しない。show に警告を出すのは 2-D |
| 8 | Claude の提案で変えてよい範囲（§3.4）。特に `planned_resolution` の変更を禁じること、address・relationships の変更を許すこと | 表のとおり（最も保守的） |
| 9 | YAML のコメント・書式が Patch 適用で失われること（F12） | 受け入れる（テンプレートにコメントを書かない運用） |
| 10 | Claude が永続的に利用できない場合に state_update を省略する経路は設けない（§2.6） | 設けない |
| 11 | 2-C の結合試験の実行場所。AGY・Claude・podman が必要で、cloud のセッションでは実行できない | Human の手元のマシン（K2/K3 と同じ） |

## 9. 既存仕様との矛盾・曖昧さが残る箇所

1. **§15.5 の2 と §8.1・§15.3**：「作業ブランチに保護対象の変更があれば FAILED」と「state_update の承認後に Controller が Patch の target を書く」。§4.2 の読み方で運用するが、文言は曖昧なまま。v0.6 で「AI の出力による変更」と明記することを提案する。
2. **§17.2「Human の承認を得てから Controller が main に merge」と final-approve の待ち状態**：Job でない待ちの表現（§6.2）。
3. **§7.1 の「PLAN_APPROVED → DRAFTED（執筆 Job が merge 可能な状態で完了）」と【要確認】の draft**：実装は【要確認】の draft を DRAFTED にしない（F3）。仕様は「merge 可能な状態」と書くので矛盾はしないが、2-B 指示書の「markers がある場合も DRAFTED にする」とは違う。2-C は実装に合わせる（§7）。
4. **§12.2 の「summary.yaml の伏線は ID で記録し、state_patch で registry.yaml の… planned_resolution… の変更を提案できる」と §3.4 の禁止**：保守的に禁じる（Human 判断事項 8）。
5. **§18.1 の章 lock と redraft／discard**：作業ブランチを捨てるのは章 lock の解除である。仕様に「Human が lock を解除する操作」が明記されていない（§7.2 の末尾の「Human の操作で削除する」が根拠）。
6. **phase2-plan §3「2-C：requests の解決（resolve_request）」と 2-B 結合試験の skip**：解決の後の続き（再実行）が仕様にない。§7.4 を設計として追加した。
7. **2-D への申し送り**：HUMAN_APPROVED 以降の `CONTENT_CHANGED`（DRAFTED に戻る）で validate を再実行すると、`_check_draft_consistency` の Context SHA-256 照合が、承認 commit で変わった characters/・registry.yaml と一致しなくなる。2-D で、照合の基準を承認 commit に置く設計が要る。

---

## 10. 2-C0〜2-C4 の境界

| 段階 | 内容 | 成果物 | 止まるところ |
|---|---|---|---|
| **2-C0** | 設計・指示書・Claude プロンプト（この文書、`agy-instruction-2c.md`、`state_update.md`、`state_patch.md`） | 文書のみ。コード・schema・テストは変更しない | Human が §8 に回答 |
| **2-C1** | Patch 適用、hash、承認記録、summary・Patch の検査、提案の保存（`statepatch.py`、`approvals.py`、`statecheck.py`、`proposals.py`、`proposal.schema.json`） | 純粋ロジック＋Controller データの読み書き。Claude・git の worktree・AGY を使わない単体テスト | commit・push して報告 |
| **2-C2** | state_update Job、Claude 連携、承認・却下・無効化（`stateupdate.py`） | 偽の Claude で動く単体テスト。実 Claude は使わない | commit・push して報告 |
| **2-C3** | 最終承認、保護対象の監査、後始末、resolve_request、redraft、discard（`finalize.py`、`requestflow.py`） | 実 git の単体テスト（AGY・Claude なし） | commit・push して報告 |
| **2-C4** | CLI の追加、実 Claude・実 AGY の結合試験（受入 1・2） | `cli.py` の追加、`tests/integration/test_statefinal_integration.py` | 想定外なら止めて報告。K2/K3 と同じ流儀 |

各段階の終わりで Human が確認し、次の段階を指示する（2-B と同じ）。
