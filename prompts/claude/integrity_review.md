【作業】章 {{chapter_id}} の本文の整合性ゲート（integrity_review）を行ってください。

上の資料のうち、chapters/{{chapter_id}}/draft.md が検証する本文、chapters/{{chapter_id}}/plan.yaml がこの章の執筆計画、chapters/{{chapter_id}}/outline.md が Human の書いた章の概要です。その他の資料は、本文を書いたときに渡したものと同じ版です。本文の `<!-- scene: S1 -->` のような行は場面の区切りで、本文の一部ではありません。

本文を資料と照らし合わせ、次の7つの観点を検査してください。資料に書かれていることだけを根拠にし、資料にない設定を正しいものとして補ってはいけません。

* character：人物の言動が characters/ の設定と合っているか。一人称（speech.first_person）、相手ごとの呼び方（address。changes があれば、その場面の位置で有効な値）、口調（speech の formality・endings・habits・forbidden）、性格・行動傾向（personality・behavior）、知識（knowledge と過去の章の summary にない情報を知っていないか）、人間関係（relationships）。exceptions に当たる場面では、その例外を正しいものとする。
* world：世界観の設定（world/ など）と矛盾していないか。
* timeline：出来事の順序・時間の経過が、plot/timeline.yaml、過去の章の summary、この章の中で矛盾していないか。
* plot：outline と plan にない出来事を加えていないか、outline の出来事が抜けていないか。
* foreshadowing：plan の場面が扱う伏線（F001 など）が、registry.yaml と plan のとおりに扱われているか。回収の内容が設定・plot と矛盾していないか。
* plan_compliance：plan の scenes（順序・内容・登場人物）、pov、style_notes、prohibitions、connection に従っているか。
* undefined_setting：資料にない設定（人物、地名、組織、出来事、能力、物の名前、時系列）を、本文が新しく決めていないか。

判定の基準：

* 各観点の result は PASS・WARNING・STOP のいずれかです。問題がなければ PASS とし、findings は空にしてください。
* STOP にするのは、世界観の矛盾、重大なキャラクター逸脱、時系列の矛盾、伏線の破壊、未定義設定の発明、plan 違反、そしてあなたが判断できない場合だけです。それ以外の問題は WARNING にしてください。
* 性格・行動傾向の判定は WARNING を基本とし、明白な逸脱だけを STOP にしてください。
* 伏線を「回収すべき」と独断で決めてはいけません。予定の変更や意図的な未回収かどうかは Human が判断します。
* result が PASS の観点に、severity が STOP の finding を入れてはいけません。
* 全体の result は、7つの観点の result のうち最も重いもの（STOP ＞ WARNING ＞ PASS）にしてください。

各 finding の書き方：

* severity：WARNING か STOP。
* anchor：問題の箇所。text は本文からそのまま抜き出した文字列（場面の区切りの行を含めない）、before・after はその直前・直後の本文の数十文字（なければ空文字）。本文の特定の箇所に当たらない問題（場面が抜けているなど）は null。
* message：何が問題かを1〜2文で。character の finding では、人物 ID、規則（例：`speech.first_person`、`address.C002`）、設定での期待値、本文での値、場面の ID を含めてください。
* evidence：根拠にした資料の箇所（例：`characters/C001.yaml speech.first_person: 俺`、`plan.yaml scenes S2 summary`）。

本文の書き直しや修正案は不要です。検出だけを行ってください。

* type：`integrity_review`
* chapter_id：`{{chapter_id}}`
