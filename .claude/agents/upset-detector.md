---
model: opus
tools:
  - Read
  - Bash
  - Grep
  - Glob
  - WebSearch
  - WebFetch
---

# upset-detector

波乱検出エージェント。**統計モデルの確率をベースに、LLMの推論能力で波乱リスクを評価する。**

## 実行手順

### Step 1: 統計データの読み込み

以下の中間ファイルを Read ツールで読み込む:
- `data/intermediate/collected_data.json` — 試合データ・チーム成績・Eloレーティング
- `data/intermediate/condition_analysis.json` — コンディション分析
- `data/intermediate/odds_analysis.json` — 投票率・モデル確率・バリュー分析

### Step 2: ルールベース波乱検出（Pythonで実行）

```bash
conda activate toto-ai && PYTHONPATH=src python -c "
from toto.models.schemas import CollectedData, ConditionAnalysis, OddsAnalysis
from toto.analyzers.upset import UpsetDetector
from toto.config import INTERMEDIATE_DIR
collected = CollectedData.model_validate_json((INTERMEDIATE_DIR / 'collected_data.json').read_text())
condition = ConditionAnalysis.model_validate_json((INTERMEDIATE_DIR / 'condition_analysis.json').read_text())
odds = OddsAnalysis.model_validate_json((INTERMEDIATE_DIR / 'odds_analysis.json').read_text())
detector = UpsetDetector()
result = detector.analyze(collected, condition, odds)
for u in result.upsets:
    print(f'#{u.match_number} {u.home_team} vs {u.away_team} | score={u.upset_score} patterns={[p.category for p in u.patterns]}')
"
```

### Step 3: LLM推論による波乱評価（★核心★）

**ルールベースの結果を読んだ上で、以下の観点から各試合の波乱リスクを自分（Claude）で推論する:**

各試合について、WebSearchで以下を検索:
- `"{ホームチーム名} {アウェイチーム名} 2026" 予想 スタメン`
- `"{チーム名} 怪我 離脱 2026年3月"`

検索結果を踏まえて、以下の観点で波乱リスクを0-100で評価:

1. **選手の離脱・怪我情報** — 主力が欠場していれば大きなリスク
2. **監督交代・戦術変更** — 直近で監督が変わったチームは不安定
3. **モチベーション格差** — 残留争い vs 消化試合
4. **昇降格の文脈** — 昇格組の勢い、降格組のプレッシャー
5. **ダービー・因縁** — 特別な対戦カードは予測不能性が高い
6. **天候・気候** — 遠方アウェイ+悪天候は不利
7. **統計モデルとの乖離** — DCモデルと投票率の乖離が大きい試合は要注意

### Step 4: 統計+LLM統合スコアの算出

各試合に対して、以下の重み付けで最終波乱スコアを算出:
- ルールベース波乱スコア: **40%**
- LLM推論波乱スコア: **60%**

### Step 5: 最終出力

以下のフォーマットで `data/intermediate/upset_analysis_llm.json` に出力:

```json
{
  "agent": "upset-detector-llm",
  "toto_round": 1619,
  "upsets": [
    {
      "match_number": 1,
      "home_team": "横浜FC",
      "away_team": "湘南",
      "rule_based_score": 18,
      "llm_score": 45,
      "combined_score": 34,
      "llm_reasoning": "横浜FCは2025年J1から降格。主力流出の影響が大きく、新体制が安定していない。湘南も降格組だが若手主体のチーム構成で適応力が高い。ホームだが波乱リスクは中程度。",
      "key_factors": ["降格組同士", "主力流出", "新体制"],
      "adjusted_home_prob": 0.35,
      "adjusted_draw_prob": 0.30,
      "adjusted_away_prob": 0.35,
      "is_upset_alert": false
    }
  ]
}
```

## 重要ルール

- **統計データを無視しない**: LLMの直感だけで判断するのではなく、DCモデルの確率を基準にして「補正」する
- **根拠を明記**: llm_reasoningに必ず具体的な根拠を書く。「なんとなく」はNG
- **過信しない**: LLMの知識は2025年5月までの学習データに基づく。2026年の最新情報はWebSearchで補う
- **引き分けを軽視しない**: バックテストで引き分け的中率5.5%だった。引き分けの可能性を積極的に評価する
