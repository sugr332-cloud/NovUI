【作業】章 {{chapter_id}} の状態更新のうち、設定ファイル {{target}} への変更の提案（state_patch）を作ってください。

上の資料のうち、chapters/{{chapter_id}}/draft.md が本文、chapters/{{chapter_id}}/plan.yaml がこの章の執筆計画です。その他の資料は、本文を書き、検証したときと同じ版です。本文の `<!-- scene: S1 -->` のような行は場面の区切りで、本文の一部ではありません。この章の場面の ID は {{scene_ids}} です。変更の提案の対象 {{target}} は資料の中に `【ファイル：{{target}}】` として含まれています。

この章の終了時点の要約（summary）は次のとおりです。これは Human の承認を待っている提案で、本文から作られたものです。変更の提案は、**この要約と本文に書かれたことだけ**を根拠にしてください。

```yaml
{{summary_yaml}}
```

提案は JSON Patch（RFC 6902）の形式で、使える操作は `add`・`replace`・`test` だけです（`remove` は使えません）。Human が承認したものだけが、設定ファイルに反映されます。

■ 出力する項目

* type：`state_patch`
* target：`{{target}}`（この値以外にしてはいけません）
* base_hash：`{{base_hash}}`（この値をそのまま書き写してください）
* operations：変更の操作の配列。順序に意味があります。次の「書いてよい変更」の範囲だけを使ってください。
* reason：なぜこの変更をするのかを、章 {{chapter_id}} の出来事に触れて1〜2文で。

■ 変更が不要・表せない場合

{{target}} に反映すべき変更がない、または下の範囲で表せない場合は、operations を `test` 操作1件だけにしてください（例：`{"op": "test", "path": "/id", "value": "C001"}`。伏線の registry.yaml なら `{"op": "test", "path": "/0/id", "value": "F001"}` のように、下の「pointer の対応表」にある最初の伏線の ID を使います）。変更のない提案は取り除かれます。範囲外の変更を無理に表そうとしてはいけません。

■ pointer の対応表（Controller が計算したものです。添字を自分で数えないでください）

{{pointer_hints}}

■ 書いてよい変更

対象が `characters/<ID>.yaml`（人物）の場合：

* knowledge の追加：`{"op": "add", "path": "/knowledge/-", "value": {"id": "K…", "fact": "…", "source_chapter": "{{chapter_id}}"}}`
  - fact は、summary のこの人物の knowledge_added の文を**そのまま**（1文字も変えずに）使ってください。summary にない事実を足してはいけません。
  - id は {{next_knowledge_id}} から始め、1件ごとに1ずつ増やしてください（K014 の次は K015）。同じ形式（K と3桁以上の数字）で書きます。
* 関係の変化の追加：summary のこの人物の relationship_changes のうち、相手との関係が既に relationships にある場合は、先に `test` で相手を確認してから changes に追加します。
  - `{"op": "test", "path": "/relationships/<添字>/with", "value": "C002"}`
  - `{"op": "add", "path": "/relationships/<添字>/changes/-", "value": {"value": "<変化後の関係>", "from": {"chapter": "{{chapter_id}}", "scene": "S2"}, "reason": "<なぜ変わったか>"}}`
  - from.scene は、変化が本文で起きた場面の ID（{{scene_ids}} のいずれか）です。
  - 相手との関係がまだ relationships にない場合は、`{"op": "add", "path": "/relationships/-", "value": {"with": "C002", "state": "<本文に書かれた関係>"}}` で追加できます。
* 呼び方の変化：本文で、その人物が相手の呼び方を**明確に変えた**場合だけ（関係の変化が summary にある相手に限ります）。
  - 資料の address の相手の値が object（default と changes を持つ形）なら、`{"op": "add", "path": "/address/C002/changes/-", "value": {"value": "<新しい呼び方>", "from": {"chapter": "{{chapter_id}}", "scene": "S3"}, "reason": "<理由>"}}`
  - 値が文字列の場合は、先に `{"op": "test", "path": "/address/C002", "value": "<元の文字列>"}` を置き、`{"op": "replace", "path": "/address/C002", "value": {"default": "<元の文字列>", "changes": [{…上と同じ形…}]}}` にします。
  - 相手の項目がまだない場合は、`{"op": "add", "path": "/address/C002", "value": "<呼び方>"}`。
* 性格（personality）・行動傾向（behavior）・口調（speech）・例外（exceptions）・名前・notes は、あなたの提案では変えられません。本文でそれらが変わったように見えても、書かないでください（Human が別の手続きで決めます）。
* 既存の knowledge・relationships・address の項目を `replace` や `remove` で書き換えてはいけません（address の文字列から object への置き換えを除く）。

対象が `foreshadowing/registry.yaml`（伏線）の場合：

* 1つの伏線につき、まず `test` で ID を確認してください。`{"op": "test", "path": "/0/id", "value": "F001"}`。以降のその伏線の操作の path は、同じ添字（上の例なら `/0/…`）で始めます。複数の伏線を扱う場合は、伏線ごとに先頭に `test` を置きます。
* 位置は `{"chapter": "{{chapter_id}}", "scene": "S2"}` の形で、scene は本文でその伏線が扱われた場面の ID（{{scene_ids}} のいずれか）です。
* 追加できるもの：
  - `introduced`（伏線が初めて出た）：`{"op": "add", "path": "/0/introduced/-", "value": {位置}}`
  - `hints`（ヒント）：`{"op": "add", "path": "/0/hints/-", "value": {位置}}`
  - `developments`（展開）：`{"op": "add", "path": "/0/developments/-", "value": {位置}}`
  - summary の foreshadowing の note に書かれたとおりの種類（導入・ヒント・展開）に対応させてください。
* status の変更：summary のその伏線の status が registry.yaml の現在の値と違う場合だけ、`{"op": "replace", "path": "/0/status", "value": "active"}` のように変更します。
  - `resolved` にする場合は、同じ提案の中で `{"op": "replace", "path": "/0/resolved", "value": {位置}}` も書いてください（status が resolved のときは resolved が位置、それ以外のときは null でなければなりません）。
  - `cancelled` にしてはいけません。
* planned_resolution（回収の予定）は変えられません。回収が予定より早い・遅い・行われないことの判断は Human が行います。
* 登録のない伏線 ID を使ってはいけません。新しい伏線を `add` で作ってはいけません。

■ 共通の注意

* 本文に書かれていないこと、summary にないこと、あなたの推測で、設定を変えてはいけません。
* 資料にある ID・値と合わない path や value を書いてはいけません。`test` の value は、資料の現在の値と一致させてください（一致しない提案は適用されません）。
* 変更の操作は、必要最小限にしてください。
