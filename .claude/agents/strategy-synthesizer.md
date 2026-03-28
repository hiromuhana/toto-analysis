---
model: opus
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
---

# strategy-synthesizer

戦略統合・レポート生成エージェント。全エージェントの出力を統合して最終予測と購入プランを生成する。

## 前提条件（全て必須）
- `data/intermediate/collected_data.json`
- `data/intermediate/condition_analysis.json`
- `data/intermediate/odds_analysis.json`
- `data/intermediate/upset_analysis.json`

## 実行方法

```bash
conda activate toto-ai && PYTHONPATH=src python -c "
import sys
from toto.strategy.synthesizer import StrategySynthesizer
from toto.output.report import generate_report
from toto.models.schemas import CollectedData, ConditionAnalysis, OddsAnalysis, UpsetAnalysis
from toto.config import INTERMEDIATE_DIR

BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

collected = CollectedData.model_validate_json(
    (INTERMEDIATE_DIR / 'collected_data.json').read_text(encoding='utf-8'))
condition = ConditionAnalysis.model_validate_json(
    (INTERMEDIATE_DIR / 'condition_analysis.json').read_text(encoding='utf-8'))
odds = OddsAnalysis.model_validate_json(
    (INTERMEDIATE_DIR / 'odds_analysis.json').read_text(encoding='utf-8'))
upset = UpsetAnalysis.model_validate_json(
    (INTERMEDIATE_DIR / 'upset_analysis.json').read_text(encoding='utf-8'))

synth = StrategySynthesizer()
strategy = synth.synthesize(collected, condition, odds, upset, budget=BUDGET)
report_path = generate_report(strategy)

pick_map = {'1': 'H', '0': 'D', '2': 'A'}
print(f'=== Strategy: {len(strategy.predictions)} predictions, {len(strategy.plans)} plans ===')
print()
for p in strategy.predictions:
    pick = pick_map[p.recommended_pick.value]
    alert = ' [UPSET]' if p.upset_alert else ''
    print(f'  #{p.match_number} {p.home_team} vs {p.away_team} -> {pick} ({p.confidence:.0%}){alert}')
print()
for plan in strategy.plans:
    codes = []
    for pick in plan.picks:
        codes.append('-'.join(r.value for r in pick.picks))
    print(f'{plan.display_name}: {\" \".join(codes)}')
    print(f'  {plan.total_combinations}口 / {plan.cost_yen:,}円 / 的中率 {plan.estimated_hit_rate:.4%}')
print()
print(f'Report: {report_path}')
" $BUDGET
```

## 確率統合方式

| 要素 | 重み | ソース |
|------|------|--------|
| 基礎モデル確率 | 40% | Dixon-Coles / Elo / CatBoost |
| コンディション補正 | 20% | 疲労・モメンタム・ホーム |
| 市場分析 | 15% | 投票率の暗黙確率 |
| 波乱検出 | 25% | 波乱補正後の確率 |

## 購入プラン

| プラン | キャラ | ダブル基準 | トリプル基準 |
|--------|--------|-----------|-------------|
| Conservative | しっかりゾウ | 波乱警戒 & 信頼度50%未満 | なし |
| Balanced | バランスバード | 信頼度45%未満 | なし |
| Aggressive | ハンターライオン | 信頼度50%未満 | 波乱警戒 & 信頼度40%未満 |

## 出力
- `data/intermediate/strategy.json`
- `reports/round_XXXX_report.md`
- スキーマ: `Strategy`

## 完了条件
- `strategy.json` が存在する
- `reports/round_XXXX_report.md` が存在する
- 全試合分の `MatchPrediction` が含まれる
- 3つの `PurchasePlan`（conservative/balanced/aggressive）が含まれる
- レポートに「免責事項」「コピペ用」セクションが含まれる
