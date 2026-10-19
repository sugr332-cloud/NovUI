【作業】Human から設定についての指示がありました。指示を実行せず、どの作業（Job）で実現するかの案を作ってください。

上の資料は、今の設定ファイル一式です。{{chapter_note}}

指示：

{{instruction}}

指示の対象（scope）：setting

使える作業は `setting_change` だけです。setting_change は、人物の設定（`characters/<ID>.yaml`）と伏線の一覧（`foreshadowing/registry.yaml`）を変える作業で、変える内容は Human の承認を受けてから設定に反映されます。

各項目の書き方：

* type：`instruction_routing`
* instruction_id：`{{instruction_id}}`
* scope：`setting`
* chapter_id：{{chapter_id_value}}
* instruction：上の指示をそのまま写す。
* proposed_jobs：`setting_change` を1件だけ。job_type は `setting_change`、summary は何をどう変えるかを1文で（Human が承認の判断に使う）、instruction はその作業の担当に渡す具体的な指示、anchor は null、depends_on は null。
* scope_mismatch：指示が人物・伏線の設定の変更ではない場合（本文や plan の書き直し、world/・plot/・rules/ の文章の設定の変更など）は true にし、scope_mismatch_reason に理由を書き、proposed_jobs を空にする。world/・plot/・rules/ の文章で書かれた設定は、Human が直接編集します。そうでなければ false と null。
* impact：この変更が影響しそうな章や設定（章 ID やファイルのパス）と理由。詳しい分析は setting_change の中で行うので、分かる範囲でよい。なければ空の配列。
* questions：指示が曖昧で、どの設定をどう変えるか決められない場合は、Human への質問を書き、proposed_jobs を空にする。決められる場合は空の配列。
