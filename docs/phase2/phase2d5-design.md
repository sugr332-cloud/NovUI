# Phase 2-D5〜 設計判断記録：書き直し、plan の改訂、指示の振り分け、キュー

**作成：2026-10-14 / 基準：docs/novel-system-spec-v0.5.2.md §5.8・§7・§8・§11.2・§11.5・§13・§18、docs/phase2/phase2-plan.md §3 の 2-D**
**対象コード：origin/phase2/chapter-flow b55f807（2-E6 完了）**
**【決定】1〜4 は 2026-10-14 に Human が推奨案で決定した（§9）。**

この文書は「なぜそうするか」の記録である。2-D で後に回した残り（range_edit、chapter_rewrite、plan_revision、instruction_routing、Job のキュー）を設計する。

---

## 0. 結論の要約

* **直し方は3種類。** 範囲の書き直し（range_edit）、章の書き直し（chapter_rewrite）、plan の改訂（plan_revision）。前の2つは作業ブランチの上で行い、plan の改訂は作業ブランチが無いときだけ main で行う。
* **検証の指摘から直すときは、Claude を通さない。** Human が review.yaml の指摘を番号で選ぶと、指摘1件につき range_edit 1件がキューに入る【決定 1】。
* **自由な指示は Claude が振り分ける。** `novui instruct` で指示を送り、Claude が Job の案（instruction_routing）を返す。Human が承認した案だけがキューに入る【決定 3】。
* **plan を直すときは作業ブランチを捨てる。** 本文を残したまま plan を変える経路は作らない【決定 2】。
* **キューは「承認した Job を順に実行する」だけ。** 作品のデータにキューを保存し、`novui run-queue` で先頭から1件ずつ実行する。Human の判断が要るところで止まり、再び `run-queue` で続きから進む。常駐するプロセスは作らない【決定 4】。

段階：

| 段階 | 内容 | 新規・変更 |
|---|---|---|
| 2-D5 | AGY の Job 実行器を既存の作業ブランチでも動くように分ける。range_edit（anchor の照合、prefix・suffix、差し込み） | `jobrunner.py`、新規 `editjob.py`、`prompts/agy/range_edit.md`、CLI `edit-range` |
| 2-D6 | chapter_rewrite。編集 Job の【要確認】の扱い | `editjob.py`、`prompts/agy/chapter_rewrite.md`、CLI `rewrite` |
| 2-D7 | plan_revision | `planjob.py`、`prompts/claude/plan_revision.md`、CLI `revise-plan` |
| 2-D8 | Job のキューと、検証の指摘からの range_edit（§11.5 の上限） | 新規 `jobqueue.py`、CLI `fix`・`run-queue`・`queue` |
| 2-D9 | instruction_routing | 新規 `routingjob.py`、`prompts/claude/instruction_routing.md`、`schemas/instruction_routing.schema.json`、CLI `instruct`・`approve-routing`・`reject-routing` |
| 2-D10 | 結合試験（実際の Claude。AGY は偽物、実際の AGY は Human の指示があるときだけ） | `tests/integration/` に新規 |

---

## 1. 現状との差分（調査で分かった事実）

| # | 事実 | 影響 |
|---|---|---|
| 1 | `jobrunner.run_draft_job` は、作業ブランチと worktree を作るところから commit までを1つの関数で行う | 既存の worktree で AGY を動かす部分を分ける（2-D5） |
| 2 | `anchor.find_anchor`・`split_by_range`・`agy_output.splice_range` は Phase 1 からある。どこからも呼ばれていない | range_edit でそのまま使う |
| 3 | `decide --action REQUEST_FIX` は理由を review.yaml に残すだけで、直す Job は作らない（2-B の指示書で「2-D で作る」） | 2-D8 の `fix` につなぐ |
| 4 | writing_review の指摘には `W1` などの ID がある。integrity_review の指摘には ID が無い（anchor は null もありうる） | 指摘の番号は Controller が付ける（§5.1） |
| 5 | validate は、作業ブランチの draft Job の記録の Context のハッシュを確かめる（§11.1）。Context に draft.md は含まれない | 本文だけを変える編集 Job の後も、同じ版の Context で validate できる。設定ファイルを変えるのは state_update だけなので、編集は HUMAN_APPROVED より前に限る（§2.2） |
| 6 | `schemas/instruction_routing.schema.json` は仕様 §13.3 の形で既にある。proposed_jobs に範囲（anchor）と指示の本文の欄が無い | 欄を足す（§6.2） |
| 7 | `locks.RunLock` はプロセスの中だけの lock。CLI は1回ごとに別のプロセス | キューの実行は、作品ごとのファイル lock で1つに限る（§7.3） |
| 8 | redraft（2-C3）は、【要確認】が残った draft の作業ブランチを捨てて書き直す | 編集 Job の【要確認】は、ブランチを捨てずに扱う（§4） |

