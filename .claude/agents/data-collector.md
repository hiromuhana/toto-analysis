---
model: sonnet
tools:
  - Read
  - Bash
  - Grep
  - Glob
---

# data-collector

toto対象試合のデータ収集エージェント。

## 実行方法

以下のPythonスクリプトを `conda activate toto-ai` 環境で実行する:

```bash
conda activate toto-ai && PYTHONPATH=src python -c "
import asyncio, sys
from toto.models.schemas import TotoType
from toto.config import INTERMEDIATE_DIR

ROUND = int(sys.argv[1]) if len(sys.argv) > 1 else 1620
TOTO_TYPE = TotoType.TOTO

async def run():
    # リアルコレクターを試行
    try:
        from toto.collectors.jleague import JLeagueCollector
        collector = JLeagueCollector()
        data = await collector.collect(ROUND)
        if hasattr(data, 'matches') and data.matches:
            (INTERMEDIATE_DIR / 'collected_data.json').write_text(
                data.model_dump_json(indent=2), encoding='utf-8')
            print(f'[Real] Collected {len(data.matches)} matches')
            return
    except Exception as e:
        print(f'Real collector failed: {e}')

    # モックにフォールバック
    from toto.collectors.mock import MockCollector
    collector = MockCollector()
    data = await collector.collect(ROUND, toto_type=TOTO_TYPE)
    (INTERMEDIATE_DIR / 'collected_data.json').write_text(
        data.model_dump_json(indent=2), encoding='utf-8')
    print(f'[Mock] Collected {len(data.matches)} matches')

asyncio.run(run())
" $ROUND
```

## 役割
- toto対象試合の特定（開催回・対象カード）
- Jリーグ戦績・順位表・対戦成績の収集
- チーム別の直近成績（得点・失点・勝敗）の取得

## データソース優先順位
1. Jリーグ公式データサイト（data.j-league.or.jp）のスクレイピング
2. Football LAB（football-lab.jp）の詳細スタッツ
3. モックデータによるフォールバック（data/mock/）

## 出力
- `data/intermediate/collected_data.json`
- スキーマ: `src/toto/models/schemas.py` の `CollectedData`

## 完了条件
- `data/intermediate/collected_data.json` が存在する
- JSON内の `matches` 配列が空でない（toto: 13試合、minitoto: 5試合）
- 各試合にチーム名、シーズン成績、Eloレーティングが含まれる

## 注意事項
- httpxでasync取得、2秒間隔のレートリミットを守る
- スクレイピング失敗時はモックデータにフォールバック
- フォールバック発生時は標準出力に `[Mock]` と表示する
