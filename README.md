# toto AI Prediction System

Jリーグのスポーツくじ「toto」をAIで予測するシステム。
5つのサブエージェントがパイプライン処理で予測を生成する。

## Quick Start

```bash
# 環境セットアップ
conda activate toto-ai

# 予測実行（モックデータ）
python src/main.py --round 1620 --budget 1000 --type toto

# テスト
PYTHONPATH=src pytest tests/ -v
```

## Architecture

```
Phase A（並列）: data-collector + condition-analyzer + odds-analyzer
Phase B（逐次）: upset-detector（Phase Aの全出力を入力）
Phase C（逐次）: strategy-synthesizer（全出力を統合）
```

### Pipeline Flow

```
[data-collector]──┐
[condition-analyzer]──┤──→ [upset-detector] ──→ [strategy-synthesizer] ──→ Report
[odds-analyzer]───┘
```

## Subagents

| Agent | Model | Role |
|-------|-------|------|
| data-collector | sonnet | Jリーグ試合データ収集 |
| condition-analyzer | sonnet | チームコンディション分析 |
| odds-analyzer | sonnet | toto投票率・バイアス分析 |
| upset-detector | opus | 波乱パターン検出（Daisy方式） |
| strategy-synthesizer | opus | 戦略統合・レポート生成 |

## Prediction Models

- **Dixon-Coles** (penaltyblog): ゴール期待値 + 低スコア補正 + 時間減衰
- **Elo Rating** (FiveThirtyEight SPI style): 攻撃/守備分離型レーティング
- **CatBoost Ensemble**: 特徴量ベースの勝敗分類

## Purchase Plans

| Plan | Style | Strategy |
|------|-------|----------|
| しっかりゾウ | Conservative | 的中確率最優先 |
| バランスバード | Balanced | 的中率と配当のバランス |
| ハンターライオン | Aggressive | 高配当を積極的に狙う |

## Data Sources

| Source | Type | Coverage |
|--------|------|----------|
| data.j-league.or.jp | Scraping | J1/J2/J3 試合・順位 |
| toto-dream.com | Scraping | 投票率 |
| toto.cam (totomo) | Scraping | 分析データ |
| football-lab.jp | Scraping | 詳細スタッツ |
| Mock data | Fallback | 開発・テスト用 |

## Disclaimer

本システムはエンターテインメント・研究目的であり、投資助言ではありません。
くじの購入は自己責任で行ってください。
