【作業】Human の設定変更の指示に基づいて、設定ファイル {{target}} への変更（JSON Patch）を作ってください。

上の資料は、今の設定ファイル一式です。対象 {{target}} は資料の中に `【ファイル：{{target}}】` として含まれています。

指示（instruction_id：{{instruction_id}}）：

{{instruction}}

このファイルで行う変更の方針（前の段階で決めたもの）：

{{change}}

提案は JSON Patch（RFC 6902）の形式で、使える操作は `add`・`replace`・`remove`・`test` です。Human が承認したものだけが、設定ファイルに反映されます。

■ 出力する項目

* type：`state_patch`
* target：`{{target}}`（この値以外にしてはいけません）
* base_hash：`{{base_hash}}`（この値をそのまま書き写してください）
* operations：変更の操作の配列。順序に意味があります。
* reason：なぜこの変更をするのかを、Human の指示に触れて1〜2文で。

■ pointer の対応表（Controller が計算したものです。添字を自分で数えないでください）

{{pointer_hints}}

■ 書いてよい変更

{{target_rules}}

■ 共通の注意

* 方針と指示に書かれていない変更をしてはいけません。
* 配列の要素を `replace`・`remove` する前に、`test` でその要素の id や値を確かめてください（例：`{"op": "test", "path": "/knowledge/2/id", "value": "K003"}`）。`test` の value は、資料の現在の値と一致させてください（一致しない提案は適用されません）。
* 変えた後のファイルは、今の形式（schema）を満たさなければなりません。資料の他の項目と同じ形で書いてください。
* 変更の操作は、必要最小限にしてください。
* 方針どおりの変更がこのファイルでは表せない場合は、operations を `test` 操作1件だけにし（{{noop_example}}）、reason にその理由を書いてください。
