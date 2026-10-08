# Phase 2-D 設計判断記録：キャラクター一貫性・伏線の検査

**作成：2026-10-08 / 基準：docs/novel-system-spec-v0.5.2.md §6.4・§6.5.1・§11.3・§11.7・§11.8・§12、docs/phase2/phase2-plan.md §3 の 2-D**
**対象コード：origin/phase2/chapter-flow 0e8de4e（2-C4 完了。結合試験 16 passed）**
**段階：2-D0（設計）。この文書はコードを含まない。【決定】1〜4 は 2026-10-08 に Human が推奨案で決定した（§8）。5 は推奨案（後に回す）で進める。**

この文書は「なぜそうするか」の記録である。実装担当（AGY）への作業指示は `docs/phase2/agy-instruction-2d.md`。

---

## 0. 結論の要約

2-D は、Phase 2 の受入3・4 を満たす検査を作る段階である。

* 受入3：一人称・相手ごとの呼び方（途中の変更を含む）の違反を Validator が検出できる
* 受入4：伏線の ID・時系列・未回収を検査できる

方針は次の3つ。

1. **「その場面の位置で有効な値」は Controller が計算する。** 今は Claude に「changes があればその位置で有効な値で」と頼んでいるが、仕様 §11.7 のとおり Controller が計算してプロンプトに入れる。Claude には「この場面で C001 が C002 を呼ぶ呼び方は『君』」という**計算済みの表**を渡し、照合だけをさせる。
2. **伏線の位置の検査（§11.8 の Controller 側）は機械検査で行う。** 結果は WARNING だけで、STOP にしない（§11.3）。
3. **受入3・4 に必要なところを先に作る。** range_edit・chapter_rewrite・plan_revision・instruction_routing・Job のキューは、受入3・4 の後（2-D5 以降）に回す。

段階：

| 段階 | 内容 | 新規・変更 |
|---|---|---|
| 2-D1 | 位置の順序と、有効な値の計算（純粋な関数と単体テスト） | 新規 `positions.py`、`charrules.py` |
| 2-D2 | キャラクター一貫性：規則表を validate のプロンプトに入れる、finding に人物の欄を足す | `validatejob.py`、`prompts/claude/integrity_review.md`、`schemas/integrity_review.schema.json`【決定 1】 |
| 2-D3 | 伏線：位置の機械検査、Claude への伏線の状況表 | 新規 `foreshadowcheck.py`、`validatejob.py`・`stateupdate.py` への接続 |
| 2-D4 | 受入3・4 の結合試験（違反を仕込んだ本文で、実際の Claude が検出すること） | `tests/integration/` に新規 |
| 2-D5〜 | range_edit、chapter_rewrite、plan_revision、instruction_routing、キュー | 受入3・4 の後で別途設計 |

---

## 1. 現状との差分（調査で分かった事実）

| # | 事実 | 2-D への影響 |
|---|---|---|
| 1 | `prompts/claude/integrity_review.md` の character の観点は、changes の適用を Claude に任せている（「changes があれば、その場面の位置で有効な値」） | Controller の計算に置き換える（2-D2） |
| 2 | `schemas/integrity_review.schema.json` の finding は severity・anchor・message・evidence だけ。人物 ID・規則・期待値・本文での値は message の文に書かせている | 欄を足すかを決める【決定 1】 |
| 3 | 場面 ID の形式は `^S[0-9]+$`（common.schema.json）。仕様の例は `S003`、試験は `S1` | 順序は**数値**で比べる（`S2` < `S10`、`S003` と `S3` は同じ位置） |
| 4 | `chapters_order.schema.json` は章 ID の配列。章の順序はこれだけが正本 | 位置の比較は（chapters-order の添字、場面の番号）の組 |
| 5 | 機械検査（`mechanical.py`）のうち伏線は `ref_ids`（ID の存在）だけ。§11.8 の時系列・未回収の検査は無い | 2-D3 で追加 |
| 6 | `statecheck.py` の Patch の検査は、追加する位置が「この章・この plan の場面」であることだけを見る。registry 全体の順序（introduced より前に hints がない、など）は見ない | 2-D3 の検査を、state_update の提案にも使える形にする【決定 4】 |
| 7 | draft の Context には、場面の登場人物の `characters/<ID>.yaml` がそのまま入る（`draftjob.collect_draft_context`）。有効な値の計算は無い | AGY にも規則表を渡すか【決定 3】 |
| 8 | characters の `knowledge` は `source_chapter` を持つ。validate の時点の characters/ には、後の章で足された知識が入りうる（章の書き直しのとき） | Claude に渡す知識は「この章より前の章で得たもの」だけに絞る |

