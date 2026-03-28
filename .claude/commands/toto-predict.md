# toto AI予測パイプライン実行

toto予測の全パイプラインを Phase A → B → C の順序で実行する。
**統計モデル（Dixon-Coles 1,140試合学習済み）+ LLM推論のハイブリッド予測。**

## 引数

`$ARGUMENTS` をパースする。フォーマット: `[ラウンド番号] [予算] [タイプ]`
- ラウンド番号（必須）: 例 `1619`
- 予算（省略時: 4000）: 円単位
- タイプ（省略時: toto）: `toto` or `minitoto`

引数が空または不正な場合、ラウンド番号をユーザーに確認してから実行する。

## 実行手順

### 事前準備

1. `data/intermediate/` 配下の古いJSONファイルを削除する:
   ```bash
   rm -f data/intermediate/*.json
   ```

### Phase A: データ収集 + 統計モデル（Python自動実行）

以下を Bash で実行する:
```bash
conda activate toto-ai && PYTHONPATH=src python src/main.py --round ROUND --budget BUDGET --type TYPE
```
ROUND, BUDGET, TYPE は引数から置換。

Phase A 完了後、以下のファイルが生成されていることを確認:
- `data/intermediate/collected_data.json`
- `data/intermediate/condition_analysis.json`
- `data/intermediate/odds_analysis.json`
- `data/intermediate/upset_analysis.json`
- `data/intermediate/strategy.json`
- `reports/round_XXXX_report.md`

### Phase B: LLM推論による波乱評価（★ハイブリッドの核心★）

upset-detector エージェントを Agent ツールで呼び出す（model: opus）。

プロンプト:
```
data/intermediate/ の collected_data.json, condition_analysis.json, odds_analysis.json を Read で読み込め。
各試合について以下を実行:
1. WebSearch で "{ホームチーム名} {アウェイチーム名} 2026 予想" を検索
2. 統計データ（DCモデル確率、投票率、Eloレーティング）を確認
3. 以下の観点でLLM推論波乱スコア（0-100）を付ける:
   - 主力選手の怪我・離脱
   - 監督交代・戦術変更
   - 昇降格の文脈・モチベーション格差
   - 対戦カードの特殊性（ダービー等）
   - 統計モデルと投票率の乖離の理由
4. 結果を data/intermediate/upset_analysis_llm.json に Write で保存
```

### Phase C: LLM統合最終判断（★最終決定★）

strategy-synthesizer エージェントを Agent ツールで呼び出す（model: opus）。

プロンプト:
```
data/intermediate/ の全JSONファイル（collected_data, condition_analysis, odds_analysis, upset_analysis, upset_analysis_llm）を Read で読み込め。
各試合について:
1. DCモデル確率をベースラインとする
2. LLM波乱評価で補正（波乱スコア高い試合は本命確率を下げる）
3. バックテスト知見を適用:
   - ホーム勝率は+4pt過小評価されている傾向がある
   - 引き分け的中率は5.5%で壊滅的。拮抗試合以外は引き分け予測を避ける
   - ダブル（上位2択）の的中率は74.9%
4. 最終的な勝敗確率と推奨ピックを決定
5. 3パターンの購入プラン生成 + minitoto推奨プラン
6. reports/round_XXXX_report.md を Edit で更新し、LLM分析セクションを追加
```

### 完了時の表示

1. レポートファイルを Read して全文表示
2. 最終推奨買い目をコピペ用で表示
3. 特に注目すべき試合（統計とLLMの見解が分かれる試合）をハイライト

## バックテスト知見（エージェントに伝えるべき数値）

| 指標 | 値 |
|------|-----|
| DCモデルのシングル的中率 | 49.3%（ランダム33.3%） |
| ダブル（上位2択）的中率 | 74.9% |
| ホーム勝率の過小評価 | +4.0ポイント |
| 引き分け的中率 | 5.5%（壊滅的） |
| minitotoシングルROI | +751%（年間利益+37,544円） |
| minitotoスマートダブル(45%) | ROI +332%（年間利益+97,105円） |

## 代替: Python直接実行（LLM推論なし）

LLM推論を省略して統計モデルだけで予測する場合:
```bash
conda activate toto-ai && PYTHONPATH=src python src/main.py --round ROUND --budget BUDGET --type TYPE
```
