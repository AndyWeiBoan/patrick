# Phase 3 計畫

## 功能 1：清空資料庫

### 決策
全刪，後續再決定是否實作局部刪除（`--before <date>`）。

### 實作方式

**`storage.py`** 新增 `reset_database()` 方法：
```python
def reset_database(self):
    db = lancedb.connect(DATA_DIR)
    for table_name in ["session_summaries", "turn_chunks"]:
        if table_name in db.table_names():
            db.drop_table(table_name)
    self._bm25_cache = None
    self._initialized = False
```

**`cli.py`** 新增 `patrick clear` 指令：
```python
@app.command()
def clear(yes: bool = typer.Option(False, "--yes", help="Skip confirmation")):
    """Delete all stored memories."""
    if not yes:
        confirm = typer.confirm("This will delete ALL memories. Are you sure?")
        if not confirm:
            raise typer.Abort()
    storage.reset_database()
    typer.echo("All memories cleared.")
```

---

## 功能 2：時間衰減搜尋

### 概念
搜尋結果的排序不只看「內容有多相關」，還考慮「這筆記憶是不是最近的」。
越新的記憶排越前面，舊的記憶會被自動降權。

### 分數計算公式
```
最終分數 = 相關度分數 × 新鮮度折扣

新鮮度折扣 = exp(-age_days / HALFLIFE_DAYS)
```

範例（預設半衰期 30 天）：
- 今天的記憶 → 折扣 = 1.0（滿分）
- 30 天前    → 折扣 ≈ 0.5（打五折）
- 60 天前    → 折扣 ≈ 0.25（打兩折半）

### 實作方式

**`config.py`** 新增兩個可調參數：
```python
TIME_DECAY_HALFLIFE_DAYS: int = 30   # 半衰期（天），越小代表越偏好新記憶
RECENCY_BLEND: float = 1.0           # 新鮮度折扣的影響力（0=完全不管時間, 1=完全套用）
```

**`storage.py`** 新增獨立函式 `search_chunks_with_recency()`：
```python
def search_chunks_with_recency(
    self,
    query_vector: list[float],
    text: str,
    top_k: int = TOP_K_CHUNKS,
    session_ids: list[str] | None = None,
    halflife_days: int = TIME_DECAY_HALFLIFE_DAYS,
) -> list[dict]:
    """先用現有 hybrid 搜尋拿結果，再套上新鮮度折扣重新排序。"""
    import math
    from datetime import datetime, timezone

    results = self.search_chunks_hybrid(query_vector, text, top_k=top_k * 2, session_ids=session_ids)
    now = datetime.now(timezone.utc)

    for chunk in results:
        created_at = chunk.get("created_at")
        if created_at:
            dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
            age_days = (now - dt).days
        else:
            age_days = 9999  # 沒有時間戳就當作很舊
        decay = math.exp(-age_days / halflife_days)
        base_score = 1.0 - chunk.get("_distance", 1.0) / 2.0
        chunk["_recency_score"] = base_score * decay

    results.sort(key=lambda c: c.get("_recency_score", 0), reverse=True)
    return results[:top_k]
```

**`tools.py`** 在 `memory_search` 和 `memory_deep_search` 加可選參數：
```python
use_recency: bool = False  # 預設 False，不改變現有行為
```
當 `use_recency=True` 時，改呼叫 `storage.search_chunks_with_recency()` 而非原本的函式。

### 重點設計原則
- **不改動現有函式**：`search_chunks_hybrid()` 原封不動，新功能全在新函式裡
- **預設關閉**：`use_recency=False` 確保現有行為不受影響
- **半衰期可調**：在 `config.py` 改 `TIME_DECAY_HALFLIFE_DAYS` 即可調整偏好新舊的程度

---

## 功能 3：hook_type 多值過濾

### 需求
`memory_search` / `memory_deep_search` 的 `hook_type` 參數原本只接受單一字串，
無法同時過濾 `assistant_text` + `user_prompt`（排除工具呼叫紀錄）。

### 實作方式

**`tools.py`** — `memory_search` 與 `memory_deep_search` 型別改為：
```python
hook_type: str | list[str] | None = None
```
docstring 新增：`["assistant_text", "user_prompt"]` — 傳 list 可同時匹配多種類型

**`storage.py`** — 四個搜尋函式型別同步更新；filter 邏輯改為：
- `search_chunks()`：`IN (...)` SQL 語法支援 list
- `search_chunks_bm25()`：post-index filter 改用 `not in` 判斷

### 設計原則
- 完全向下相容：傳單一字串行為不變，`None` 不過濾行為不變
- 各層型別一致：tools → hybrid → bm25/vector 全部同步，無 Pyright 警告

---

## 待決定
- [ ] 是否在未來加入 `--before <date>` 局部刪除
