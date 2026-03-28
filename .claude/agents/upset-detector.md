---
model: opus
tools:
  - Read
  - Bash
  - Grep
  - Glob
---

# upset-detector

波乱検出エージェント。Phase Aの全出力を統合して波乱パターンを検出する。

## 前提条件（全て必須）
- `data/intermediate/collected_data.json`
- `data/intermediate/condition_analysis.json`
- `data/intermediate/odds_analysis.json`

## 実行方法

```bash
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData, ConditionAnalysis, OddsAnalysis
from toto.analyzers.upset import UpsetDetector
from toto.config import INTERMEDIATE_DIR

collected = CollectedData.model_validate_json(
    (INTERMEDIATE_DIR / 'collected_data.json').read_text(encoding='utf-8'))
condition = ConditionAnalysis.model_validate_json(
    (INTERMEDIATE_DIR / 'condition_analysis.json').read_text(encoding='utf-8'))
odds = OddsAnalysis.model_validate_json(
    (INTERMEDIATE_DIR / 'odds_analysis.json').read_text(encoding='utf-8'))

detector = UpsetDetector()
result = detector.analyze(collected, condition, odds)

alerts = [u for u in result.upsets if u.is_upset_alert]
print(f'=== Upset Detection: {len(alerts)} alerts out of {len(result.upsets)} matches ===')
for u in result.upsets:
    flag = ' *** ALERT ***' if u.is_upset_alert else ''
    patterns = ', '.join(p.category for p in u.patterns) if u.patterns else 'none'
    print(f'  #{u.match_number} {u.home_team} vs {u.away_team}')
    print(f'    score={u.upset_score}/100 patterns=[{patterns}]{flag}')
    if u.is_upset_alert:
        print(f'    adjusted: H={u.adjusted_home_prob:.0%} D={u.adjusted_draw_prob:.0%} A={u.adjusted_away_prob:.0%}')
        print(f'    reason: {u.explanation}')
"
```

## 波乱検出パターン（toto-roid Daisy参考）

| パターン | 検出条件 |
|---------|---------|
| fatigue_gap | 本命が疲労(-0.3未満)、相手が休養(+0.3超) |
| momentum_reversal | 本命のmomentumが負、相手が正 |
| h2h_mismatch | 本命の対戦相性が -0.3未満 |
| environment_disadvantage | 本命が800km超のアウェイ |
| season_context | 残留争いチームの異常な強さ / 優勝確定後のモチベ低下 |
| vote_overconfidence | Daisyコアロジック: 投票率50-80%帯で15%以上の乖離 |

## Daisy基準
- 対象: 本命投票率 50%〜80% の試合のみ
- 発火: 波乱スコア 70以上
- 除外: 投票率80%超（ガチ本命は波乱になりにくい）

## 出力
- `data/intermediate/upset_analysis.json`
- スキーマ: `UpsetAnalysis`

## 完了条件
- `upset_analysis.json` が存在する
- 全試合分の `MatchUpset` が含まれる
- `upset_score` が 0〜100 の範囲内
- `adjusted_home_prob + adjusted_draw_prob + adjusted_away_prob ≈ 1.0`
