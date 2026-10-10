フォルダ構成の統一

main13.py                    Flaskアプリ本体
job_timing.py                既存の処理時間予測モジュール
HTML/                        元templates/の全画面
static/                      元のBGM・背景動画・JavaScript・CSS・フォント
ACCOUNT/                     アカウント処理とaccount.db一式
DISCORD/                     Discord連携
Paython/                     既存の通信・支払いモジュール、awswaf等
Nyanko_new                   新規アカウント用データ
Nyanko_beginner              初心者用データ
Nyanko_Intermediate          中級者用データ
Nyanko_advanced              上級者用データ
free_usage.py                無料利用関連
count.db                     利用回数・ジョブ記録
Login/                       ログインボーナスDB
invitation/                  招待DB
BACKUP/                      管理者復旧DB
wsgi.py / gunicorn.conf.py   Render起動設定

起動
Windowsのコマンドプロンプトで、main13.pyのあるフォルダへ移動して実行：
python main13.py
ブラウザで http://127.0.0.1:5001/ を開きます。
Renderの設定はRENDER_SETUP.mdを参照してください。

変更範囲
構成変更ではmain13.pyのstaticとHTMLを探す基準パス、テンプレートのフォルダ名を変更しました。
wsgi.pyはルートのmain13.pyを読み込みます。ゲーム処理、API、権限、テーマ、BGM再生処理は変更していません。
HTML・staticの内容、4つのNyankoデータ、DB・WAL・SHMの内容は元ZIPのままです。
Windows ZIPの日本語ファイル名はCP932として読み取り、出力ZIPではUnicodeで保持しています。

VIPガイドのDiscordログイン
/vip-guideのヘッダーへ「Discordでログイン（管理者用）」を追加しました。
ログイン完了後は/vip-guideへ戻ります。
管理者認証済みなら「管理パネル」「VIP管理」が表示されます。
管理者の判定は従来どおり、既存ADMIN_USERとDiscordユーザーIDの照合を使用します。

既存環境を更新する場合
1. サーバーを停止し、既存プロジェクト全体をバックアップします。
2. 別フォルダへ解凍した今回のコード・素材へ、既存の.envと使用中のDBを引き継ぎます。
3. 既存DBは次の配置へ移動します。同梱DBで使用中のデータを上書きしないでください。
   Paython/count.db と -wal/-shm       → ルートのcount.dbと同名の付随ファイル
   Paython/ACCOUNT/account.db一式    → ACCOUNT/account.db一式
   Paython/Login/                    → Login/
   Paython/invitation/               → invitation/
   Paython/BACKUP/                   → BACKUP/
4. 既存の.gitを使う場合はそのまま維持し、新しい構成のソースを反映します。
5. 新しい起動コマンドで再起動します。Paython/main13.pyは使用しません。

提供されたmy_project(3).zipにないCHATフォルダ等の機能は追加していません。
今回の変更は配置の変更です。過去に渡したタイムアウト対策パッチは新たに適用していません。
