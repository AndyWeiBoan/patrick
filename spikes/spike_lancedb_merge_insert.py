#!/usr/bin/env python3
"""
Spike 2: 驗證 LanceDB merge_insert 在 embedded 模式下的行為
特別關注：
  - nullable 欄位（source_file）的處理
  - 同 session_id upsert 是否正確覆蓋
  - vector 欄位的 upsert 行為

執行方式：
  pip install lancedb pyarrow
  python3 spike_lancedb_merge_insert.py

結果會直接印出，每個 case PASS/FAIL 清楚標記。
"""

import tempfile
import shutil
from datetime import datetime, timezone

def run():
    try:
        import lancedb
        import pyarrow as pa
    except ImportError:
        print("FATAL: pip install lancedb pyarrow")
        return

    tmp_dir = tempfile.mkdtemp(prefix="patrick_spike_")
    print(f"[INFO] Working dir: {tmp_dir}\n")

    try:
        db = lancedb.connect(tmp_dir)

        # --- Schema 定義（和 phase1.md 一致）---
        schema = pa.schema([
            pa.field("session_id", pa.string()),
            pa.field("summary_text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 4)),  # 4-dim 方便測試
            pa.field("created_at", pa.timestamp("us", tz="UTC")),
            pa.field("updated_at", pa.timestamp("us", tz="UTC")),
            pa.field("source_file", pa.string()),  # nullable
        ])

        # 建立 table（空）
        table = db.create_table("session_summaries", schema=schema)
        print("=== CASE 1: 初次 upsert（insert path）===")
        now = datetime.now(timezone.utc)
        row1 = {
            "session_id": "sess-001",
            "summary_text": "第一次 summary",
            "vector": [0.1, 0.2, 0.3, 0.4],
            "created_at": now,
            "updated_at": now,
            "source_file": None,  # nullable
        }
        _merge_insert(table, [row1])
        results = table.to_pandas()
        assert len(results) == 1, f"FAIL: expected 1 row, got {len(results)}"
        assert results.iloc[0]["summary_text"] == "第一次 summary"
        import pandas as pd
        sf_val = results.iloc[0]["source_file"]
        # LanceDB nullable string 欄位在 pandas 裡回傳 float nan，不是 None
        assert pd.isna(sf_val) or sf_val is None, f"source_file expected null/nan, got {repr(sf_val)}"
        print(f"  PASS: 插入 1 筆，source_file nullable OK（實際值: {repr(sf_val)}, type: {type(sf_val).__name__}）")
        print(f"  [重要發現] nullable string 欄位在 pandas 回傳 float nan，需用 pd.isna() 判斷，不能用 `is None`")
        print(f"  row: {results.to_dict('records')}\n")

        print("=== CASE 2: 同 session_id upsert（update path）===")
        now2 = datetime.now(timezone.utc)
        row2 = {
            "session_id": "sess-001",
            "summary_text": "更新後的 summary（應覆蓋）",
            "vector": [0.9, 0.8, 0.7, 0.6],
            "created_at": now,   # created_at 不變
            "updated_at": now2,
            "source_file": "README.md",  # 這次有值
        }
        _merge_insert(table, [row2])
        results = table.to_pandas()
        assert len(results) == 1, f"FAIL: expected still 1 row, got {len(results)}"
        assert results.iloc[0]["summary_text"] == "更新後的 summary（應覆蓋）"
        assert results.iloc[0]["source_file"] == "README.md"
        print(f"  PASS: upsert 後仍 1 筆，summary 覆蓋 OK，source_file 更新 OK")
        print(f"  row: {results.to_dict('records')}\n")

        print("=== CASE 3: 不同 session_id（insert 新行）===")
        row3 = {
            "session_id": "sess-002",
            "summary_text": "第二個 session",
            "vector": [0.5, 0.5, 0.5, 0.5],
            "created_at": now2,
            "updated_at": now2,
            "source_file": None,
        }
        _merge_insert(table, [row3])
        results = table.to_pandas()
        assert len(results) == 2, f"FAIL: expected 2 rows, got {len(results)}"
        print(f"  PASS: 新 session_id 插入為新行，共 {len(results)} 筆 OK\n")

        print("=== CASE 4: 批次 upsert（兩筆同時，一 update 一 insert）===")
        now3 = datetime.now(timezone.utc)
        batch = [
            {
                "session_id": "sess-001",
                "summary_text": "批次更新 sess-001",
                "vector": [0.1, 0.1, 0.1, 0.1],
                "created_at": now,
                "updated_at": now3,
                "source_file": None,
            },
            {
                "session_id": "sess-003",
                "summary_text": "批次新增 sess-003",
                "vector": [0.3, 0.3, 0.3, 0.3],
                "created_at": now3,
                "updated_at": now3,
                "source_file": None,
            },
        ]
        _merge_insert(table, batch)
        results = table.to_pandas()
        assert len(results) == 3, f"FAIL: expected 3 rows, got {len(results)}"
        sess1 = results[results["session_id"] == "sess-001"].iloc[0]
        assert sess1["summary_text"] == "批次更新 sess-001"
        print(f"  PASS: 批次 upsert OK，共 {len(results)} 筆\n")

        print("=== CASE 5: vector search 在 upsert 後仍正常 ===")
        query_vec = [0.1, 0.1, 0.1, 0.1]
        search_results = table.search(query_vec).limit(1).to_pandas()
        assert len(search_results) == 1
        print(f"  PASS: vector search 正常，top-1: session_id={search_results.iloc[0]['session_id']}\n")

        print("=" * 50)
        print("全部 CASE PASS — LanceDB merge_insert 在 embedded 模式行為符合 Phase 1 設計預期")
        print(f"  特別確認：nullable source_file 欄位處理正常")
        print(f"  特別確認：同 session_id 覆蓋更新正常")
        print(f"  特別確認：vector search 在 upsert 後仍可用")

    except AssertionError as e:
        print(f"\n[FAIL] {e}")
    except Exception as e:
        import traceback
        print(f"\n[ERROR] {e}")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"\n[INFO] 清理完成：{tmp_dir}")


def _merge_insert(table, rows: list[dict]):
    """封裝 merge_insert，對應 phase1.md 的 upsert pattern"""
    import pyarrow as pa

    # 把 list[dict] 轉成 pa.Table（LanceDB merge_insert 需要）
    arrow_table = pa.Table.from_pylist(rows, schema=table.schema)
    (
        table.merge_insert("session_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(arrow_table)
    )


if __name__ == "__main__":
    run()
