【作業】章 {{chapter_id}} の執筆計画（plan）を作ってください。

上の資料のうち、chapters/{{chapter_id}}/outline.md がこの章の outline（Human が書いた章の目的・出来事・登場人物・伏線・次章への接続）です。outline の内容を変えたり、outline にない出来事を加えたりしてはいけません。outline が曖昧な部分は、資料の範囲で最も自然な解釈を選び、選んだことを該当する場面の summary に書いてください。

各項目の書き方：

* type：`plan`
* chapter_id：`{{chapter_id}}`
* pov：視点（例：「三人称・C001 に寄り添う」）。outline や rules/style.md に指定があればそれに従う。
* style_notes：rules/style.md とこの章の内容から、この章で特に守る文体の注意を数個。
* target_chars：本文の目標文字数（空白を除く文字数）の範囲。outline に指定があればそれに従い、なければ min 4000、max 6000。
* context：この章の執筆と検証に必要な資料。
  - past_summaries：要約が必要な過去の章の ID（直前の章は必ず含める。最初の章なら空）
  - past_drafts：本文そのものが必要な過去の章の ID（台詞や描写を正確に引き継ぐ必要がある場合だけ。通常は空）
  - settings：参照が必要な設定ファイルのパス（資料に `【ファイル：<パス>】` として現れたパスだけを使う）
* scenes：場面の構成。id は S1 から順に連番。各場面に、summary（何が起きるか）、actions（主な行動）、characters（登場人物の ID）、settings_used（使う設定の要素。資料にある語で）、foreshadowing（扱う伏線の ID。なければ空）を書く。
* prohibitions：この章で書いてはいけないこと（まだ明かさない情報、使わない表現など）。
* connection：from_previous（前の章からのつながり。最初の章なら「冒頭」）、to_next（次の章へのつながり）。
