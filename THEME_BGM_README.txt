CPS 写真風エヴァテーマ・BGM機能
=============================

■ テーマを選ぶ
画面右下「表示 / BGM / Language」を開き、テーマを選んでください。
・エヴァ：赤・緑の管制画面
  黒地、赤い輪郭文字、緑の六角形、警戒ストライプ、グリッド、波形、日本標準時。
・エヴァ：アンバー端末
  写真の端末のようなオレンジ／アンバー色と緑の表示。
テーマ設定ページ /theme-settings の選択肢からも変更できます。
設定はブラウザごとに保存され、サービス・アカウント・管理画面に反映されます。
大きな端末コードや波形は装飾です。サーバーの稼働状況・実際の計測値ではありません。

■ BGMを追加する
my_project/static/bgm/ に音楽ファイルを入れてください。
MP3、OGG、WAV、M4A、AAC、FLACを一覧に表示します。
MP3がおすすめです。対応コーデックはブラウザによって異なります。
フォルダとREADME、.gitkeepを用意してあります。音楽ファイルは未同梱です。

例:
my_project/
  Paython/main13.py
  templates/site_enhancements.html
  static/bgm/01-control-room.mp3
  static/bgm/02-night-terminal.mp3

このフォルダの音楽はサイト利用者にも公開されます。
拡張子に対応していても、音声形式が非対応の場合はエラーメッセージが出ます。

■ BGMを再生する
画面右下「表示 / BGM / Language」→ 曲を選択 →「再生」。
テーマ設定ページには大きなBGM設定欄もあります。
一時停止、停止、音量、繰り返しを操作できます。
初期状態は無音、音量30%、繰り返しONです。
曲と音量、繰り返し、再生位置はブラウザに保存します。
ページ移動後は「再生」を押すと、前回の曲の続きから再生できます。
画面を離れたり、別のタブでBGM設定を変えた場合は一時停止します。

「端末の音楽ファイルを試す」から選んだ曲は、そのページだけで試聴できます。
端末から選択したファイルはサーバーへアップロードしません。
この試聴ファイルはページ移動・再読み込み後に再選択してください。

■ GitHub / Renderへ反映する
ZIP内のmy_projectを、今のローカルmy_projectへ上書きしてください。
プロジェクトのフォルダ内で変更を確認し、GitHubへ送ります。

git add .
git status
git commit -m "Add EVA themes and BGM player"
git push

Renderで自動デプロイしていない場合:
Manual Deploy → Deploy latest commit

■ 主な追加・変更
Paython/main13.py: 2つのテーマと /api/theme/bgm の曲一覧。
templates/site_enhancements.html: 全ページ共通のテーマ・BGM読み込み。
templates/theme_manager.html: テーマ選択肢とBGM設定欄。
static/css/site-eva.css: 赤・緑 / アンバーの2テーマ。
static/js/eva-hud.js: 端末表示と日本標準時。
static/css/site-bgm.css / static/js/site-bgm.js: BGMプレイヤー。
static/bgm/: 音楽の追加先。
各ページのテンプレート、theme-local.js、site-locale.jsを更新しています。
既存のアカウント・VIP・Discord認証の判定は変更していません。
