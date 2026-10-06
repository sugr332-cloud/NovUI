章 {{chapter_id}} の本文を書いてください。

上の資料のうち、chapters/{{chapter_id}}/plan.yaml がこの章の執筆計画、chapters/{{chapter_id}}/outline.md がこの章の outline です。

* plan の scenes の順に、すべての場面を書いてください。場面を足したり、順番を入れ替えたり、省いたりしてはいけません。
* 各場面の書き始めに、その場面の ID を次の形の1行で書いてください（この行は本文とは別の、場面の区切りの印です）：
  `<!-- scene: S1 -->`
  ID は plan の scenes の id と同じにし、それぞれちょうど1回だけ書いてください。
* 各場面では、plan の summary と actions の出来事を描いてください。characters にない人物を名前付きで登場させてはいけません。
* plan の pov（視点）、style_notes（文体の注意）、prohibitions（書いてはいけないこと）を守ってください。
* 人物の話し方は characters/ の設定（一人称、口調、相手の呼び方）に従ってください。
* 本文の長さは、空白を除いて {{min_chars}}〜{{max_chars}} 文字にしてください。
* 章の題名や見出しは書かないでください。最初の行は `<!-- scene: S1 -->` です。
