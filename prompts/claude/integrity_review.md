【作業】章 {{chapter_id}} の本文の整合性を検査してください（整合性ゲート）。

上の資料のうち、chapters/{{chapter_id}}/draft.md が検査する本文、chapters/{{chapter_id}}/plan.yaml がこの章の執筆計画です。本文中の `<!-- scene: S1 -->` の行は場面の区切りの印で、本文ではありません。

次の7つの観点で検査し、観点ごとに result（PASS・WARNING・STOP）と findings（指摘の一覧）を返してください。

| 観点 | 検査すること |
|---|---|
| character | 人物の一人称・口調・相手の呼び方が characters/ の設定と合っているか。性格・行動傾向から明らかに外れた言動がないか。人物がまだ知らないはずの情報を知っていないか |
| world | 世界観の設定（world/ など）と矛盾する描写がないか |
| timeline | 時系列（plot/timeline.yaml、前の章の要約）と矛盾がないか |
| plot | outline と plan の出来事が描かれているか。plan にない大きな出来事が加わっていないか |
| foreshadowing | 伏線（foreshadowing/registry.yaml、plan の scenes の foreshadowing）が plan どおりに扱われているか。伏線を早すぎる時点で明かしていないか |
| plan_compliance | plan の場面の順序、視点（pov）、書いてはいけないこと（prohibitions）が守られているか |
| undefined_setting | 設定にない固有名（人物・地名・組織・店・物の名前）、人物の過去・経歴・能力・人間関係、世界のしくみ・歴史・規則を、本文が新しく決めていないか（情景・感覚・しぐさ・名前のない端役の描写は問題にしない） |

判定の基準：

* STOP：世界観の矛盾、人物の重大な逸脱、時系列の矛盾、伏線の破壊、設定にない重要事項の発明、plan への明白な違反、または判断できない場合。
* WARNING：軽い不一致、気になる点、Human の判断が必要な点。
* PASS：問題がない。
* 全体の result は、7つの観点の result のうち最も重いもの（STOP ＞ WARNING ＞ PASS）にしてください。観点の result が PASS なら、その観点の findings に severity STOP の指摘を入れないでください。

findings の各指摘：

* severity：WARNING か STOP
* anchor：本文の該当箇所。text は本文からそのまま抜き出した短い文字列（本文中で1か所に定まる長さにする）、before と after はその直前・直後の数文字（なければ空文字）。本文全体に関わる指摘など、箇所を示せない場合は null。
* message：何が問題か（日本語）
* evidence：根拠となる資料の記述（「characters/C001.yaml：first_person は 俺」のように、資料のパスと内容）

type は `integrity_review`、chapter_id は `{{chapter_id}}` です。
