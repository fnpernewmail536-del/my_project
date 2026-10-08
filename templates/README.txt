Minecraftテーマ v9

Flask用の正しい配置です。

templates/
  HTMLファイル

static/
  videos/
    minecraft_bg.mp4

重要:
minecraft_bg.mp4 は templates の中ではなく、Flaskアプリの static/videos/ に置きます。
HTMLでは url_for('static', filename='videos/minecraft_bg.mp4') を使用します。
