# comfyui-client

ComfyUI の Python API クライアント（単一ファイル CLI）。ワークフローテンプレートを使って画像を生成し、ローカルに保存します。

## 必要環境

- Python 3.10 以上
- 起動済みの [ComfyUI](https://github.com/comfyanonymous/ComfyUI) サーバー

## セットアップ

```bash
# 1. リポジトリをクローン（またはファイルをコピー）
git clone <repo-url>
cd comfyui_client

# 2. 仮想環境を作成・有効化
python -m venv venv

# Windows (CMD / PowerShell)
.\venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# 3. 依存ライブラリをインストール
pip install -r requirements.txt
```

## 使い方

### 基本

```bash
python comfy_client.py -p "1girl, sunset, detailed background" -W 1152 -H 896 -b 1
```

### キャラクタープロンプトとシードを指定

```bash
python comfy_client.py \
  -p "dutch angle, forest, dark" \
  -c "1girl, blue hair, school uniform" \
  -s 12345 \
  -W 1152 -H 896 -b 2 \
  -o ./outputs
```

### リモートサーバーを使用

```bash
COMFYUI_SERVER=http://192.168.1.10:8188 \
  python comfy_client.py -p "prompt" -W 1024 -H 1024 -b 1
```

### 標準入力からプロンプトを渡す（複数行対応）

```bash
# ファイルからリダイレクト
python comfy_client.py -W 1152 -H 896 -b 1 < prompt.txt

# ヒアドキュメント (Bash)
python comfy_client.py -W 1152 -H 896 -b 1 <<'EOF'
1girl, detailed face
high quality, 8k
EOF
```

## オプション一覧

| オプション         | 短縮形 | デフォルト              | 説明                                       |
|--------------------|--------|-------------------------|--------------------------------------------|
| `--prompt`         | `-p`   | （stdin 読み込み）      | ポジティブプロンプト                       |
| `--width`          | `-W`   | `1024`                  | 画像の幅 (px)                              |
| `--height`         | `-H`   | `1024`                  | 画像の高さ (px)                            |
| `--batch`          | `-b`   | `1`                     | バッチサイズ                               |
| `--character`      | `-c`   | なし                    | キャラクタープロンプト（任意）             |
| `--seed`           | `-s`   | ランダム                | シード値                                   |
| `--output-dir`     | `-o`   | `./outputs`             | 出力ディレクトリ                           |
| `--server`         |        | `http://127.0.0.1:8188` | ComfyUI サーバー URL（環境変数でも指定可） |
| `--template`       | `-t`   | `t2iv2.json`            | ワークフローテンプレートファイル           |

## 環境変数

| 変数             | 説明                                          |
|------------------|-----------------------------------------------|
| `COMFYUI_SERVER` | ComfyUI サーバーの URL（`--server` より優先） |

## ワークフローテンプレートについて

`t2iv2.json` は ComfyUI の API Export 形式のワークフローファイルです。CLI は以下のノードを `_meta.title` で検索して値を上書きします。

| ノードタイトル      | 役割                     |
|---------------------|--------------------------|
| Positive Prompt     | ポジティブプロンプト本文 |
| Character Prompt    | キャラクター説明テキスト |
| Seed                | 生成シード値             |
| Empty Latent Image  | 解像度・バッチサイズ     |

別のワークフローを使う場合は、上記タイトルのノードが存在するか、または `comfy_client.py` 内の `apply_workflow_args()` を修正してください。

## 出力

生成された画像は `--output-dir`（デフォルト: `./outputs`）に保存されます。同名ファイルが存在する場合は `_1`, `_2` ... のサフィックスが自動で付与されます。
