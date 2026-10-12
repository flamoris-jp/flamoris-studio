# Studio：Updaterの入力値と導入後の設定

対象は公開済み1.0.3のインストール定義です。別の版では選定Releaseのカタログと[インストール定義](../release/install-recipe.json)を照合します。
Updaterの画面には短い項目名が表示されます。本書で用途・取得元・空欄時の動作を確認してください。
既定値は入力欄の初期値で、generate=true以外の欄を空にしても自動生成されません。
共通のDB・秘密値の扱い、配置の調べ方、作業の順番は[共通設定ガイド](https://github.com/flamoris-jp/flamoris-updater/blob/main/docs/APPLICATION_SETUP.md)にあります。

## 導入前に用意するもの

PostgreSQL管理者接続とDBの到達性、Studioを開くURLを用意します。
Generation／Agent／raw Intelligenceの連携は任意です。使う連携について接続先を準備します。
Updaterは新規DB・role作成とアプリ所有のAlembicを実行します。導入後に初期migrationを重複実行しません。

## Updaterに入力する値

| 項目（キー） | 用途・値の用意 | 初期表示／空欄の動作 |
| --- | --- | --- |
| 待受ポート（PORT） | ブラウザー／proxyからStudioへ接続するホスト側ポート | 5087 |
| PostgreSQL管理者接続（ADMIN_DSN） | 新規DB・role作成用。既存DB管理者から取得した接続文字列 | 必須、自動生成なし |
| DBホスト（PGHOST） | ホスト側初期化とStudioコンテナ双方から到達できるPostgreSQLの名前／アドレス | 必須、自動生成なし |
| DBポート（PGPORT） | 既存PostgreSQLの待受ポートを確認 | 5432 |
| 新規DB名（DATABASE） | Studio用に新しく作るDB名を選ぶ | flamoris_studio。既存DBの再初期化には使わない |
| DB所有者パスワード（OWNER_PASSWORD） | 新しいflamoris_studio_owner用。既存管理者のパスワードとは別 | 空欄でUpdaterが生成 |
| アプリ用DBパスワード（RUNTIME_PASSWORD） | 新しいflamoris_studio_app用。StudioがDBへ接続する値 | 空欄でUpdaterが生成 |
| Controller URL（GENERATION_ENDPOINT） | Generationをhostingする接続先の実際のURL。正確な/api/v1/generationのbaseを含める | 空欄で生成連携は未設定 |
| Controller token（GENERATION_TOKEN） | Generation側CONTROLLER_TOKENと同じ値を使う。生成元の保存済み設定から取得 | 任意、自動生成なし。生成連携にはURLとtokenの両方が必要 |
| Agent URL（AGENT_ENDPOINT） | Agent serviceのorigin。/api/v1や/mcpを付けない | 空欄でAssistant連携は未設定 |
| Agent token（AGENT_TOKEN） | Agent側AGENT_TOKENと同じ値を使う。生成元の保存済み設定から取得 | 任意、自動生成なし。Assistant連携にはURLとtokenが必要 |
| raw推論URL（INTELLIGENCE_ENDPOINT） | 直接接続するllama.cppのbase URL。MCP／HubのURLではない | 空欄でraw Intelligence連携は未設定 |
| rawモデル一覧（INTELLIGENCE_MODELS） | 実際のprovider model IDに対応するStudio設定JSON。構造は[raw Intelligence](RAW_INTELLIGENCE.md)で確認 | []。rawモデルを使う場合は設定する |
| ユーザー登録許可（ALLOW_REGISTRATION） | 登録画面で初回accountを作る場合は運用者が1を選ぶ。管理CLIを使う場合は0のままでよい | 0。登録画面は閉じた状態 |

Studioのtoken欄で空欄にしても、相手側tokenを自動取得・生成する機能はありません。
相手の設定と同じ値を使う手順は[共通対応表](https://github.com/flamoris-jp/flamoris-updater/blob/main/docs/APPLICATION_SETUP.md#トークンとパスワードは何者か)を参照してください。

**URLのパスは接続ごとに違います。** Generationは/api/v1/generation、Agentはoriginのみ、raw推論はprovider baseです。
接続先が別hostの場合は、それぞれの現行契約が許可するHTTPS／trusted loopbackを用います。
公開recipeのStudioはbridge networkなので、127.0.0.1はコンテナ自身を指します。DB・Agent・Controllerの実際の到達性を確認します。

## 導入後にユーザー／AIが行うこと

1. Updaterで起動し、[公開URL・proxy設定](DEPLOYMENT.md)を行う。
2. 管理CLI、または意図して許可した登録画面でStudio accountを作る。登録画面を使った場合は、作成後にALLOW_REGISTRATIONを0へ戻して設定を反映する。
3. 生成を使う場合、Controller URLと同じtoken、モデル発見・対象Imageの実生成・取得を確認する。[生成契約](GENERATION_CONTROLLER.md)を参照。
4. Assistantを使う場合、先にAgent側のidentity・モデル・人格・delegation/grantを設定する。[Studio側の正確なaccount対応](SCOPED_ASSISTANT.md)と[モデル／人格設定](ASSISTANT_SETTINGS.md)に従い、STUDIO_AGENT_BINDINGS、設定機能の有効化等を行う。サービス接続tokenだけではこの対応は作られない。
5. raw Intelligenceを使う場合だけ、[直接推論設定](RAW_INTELLIGENCE.md)を設定する。
6. Speech／Musicを使う場合だけ、それぞれ[Speech](SPEECH_OPERATION.md)／[Music](MUSIC_OPERATION.md)のprovider・profileとStudio側の有効化を確認する。

1.0.2→1.0.3更新では既存DB・設定を保持し、初回DB作成やaccount作成を繰り返しません。
現在の画面にはremoteモデル送信の同意欄があり、廃止は[#68](https://github.com/flamoris-jp/flamoris-studio/issues/68)の後続変更です。

## 完了の見分け方

Webログイン、対象ユーザーのAssistant会話、モデル切替、対象生成の表示・downloadを個別に確認します。
使わない連携は未設定のままにでき、サービスhealthが成功しただけで全制作機能の設定完了とはしません。
