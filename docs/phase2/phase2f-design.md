# Phase 2-F 設計判断記録：フラグに結び付いた設定

**作成：2026-10-15 / 基準：docs/phase2/phase2e-design.md §5.3、docs/phase2/phase2d-design.md**
**対象コード：origin/phase2/chapter-flow af840f7（2-D10 完了）**
**【決定】1〜4 は 2026-10-15 に Human が推奨案で決定した（§9）。**

この文書は「なぜそうするか」の記録である。2-E で後に回した「設定の変化をフラグに結び付ける部分」を設計する。

---

## 0. 結論の要約

章の中で分かれて合流する台本では、分岐の中だけを通る場面（以下「分岐の場面」）で設定が変わると、合流した後の設定が道によって違う。今は state_update でこれを WARNING にするだけで、規則表・伏線・知識はフラグを知らない。

* **設定の変化に条件 `when` を付ける。** `when` はフラグ1つかその否定（`@if` と同じ形）【決定 1】。付けられるのは、呼び方・関係の changes、knowledge、伏線の位置【決定 2】。
* **Controller が「その場面で立ちうるフラグの組み合わせ」を計算する。** 各章の plan の choice から、場面ごとに「入る時点で立っているフラグの集合」として起こりうるものを全部求める（§2）。前の章の分岐も含む。
* **規則表は、値が組み合わせによって違うときだけ、フラグごとの値を並べる。** 全部同じなら今と同じ1つの値。台本の `@if` の中では、そのフラグの値の行を使う（§3）。
* **分岐の場面での設定の変化には `when` が要る。** その場面に入る全ての道で成り立つ条件でなければ WARNING（今の `branch_scene_change` を置き換える。§5）。
* **summary に、分岐ごとの出来事の欄を足す【決定 3】。**
* **組み合わせの数を制限する。** 1つの値が依存するフラグは 2 つまで。超えたら WARNING にし、変化をそのまま並べる【決定 4】。

段階：

| 段階 | 内容 | 新規・変更 |
|---|---|---|
| 2-F1 | フラグの状態の計算（純粋な関数）、schema の `when` | 新規 `flagstate.py`、`schemas/common.schema.json`・`character.schema.json`・`registry.schema.json` |
| 2-F2 | 規則表と伏線の表をフラグごとに | `charrules.py`、`foreshadowcheck.py`、`validatejob.py`・`draftjob.py`・`editjob.py`、プロンプト |
| 2-F3 | 機械検査 `flag_conditions`、state_update（Patch の `when`、summary の分岐） | `statecheck.py`、`stateupdate.py`、`schemas/summary.schema.json`、`prompts/claude/state_update.md`・`state_patch.md` |
| 2-F4 | 結合試験（実際の Claude。AGY は偽物） | `tests/integration/` |

---

## 1. 現状との差分（調査で分かった事実）

| # | 事実 | 影響 |
|---|---|---|
| 1 | `charrules.effective_value` は、位置が今の場面以前で最も後ろの change を選ぶ。条件は無い | change に `when` を足し、フラグの組み合わせごとに選ぶ（§3） |
| 2 | `common.schema.json` の `change` は `{value, from, reason}`。address と relationships の changes が共有する | `when` を `change` に足せば両方に効く |
| 3 | knowledge は `{id, fact, source_chapter}` で、場面を持たない。規則表では前の章のものだけを載せる | 場面を持たないので、分岐の場面かどうかを機械では判定できない。`when` は Claude が付け、§5 の検査は位置を持つものだけ |
| 4 | 伏線の位置（introduced・hints・developments・resolved）は `position` を共有する | 伏線の位置用に `{chapter, scene, when?}` を別に定義する（`position` は exceptions などでも使うので変えない） |
| 5 | `scriptcheck.flags_set_before` は「前の章の全ての choice のフラグと、この章で前にある場面の choice のフラグ」を返す（立ちうるか、だけ） | `script_flags`（`@if` のフラグが前で立ちうるか）にはこれで足りるので残す。「必ず立っている」「立ちえない」の区別が要る規則表と §5 の検査は、§2 の計算を使う |
| 6 | `scriptcheck.branch_only_scenes` と `statecheck.check_branch_scene_changes` は 2-E4 で入れた暫定の検査 | 分岐の場面の計算は `flagstate.branch_scenes` に移し（`branch_only_scenes` は同じ関数の別名）、`check_branch_scene_changes` は §5 の `flag_conditions` に置き換える |
| 7 | エンジンのフラグは真偽値で、一度立てたら下ろさない（2-E） | 「立っているフラグの集合」で状態を表せる |
| 8 | 小説の形式の作品には選択肢が無く、フラグは立たない | 小説の形式では、`when` の無い今の動作と同じ（組み合わせは空集合1つだけ） |

---

## 2. フラグの状態（2-F1）