---

## 2. range_edit（2-D5）

### 2.1 流れ

    Human（または fix・キュー）：章、範囲（anchor）、指示
    → Controller：作業ブランチの draft.md で anchor を照合（1件だけ一致すること。§13.5）
    → Controller：prefix（範囲より前）、範囲、suffix（範囲より後）に分けて保存
    → AGY：範囲の文章と前後の文脈、指示を受け取り、置き換え後の文章だけを返す
    → Controller：prefix + 出力 + suffix を draft.md に書き込み、変更検査（§8.3）
    → Controller：chapter.yaml を DRAFTED にして commit（AI_VALIDATED だった場合）

### 2.2 実行できる条件

* 章に作業ブランチがあり、ブランチ上の chapter.yaml が **DRAFTED または AI_VALIDATED** であること。HUMAN_APPROVED（状態更新を適用した後）では行わない。本文を変えると、承認した状態更新と本文が食い違うため。直したいときは、作業ブランチを捨てて書き直す。
* その作業ブランチに、Human の判断を待つ Job（WAITING_HUMAN）が無いこと。validate の判断待ちなら、先に `decide` で判断する。
* 状態更新の提案（proposals）が pending でないこと。
* 実行の直前に anchor を照合する。一致が0件・2件以上なら、AGY を呼ばずに Job を WAITING_HUMAN にする（理由 `anchor_not_unique: <件数>`。§13.5）。
* 判断を待つ range_edit は、`edit-range --retry <job_id>`（同じ範囲と理由でやり直す。【要確認】の判断を指示に足す）か、`cancel-job --job <job_id>`（CANCELLED にする）で閉じる。閉じるまで、その作業ブランチの次の編集は受け付けない。

### 2.3 範囲の決まり

* 範囲は anchor の `text` そのもの。`before`・`after` は照合のための文脈で、書き換えない。
* 範囲に場面の区切りの行（`<!-- scene: … -->`）を含めてはいけない。範囲は1つの場面の中に収まること。満たさない場合は、Job を作らずにエラーにする（指定の誤りなので、Human が範囲を指定し直す）。
* AGY の出力に場面の区切りの行があれば FAILED。
* 差し込んだ後の本文に、draft と同じ機械検査を行う（`prepare_draft` の `extra_checks`）。draft で FAIL になる検査（台本の形式の構文・流れ・フラグ）は、range_edit でも FAIL とする。それ以外（場面の区切り、文字数など）は draft と同じく WARNING として記録する。

### 2.4 AGY に渡すもの

* Context：その作業ブランチの draft Job と同じファイル（draft Job の記録の `context` のパス）。版も同じであることを、実行の前に確かめる（validate と同じ照合。食い違えば Job を作らずにエラー）。
* 指示（`prompts/agy/range_edit.md`）：
  - 範囲の文章
  - 前の文脈：範囲の前の、同じ場面の本文（最大 1,500 字。場面の始めまで）
  - 後の文脈：範囲の後の、同じ場面の本文（最大 1,500 字。場面の終わりまで）
  - その場面の人物の規則表（`charrules` の表のうち、その場面の分）
  - 直す理由（指摘の文、提案、Human の指示）
  - 台本の形式の作品では、台本の形式の決まり（draft_script.md の決まりの部分を共通にして読む）
* 「範囲の外を書き換えない。置き換え後の文章だけを出力する。前後の文脈を出力に含めない」ことを指示に書く。

### 2.5 編集の後の validate

* 編集で章は DRAFTED に戻る。validate はもう一度実行できる。前の review.yaml の decision が null でも、その validate Job が COMPLETED（PASS で自動で進んだもの）なら、判断待ちとは見なさない。

### 2.6 Job 記録