---

## 2. 位置の順序（2-D1）

* 位置は `{chapter, scene}`。比較の鍵は `(chapters-order.yaml での章の添字, 場面 ID の数値部分)`。
* 章が chapters-order.yaml に無い位置は、比較できない。**例外にせず、検査結果に WARNING として出す**（設定ファイルの誤りを Human に見せるため）。
* `from` は「その位置から（含む）」、例外の `to` は「その位置まで（含む）」（§6.4.1）。
* 章全体の比較（「この章より前」）は、章の添字だけで比べる。

関数の形（`positions.py`）：

```text
position_key(order, pos) -> (int, int)          # 比較できなければ PositionError
compare_positions(order, a, b) -> -1 | 0 | 1
chapter_index(order, chapter_id) -> int
```

---

## 3. 有効な値の計算（2-D1）

`charrules.py` は、1人の人物の characters/<ID>.yaml と、1つの位置から、その位置で有効な規則を返す純粋な関数を持つ。

| 項目 | その位置での値 |
|---|---|
| `speech.first_person`・`formality`・`endings`・`habits`・`forbidden` | 設定の値そのまま（changes は持たない） |
| `address.<相手>` | 文字列ならそのまま。object なら、`from` がその位置以前の changes のうち最も後のものの value。無ければ default |
| `address.default` | そのまま（相手を特定しない場合の呼び方） |
| `relationships[with=X].state` | address と同じ規則で changes を適用 |
| `exceptions` | `from` ≤ 位置 ≤ `to` のものを「この位置で停止している規則」として列挙【決定 2】 |
| `knowledge` | `source_chapter` がこの章より前のものだけ |
| `personality`・`behavior` | そのまま |

* 同じ位置に `from` を持つ changes が2つ以上ある場合は、設定の誤りとして WARNING を出し、リストの後の方を採る。
* 結果は「規則表」として YAML で出す。例（C001 の ch-018 S004 での表）：

```yaml
character: C001
name: 山田太郎
position: {chapter: ch-018, scene: S004}
speech: {first_person: 俺, formality: casual, endings: [だ, だろ], habits: [], forbidden: [僕]}
address:
  default: お前
  C002: 君        # ch-018 S004 から（関係性の変化）。それより前は「美咲」
  C003: 先生
relationships: [{with: C002, state: 幼なじみ}]
suspended_rules: []   # exceptions のうち、この位置で効いているもの
knowledge: [...]      # この章より前に得た知識
```

* 場面ごとに表を作る。表に入れる人物は、plan の `scenes[].characters` に載っている人物。address は、設定にある相手を**すべて**入れる（その場にいない人物を台詞で話題にするときの呼び方も照合するため。同じ場面の人物だけに絞ると、表に無い相手を default と比べて誤検出する）。

---

## 4. キャラクター一貫性の検査（2-D2）

* validate Job は、plan の各場面について規則表を作り、integrity_review のプロンプトに「場面ごとの人物の規則」の節として入れる。表は Controller が作るので、Context のファイルではなくプロンプトの本文に入れ、その SHA-256 を Job 記録に残す（Context の記録 §16.2 と同じ考え）。
* integrity_review.md の character の観点は、「上の規則表の値を正とし、changes の計算をし直さない」に書き換える。プロンプトは Claude（設計担当）が書き、AGY は変更しない（phase2-plan §4）。
* 判定の基準は仕様どおり：一人称・呼び方の明確な違反は WARNING（Human が REQUEST_FIX を選べば range_edit で直せる）。性格・行動傾向は WARNING を基本とし、明白な逸脱だけ STOP（§11.7）。
* 台詞の話者は Claude が文脈で判断する。話者を確定できない台詞は、finding にしない（誤検出を避ける）。
* 規則表に無い人物（名前のない端役、characters/ に無い人物、plan の場面に載っていない人物）の台詞は、character の観点で指摘しない（照合する規則が無いため）。その人物が登場すること自体が plan・設定に合わない場合は、plan_compliance か undefined_setting で扱う。表の人物がその人物に呼びかける場合は、address に相手が無ければ `address.default` で照合する。

**【決定 1】finding に人物の欄を足すか**