### 2.1 計算

* 状態は「その時点で立っているフラグの集合」。場面ごとに、**その場面に入る時点**で起こりうる状態の集合を求める。
* 章の最初の場面に入る状態は、前の章の終わりの状態の集合（最初の章は空集合1つ）。
* 場面の次へ進むとき：choice があれば、選択肢ごとに、その選択肢の flag を足した状態で行き先へ。choice が無ければ、そのままの状態で next（または plan の次の場面）へ。
* 章の終わりの状態は、次の場面が無い場面から出る状態の和集合。
* 場面のつながりはループを持たない（2-E4 の plan の semantics）ので、plan の場面の順に1回なぞれば求まる。
* plan.yaml が無い章（まだ plan を作っていない後ろの章）は、状態を変えない。前の章で plan が読めないものがあれば、その章では状態を変えないとして続け、警告を返す。

```python
@dataclass(frozen=True)
class FlagStates:
    at_start: Mapping[str, States]                 # 章 → 最初の場面に入る時点
    at_scene: Mapping[str, Mapping[str, States]]   # 章 → 場面 → 入る時点（plan のある章だけ）
    at_end: Mapping[str, States]                   # 章 → 章の終わり
    warnings: tuple[str, ...]
    def states_at(chapter_id, scene_id=None) -> States | None

def work_flag_states(order, plans) -> FlagStates
def condition_holds(cond: str | None, state: frozenset[str]) -> bool   # "f" / "not f"
```

* 状態の数の上限は 256。超えたら後ろの章の計算を打ち切って警告を返す。打ち切った章では状態が分からない（None）ので、規則表は `when` を持つ値を「default と changes の一覧」のまま出し（§3.2 と同じ）、§5 の検査はその位置を見ない。

### 2.2 条件の形【決定 1】

* `when: market_together` または `when: not market_together`。`@if` と同じで、and・or を作らない。
* 2つのフラグの両方に依存する変化は、changes を2つ書けば表せる（例：f で「君」、g で「お前」。両方立てば位置が後ろの方）。

## 3. 規則表（2-F2）

### 3.1 値の選び方

* change は、`from` がその場面以前で、かつ `when` がその状態で成り立つものの中から、位置が最も後ろのものを選ぶ（同じ位置なら、リストの後ろが勝つ。今と同じ）。
* knowledge は、`when` がその状態で成り立つものだけ。
* 場面の状態の集合のそれぞれで値を求め、全部同じなら今と同じ1つの値を書く。違えば、値が依存するフラグ（`when` に現れるフラグのうち、場面の状態の中で立つものと立たないものがあるもの）ごとに並べる。

```yaml
- scene: S4
  characters:
    - character: C001
      address:
        C002:
          - {value: 美咲, flags: {market_together: false}}
          - {value: 君, flags: {market_together: true}}
        default: あんた
      knowledge:
        - {id: K004, fact: 美咲は魚が苦手, source_chapter: ch-001, when: market_together}
```

* 場面の状態で必ず立っている（または必ず立っていない）フラグの条件は、表では解いておく。例：選択肢 +market_together でしか入れない S2 では、`when: market_together` の change は条件なしの値として出る。

### 3.2 組み合わせの制限【決定 4】

* 1つの値が依存するフラグは 2 つまで（並べる行は最大 4）。超えたら `character_rules` の WARNING にし、その値は「default と changes の一覧（when 付き）」をそのまま表に出す。Claude と AGY は、その一覧から判断する。
* 章の中の分岐の規模（2-E の決定：章の中で分かれて合流する）なら、ほとんどの値は 0〜1 個のフラグにしか依存しない見込み。

### 3.3 AI への説明

* AGY の draft・range_edit・chapter_rewrite と、Claude の integrity_review のプロンプトに、表の読み方を足す：
  - 値が並んでいるときは、`@if <フラグ>` の中では立っている行、`@else`（または `@if not`）の中では立っていない行の値を使う。
  - `@if` の外で、値が並んでいるものを使ってはいけない（どの道を通っても正しい表現にするか、`@if` で書き分ける）。
* integrity_review では、`@if` の外で並んだ値の一方だけを使っていれば character の指摘（WARNING）にする。

## 4. 伏線（2-F2）

* registry の位置に任意の `when`（§2.2 の形）を足す。
* 伏線の状態表（2-D）の各行に、位置の `when` をそのまま出す。表の `introduced` などが条件付きのとき、Claude は「その条件の道でだけ張られた伏線」として扱う。
* 順序の検査（`foreshadow_order`）は `when` を無視して今のまま。条件が違う位置どうしでも、張る前に回収するような並びは誤りであることが多いため。

## 5. 機械検査 `flag_conditions`（2-F3）

台本の形式の作品の validate と state_update で WARNING（止めない）。

