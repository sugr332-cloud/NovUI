【作業】Human から章 {{chapter_id}} についての指示がありました。指示を実行せず、どの作業（Job）で実現するかの案を作ってください。

上の資料のうち、chapters/{{chapter_id}}/outline.md が Human の書いた章の概要、chapters/{{chapter_id}}/plan.yaml がこの章の執筆計画です。{{draft_note}}

指示：

{{instruction}}

指示の対象（scope）：{{scope}}
{{scope_detail}}

章の状態：{{chapter_state}}

使える作業は次の3つだけです。

* `range_edit`：本文の一部（範囲）だけを書き直す。本文があるときだけ使えます。範囲は anchor で指定します（text は本文にそのまま1回だけ現れる文字列。同じ文字列が2回以上現れるなら before・after に直前・直後の文字列を入れて1か所に決まるようにする）。範囲に場面の区切りの行（`<!-- scene: … -->`）を含めてはいけません。範囲は1つの場面の中に収めてください。
* `chapter_rewrite`：plan を変えずに、章の本文全体を書き直す。本文があるときだけ使えます。
* `plan_revision`：plan（場面の構成など）を改訂する。改訂すると今の本文は捨てられ、改訂した plan を Human が承認した後に、本文を書き直します。そのため、plan_revision の後に本文を書き直す必要があれば、続けて `chapter_rewrite` を `depends_on`（plan_revision の番号）付きで提案してください。

各項目の書き方：

* type：`instruction_routing`
* instruction_id：`{{instruction_id}}`
* scope：`{{scope}}`
* chapter_id：`{{chapter_id}}`
* instruction：上の指示をそのまま写す。
* proposed_jobs：作業の案を、実行する順に。各件は job_type、summary（何をするかを1文で。Human が承認の判断に使う）、instruction（その作業の担当に渡す具体的な指示。何をどう変えるかを、指示の意図に沿って具体的に）、anchor（range_edit のときの範囲。それ以外は null）、depends_on（先に終わっている必要がある案の番号。0 始まり。なければ null）。
  - 必要最小限の作業にしてください。range_edit で足りるなら chapter_rewrite にしない、本文の書き直しで足りるなら plan_revision にしない。
  - 1つの range_edit は1か所だけです。複数か所を直すなら、range_edit を複数並べてください。
* scope_mismatch：指示を実現するのに必要な変更の単位が scope と異なる場合（例：scope が range なのに章全体の書き直しが要る）は true にし、scope_mismatch_reason に理由を書く。scope を勝手に広げた案を出してはいけません。異ならなければ false と null。
* impact：この変更が他の章や設定に影響する場合、その対象（ファイルのパスや章 ID）と理由。なければ空の配列。
* questions：指示が曖昧で案を決められない場合は、Human への質問を書き、proposed_jobs を空にする。決められる場合は空の配列。

設定（characters/、world/、foreshadowing/ など）を変える作業は、ここでは提案できません。指示が設定の変更を必要とする場合は、questions にその旨を書いてください。