* 案A（推奨）：`integrity_review.schema.json` の finding に、省略できない欄 `character` を足す。値は null か `{character_id, rule, expected, actual, scene}`。character の観点の finding だけが値を持つ。
  - 利点：受入3 の結合試験で「C001 の address.C002 の違反を検出した」を機械的に確かめられる。後の UI（Phase 4）で一覧にできる。
  - 欠点：既存の schema の変更。既存の review.yaml（2-B・2-C の試験データ）とは形が変わる（`character: null` を足せば読める）。
* 案B：schema を変えず、今のように message の文に書かせる。結合試験は message の文字列で判定する（壊れやすい）。

**【決定 2】exceptions の意味**

* 仕様の exceptions は `rule`・`from`・`to`・`reason` だけで、例外のときの値を持たない。
* 案A（推奨）：その範囲では、その規則を**検査しない**（「公的な場面では formality の検査を止める」）。値を持たないので、これが仕様の書き方に一番合う。
* 案B：仕様を変えて `value` を足し、その範囲では別の値を正とする。

**【決定 3】AGY にも規則表を渡すか**

* 案A（推奨）：渡す。draft の段階で正しい呼び方を書かせれば、検出より前に違反を減らせる。`prompts/agy/draft.md` に「場面ごとの人物の規則」の節を足す（AGY のプロンプトの文面も Claude が書く）。
* 案B：渡さない。AGY は今のとおり characters/<ID>.yaml をそのまま読む。検出は Validator だけで行う。
* 注意：今回（2-C4）の K2 の件で「prompts/agy/draft.md は変えない」としたのは、試験を通すためにプロンプトを変えないという意味だった。2-D の機能としての変更は別の判断になる。

---

## 5. 伏線の検査（2-D3）

### 5.1 機械検査（Controller。結果は WARNING だけ）

registry.yaml と chapters-order.yaml と、検証する章の plan から、次を検査する。名前は review.yaml の mechanical に入る `check_result` の name。

| name | 内容 |
|---|---|
| `foreshadow_positions` | registry の位置が比較できること（章が chapters-order にある、場面 ID の形式） |
| `foreshadow_order` | 各伏線で、hints・developments が introduced より前にない。resolved の後に hints・developments がない。resolved が introduced より前にない |
| `foreshadow_overdue` | importance が major で、planned_resolution の章がこの章より前なのに resolved が null（status が cancelled のものを除く） |
| `foreshadow_plan_refs` | この章の plan の場面が参照する伏線が、status が resolved・cancelled でない（回収・中止したあとに扱っていないか） |

* `foreshadow_overdue` の「過ぎた」は、**検証する章との比較**とする。planned_resolution がこの章の中なら、まだ過ぎていない（回収の扱いは Claude が見る）。
* ID の存在は既存の `ref_ids` が見るので、重ねない。

### 5.2 Claude の検査

* validate Job は、Controller が作る「伏線の状況表」をプロンプトに入れる。表には、この章の plan が参照する伏線と、major で未回収のもの（status が planned・active）を入れる。各行は、status、introduced、最後の hints・developments、planned_resolution、この章が planned_resolution の章か、の要約。
* Claude は仕様 §11.8 のとおり、扱いと registry・plan の整合、回収の内容と設定・plot の矛盾、伏線の状態の変化と summary の整合を見る。「回収すべき」と独断で確定しない。

**【決定 4】伏線の順序の検査を state_update にも使うか**

* 案A（推奨）：使う。state_update の Patch を dry-run で当てたあとの registry に `foreshadow_order` をかけ、問題があれば提案の checks に WARNING として出す。承認の前に Human が気づける。
* 案B：validate だけで使う。

---

## 6. 受入3・4 の結合試験（2-D4）

本文は AGY に書かせず、**違反を仕込んだ draft.md を偽の AGY の出力として返し**（draft・validate の流れは本物を通す）、validate Job（実際の Claude）が検出することを確かめる。AGY の出力のゆらぎに左右されないようにするため。

| 試験 | 仕込み | 期待 |
|---|---|---|
| D1 一人称 | C001（first_person: 俺）の台詞に「僕」 | character の finding に C001・`speech.first_person`・期待「俺」・本文「僕」 |
| D2 呼び方 | C001 → C002 の address が default「美咲」で、台詞が「美咲ちゃん」 | `address.C002` の WARNING |
| D3a 途中の変更（違反） | address.C002 に `changes: [{value: 君, from: {chapter: ch-001, scene: S2}}]`。S1 で「君」、S2 で「美咲」 | S1 の「君」と S2 の「美咲」の両方を検出 |
| D3b 途中の変更（正しい）・不在の人物 | D3a と同じ changes。S1 で「美咲」、S2 で「君」。S2 で C001 が不在の C003 を「先生」と呼んで話題にする | address.C002・address.C003 の finding がない |
| D4 伏線の時系列 | registry の F001 で hints が introduced より前 | mechanical の `foreshadow_order` が WARNING |
| D5 未回収 | major の F002 の planned_resolution が前の章、resolved が null | mechanical の `foreshadow_overdue` が WARNING |

