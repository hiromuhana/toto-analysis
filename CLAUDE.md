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

## サブエージェント
- `.claude/agents/data-collector.md` — 試合データ収集
- `.claude/agents/condition-analyzer.md` — コンディション分析
- `.claude/agents/odds-analyzer.md` — オッズ・投票率分析
- `.claude/agents/upset-detector.md` — 波乱検出（opus）
- `.claude/agents/strategy-synthesizer.md` — 戦略統合・レポート生成（opus）

## 開発環境
- conda環境: `forecast`（`conda activate forecast`）
- Python 3.13

## コーディング規約
- Python 3.13+
- 型ヒント必須
- Google-style docstring
- async httpx でデータ取得、2秒レートリミット
- pytest でテスト
- コードブロック内はクリーンで業務レベル品質

## ディレクトリ構造
```
src/toto/           メインパッケージ
  models/           予測モデル（ポアソン回帰、Elo、Dixon-Coles等）
  collectors/       データ収集（スクレイピング、API）
  analyzers/        分析ロジック
  utils/            ユーティリティ
data/
  intermediate/     エージェント間の中間データ（JSON）
  mock/             モックデータ（開発用）
reports/            生成レポート
research/           調査レポート
tests/              テスト
```

## 中間データ形式
エージェント間のデータ受け渡しはJSON。スキーマは `src/toto/models/schemas.py` で定義。
- `data/intermediate/collected_data.json`
- `data/intermediate/condition_analysis.json`
- `data/intermediate/odds_analysis.json`
- `data/intermediate/upset_analysis.json`
- `data/intermediate/strategy.json`

## 注意事項
- スクレイピングは各サイトの robots.txt / 利用規約を遵守
- 免責事項: エンターテインメント・研究目的であり投資助言ではない
- モックデータファーストで開発し、後からリアルデータに切り替え
