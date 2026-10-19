対象は伏線の一覧です。

* 既存の伏線を扱うときは、まず `test` で ID を確認してください：`{"op": "test", "path": "/0/id", "value": "F001"}`。以降のその伏線の操作の path は、同じ添字で始めます。
* 既存の伏線の `id` は変えられません。伏線を消すこと（`/<添字>` 全体の `remove`）はできません。
* status を `cancelled` にできます。status と resolved の関係（status が resolved のときは resolved が位置、それ以外のときは null）を保ってください。
* 新しい伏線の追加：{{new_ids_rule}}