| 内容 | 例 |
|---|---|
| `when` のフラグが flags/registry.yaml にある | `C001 address.C002 changes[1] when 'market_togeter' is not in flags/registry.yaml` |
| `when` が、その位置に入るどの道でも成り立たない（その設定は使われない）。knowledge は場面を持たないので、source_chapter の章の終わりで見る | `... when 'market_together' never holds at ch-002 S1` |
| 分岐の場面に位置を持つ設定の変化（change の from、伏線の位置）は、`when` を持ち、その場面に入る全ての道で成り立つ | `C001 address.C002 changes[0] at ch-001 S2 (branch scene) has no when` |

* 対象は、characters/ の全員と registry の全項目のうち、位置が今の章以前のもの（後ろの章は、その章の validate で見る）。
* どの道でも通る場面で全ての道で成り立つ `when` は、問題にしない（前の章で必ず立つフラグの場合など、害が無いため）。
* state_update では、Patch を適用した後の設定で計算し、Patch で新しく生じた問題だけを WARNING にする（`foreshadow_order` と同じ。2-D 決定 4）。
* 今の `branch_scene_change` は削除する。

## 6. state_update（2-F3）

### 6.1 Patch

* change と伏線の位置の値に `when` を許す（`_change_errors`・`_position_errors` で、`when` は任意の欄として受け、形を確かめる）。knowledge の追加にも `when` を許す。
* state_update と state_patch のプロンプトに、この章の分岐の場面と、各場面で必ず成り立つ条件（例：`S2: market_together`、`S3: not market_together`）を渡す。分岐の場面での変化と、分岐の場面でしか起きない知識には、その条件を `when` に書くよう指示する。

### 6.2 summary【決定 3】

* 任意の欄 `branches` を足す：

```yaml
branches:
  - scenes: [S2]
    when: market_together
    events: [カイと美咲が市場を回り、美咲が魚を苦手だと打ち明ける]
```

* `events` は全ての道で起きる出来事だけにし、分岐の場面の出来事は `branches` に書く。`characters`・`foreshadowing` は今のまま（Patch との対応の検査を変えないため）。
* 次の章の plan・draft に渡す前の章の summary は、`branches` を含めてそのまま渡す（今と同じ。読む側の Claude・AGY が `when` を読む）。

## 7. 変えないもの

* 台本の形式（`@if` の形、選択肢のフラグは1つまで）とエンジンへの書き出し
* フラグの登録（flags/registry.yaml。Human が書く）
* exceptions（一時的に規則を止める）。条件を付けない（分岐の中だけで規則を止める必要は、`@if` の中の書き分けで足りる）
* `when` の無い設定の動作（既存の作品と試験はそのまま通る）

## 8. 範囲外

* 数値のフラグ（好感度）、侵食度・ルート・視点ごとの台詞（2-E 決定 9。別に設計する）
* 章をまたぐルートの分岐

## 9. 決定事項

| # | 内容 | 推奨案 | 決定 |
|---|---|---|---|
| 1 | 条件の形 | フラグ1つかその否定だけ（`@if` と同じ） | 推奨案（2026-10-15） |
| 2 | 条件を付けられる設定 | 呼び方・関係の changes、knowledge、伏線の位置（exceptions は付けない） | 推奨案（2026-10-15） |
| 3 | summary の分岐の記録 | `branches` の欄を足す（場面・条件・出来事） | 推奨案（2026-10-15） |
| 4 | 組み合わせの制限 | 1つの値が依存するフラグは 2 つまで。超えたら WARNING にして一覧をそのまま出す | 推奨案（2026-10-15） |

## 10. 実装の記録（2-F1〜2-F3）

| 段階 | 実装 |
|---|---|
| 2-F1 | `flagstate.py`（FlagStates、work_flag_states、branch_scenes、branch_conditions、load_plans・load_flag_states）。schema：`common` に `flag_condition`・`conditional_position`、`change.when`。character の knowledge と registry の位置（planned_resolution を除く）に `when` |
| 2-F2 | `charrules.flag_value`（値の行 `{value, flags}`、MAX_FLAGS_PER_VALUE = 2、超えたら `{default, changes}`）。knowledge の `when` を状態で解く。draft・range_edit・chapter_rewrite・validate が規則表にフラグの状態を渡す。読み方の説明は `prompts/claude/script_notes.md` と `prompts/agy/script_format.md`（台本の形式の作品だけに入る） |
| 2-F3 | `flagcheck.py`（`flag_conditions`）。validate（台本の形式）と state_update（Patch で生じた問題だけ）。Patch の方針で `when` を受ける。summary の `branches`。分岐の説明 `prompts/claude/branch_notes_summary.md`・`branch_notes_patch.md` は、分岐の場面か `@if` がある章だけ state_update・state_patch のプロンプトに入る。`statecheck.check_branch_scene_changes` を削除 |
