【作業】章 {{chapter_id}} の本文の文章レビュー（writing_review）を行ってください。

上の資料のうち、chapters/{{chapter_id}}/draft.md がレビューする本文です。本文の `<!-- scene: S1 -->` のような行は場面の区切りで、本文の一部ではありません。

文章としての問題を指摘してください。設定・plan との整合は別の検査で扱うので、ここでは指摘しないでください。指摘は Human が読み、採用したものだけが修正に回ります。問題がなければ findings は空で構いません。

指摘の種類（category）：

* unnatural：不自然な表現
* redundant：冗長な表現
* over_explained：説明過多
* monotonous_endings：文末の単調さ（同じ文末が続くなど）
* cliche：定型表現
* other：上のどれにも当たらない文章上の問題（rules/style.md や plan の style_notes に反する文体など）

各 finding の書き方：

* id：W1 から順に連番。
* anchor：指摘する箇所。text は本文からそのまま抜き出した文字列（場面の区切りの行を含めない）、before・after はその直前・直後の本文の数十文字（なければ空文字）。
* message：何が問題かを1〜2文で。
* suggestion：書き換えの案（anchor の text を置き換える文）。案を出さない場合は null。

本文全体の書き直しはしないでください。

* type：`writing_review`
* chapter_id：`{{chapter_id}}`
