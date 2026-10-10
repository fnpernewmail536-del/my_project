# Render無料版でデータを保持する

RenderのアプリはFreeのまま使い、データをTursoのFreeプランに保存します。
Renderが停止・再起動・再デプロイしても、外部DBのデータはアプリのファイルと一緒に削除されません。

## 1. Tursoに無料DBを作る

1. https://app.turso.tech/ で無料アカウントを作成します。
2. Freeプランで、アプリ専用のDB（例: `catproxyservice`）を作成します。
3. DBエンジンを選択できる場合は **libSQL** を選択してください。
   このアプリは `libsql` SDKを使います。`--tursodb` で作成する新しいTursoエンジン用ではありません。
4. 作成したDBの接続情報から **Database URL** と読み書き可能な **Auth Token** を取得します。
   URLは `libsql://...turso.io` の形式です。

CLIを使う場合は、ログイン後に次を実行します（`--tursodb` は付けません）。

```bash
turso db create catproxyservice
turso db show catproxyservice --url
turso db tokens create catproxyservice
```

接続トークンや秘密キーはRenderの環境変数に設定してください。チャットやGitHubには貼りません。
無料のまま使う場合はFreeプランを維持し、有料の超過利用を有効にしないでください。

## 2. Renderの環境変数

対象のWeb Service → Environmentに、以下を設定します。

| Key | Value |
| --- | --- |
| `TURSO_DATABASE_URL` | 作成したlibSQL DBの接続URL |
| `TURSO_AUTH_TOKEN` | 作成したDBの読み書き可能な認証トークン |
| `FLASK_SECRET_KEY` | 再起動後も同じ固定値。設定済みなら変更しない |

`FLASK_SECRET_KEY` が未設定の場合だけ、手元の端末で作った長いランダム文字列を設定します。
例: `python -c "import secrets; print(secrets.token_hex(32))"`
既存の `IDENTITY_HASH_KEY`・`ACCOUNT_IDENTITY_HASH_KEY`・`ADMIN_SNAPSHOT_KEY` も維持します。
識別キーを変えると既存利用者との照合が変わり、復旧暗号キーを変えると保存済みデータを復号できません。

必要なら `REQUIRE_PERSISTENT_DB=1` も設定できます。
この設定ではTursoが未設定の起動を止め、一時領域への保存を防ぎます。新規Blueprintには設定済みです。

保存して再デプロイします。既存の手動作成サービスでは、GitHubのrender.yamlを変更するだけでは
Environmentに自動追加されないため、上の環境変数をRender側で追加してください。

起動コマンドは変更不要です。

```text
Build Command: pip install -r requirements.txt
Start Command: python -m gunicorn --config gunicorn.conf.py wsgi:app
Root Directory: 空欄
```

## 保存される内容

| 既存のローカル配置 | Tursoに保存する内容 |
| --- | --- |
| `ACCOUNT/account.db` | サイトアカウント、VIP、購入チャット、管理ログ、BAN、通知購読 |
| `Login/Bonus.db` | 週間利用枠、利用予約、残高、ログイン履歴 |
| `invitation/invitation.db` | 招待リンク、招待履歴、報酬状態 |
| `count.db` | 利用回数、ジョブ結果、APIキー、処理時間履歴 |
| `BACKUP/admin_recovery.db` | 復旧履歴、暗号化した復旧データ・発行結果 |

接続URLは1つだけで、アプリ内のすべてのDB処理が同じ外部DBへ接続します。
既存のテーブル名・制約・トランザクション・復旧期限を維持します。
復旧データは従来どおり暗号化し、48時間の保持期限もそのままです。
ゲーム定義のキャッシュは再取得できるため、引き続きアプリ側の一時ファイルです。

## 動作確認

1. Renderのログに `[DATABASE] Tursoの永続DBを使用します` が出ることを確認します。
2. サイトでテスト用アカウントを作成し、ログインできることを確認します。
3. Renderで再起動して、同じアカウントでログインできることを確認します。
4. 次に15分以上アクセスせず停止させ、再アクセス後にも同じアカウントでログインできることを確認します。

DB接続・認証・利用枠のエラー時は、空のローカルDBへ切り替えません。
片方の接続情報だけが設定されている場合や、固定のFLASK_SECRET_KEYがない場合は起動エラーになります。
`[DATABASE WARNING] Turso未設定` は外部DBがまだ設定されていないことを示します。

## 既存データと確認範囲

切り替え時にローカルDBから自動インポートは行いません。既存DBの上書きや古いバックアップの自動投入もしません。
再起動ですでに失われたデータは、この修正だけで元に戻りません。
既存DBやバックアップが残っている場合は、移行前に保管し、実データの状態を確認してから別途移行します。

実際のlibsql SDKをローカルのテストDBへ接続し、アプリ側ファイルを全削除して再起動した後も
アカウント・VIP・購入チャット・利用回数・利用枠・招待・ジョブ・APIキー・処理時間・暗号化復旧データが
残ることを確認しています。並行した利用枠予約、失敗時のロールバック、招待報酬の重複防止も検証しています。
Tursoの実アカウントへの接続、Render上での停止・再起動は、環境変数を設定した後に確認が必要です。

参考:
- https://render.com/docs/free
- https://turso.tech/pricing
- https://docs.turso.tech/sdk/python/quickstart
