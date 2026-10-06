【作業】章 {{chapter_id}} の本文の文章を読み、直すとよくなる箇所を指摘してください（文章レビュー）。

上の資料のうち、chapters/{{chapter_id}}/draft.md が対象の本文です。本文中の `<!-- scene: S1 -->` の行は場面の区切りの印で、本文ではありません。設定との整合性は別の検査で見るので、ここでは文章そのものだけを見てください。

指摘の種類（category）：

| category | 内容 |
|---|---|
| unnatural | 日本語として不自然な表現 |
| redundant | 冗長な表現、同じ内容の繰り返し |
| over_explained | 説明しすぎ（読者に任せてよいことを地の文で説明している） |
| monotonous_endings | 文末の単調さ（同じ文末が続く） |
| cliche | 使い古された定型的な表現 |
| other | 上のどれにも当たらないもの |

* 指摘は重要なものから最大10件まで。問題がなければ findings は空で構いません。
* id は W1 から連番。
* anchor：text は本文からそのまま抜き出した短い文字列（本文中で1か所に定まる長さにする）、before と after はその直前・直後の数文字（なければ空文字）。
* message：何が問題か。suggestion：書き換えの案（案を出せない場合は null）。書き換えの案で、設定にない固有名や新しい事実を加えないでください。

type は `writing_review`、chapter_id は `{{chapter_id}}` です。
