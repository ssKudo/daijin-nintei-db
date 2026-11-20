# daijin-nintei-db（建築基準法 大臣認定データベース）

[![Web App](https://img.shields.io/badge/GitHub%20Pages-Web%20App-4338ca?style=flat-square)](https://ssKudo.github.io/daijin-nintei-db/)
[![Records](https://img.shields.io/badge/Indexed%20Records-93%2C334%20(%E5%85%A821%E3%82%AB%E3%83%86%E3%82%B4%E3%83%AA)-059669?style=flat-square)](#-収録データ構成全21カテゴリ)
[![Code License: MIT](https://img.shields.io/badge/Code%20License-MIT-334155?style=flat-square)](./LICENSE)
[![Data License: PDL1.0](https://img.shields.io/badge/Data%20License-PDL1.0%20(%E5%85%AC%E5%85%B1%E3%83%87%E3%83%BC%E3%82%BF%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%841.0)-d97706?style=flat-square)](#-出典ライセンスおよび加工表示pdl10)

国土交通省が公開している「建築基準法に基づく構造方法等の認定に係る帳簿等」（令和8年9月30日版・PDF全42ファイル）を機械可読な **JSON / CSV オープンデータ（全 93,334 件・21 カテゴリ）** へ構造化し、**静的 Web アプリ（GitHub Pages）** および **Python ライブラリ／CLI ツール** を提供する非公式プロジェクトです。

- **Web アプリ（ブラウザで即利用・インストール不要）**: https://ssKudo.github.io/daijin-nintei-db/
- **データ基準日**: 令和8年9月30日（収録期間：`平成12年04月01日～令和8年09月30日`、実データ認定日レンジ `2000-08-07` 〜 `2026-09-30`、国交省ページ最終更新日 `令和08年06月01日`）
- **最新認定の公式照会先**: [一般社団法人 建築性能基準推進協会（性能協）大臣認定等検索システム](https://www.seinokyo.jp/ninteidb/pub/)

---

## 主な特徴

1. **機械可読オープンデータセット（JSON / CSV）**
   - 国土交通省公開帳簿の全21カテゴリ（平成25年4月以降 `H25_after` ／ 平成25年3月以前 `H25_before`）から、PDF表枠の座標ベース抽出により列ズレ・文字化けを排除した **93,334 件** を収録。
   - `data/categories/<category_id>.json`（UTF-8）および Excel でそのまま開ける `data/categories/<category_id>.csv`（UTF-8 BOM付）を整備。
   - 国交省公式参考資料（`001709467.pdf`、`001069848.pdf`、`001069853.pdf`、`001069849.pdf`）および各最新帳簿PDF p.1 目次テーブルから構築した認定記号ルールマスター `data/code_rules.json`（v2.0.0：189記号・9構造種別・13部位コード・6プレフィックス規則）を同梱。

2. **最新認定番号にも対応した記号デコーダ**
   - 公開帳簿の基準日以降に交付された未掲載番号（例：`QF045BE-9999` 等）であっても、国交省の付番規則から **構造種別・耐火時間・対象部位・耐力／非耐力区分・根拠条文** を決定的（ゼロハルシネーション）に分解表示します。
   - 全角英数字・異体ハイフン（`ＦＰ０６０ＢＥ－０１２３` 等）、枝番・括弧付番（`FP030RF-2027(2)`, `FP060FL-0093-2(1)`）、最新記号（`ED`［小規模な竪穴区画の開口部・令第112条第12項］、`EH***`［火熱遮断壁等の防火設備・遮熱］、`EQ***`、`FPLH***`［長時間遮熱性耐火構造］、`QPEP***`、`QFRM`、`MCLT`、`MCMU`、`MBLK`、`EBN***`/`ECN***`）、建築物7文字コード（`HFNBNNN-12345-3` 等）、昇降機・遊戯施設コードまで全93,334件をエラー0で解析可能です。

3. **完全ローカル処理の図書一括スクリーニング（Web UI & CLI）**
   - 設計図書・仕上表・建具表・特記仕様書・CSV・DXF等から大臣認定番号を一括抽出し、公開帳簿との照合・取消／失効記載の検知・1行複数番号の局所文脈切り分けによる整合性アラート（耐火時間／耐力壁／部位等）を出力します。
   - 台帳照合を行わなかった場合（CLIの `--no-db` 指定時や、Web側でのカテゴリJSON取得失敗時）は **`UNCHECKED`（台帳未照合）** として扱い、`UNLISTED_VALID_FORMAT`（未掲載・要確認）とは厳密に区別します。
   - **Web アプリは 100% ブラウザ内メモリ処理（外部サーバー送信なし）**。社外秘の図書テキストやローカルファイルも安全にスクリーニング可能です。
   - Python CLI（`tools/screen_cert_ids.py` / `daijin-nintei`）はコア機能に外部依存ゼロ（Python 3.10+ 標準ライブラリのみ）で動作し、任意オプションで `.pdf` / `.dxf` / `.ifc` の直接読み込みにも対応します。

---

## クイックスタート

### 1. Web アプリを使う

公開中の GitHub Pages からそのまま利用できます。

- **公開URL**: https://ssKudo.github.io/daijin-nintei-db/

ローカル環境で動かしたい場合は、リポジトリをクローンして `docs/` を静的配信してください（ビルド不要）。

```bash
git clone https://github.com/ssKudo/daijin-nintei-db.git
cd daijin-nintei-db
python -m http.server --directory docs 8000
# ブラウザで http://localhost:8000/ を開く
```

Web アプリの3機能：
- **大臣認定 検索**: 初期選択「すべて（全21区分）」によるシームレスなバックグラウンド読込＆全93,334件横断検索・個別区分絞込・記号／期間フィルタ・詳細モーダル（自動記号分解＋公式DBリンク）・CSV出力（一部区分読込失敗時の再試行・不完全明示対応）
- **認定番号 デコーダ**: 記号のビジュアル分解・根拠条文表示・公開帳簿の自動照合（読込失敗時は「台帳未照合」＋再試行ボタン表示、未掲載時は性能協公式DBリンク提示）
- **一括スクリーニング**: テキスト貼付＆ローカルファイルD&D（Shift_JIS自動判別対応）・KPIサマリー（検出／台帳一致／未掲載／台帳未照合／注意アラート）・1行複数番号の局所文脈判定・CSVダウンロード／Markdownレポートコピー

### 2. Python CLI を使う（`tools/screen_cert_ids.py` / `daijin-nintei`）

テキスト・CSV・Markdown・JSON の処理は **Python 3.10+ 標準ライブラリのみ（追加パッケージ不要）** で実行できます。

```bash
# 1) 認定番号のデコード＆公開帳簿照合（全角・複数指定・最新記号対応）
python tools/screen_cert_ids.py decode QF045BE-1232 ＦＰ０６０ＮＥ－０００１ ED-0001 EH090-0001 QF045BE-9999

# JSON形式で出力（--no-db 指定時は台帳照合せず UNCHECKED を返却）
python tools/screen_cert_ids.py decode SOI-0249 --no-db --json

# 2) ローカル台帳の検索
python tools/screen_cert_ids.py search --list-categories
python tools/screen_cert_ids.py search --applicant アイジー工業 --prefix QF045BE
python tools/screen_cert_ids.py search --q "せっこうボード 界壁" --category sound_insulation --limit 20

# 3) 図書ファイル・ディレクトリの一括スクリーニング（Markdown / JSON / CSV）
python tools/screen_cert_ids.py screen ./specs/ --format md --out report.md
python tools/screen_cert_ids.py screen ./finish_schedule.csv --format csv --out screening.csv

# 標準入力からのパイプ処理（1行複数番号も局所文脈で独立判定）
echo "外壁 60分 QF045BE-1232 ／ 間仕切壁 45分 QF045BP-0001" | python tools/screen_cert_ids.py screen -
```

`.pdf` / `.dxf` / `.ifc` ファイルを直接スクリーニングする場合は、必要に応じてオプション依存をインストールしてください。

```bash
# 必要な形式のみ追加インストール（pyproject.toml の extras）
pip install -e ".[pdf,dxf]"
# または全形式対応
pip install -e ".[all]"
```

### 3. Python パッケージ（wheel）単体インストール時のデータ指定方法

本パッケージを wheel 等から単独インストール（`pip install .` 等）した場合、軽量な認定記号ルール（`bundled_code_rules.json`）はパッケージ内に同梱されているため、**記号デコード（`daijin-nintei decode ...`）および文脈チェック（`daijin-nintei screen --no-db ...`）は追加データなしでそのまま動作します**。

一方、約 70 MB の公開帳簿データセット（`data/` ディレクトリ）は wheel サイズ肥大化を避けるためパッケージには同梱していません。wheel 単体環境からローカル台帳検索（`daijin-nintei search`）や台帳照合付きスクリーニングを行う場合は、以下のいずれかの方法で `data/` を用意し、パスを指定してください。

1. **外部データの入手方法**
   - 本リポジトリをクローンして `data/` ディレクトリを取得する（`git clone https://github.com/ssKudo/daijin-nintei-db.git`）、または
   - 後述のデータ再構築スクリプト（`scripts/`）を実行して国交省一次PDFから `data/` を生成する。
2. **データディレクトリの指定方法（2通り）**
   - **CLI オプション `--data-dir`**:
     ```bash
     daijin-nintei search --data-dir /path/to/daijin-nintei-db/data --list-categories
     daijin-nintei decode QF045BE-1232 --data-dir /path/to/daijin-nintei-db/data
     daijin-nintei screen ./specs/ --data-dir /path/to/daijin-nintei-db/data
     ```
   - **環境変数 `DAIJIN_NINTEI_DATA_DIR`**:
     ```bash
     # Linux / macOS
     export DAIJIN_NINTEI_DATA_DIR=/path/to/daijin-nintei-db/data
     daijin-nintei search --list-categories

     # Windows (PowerShell)
     $env:DAIJIN_NINTEI_DATA_DIR = "C:\path\to\daijin-nintei-db\data"
     daijin-nintei search --list-categories
     ```

### 4. テストの実行

```bash
pip install -e ".[dev]"
pytest
```

### 5. 元PDFからのデータ再構築（`scripts/`）

国土交通省の公開ページ（`jutakukentiku_house_tk_000042.html`）から一次PDFを取得し、`data/` および `docs/data/` をゼロから再現できます（ダウンロードした生PDFは `.cache/pdfs/` に保存され、`.gitignore` によりGit管理対象外です）。

```bash
pip install -e ".[pipeline]"

# 1. 国交省公開PDFの取得（冪等・既存ファイルはスキップ、再取得は --force）
python scripts/fetch_mlit_pdfs.py

# 2. 公式参考PDF・最新帳簿目次から記号ルール（code_rules.json）を生成
python scripts/build_code_rules.py

# 3. 全42帳簿PDFを解析し、data/ と docs/data/ を生成
python scripts/parse_pdfs.py
```

---

## 収録データ構成（全21カテゴリ）

出典元PDF 42ファイル（21カテゴリ × 平成25年4月以降／平成25年3月以前）から改ページ跨ぎの共同申請継続行（721件）を統合した **全 93,334 件** を収録しています（詳細は [`data/manifest.json`](./data/manifest.json) を参照）。

| # | カテゴリID | カテゴリ名 | グループ | 収録件数 | 主な認定記号（抜粋） |
|---:|---|---|---|---:|---|
| 1 | `buildings` | 建築物（時刻歴・耐火・避難） | 建築物／工作物 | 15,800 | `HFNBNNN`, `HNNNNNN`, `MNNNNNN`, `NNNBNNN` 等 |
| 2 | `structures` | 工作物 | 建築物／工作物 | 1,113 | `SNNNNNN`, `SNNNN`, `SNNN` |
| 3 | `fireproof` | 耐火構造 | 防耐火構造 | 11,364 | `FP060BE`, `FP090BE`, `FP120CN`, `FP150CN`, `FP030RF` 等 |
| 4 | `quasi_fireproof` | 準耐火構造 | 防耐火構造 | 4,991 | `QF045BE`, `QF060BE`, `QF075BE`, `QF090CN`, `QF120RS` 等 |
| 5 | `fire_resistive` | 防火構造・準防火構造 | 防耐火構造 | 9,043 | `PC030BE`, `PC030NE`, `PC030RS`, `QP020BE`, `QP020NE` |
| 6 | `fire_brand` | 飛び火構造 | 防耐火構造 | 3,434 | `DR`, `DW`, `UR`, `UW` |
| 7 | `fire_other` | その他（柱の防火被覆、不燃性を有する建築物の部分等、ほか） | 防耐火構造 | 43 | `CC`, `CCN`, `FI020RF`, `NH030FL` |
| 8 | `fire_materials` | 不燃材料・準不燃材料・難燃材料 | 防火材料／防火設備 | 11,557 | `NM`, `NE`, `QM`, `QE`, `RM`, `RE` |
| 9 | `fire_doors` | 防火設備・特定防火設備 | 防火材料／防火設備 | 10,126 | `EA`, `EB`, `EC`, `ED`, `EH060`, `EH090`, `EBN010`, `CAT`, `CAS` |
| 10 | `wall_multiplier` | 壁倍率 | 指定建築材料等 | 1,082 | `FRM`, `QFRM`, `TBFC` |
| 11 | `steel_joints` | 鋼材の接合部 | 指定建築材料等 | 205 | `HSB`, `JC`, `JM` |
| 12 | `designated_materials_1` | 指定建築材料（１）（鋼材・ボルト・ケーブル・免震材料・木質パネル等） | 指定建築材料等 | 2,093 | `MSTL`, `MBLT`, `MSRB`, `MVBR`, `MWGM` 等 |
| 13 | `designated_materials_2` | 指定建築材料（２）（膜材料・石綿飛散防止材・コンクリート等） | 指定建築材料等 | 5,287 | `MCON`, `MALC`, `MMEM`, `MTPS`, `MAEN` 等 |
| 14 | `steel_factories` | 鉄骨工場 | 鉄骨工場 | 5,645 | `TFBJ`, `TFBR`, `TFBM`, `TFBH`, `TFBS` |
| 15 | `penetration` | 区画貫通 | 区画貫通 | 2,159 | `PS060WL`, `PS060FL`, `PS045WL` |
| 16 | `sound_insulation` | 界壁遮音・天井遮音 | 遮音 | 576 | `SOI` |
| 17 | `sick_house` | ホルムアルデヒド発散建築材料・その他 | シックハウス | 3,729 | `MF2`, `MF3`, `MFN`, `RLFC` |
| 18 | `doc_omission` | 図書省略（構造計算プログラム・プレハブ駐車場・基礎杭等） | 図書省略・その他の構造 | 1,376 | `TACP`, `TPRG`, `TPPG`, `TSTB`, `TWDB`, `TRCB`, `SPRG` |
| 19 | `building_equipment` | 浄化槽・換気設備・非常用照明 | 建築設備／遊戯施設 | 389 | `DW1A`, `DW1B`, `DW1E`, `DW1N`, `DW3N`, `LAE`, `VEC` |
| 20 | `elevators` | 昇降機（エレベーター・エスカレーター・小荷物専用昇降機） | 建築設備／遊戯施設 | 3,249 | `EPNNNN`, `ENNNUN`, `ESNNF`, `ESNSN` 等 |
| 21 | `amusement_facilities` | 遊戯施設（コースター等） | 建築設備／遊戯施設 | 73 | `ANNE`, `ANSE`, `ANSN`, `ANCN`, `ANNS`, `APCS`, `APSE` |
| | **合計** | **全21カテゴリ** | | **93,334** | |

### レコード JSON スキーマ

各 `data/categories/<category_id>.json`（および `docs/data/categories/<category_id>.json`）は以下の11項目を持つオブジェクト配列です。

```json
{
  "cert_no": "QF045BE-1232",
  "prefix": "QF045BE",
  "category_id": "quasi_fireproof",
  "category_name": "準耐火構造",
  "date_jp": "平成25年02月15日",
  "date": "2013-02-15",
  "title": "人造鉱物繊維断熱材充てん／鋼板・イソシアヌレートフォーム・硬質ウレタンフォーム保温板・せっこうボード表張／せっこうボード重裏張／木製軸組造外壁",
  "applicant": "アイジー工業株式会社",
  "address": "山形県東根市蟹沢上縄目1816-12",
  "evaluator": "（公財）日本住宅・木材技術センター",
  "period": "H25_before"
}
```

- 共同申請レコードは複数の申請者・住所を ` / ` 区切りで保持しています。
- 帳簿上に取消・失効日が併記されているレコード（1,120件）は `date_jp` に原文の記載（例：`令和4年12月20日 令和5年03月29日取消`）を保持し、`date` には当初認定日を ISO 8601 形式で格納しています。

### リポジトリ構成

```text
daijin-nintei-db/
├── data/                    # 構造化データセット（manifest.json, code_rules.json, categories/*.json, *.csv）
├── docs/                    # GitHub Pages 静的Webアプリ（index.html, style.css, app.js, decoder.js, data/）
├── daijin_nintei/           # Python ライブラリ（decoder.py, db.py, screener.py, cli.py, bundled_code_rules.json）
├── tools/                   # CLI エントリーポイント（screen_cert_ids.py）
├── scripts/                 # 国交省公開PDFの取得・ルール生成・帳簿解析パイプライン
├── tests/                   # pytest テストスイート
├── LICENSE                  # MIT License（ソースコード対象）
└── README.md
```

---

## 出典・ライセンスおよび加工表示（PDL1.0）

### ソースコードのライセンス

本リポジトリに含まれるソースコード（`docs/` UI、`daijin_nintei/`、`tools/`、`scripts/`、`tests/`）は **[MIT License](./LICENSE)** の下で公開しています。

### データセットの出典・加工表示（公共データ利用規約 第1.0版）

`data/` および `docs/data/` に収録しているデータセットは、国土交通省の公開データを利用し、**公共データ利用規約（第1.0版）（PDL1.0）** に基づいて加工・再構成したものです。

- **出典**: 「建築基準法に基づく構造方法等の認定に係る帳簿等」（国土交通省）
- **出典URL**: https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000042.html
- **参考資料URL**:
  - 認定の区分と対象条文（建築物・工作物関連）: https://www.mlit.go.jp/common/001069848.pdf
  - 認定の区分と対象条文（防耐火構造・防火材料ほか）: https://www.mlit.go.jp/common/001069853.pdf
  - 認定の区分と対象条文（昇降機・遊戯施設関連）: https://www.mlit.go.jp/common/001069849.pdf
  - 構造方法等の認定に係る記号一覧（最新参考資料）: https://www.mlit.go.jp/jutakukentiku/build/content/001709467.pdf
- **加工表示**: 本データセットは上記国土交通省公開PDF帳簿からテキスト抽出・正規化・日付ISO変換・JSON/CSV形式への構造化を行って作成した加工データです。
- **非公式の明記**: 本リポジトリおよび公開Webアプリは、国土交通省その他の公的機関・指定性能評価機関が作成、監修、承認または推奨したものではありません。

二次利用される場合は、公共データ利用規約（第1.0版）に従い、上記出典および加工して作成した旨を適切に表示してください。

---

## 重要な制約および建築基準法上の免責事項

1. **データ基準日（令和8年9月30日）による制約**
   - 本データセットは国土交通省の現行公開帳簿ページ（`jutakukentiku_house_tk_000042.html`、令和8年9月30日版）の公開PDF群を取り込んだスナップショットです。
   - **基準日以降に交付された新規認定、変更認定、および最新の認定取消・失効情報は反映されていない場合があります。**
   - 一括スクリーニング等で「公開帳簿未掲載（記号形式は妥当）」と判定された番号は、基準日以降の新規認定番号や転記ミスの可能性があるため、必ず [一般社団法人 建築性能基準推進協会（性能協）大臣認定等検索システム](https://www.seinokyo.jp/ninteidb/pub/) にて最新情報を照会してください。

2. **別添（構造仕様・施工条件）の非収録**
   - 本データセットに収録しているのは公開帳簿上の書誌事項（認定番号・名称・申請者・住所・評価機関・認定年月日）のみです。具体的な材料構成・寸法・納まり・施工条件等が記載された**認定書の別添は含まれていません**。

3. **法適合判断に関する免責**
   - 本プロジェクトおよびツール群は、建築設計・確認申請・図書チェック業務における**機械的な一次確認・スクリーニング補助**を目的とするものであり、建築基準法その他の法令への適合性を判定・保証するものではありません。
   - 実際の設計・施工・確認申請における法適合判断にあたっては、必ず建築士等の有資格者が公的検索システム（一般社団法人 建築性能基準推進協会〔性能協〕等）、メーカー提供の認定書原本（別添含む）、および特定行政庁・指定確認検査機関の見解に基づき最終確認を行ってください。
   - 本データ・ツールの利用により生じたいかなる損害についても、作者は一切の責任を負いません。
