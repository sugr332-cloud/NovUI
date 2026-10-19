対象は人物の設定です。

* `id` は変えられません（`/id` への `replace`・`remove` は書けません）。
* それ以外の項目は、追加・変更・削除できます。項目を消すときは `remove` を使います。
* 知識（knowledge）を追加するときの id は {{next_knowledge_id}} から始め、1件ごとに1ずつ増やしてください。source_chapter には、その知識を得る章の ID を書きます（指示に章が無ければ、資料の今の項目にならってください）。
* 関係（relationships）・呼び方（address）の変化（changes）を書くときの from は `{"chapter": "<章 ID>", "scene": "<場面 ID>"}` の形です。指示に場面が無い変化は、changes ではなく、今の値（state や default）の変更として書いてください。
