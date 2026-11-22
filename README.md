# 受信

Herdr のレイアウトです。メール、X の DM、予定を一つの画面に並べ、選んだやり取りを右のペインで読み、Agent に相談できます。

個人のメール、DM、予定、アカウント、接続情報は含みません。このリポジトリにあるのは画面の定義と、それを動かすコードだけです。

## 入っているもの

- `layouts/inbox.json` と `layouts/inbox.svg`
- `mods/inbox/`
- `themes/light.json`（ライト）と `themes/yumekawa.json`（ユメカワ）

## 置く場所

Herdr のホーム（通常は `~/.config/herdr-app`）に、同じ相対パスでコピーします。同名のファイルがある場合は上書きしません。

```sh
herdr-app check ~/.config/herdr-app/mods/inbox
herdr-app do layout.switch '{"name":"inbox"}'
```

## 動き始めるまでに必要なもの

- メールと予定: `gws` で Gmail とカレンダーに接続する。予定を作るにはカレンダーの書き込み許可も要る
- X の DM: Cursor の X 接続と、この PC の `~/.config/CHAT_PIN`
- 要約: `cursor-agent`

接続や PIN は、このリポジトリには入っていません。それぞれの PC で用意します。
