---
model: sonnet
tools:
  - Read
  - Bash
  - Grep
  - Glob
---

# condition-analyzer

チームのコンディション分析エージェント。

## 前提条件
- `data/intermediate/collected_data.json` が存在すること（data-collectorの出力）

## 実行方法

```bash
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData
from toto.analyzers.condition import ConditionAnalyzer
from toto.config import INTERMEDIATE_DIR

data = CollectedData.model_validate_json(
    (INTERMEDIATE_DIR / 'collected_data.json').read_text(encoding='utf-8'))

analyzer = ConditionAnalyzer()
result = analyzer.analyze(data)

print(f'Analyzed {len(result.conditions)} matches')
for c in result.conditions:
    print(f'  #{c.match_number} {c.home_team} vs {c.away_team}')
    print(f'    fatigue: H={c.fatigue_home:+.2f} A={c.fatigue_away:+.2f}')
    print(f'    momentum: H={c.momentum_home:+.2f} A={c.momentum_away:+.2f}')
    print(f'    venue={c.venue_advantage:+.2f} h2h={c.h2h_affinity:+.2f}')
    print(f'    adjustment: H={c.total_home_adjustment:+.3f} A={c.total_away_adjustment:+.3f}')
"
```

## 分析ファクター（各 -1.0 〜 +1.0 で正規化）

| ファクター | 計算方法 |
|-----------|---------|
| fatigue | 中日数 + 直近30日の試合数 |
| momentum | 直近5試合の勝敗パターン + 得失点差の線形回帰 |
| venue | ホーム+0.3基準 + 移動距離補正（800km超で+0.1） |
| h2h_affinity | 過去の直接対決成績（3試合未満は0.0） |
| weather | プレースホルダー（将来実装予定） |

## 出力
- `data/intermediate/condition_analysis.json`
- スキーマ: `ConditionAnalysis`

## 完了条件
- `condition_analysis.json` が存在する
- 全試合分の `MatchCondition` が含まれる
- 各ファクターが -1.0〜+1.0 の範囲内
