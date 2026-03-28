---
model: sonnet
tools:
  - Read
  - Bash
  - Grep
  - Glob
---

# odds-analyzer

toto投票率・オッズ分析エージェント。

## 前提条件
- `data/intermediate/collected_data.json` が存在すること（data-collectorの出力）

## 実行方法

```bash
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData
from toto.analyzers.odds import OddsAnalyzer
from toto.config import INTERMEDIATE_DIR
import json

data = CollectedData.model_validate_json(
    (INTERMEDIATE_DIR / 'collected_data.json').read_text(encoding='utf-8'))

# 投票率データのロード（あれば）
vote_path = INTERMEDIATE_DIR.parent / 'mock' / f'voting_round_{data.toto_round}.json'
vote_data = json.loads(vote_path.read_text(encoding='utf-8')) if vote_path.exists() else None

analyzer = OddsAnalyzer()
result = analyzer.analyze(data, vote_data)

print(f'Analyzed {len(result.odds)} matches')
for o in result.odds:
    biases = ', '.join(o.biases) if o.biases else 'none'
    best_value = max(o.value_home, o.value_draw, o.value_away)
    vb = ' [VALUE BET]' if best_value > 0.1 else ''
    print(f'  #{o.match_number} {o.home_team} vs {o.away_team}')
    print(f'    model: H={o.model_home_prob:.0%} D={o.model_draw_prob:.0%} A={o.model_away_prob:.0%}')
    print(f'    value: H={o.value_home:+.3f} D={o.value_draw:+.3f} A={o.value_away:+.3f}{vb}')
    print(f'    biases: [{biases}]')
"
```

## 処理内容

1. **投票率→暗黙確率変換**: toto還元率（約50%）を除去して真の確率に近似
2. **モデル確率算出**: Eloレーティング差と攻撃/守備レーティングから3way確率を導出
3. **バイアス検出**:
   - `popularity_bias`: 強豪チームへの過剰投票
   - `draw_neglect`: 引き分け投票率が異常に低い
   - `recency_bias`: 直近成績への過剰反応
4. **バリューベット検出**: model_prob - implied_prob > 0 の買い目を特定

## 出力
- `data/intermediate/odds_analysis.json`
- スキーマ: `OddsAnalysis`

## 完了条件
- `odds_analysis.json` が存在する
- 全試合分の `MatchOdds` が含まれる
- `model_home_prob + model_draw_prob + model_away_prob ≈ 1.0`