* 実際の Claude の判定はゆらぐ。期待の finding が出なかった場合は、**試験やプロンプトを合わせて通さず**、出力を print して止め、報告する（2-C4 と同じ方針）。
* 単体テスト（2-D1〜2-D3）は偽の Claude で、規則表の中身と機械検査を厳密に確かめる。結合試験は「実際の Claude が、渡した表を使って検出できるか」の確認に絞る。

---

## 7. 変えないもの

* 既存の状態遷移、draft・validate の流れ、review.yaml の構造（【決定 1】で finding の欄を足す場合を除く）
* 機械検査を STOP にしない規則（§11.3）
* characters/・registry.yaml を AI が書き換えない規則（§11.7・§15）。2-D の検査は**検出だけ**を行う

---

## 8. Human の決定（2026-10-08）

| # | 内容 | 決定 |
|---|---|---|
| 1 | finding に人物の欄（character_id・rule・expected・actual・scene）を足す schema の変更 | 足す（案A） |
| 2 | exceptions の意味 | その範囲では規則を検査しない（案A） |
| 3 | AGY の draft のプロンプトにも規則表を入れる | 入れる（案A） |
| 4 | 伏線の順序の検査を state_update の提案にも使う | 使う（案A） |
| 5 | 2-D5 以降（range_edit など）を受入3・4 の後に回す順番 | 後に回す（推奨案で進める。異論があれば 2-D5 の設計の前に変える） |

---

## 9. 決定を受けて詰めた点（agy-instruction-2d.md に反映）

* **introduced は位置の配列**（registry.schema.json）。順序の検査の基準は introduced の**最も早い位置**とする。introduced が空で hints・developments があるものも WARNING にする。
* **resolved の後**に置けないのは hints と developments の両方（§11.8 は hints だけを挙げるが、回収のあとの展開も同じく誤りなので含める）。同じ位置（同じ場面）は誤りにしない。
* `foreshadow_plan_refs` は、resolved の位置がこの章のもの（章の書き直し）を除く。
* state_update では、Patch を当てる**前**の registry にも同じ検査をかけ、当てた**後**に新しく出た問題だけを WARNING にする（元からある問題を提案のせいにしないため）。WARNING は Job 記録の checks に入る（提案の承認は止めない）。
* 規則表と伏線の状況表の計算で出た WARNING（比較できない位置、同じ位置の changes、不明な exceptions の rule など）は、review.yaml の mechanical に `character_rules`・`foreshadow_positions` として入る。表の本文の SHA-256 は、Job 記録の checks の `prompt_tables`（PASS）の details に残す（job_record の schema を変えないため）。
* 規則表の各人物の項目は、**ファイルにある欄だけ**を出す（2-C4 の state_patch と同じ考え。無い欄を空で出すと、Claude が「空が正しい」と読む）。
* 規則表は人物の characters/<ID>.yaml を Context から外さない（AGY に渡したのと同じ版を Claude に渡す §11.1 の規則を保つ）。プロンプトで「表の値を正とする」と書く。knowledge は、表に出したもの（この章より前の章で得たもの）だけを根拠にさせる。
* exceptions の rule に使える名前：`speech.first_person`・`speech.formality`・`speech.endings`・`speech.habits`・`speech.forbidden`・`address.default`・`address.<人物 ID>`・`relationships.<人物 ID>`・`personality`・`behavior`。それ以外は WARNING を出したうえで、表にはそのまま載せる。
* integrity_review の finding の `character` は**省略できない欄**（null か object）。character 以外の観点の finding は null でなければならない（semantics で検査）。既存の review.yaml（2-D 以前の validate が書いたもの）は schema に合わなくなるため、判断待ち（WAITING_HUMAN）の validate がある作品は、2-D2 を入れる前に decide で片付ける（開発中の作品だけなので移行の処理は作らない）。
* プロンプトの文面（`prompts/claude/integrity_review.md`、`prompts/agy/draft.md`）の変更は、2-D2 の開始時に Claude（設計担当）が行う（先に変えると、置換されない `{{…}}` で既存のコードが止まるため）。差し込みの名前は `{{character_rules}}`・`{{foreshadow_status}}` に固定する。
