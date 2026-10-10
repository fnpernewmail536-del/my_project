# ゲーム版数・キャラ定義の自動更新

## 適用

1. 差分ZIPのmain13.pyを、現在使用中のmy_project直下のmain13.pyへ上書きする。
2. z readmeの説明書・ライセンスも同名フォルダへコピーする。
3. ローカルではPythonを停止して再起動する。RenderではGitへ反映して再デプロイする。
4. ブラウザでCtrl＋F5を押す。

既存の.env・DB・BGM・背景・HTML・JavaScript・Render起動設定は維持する。
今回の最低版はJP 15.7.1。15.7.0で追加した5キャラ・新形態3件も同梱したまま。

## 自動更新の動作

- 起動時に確認し、稼働中は1時間ごとに再確認する。
- 公式日本版App StoreのアプリID547145938・bundleId jp.co.ponos.battlecatsを照合して最新公開版数を取得する。
- 15.10.0と15.9.0などは数値で比較する。古いストア応答でも版数を下げない。
- 通信・新規作成・複製・編集後保存には、処理開始時に取得済みの最新公開版を使う。途中の更新検出で開始済みジョブの版数は変更しない。
- battlecatsinfoの同一コミットのcat.tsv・catstat.tsv・pools.jsonから、キャラ名・保存番号・レアリティ・実装形態・本能・ガチャ所属を取得する。
- BCDataに新しいJPデータが公開されたら、キャラ定義とrankGift.csvも取得する。
- 画面表示・検索・選択・全解放・全本能は同じ検証済みのキャラ定義を使う。召喚される精霊の内部枠は通常のキャラ選択・全解放から除外する。
- UR報酬はrankGift.csvの行番号を保存indexとして扱い、表示用一覧で並べ替えない。既存indexが変わるデータは自動採用しない。
- 取得失敗・欠損・不正な形式・既知の形態を減らす定義は採用せず、前回の検証済み状態を保持して5分後に再試行する。
- 更新キャッシュはBACKUP/game_updates.jsonへ保存する。書込不能でも稼働中の更新結果は保持する。
- 再起動時はキャッシュを再検証して読み込み、最新確認も再実行する。キャッシュがなくても同梱の版数・キャラ一覧で起動できる。
- Renderなどでプロセスが休止している間は確認しない。復帰・起動後に再開する。
- 既に開いているブラウザ画面は再読込すると新しい一覧が反映される。

## 確認方法

サイトURLの末尾に/api/game_update_statusを付けて開くと、公開情報だけを確認できる。

| 項目 | 意味 |
| --- | --- |
| game_version | 通信・作成・保存で使用する最新公開ゲーム版数 |
| character_data_version | 実際に取得して検証できたキャラ定義の版数 |
| rank_gift_data_version | 実際に取得して検証できたUR報酬定義の版数 |
| status | pending: 確認中、ok: 確認成功、partial: 一部取得失敗、offline: 取得失敗 |
| last_checked_at / next_check_at | 前回確認・次回確認予定のUnix時刻 |

15.7.1は不具合修正版なので、ゲーム版数15.7.1・キャラ定義15.7.0の組合せでも正常。
「ゲームの版数だけ新しい」と「新キャラ定義が実際に取得できた」を区別する。

任意設定（通常は設定不要）:
- AUTO_GAME_UPDATE_ENABLED=0: 外部の自動確認を停止する。同梱版・保存済みキャッシュは使う。
- GAME_UPDATE_CACHE_PATH: キャッシュの保存先を変更する。

## 対応範囲・検証

これは公開された版数・定義データを反映する仕組み。Pythonコードやbcsfeライブラリ自体は自動書換えしない。
配布元の公開が遅れる場合、新キャラ等は公開・検証できてから反映する。未知の番号・形態・報酬条件は推測しない。
ゲームの保存形式や通信方式が大きく変わった場合は別途コード・ライブラリの修正が必要になる。

オフラインで将来版の更新・欠損データ・通信失敗・再起動キャッシュ・並行実行・処理中の版数保持を検証。
4つの作成テンプレートで追加キャラ・新形態・UR報酬を保存して再読込する処理も検証。
公開App Store API・同一コミットのGitHub TSV・BCDataメタデータへの実際の読み取りも確認。
実際のゲームアカウントの引き継ぎ・ゲーム起動は未確認。

取得元:
- https://itunes.apple.com/lookup?id=547145938&country=jp
- https://github.com/battlecatsinfo/battlecatsinfo.github.io
- https://git.battlecatsmodding.org/fieryhenry/BCData/raw/branch/main/metadata.json

battlecatsinfoのMIT Licenseは同フォルダのBATTLECATSINFO_LICENSE.txt。
