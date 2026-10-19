【作業】Human の設定変更の指示について、どの設定ファイルをどう変えるかの方針（setting_change_plan）を作ってください。まだファイルの変更そのものは書きません。

上の資料は、今の設定ファイル一式と章の一覧です。

指示（instruction_id：{{instruction_id}}）：

{{instruction}}

変えてよいファイルは次のものだけです。

{{allowed_targets}}

* 人物（`characters/<ID>.yaml`）：`id` 以外のすべての項目（名前、性格、行動傾向、口調、呼び方、関係、知識、例外、変化、notes など）。人物のファイルを新しく作ったり、消したりすることはできません。
* 伏線（`foreshadowing/registry.yaml`）：既存の伏線のすべての項目（回収の予定 planned_resolution、status を `cancelled` にすることを含む）と、新しい伏線の追加。伏線を消すことはできません。

■ 出力する項目

* type：`setting_change_plan`
* instruction_id：`{{instruction_id}}`
* targets：変えるファイルごとに1件。target はファイルのパス（上の一覧のどれか）、change はそのファイルで何をどう変えるかを具体的に（次の作業で、この文だけを手がかりに変更を書きます）。
  - 指示を実現するのに必要なファイルだけにしてください。
  - 同じファイルを2回挙げてはいけません。
* new_foreshadowing：新しく追加する伏線の数（なければ 0）。追加する場合は、targets に `foreshadowing/registry.yaml` を入れ、change に追加する伏線の内容を書いてください。ID は Controller が決めます。
* questions：指示が曖昧で方針を決められない場合、または上の範囲では実現できない場合（world/ の文章の設定を変える必要がある、人物を新しく作る必要がある、など）は、Human への質問や説明を書き、targets を空にしてください。決められる場合は空の配列。

指示に書かれていない変更を足してはいけません。
