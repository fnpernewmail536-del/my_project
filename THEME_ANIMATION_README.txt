CPS テーマ・アニメーション更新 v36

追加内容
・エヴァ／エヴァ・アンバー：処理開始時に全画面HUDを展開。大型7セグタイマー、走査線、回転リング、処理段階、学習件数、実測時間のばらつきを表示します。
・「小さくする」またはEscで元の画面へ戻れます。処理は続きます。結果枠のボタンで再表示できます。
・サーバーが完了を通知した場合だけ完了演出を出し、約2.4秒後に結果へ戻ります。
・マイクラ：ツルハシの振り下ろし、段階的なひび割れ、打撃時の破片、ブロック破壊をCanvasで描画します。
・テーマ設定で「採掘」「TNT」「クリーパー」「おまかせ」を選べます。TNT／クリーパーは待機中の導火線・接近演出と完了時の爆発・煙・破片を表示します。
・8秒プレビューは実際の処理を行いません。プレビューは学習データに含めません。
・OSの動きを減らす設定を尊重します。外部動画・音声は追加していません。既存のBGM機能は使えます。

残り時間の学習
・処理の種類、件数、似た設定ごとに成功した処理を学習します。サーバーのcount.dbへ時間情報と設定の要約ハッシュを保存します。認証コード・トークン・アカウント情報は学習に使いません。
・接続／保護／作成／適用／保存の実測から、その段階以降の残り時間を予測します。複数作成・複製では、今回の完了済み件数の実績でも補正します。
・サーバーの待機列にいる時間は処理時間から除外します。
・最近の履歴を重視します。最大90日、2000件のサーバー履歴を保持します。ブラウザにも成功時間の履歴を保持します。
・最初は目安です。過去の実測が増えると予測が改善しますが、外部通信の遅延を含むため正確な完了時刻は保証できません。「±秒」は過去の所要時間のばらつきで、今回の誤差の保証ではありません。
・通信が途切れた場合や現在の段階が予測を超えた場合は不確定表示に戻します。タイマーがゼロになっただけでは完了扱いにしません。
・サーバーの保存先が再デプロイなどで消える環境ではサーバーの学習もリセットされます。同じブラウザの履歴はフォールバックとして使います。

開始時の待ち時間
・空のキャラ指定、城指定、ステージ指定、施設、オーブ、空の編成で不要な外部metadata取得をしないよう改善しました。
・詳細を選択した場合の外部通信や混雑など、すべての60秒タイムアウトを解消したという意味ではありません。まだ出る場合は開始した操作と同時刻のRenderログを確認してください。開始済みか不明なときは連続再実行しないでください。

更新方法（Git接続済みの既存my_projectを使います）
1. このZIPを既存my_projectとは別の場所へ展開します。
2. 展開したmy_projectから、次の8ファイルだけを既存my_projectの同じ場所へコピー・上書きします。
   Paython/main13.py
   Paython/job_timing.py（新規・必須）
   static/js/theme-motion.js
   static/js/mc-motion.js（新規）
   static/css/site-motion.css
   templates/site_enhancements.html
   templates/theme_manager.html
   THEME_ANIMATION_README.txt
3. 既存の.git、.env、各.dbファイル、BGMファイルはそのまま使います。既存my_projectフォルダ自体は削除・置き換えません。
4. PowerShellで次を実行します。
   cd C:\Users\a\Desktop\my_project
   git add Paython/main13.py Paython/job_timing.py static/js/theme-motion.js static/js/mc-motion.js static/css/site-motion.css templates/site_enhancements.html templates/theme_manager.html THEME_ANIMATION_README.txt
   git status
   git commit -m "Add fullscreen EVA and Minecraft effects"
   git push
5. Renderへの反映後、ブラウザを更新し、テーマ設定の8秒プレビューで確認します。

この更新では追加のPythonパッケージは不要です。
Gunicornの既存の起動設定はそのまま使えます。
新しいmodule（Paython/job_timing.py）も必ず一緒に反映してください。
