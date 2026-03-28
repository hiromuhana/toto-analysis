# toto AI予測パイプライン実行

toto予測の全パイプラインを Phase A → B → C の順序で実行する。

## 引数

`$ARGUMENTS` をパースする。フォーマット: `[ラウンド番号] [予算] [タイプ]`
- ラウンド番号（必須）: 例 `1620`
- 予算（省略時: 1000）: 円単位
- タイプ（省略時: toto）: `toto` or `minitoto`

引数が空または不正な場合、ラウンド番号をユーザーに確認してから実行する。

## 実行手順

### 事前準備

1. `conda activate toto-ai` 環境で実行すること
2. `data/intermediate/` 配下の古いJSONファイルを削除する:
   ```bash
   rm -f data/intermediate/*.json
   ```

### Phase A: データ収集＋分析（3エージェント並列）

以下の3つのサブエージェントを **同時に（並列で）** Agent ツールで呼び出す:

**data-collector** (model: sonnet):
```
conda activate toto-ai && PYTHONPATH=src python -c "
import asyncio
from toto.collectors.mock import MockCollector
from toto.models.schemas import TotoType
async def run():
    collector = MockCollector()
    data = await collector.collect(ROUND, toto_type=TotoType.TOTO)
    from toto.config import INTERMEDIATE_DIR
    (INTERMEDIATE_DIR / 'collected_data.json').write_text(data.model_dump_json(indent=2), encoding='utf-8')
    print(f'Collected {len(data.matches)} matches')
asyncio.run(run())
"
```
ここで ROUND は引数のラウンド番号に置換。
リアルコレクター（JLeagueCollector等）が使える場合はそちらを優先し、失敗時のみMockCollectorを使用。

**condition-analyzer** (model: sonnet):
```
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData
from toto.analyzers.condition import ConditionAnalyzer
from toto.config import INTERMEDIATE_DIR
data = CollectedData.model_validate_json((INTERMEDIATE_DIR / 'collected_data.json').read_text())
analyzer = ConditionAnalyzer()
result = analyzer.analyze(data)
print(f'Analyzed {len(result.conditions)} matches')
for c in result.conditions:
    print(f'  Match {c.match_number}: {c.home_team} vs {c.away_team} | home_adj={c.total_home_adjustment:.3f}')
"
```
注意: このエージェントは data-collector の出力 `collected_data.json` を読むため、data-collector が先に完了している必要がある。ただし Phase A で並列起動する場合は、data-collector を少し先に起動するか、collected_data.json が存在するまでリトライする。

**odds-analyzer** (model: sonnet):
```
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData
from toto.analyzers.odds import OddsAnalyzer
from toto.config import INTERMEDIATE_DIR
data = CollectedData.model_validate_json((INTERMEDIATE_DIR / 'collected_data.json').read_text())
analyzer = OddsAnalyzer()
result = analyzer.analyze(data)
print(f'Analyzed {len(result.odds)} matches')
for o in result.odds:
    biases = ', '.join(o.biases) if o.biases else 'none'
    print(f'  Match {o.match_number}: value_h={o.value_home:.3f} value_d={o.value_draw:.3f} value_a={o.value_away:.3f} biases=[{biases}]')
"
```

**重要**: condition-analyzer と odds-analyzer は collected_data.json に依存するため、実際の並列実行は以下のパターンが推奨:
1. まず data-collector を起動して完了を待つ
2. condition-analyzer と odds-analyzer を並列で起動

Phase A 完了後、以下の3ファイルが存在することを確認:
- `data/intermediate/collected_data.json`
- `data/intermediate/condition_analysis.json`
- `data/intermediate/odds_analysis.json`

存在しないファイルがあればエラーを報告してユーザーに判断を仰ぐ。

### Phase B: 波乱検出（逐次）

**upset-detector** (model: opus) を呼び出す:
```
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData, ConditionAnalysis, OddsAnalysis
from toto.analyzers.upset import UpsetDetector
from toto.config import INTERMEDIATE_DIR
collected = CollectedData.model_validate_json((INTERMEDIATE_DIR / 'collected_data.json').read_text())
condition = ConditionAnalysis.model_validate_json((INTERMEDIATE_DIR / 'condition_analysis.json').read_text())
odds = OddsAnalysis.model_validate_json((INTERMEDIATE_DIR / 'odds_analysis.json').read_text())
detector = UpsetDetector()
result = detector.analyze(collected, condition, odds)
alerts = [u for u in result.upsets if u.is_upset_alert]
print(f'Upset analysis: {len(result.upsets)} matches, {len(alerts)} alerts')
for u in result.upsets:
    flag = ' *** ALERT ***' if u.is_upset_alert else ''
    print(f'  Match {u.match_number}: {u.home_team} vs {u.away_team} | score={u.upset_score} patterns={len(u.patterns)}{flag}')
"
```

Phase B 完了後、`data/intermediate/upset_analysis.json` の存在を確認。

### Phase C: 戦略統合＋レポート生成（逐次）

**strategy-synthesizer** (model: opus) を呼び出す:
```
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.strategy.synthesizer import StrategySynthesizer
from toto.output.report import generate_report
from toto.models.schemas import CollectedData, ConditionAnalysis, OddsAnalysis, UpsetAnalysis
from toto.config import INTERMEDIATE_DIR
collected = CollectedData.model_validate_json((INTERMEDIATE_DIR / 'collected_data.json').read_text())
condition = ConditionAnalysis.model_validate_json((INTERMEDIATE_DIR / 'condition_analysis.json').read_text())
odds = OddsAnalysis.model_validate_json((INTERMEDIATE_DIR / 'odds_analysis.json').read_text())
upset = UpsetAnalysis.model_validate_json((INTERMEDIATE_DIR / 'upset_analysis.json').read_text())
synth = StrategySynthesizer()
strategy = synth.synthesize(collected, condition, odds, upset, budget=BUDGET)
report_path = generate_report(strategy)
print(f'Strategy: {len(strategy.predictions)} predictions, {len(strategy.plans)} plans')
print(f'Report: {report_path}')
for p in strategy.predictions:
    pick = {'1': 'H', '0': 'D', '2': 'A'}[p.recommended_pick.value]
    alert = ' [UPSET]' if p.upset_alert else ''
    print(f'  Match {p.match_number}: {p.home_team} vs {p.away_team} -> {pick} ({p.confidence:.0%}){alert}')
"
```
ここで BUDGET は引数の予算に置換。

### 完了時の表示

パイプライン完了後、以下を表示する:

1. **レポートファイルの内容**を Read ツールで読み込んで全文表示
2. **3パターンのコピペ用買い目**を強調表示
3. `data/intermediate/` の全JSONファイルの存在確認結果

### エラーハンドリング

- 各フェーズでPythonスクリプトがエラーになった場合、エラー内容をユーザーに表示し、リトライするか聞く
- モックデータへのフォールバックが発生した場合、その旨をユーザーに通知する
- 中間ファイルが欠けている場合、そのフェーズの再実行を提案する

### 代替: Python直接実行

サブエージェント経由ではなくPython直接実行も可能:
```bash
conda activate toto-ai && PYTHONPATH=src python src/main.py --round ROUND --budget BUDGET --type TYPE
```
