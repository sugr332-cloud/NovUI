章 {{chapter_id}} の本文を書いてください。

上の資料のうち、chapters/{{chapter_id}}/plan.yaml がこの章の執筆計画、chapters/{{chapter_id}}/outline.md が Human の書いた章の概要です。plan の pov（視点）、style_notes（文体の注意）、scenes（場面の構成）、prohibitions（書いてはいけないこと）、connection（前の章・次の章とのつながり）に従ってください。outline と plan にない出来事を加えてはいけません。

* 場面は plan の scenes の順に、{{scene_ids}} の {{scene_count}} 場面を書いてください。場面を増やしたり、減らしたり、順番を変えたりしてはいけません。
* 各場面の先頭に、その場面の区切りの行を1行だけ置いてください。区切りの行は次のとおりで、前後に他の文字を付けてはいけません。

{{scene_marker_lines}}

* 区切りの行は本文の一部として出力してください。区切りの行のほかに、見出しや場面の番号は書かないでください。
* 本文の長さは、空白と改行を除いて {{min_chars}} 字以上 {{max_chars}} 字以下にしてください。
* 資料にない設定が必要になった場合は、推測で決めずに、その箇所に【要確認：何が不明か】と書いてください。