* job_type `range_edit`、branch・worktree は作業ブランチのもの、base_commit は編集の前の HEAD。
* 記録に `edit` を足す：`{anchor, range_bytes: [start, end], reason, source}`。source は `{kind: finding, review_job: <validate の job_id>, finding: I3}`・`{kind: instruction, instruction_id}`・`{kind: human}` のいずれか。

---

## 3. chapter_rewrite（2-D6）

* 実行できる条件は range_edit と同じ（§2.2）。plan は変えない（plan を変えるなら §5）。
* AGY に渡すもの：draft と同じ Context と指示（plan に従うこと、場面の区切り、文字数、人物の規則表）に加えて、**今の本文と、書き直しの指示**。
* 出力は draft.md の全体を置き換える。検査は draft と同じ（文字数の範囲も draft と同じ扱い）。
* 成功したら chapter.yaml を DRAFTED にして commit する。

## 4. 編集 Job の【要確認】（2-D6）

draft の【要確認】は、本文に印を残したまま WAITING_HUMAN にし、redraft で作業ブランチごと書き直す（2-C3）。編集 Job では作業ブランチを捨てたくないので、扱いを変える。

* 出力に【要確認】があれば、**draft.md は元に戻し**、requests.yaml の追記だけを commit して、Job を WAITING_HUMAN にする。
* Human は `resolve-request` で判断を記録し、同じ編集をもう一度実行する（`edit-range` / `rewrite` に `--retry <job_id>` を付ける。範囲と指示は前の Job の記録から取る）。指示の最後に、その Job から出た要求への判断を、redraft と同じ文面で付ける。前の Job は CANCELLED（理由 `retried as <新しい Job>`）にする。
* 2-D5 で range_edit、2-D6 で chapter_rewrite について実装した（`rewrite --retry <job_id>`）。判断を待つ編集 Job は、どちらも `cancel-job` で閉じられる。
* 判断の済んでいない要求が残っている間は、validate・state_update に進めない（既存の規則のまま）。

---

## 5. plan_revision（2-D7）

* 実行できる条件：main の chapter.yaml が **PLAN_APPROVED** で、作業ブランチが無いこと【決定 2】。作業ブランチがある場合は、先に `discard` で捨てる（CLI はその旨を案内する）。FINAL の章の改訂（後続の章に要確認を付ける。§7.3）は Phase 3 に回す。
* Claude に渡すもの：plan Job と同じ Context に、今の plan.yaml と、改訂の指示。出力の schema は plan と同じ。
* Controller は plan と同じ検査（schema、意味の検査、ID の参照）をして、main に plan.yaml と chapter.yaml（PLANNED）を commit する（`NovUI-Job`）。あとは今までどおり `approve-plan` → `draft`。

---

## 6. instruction_routing（2-D9）

### 6.1 CLI

    novui instruct --work W --chapter ch-004 --scope chapter "全体的にテンポが遅い"
    novui instruct --work W --chapter ch-004 --scope range --anchor "…" [--before …] [--after …] "ここを短く"
    novui instruct --work W --chapter ch-004 --scope plan "S2 と S3 を入れ替えたい"

* Phase 2 で受け付ける scope は `range`・`chapter`・`plan`。`setting`（設定変更）と `redline`（添削）は Phase 3・4、`fix` は `novui fix`（§7.2）が受け持つ。
* instruction_id（`INS-0001`…）は Controller が採番する。

### 6.2 Claude の出力

仕様 §13.3 の形に、次を足す。

* proposed_jobs の各項目に `anchor`（range_edit のときの範囲。それ以外は null）と `instruction`（AGY または Claude に渡す具体的な指示）。
* job_type は `range_edit`・`chapter_rewrite`・`plan_revision` のいずれか（Controller が確かめる。それ以外は schema 不適合と同じく再要求）。
* `plan_revision` の後に本文を書き直す場合、Claude は `chapter_rewrite` を `depends_on` 付きで提案する。Controller は、plan_revision の後の chapter_rewrite を「plan の承認の後の draft」に置き換えてキューに入れる【決定 2】。

### 6.3 振り分けの記録と承認

