# Render起動設定

このZIPは、提供されたmy_project(3).zipの機能・画面・素材を保ち、main13.pyをルート、画面をHTML/に移動した構成です。
ZIP内のmy_projectフォルダの中身をリポジトリ直下に置いてください。

- Root Directory: 空欄（main13.py・requirements.txt・wsgi.pyのある階層）
- Build Command: `python -m pip install -r requirements.txt`
- Start Command: `python render_start.py`

既存RenderサービスではSettingsのStart Commandも上記に設定してください。
以前の `--chdir Paython main13:app` は新しい配置に対応しません。
render.yamlは上記の起動入口を使用します。
Render既定の `GUNICORN_CMD_ARGS` に含まれる `--preload` を引き継がず、
DB接続とバックグラウンドスレッドをワーカー内で初期化します。
Gunicornの制御ソケットも無効にして、分岐前の制御スレッドを作りません。
待ち時間の上限120秒は維持します。起動を終えていないアプリを正常として扱いません。
`[STARTUP]` ログでゲームライブラリ・DB・Flaskの起動進捗を確認できます。
45秒を超える初期化ではスタックを記録し、秘密値やローカル変数は出力しません。
ローカルのWindowsではプロジェクトのルートで `python main13.py` を実行します。

## 設定とデータ

既存の環境変数と.envを維持してください。秘密キーは変更しないでください。
このZIPには元ZIPのDBとバックアップを移動して含めています。元ZIPに実際の.envはありません。
既存環境を更新する場合は、使用中のDBを停止・バックアップして移行し、同梱DBで上書きしないでください。
移動先はLAYOUT_README.txtに記載しています。DBとBACKUPはGitの除外対象です。
Render無料版でデータを保持するにはTursoの外部DBを設定してください。
設定手順と確認方法は [無料DBの設定手順](z%20readme/FREE_DATABASE_SETUP.md) を参照してください。
TURSO_DATABASE_URL・TURSO_AUTH_TOKEN設定時は、全アプリDBを同じ外部DBへ保存します。
両方が未設定の場合はローカルSQLiteを使うため、Renderの停止・再起動ではデータを保持できません。
実際のRender環境へのデプロイとゲーム通信の検証は未実施です。

