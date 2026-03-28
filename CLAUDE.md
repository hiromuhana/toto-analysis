# toto-analysis

## プロジェクト概要
Jリーグのスポーツくじ「toto」のAI予測システム。
5つのサブエージェントがパイプライン処理で予測を生成する。

## アーキテクチャ
```
Phase A（並列）: data-collector + condition-analyzer + odds-analyzer
Phase B（逐次）: upset-detector（Phase Aの全出力を入力）
Phase C（逐次）: strategy-synthesizer（全出力を統合）
```

## スラッシュコマンド

### `/toto-predict [ラウンド番号] [予算] [タイプ]`
toto予測パイプラインを実行するオーケストレーションコマンド。
- 例: `/toto-predict 1620 1000 toto`
- 詳細は `.claude/commands/toto-predict.md` を参照

## オーケストレーションルール

「toto予測を実行して」または `/toto-predict` と言われたら、以下の順序でサブエージェントを呼び出す:

1. **Phase A（並列）**: data-collector、condition-analyzer、odds-analyzer を同時に呼び出す
2. **Phase B（逐次）**: Phase Aの3つが全て完了したら、upset-detector を呼び出す。入力として data/intermediate/ の3ファイルを渡す
3. **Phase C（逐次）**: upset-detector完了後、strategy-synthesizer を呼び出す。data/intermediate/ の4ファイル全てを渡す

### ルール
- 各サブエージェントは自分の出力を data/intermediate/ にJSONで書き出す
- 次のPhaseのサブエージェントはそのJSONを読み込んで処理する
- エラー時はメインセッションに報告し、リトライするかフォールバックするか判断を仰ぐ
- Phase完了時に中間結果のサマリーをメインセッションに表示し、問題があれば人間が介入できるようにする
- サブエージェントはPythonモジュール（src/toto/）を `conda activate toto-ai` 環境で実行する

### Pythonでの直接実行
```bash
conda activate toto-ai
python src/main.py --round 1620 --budget 1000 --type toto
```

## サブエージェント
- `.claude/agents/data-collector.md` — 試合データ収集（sonnet）
- `.claude/agents/condition-analyzer.md` — コンディション分析（sonnet）
- `.claude/agents/odds-analyzer.md` — オッズ・投票率分析（sonnet）
- `.claude/agents/upset-detector.md` — 波乱検出（opus）
- `.claude/agents/strategy-synthesizer.md` — 戦略統合・レポート生成（opus）

## 開発環境
- conda環境: `toto-ai`（`conda activate toto-ai`）
- Python 3.13.12
- 主要ライブラリ: penaltyblog 1.9.0, soccerdata 1.8.8, httpx 0.28.1, pandas 2.3.3, scikit-learn 1.8.0, pydantic 2.12.5, xgboost 3.2.0, lightgbm 4.6.0, catboost 1.2.10

## コーディング規約
- Python 3.13+
- 型ヒント必須
- Google-style docstring
- async httpx でデータ取得、2秒レートリミット
- pytest でテスト
- ログ: logging モジュール使用、LOG_LEVEL 環境変数で制御
- コードブロック内はクリーンで業務レベル品質

## ディレクトリ構造
```
src/
  main.py             Pythonエントリーポイント
  toto/               メインパッケージ
    config.py          設定・定数
    models/
      schemas.py       Pydanticデータスキーマ
      dixon_coles.py   Dixon-Colesモデル（penaltyblog wrapper）
      elo.py           Eloレーティング（攻撃/守備分離）
      ensemble.py      アンサンブル予測器
    collectors/
      base.py          ベースコレクター（レートリミット、キャッシュ）
      jleague.py       data.j-league.or.jp スクレイパー
      toto_official.py toto-dream.com スクレイパー
      totomo.py        toto.cam スクレイパー
      football_lab.py  football-lab.jp スクレイパー
      mock.py          モックデータフォールバック
    analyzers/
      condition.py     コンディション分析
      odds.py          オッズ・投票率分析
      upset.py         波乱検出
    strategy/
      synthesizer.py   戦略統合
    output/
      report.py        Markdownレポート生成
    utils/
      cache.py         キャッシュ機構（有効期限6時間）
data/
  intermediate/        エージェント間の中間データ（JSON）
  mock/                モックデータ（フォールバック用）
  cache/               キャッシュデータ（6時間有効）
reports/               生成レポート
research/              調査レポート
tests/                 テスト
```

## 予測モデル
- Dixon-Colesモデル（penaltyblog）: ゴール期待値 + 低スコア補正 + 時間減衰(ξ=0.001)
- Eloレーティング: 攻撃/守備分離型（FiveThirtyEight SPI方式）
- CatBoost/LightGBMアンサンブル: 特徴量ベースの勝敗分類
- 加重統合: 基礎モデル40%, コンディション20%, 市場分析15%, 波乱検出25%

## 中間データ形式
エージェント間のデータ受け渡しはJSON。スキーマは `src/toto/models/schemas.py` で定義。
各JSONには agent名、timestamp、toto_round を必ず含める。
- `data/intermediate/collected_data.json`
- `data/intermediate/condition_analysis.json`
- `data/intermediate/odds_analysis.json`
- `data/intermediate/upset_analysis.json`
- `data/intermediate/strategy.json`

## 注意事項
- スクレイピングは各サイトの robots.txt / 利用規約を遵守
- async httpx で2秒間隔のレートリミット
- data/cache/ へのキャッシュ機構（有効期限6時間）
- エラー時3回リトライ後にモックデータへgraceful degradation
- 免責事項: エンターテインメント・研究目的であり投資助言ではない
