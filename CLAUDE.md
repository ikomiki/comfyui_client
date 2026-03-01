# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 開発コマンド

```bash
# 仮想環境のセットアップ（初回のみ）
python -m venv venv
.\venv\Scripts\activate          # Windows
source venv/bin/activate         # Unix/macOS

pip install -r requirements.txt

# 実行
python comfy_client.py -p "プロンプト" -W 1152 -H 896 -b 1

# リモートサーバー指定
COMFYUI_SERVER=http://192.168.1.10:8188 python comfy_client.py -p "prompt" -W 1152 -H 896 -b 1

# キャラクタープロンプトを環境変数で固定
COMFYUI_CHARACTER="1girl, solo" python comfy_client.py -p "prompt" -W 1152 -H 896 -b 1

# stdin からプロンプト読み込み
python comfy_client.py -W 1152 -H 896 -b 1 < prompt.txt
```

## アーキテクチャ

単一ファイル CLI (`comfy_client.py`) が ComfyUI の REST + WebSocket API を直接呼び出す構成。

### 処理フロー

1. `t2iv2.json`（ワークフローテンプレート）を読み込む
2. `apply_workflow_args()` でノードを書き換え（後述）
3. WebSocket 接続を **POST /prompt より先に** 確立する（`ws://{server}/ws?clientId={uuid}`）
4. POST /prompt でワークフローを送信し `prompt_id` を取得
5. WS メッセージを監視して進捗表示 → `executed` メッセージから画像情報を収集
6. `execution_success` 受信後に GET /view で画像をダウンロード

### ノード特定方法

ノードは **数値 ID ではなく `_meta.title`** で検索する（`find_node_by_title()`）。
書き換えるノードとフィールド：

| `_meta.title`       | `class_type`              | 書き換えフィールド             |
|---------------------|---------------------------|-------------------------------|
| Positive Prompt     | PrimitiveStringMultiline  | `inputs["value"]`             |
| Character Prompt    | Text Multiline            | `inputs["text"]`              |
| Seed                | PrimitiveInt              | `inputs["value"]`             |
| Empty Latent Image  | EmptyLatentImage          | `inputs["width/height/batch_size"]` |

### ワークフロー (`t2iv2.json`)

- **2 パス構成**: Pass1（KSampler → VAEDecode → PreviewImage）と Pass2（FaceDetailer → SaveImage）
- Pass1 の latent は FaceDetailer (node `27:22`) へ渡され、顔領域を再生成して保存
- Positive Prompt (node 29) と Character Prompt (node 2) は StringFunction で結合されて CLIPTextEncode へ入力される
- テンプレートファイルは読み取り専用参照として扱うこと（ランタイムでコピーを変更する）

### 主要関数

- `find_node_by_title()` — `_meta.title` でノードを検索、見つからない場合は `ValueError`
- `apply_workflow_args()` — ワークフロー dict をインプレースで書き換え
- `listen_and_download()` — async。WebSocket 監視 + 画像ダウンロードをまとめて担当
- `_server_to_ws()` — `http://` → `ws://`、`https://` → `wss://` に変換
- `_unique_path()` — 同名ファイルが存在する場合に `_N` サフィックスを付与

### 環境変数

| 変数                  | デフォルト              | 説明                                        |
|-----------------------|-------------------------|---------------------------------------------|
| `COMFYUI_SERVER`      | `http://127.0.0.1:8188` | ComfyUI サーバーの URL                      |
| `COMFYUI_CHARACTER`   | なし                    | キャラクタープロンプトのデフォルト値（`-c` オプションの省略時に使用） |
