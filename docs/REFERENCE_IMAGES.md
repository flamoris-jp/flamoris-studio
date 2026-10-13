# 参照画像から制作する

この変更はStudio 1.0.4向けのソース・配布準備です。リリース公開と実機での生成確認は別工程です。Generation MCP 1.0.3と、そこに同梱されるController 1.0.1の対応profileを使います。

| やりたいこと | 選ぶ画像 | 操作 |
| --- | --- | --- |
| 元の絵を描き直す | 元画像 | img2imgとDenoise |
| キャラクターの外見を参考にする | キャラクターの画像 | Character referenceとCharacter influence |
| 画風・色・質感を参考にする | 画風の見本 | Visual style referenceとStyle influence |
| ポーズ・構図を指定する | 対応モデル用の骨格・深度・輪郭画像 | Pose / structure referenceとその強度 |

画風の画像参照と、文章のpositive/negative promptを保存する従来のStyleは別のものです。

## 導入とComfyWorkFlow作成

1. [Controllerのモデル準備・具体的なComfyWorkFlow・作成CLI](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/REFERENCE_IMAGES.md)を読みます。checkpoint、IPAdapterと対応CLIP Vision、ControlNetの必要な組み合わせをComfyUIへ用意します。
2. ComfyUIとGenerationが共有する入力ディレクトリを明示的にmountし、GenerationのFLAMORIS_COMFYUI_INPUT_ROOTを設定します。モデル名も両方で一致させます。
3. 外部MCPのcomfy.registerで対応するComfyWorkFlowを登録します。登録は静的検証で、GPUでの生成成功やモデル互換性の確認ではありません。
4. Studioを対応ソースへ更新し、通常のアプリ自身のmigrationでAlembic 20261012_13へ進めます。追加するexecution_inputsは生成と参照画像の対応表です。既存ユーザー・会話・画像を移し替えません。Updaterの通常のStudio導入/更新経路が既存のmigration処理を呼びます。
5. 既存の[Controller HTTP接続](GENERATION_CONTROLLER.md)を使います。Studio内部通信へMCPやHubを追加しません。

ComfyUIの追加ノードやモデルをStudio/Controllerが自動取得することはありません。原本の画像ファイルも手元に残してください。

## 画面で使う

Image templateで登録したComfyWorkFlowを選びます。そのComfyWorkFlowで必要な参照だけが表示されます。画像はファイル選択/ドロップか、自分の生成済みAssetsから選べます。PNG/JPEG/WebP、1画像8 MiBまでです。必要な参照モデルが見つからない場合は選択を無効にし、Reference model setupに必要なモデル名と準備手順を表示します。すべての必要な画像を選び、強度を設定してGenerate imageを押します。

新しい登録profileは宣言されたcheckpoint/参照モデルの組み合わせを使います。別のモデルの組み合わせを使う場合は、そのComfyWorkFlowを登録してください。登録profileは追加LoRAに対応しません。従来のLoRA付き文章生成は引き続き使えます。

ControlNet欄は**作成済みの制御画像**です。写真から骨格や深度を自動抽出しません。IPAdapterの外見参照も、完全な同一人物・衣装・細部の固定を保証しません。複数人物を個別の領域へ分ける機能は今回含みません。

## 続きのコマを作る

生成物の詳細からUse settingsを押すと、元のComfyWorkFlow、内容digest、全参照画像と強度が戻ります。文章やポーズだけ変えて次を生成できます。

画像の期限切れ/削除やComfyWorkFlowの欠落を黙って無視しません。必要な参照を取り替えるか、対応するComfyWorkFlowを明示的に選び直します。参照画像は既存の24時間期限を維持します。

Clear inputsは全参照を下書きから外し、生成設定もリセットします。保存済みの画像を削除したり、進行中ジョブを再送したりしません。

## 所有と履歴

すべての画像handleはStudioユーザーの所有を上流呼び出し前に検証します。ブラウザにはControllerの生のinput IDを渡しません。実行中/結果不明の生成が使う各参照は削除できません。生成の送信結果が不明でも自動再送しません。

元の生成画像を削除しても、独立した参照snapshotの寿命や利用中の保護は別に扱います。新しい実行参照の履歴がある状態では、DB downgradeで対応表を消せません。

## Verification

Normal tests cover metadata/digest validation, user-scoped handles, multi-reference deletion fences and settings restoration, current gateway-to-Controller-to-fake-ComfyUI flows, and browser interactions. Actual model compatibility, GPU memory use and output quality remain host acceptance work.

