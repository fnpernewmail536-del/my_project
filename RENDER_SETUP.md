# Render用 my_project

既存のRenderサービスの Settings を次に設定してください。
- Root Directory: 空欄（requirements.txt と wsgi.py がある階層）
- Build Command: `pip install -r requirements.txt`
- Start Command: `python -m gunicorn --config gunicorn.conf.py wsgi:app`
- Environment: `PYTHON_VERSION=3.13.7`

Procfileを置き換えるだけでは、既存サービスのStart Commandは更新されません。
保存後、更新したソースをGitHubへ送って Manual Deploy で最新コミットをデプロイしてください。
ZIPはmy_projectフォルダを含みます。そのフォルダの中身をリポジトリ直下に置いてください。

## 環境変数
既存のRender環境変数を維持してください。FLASK_SECRET_KEY・ADMIN_SNAPSHOT_KEY・IDENTITY_HASH_KEY等の既存キーは変更しないでください。
Discord設定とADMIN_USER、必要ならDISCORD_BOT_TOKEN・PROXY_URLも既存設定を使用してください。
PUBLIC_BASE_URLは実際のサイトURL、DISCORD_REDIRECT_URIは既存のOAuthコールバックURLに合わせ、Discord Developer Portalにも同じURIを登録してください。
秘密の値はGitHubに追加しないでください。

## データについて
この配布ZIPには.env、Git履歴、実行中のDB・バックアップ・キャッシュを含めていません。
既存PCへ展開する場合、既存のDB・BACKUP・.envを削除しないでください。
このアプリはSQLiteを使用します。Renderの通常のローカルファイル領域では、再デプロイなどで実行中のデータを失う可能性があります。
データの永続化には永続ディスクとDB保存先の対応、または外部DBへの移行が別途必要です。今回のZIPはその移行を行っていません。

## 変更内容・確認
起動入口wsgi.pyでルートのACCOUNTパッケージを優先し、Paython/ACCOUNTデータフォルダとの衝突を防止します。
GunicornはRenderのPORTを使用し、メモリ内ジョブ管理に合わせてワーカーを1つにしています。
Northflank用8080固定・chdir起動を廃止しました。
元データは2026年10月10日に提供されたmy_project(2).zipです。後からPCで加えた未提供の変更は含められません。
実際のRender環境へのデプロイ検証は未実施です。