* 振り分けの結果は、作品のデータ `routings/INS-0001.yaml` に保存する（status：`pending` / `approved` / `rejected` / `questions`）。
* `questions` が空でなければ status は `questions`。Human は答えを含めて、もう一度 `instruct` する（その振り分けは承認できない）。
* `scope_mismatch` が true のときも承認はできるが、CLI はその理由を表示する。scope を広げるかは Human が決める（新しく `instruct` し直す）。
* `approve-routing --id INS-0001` で、proposed_jobs を順にキューに入れる。plan_revision を含み、章に作業ブランチがある場合は、`--discard-branch` を付けないと承認できない（ブランチを捨てることを Human が明示する）。
* `reject-routing --id INS-0001` で破棄する。破棄した振り分けのファイルは消す（§13.3「破棄した指示は保存しない」）。
* 承認した振り分けの instruction_id は、キューから実行した Job の記録の `instruction_id` に残す。

---

## 7. Job のキュー（2-D8）

### 7.1 形

作品のデータ `queue.yaml`：

    items:
      - item_id: Q-0001
        job_type: range_edit
        chapter_id: ch-004
        source: {kind: finding, review_job: job-0012, finding: I2}
        anchor: {text: …, before: …, after: …}
        instruction: …
        status: pending        # pending / running / done / waiting / failed / cancelled
        job_id: null
        added_at: …

* `run-queue --work W` は、先頭の `pending` の項目から1件ずつ実行する。Job が COMPLETED なら `done` にして次へ。WAITING_HUMAN なら `waiting`、FAILED なら `failed` にして止まる。後ろの項目は `pending` のまま。
* `waiting` の項目は、Human が判断した後に `run-queue --retry` で同じ項目をやり直すか、`queue --cancel Q-0003` で取り消す。`failed` の項目は取り消すか、やり直す。
* plan の承認が要る項目（plan_revision の後の draft）は、章が PLAN_APPROVED になるまで実行しない（`run-queue` は「approve-plan を待っています」と表示して止まる）。
* 範囲の項目は、実行の直前に anchor を照合し直す（§13.4）。前の項目で本文が変わって一致しなくなったら、その項目は `waiting`。

### 7.2 検証の指摘からの range_edit

    novui fix --work W --chapter ch-004 --findings I2,W1,W3 [--note "…"]

* 対象は、作業ブランチの最新の review.yaml の指摘。番号は Controller が付ける：integrity の指摘は、`checks` の観点の順（character、world、timeline、plot、foreshadowing、plan_compliance、undefined_setting）に、各観点の中の順で `I1`、`I2`…。writing の指摘は Claude の付けた `W1`… をそのまま使う。`novui show` に番号を出す。
* anchor が null の指摘は選べない（エラーにして、`instruct --scope chapter` を案内する）。
* 実行できる条件：review.yaml の decision が `REQUEST_FIX`（章は DRAFTED）、または章が AI_VALIDATED（文章レビューの指摘を採用するとき）。
* AGY への指示：指摘の文（message）、提案（suggestion）、REQUEST_FIX の理由、`--note`。
* §11.5 の上限：同じ作業ブランチで、`fix` の回数（1回の `fix` で何件選んでも1回）が2回に達したら、3回目は `--force` を付けないと受け付けない（Human が明示的に再開する）。回数は、作業ブランチの range_edit の Job 記録の source から数える。

### 7.3 同時に実行しない

* `run-queue` は、作品のデータの `queue.lock` を `fcntl.flock` で取ってから実行する。取れなければ「別の run-queue が実行中」で終わる。
* `edit-range`・`rewrite` などを直接実行するときも、同じ lock を取る（キューの外から割り込まないため）。

---

## 8. 範囲外（後の段階）

* scope `setting`（設定変更と影響範囲分析）、`redline`（添削）：Phase 3・4
* FINAL の章の plan_revision と chapter_rewrite（後続章の要確認）：Phase 3
* Web UI からのキューの操作と常駐の実行：Phase 4
* 本文を残したまま plan を改訂する経路：作らない【決定 2】

---

## 9. 決定事項

| # | 論点 | 決定（2026-10-14） |
|---|---|---|
| 1 | REQUEST_FIX の後の直し方 | 指摘を番号で選び、指摘1件につき range_edit 1件。Claude の振り分けは通さない |
| 2 | 作業ブランチがある間の plan の改訂 | 作業ブランチを捨ててから改訂する |
| 3 | 自由な指示の入口 | Phase 2 の CLI に作る（`instruct`） |
| 4 | キュー | 承認した Job を作品のデータに保存し、`run-queue` で順に実行。常駐しない |
