# 整合測試計劃 — Patrick Memory Server

**狀態：** 描述 centroid + hint 架構改動後的預期行為。  
**目前狀況：** 所有功能（`summary_text` 自動生成、`hint` 欄位、Layer 1 hint 優先）已實作完畢，兩個 bug fix 也已套用。

---

## 測試基礎設施

### 需要的元件

**測試資料庫 — 每個測試獨立的 LanceDB 實例**

每個測試必須對全新的、用完即棄的資料庫執行，避免測試之間狀態污染。建議用 `pytest` fixture：

1. 用 `monkeypatch.setattr` 把 `config.DATA_DIR` 指向 `tmp_path` 子目錄。
2. patch 之後呼叫 `storage.initialize()`。
3. 測試結束後呼叫 `storage._db.close()` 並刪除 tmp 目錄。

因為 `Storage` 和 `EmbeddingProvider` 是模組層級的 singleton，每個測試之間還需要重設 `Storage._instance._initialized = False`（或用 factory pattern）。最乾淨的做法是用 `autouse` fixture 在每個測試前執行這個重設。

**模擬 embedding provider**

在測試裡載入完整 ONNX 模型（~220 MB）太慢。改用 `FakeEmbeddingProvider`：

- 回傳確定性、可重現的向量（例如：從文字字串衍生的 hash-seeded 單位向量）。
- `chunk_text()` 用簡單的字元數分割，讓 chunking 測試仍然有意義。
- `embed_async()` 回傳假向量，不載入 fastembed。
- 用 `monkeypatch.setattr("patrick.tools.provider", fake_provider)` 和 `monkeypatch.setattr("patrick.storage.provider", fake_provider)` 注入。

關鍵特性：**兩個相似文字的向量，cosine similarity 必須 > MIN_SESSION_SCORE**。設計時讓包含相同關鍵字的文字向量距離較近，或在需要相似度排序的 fixture 中直接硬編碼文字→向量對應關係。

**非同步測試支援**

所有 tool function 都是 async。在 `pyproject.toml` 裡設定 `asyncio_mode = "auto"`（或在每個測試上加 `@pytest.mark.asyncio`）。

**Fixtures 結構**

```
conftest.py
  ├── tmp_db(tmp_path, monkeypatch)       — 隔離的 LanceDB 目錄 + 已 patch 的 DATA_DIR
  ├── fake_provider(monkeypatch)          — 確定性 embedding，不載入模型
  ├── storage_ready(tmp_db, fake_provider)— 呼叫 storage.initialize()，重設 singleton
  └── session_id()                        — 回傳新的 uuid4 字串
```

**測試檔案結構（建議）**

```
tests/
  integration/
    test_centroid_autogeneration.py
    test_hint_field.py
    test_deep_search_layer1.py
    test_next_required_action.py
    test_session_id_isolation.py
    test_backward_compatibility.py
    test_multiple_saves.py
    test_hook_vs_agent.py
```

---

## 場景 1 — Chunk 存入時自動生成 Centroid

### 背景

每次透過 `memory_save` 存入 chunk 時，`summary_text` 必須在伺服器端自動計算。計算方式是對該 session 所有 chunk 向量取平均（centroid），再找最近的 3 個自然語言 chunk 拼接成摘要。第一次存入後，`session_summaries` 裡的 `summary_text` 欄位必須永遠有值。

---

### 1a — 單一 chunk 產生含 summary_text 的 session 記錄

**前提條件：**
- 乾淨資料庫，無任何 session。
- `memory_save` 以 `text="The project uses LanceDB for vector storage."` 呼叫，不帶 `summary` 參數。

**步驟：**
1. 呼叫 `memory_save(text="The project uses LanceDB for vector storage.", session_id=SID)`。
2. 呼叫 `memory_sessions()`。
3. 找到 `session_id == SID` 的條目。

**預期結果：**
- 回傳值中 `chunks_written == 1`。
- `hint_saved == False`（沒有傳入明確的 summary）。
- `memory_sessions()` 回傳 SID 的條目。
- 該條目的 `summary_text` **不是** `None` 也**不是**空字串。
- session 記錄的 `vector` 欄位非零，維度為 384。

**失敗代表什麼：**
- `summary_text is None` → chunk 存入時沒有觸發 centroid 自動生成。
- session 表中沒有 `summary_text` → `upsert_session_summary` 從未在 chunk 存入路徑中被呼叫。

---

### 1b — 三個 chunk，每次存入後 centroid 重新計算

**前提條件：**
- 乾淨資料庫。三次呼叫都使用相同的 `session_id = SID`。

**步驟：**
1. `memory_save(text=TEXT_A, session_id=SID)` → 記錄 session row 的 vector_1。
2. `memory_save(text=TEXT_B, session_id=SID)` → 記錄 vector_2。
3. `memory_save(text=TEXT_C, session_id=SID)` → 記錄 vector_3。

**預期結果：**
- 步驟 1 後：session vector 等於 embed(TEXT_A)（單一 chunk 的 centroid 就是它本身）。
- 步驟 2 後：session vector 等於 mean(embed(TEXT_A), embed(TEXT_B))。
- 步驟 3 後：session vector 等於 mean(embed(TEXT_A), embed(TEXT_B), embed(TEXT_C))。
- 三個向量各不相同（centroid 確實在移動）。

**失敗代表什麼：**
- 三個向量完全相同 → centroid 只算了一次，後續存入沒有更新。
- 步驟 2 和 3 的向量相同 → 後續存入沒有重新計算 centroid。

---

### 1c — 十個 chunk，centroid 收斂且不出錯

**前提條件：**
- 乾淨資料庫。相同的 `session_id = SID`。

**步驟：**
1. 迴圈：對 i in 0..9 呼叫 `memory_save(text=f"Turn {i} content.", session_id=SID)`。
2. 取得最終 session row，檢查 `summary_text` 和 vector。

**預期結果：**
- 10 次呼叫都回傳 `chunks_written >= 1, status == "saved"`。
- Session row 存在，`summary_text` 非 null。
- 10 次存入過程中沒有拋出任何例外。
- vector 維度仍為 384（平均運算沒有 shape error）。

**失敗代表什麼：**
- 任何一次存入拋出例外 → centroid 重算在 N 增加時有 bug。
- 10 次後 `summary_text` 仍為 null → 自動生成靜默失敗。

---

## 場景 2 — Hint 欄位行為

### 背景

`hint` 是 agent 撰寫的可選合成文字，存在 `session_summaries` 表中。只有在 agent 呼叫 `memory_save(summary=<文字>)` 時才會寫入。它完全獨立於 `summary_text`（自動生成的 centroid 標籤）。

---

### 2a — 帶 summary= 的 memory_save 寫入 hint，summary_text 也跟著更新

**前提條件：**
- Session SID 已有一個 chunk（`summary_text` 已自動填入）。
- 記錄目前的 `summary_text` 值為 `ST_BEFORE`。

**步驟：**
1. `memory_save(text="Additional note.", session_id=SID, summary="Agent synthesis: we decided to use LanceDB.")`。
2. 取得 SID 的 session row。

**預期結果：**
- 回傳值中 `hint_saved == True`。
- `session_row["hint"] == "Agent synthesis: we decided to use LanceDB."`。
- `session_row["summary_text"]` **與 ST_BEFORE 不同**——因為 `"Additional note."` 是新 chunk，centroid 移動，`summary_text` 跟著更新。

**失敗代表什麼：**
- `summary_text` 存的是 hint 文字原文（不是 centroid）→ 舊行為（重構前）仍在。
- `hint` 欄位不存在 → schema migration 缺失。
- `summary_text` 與 ST_BEFORE 完全相同 → 有提供 hint 時，centroid 自動更新沒有被觸發。

---

### 2b — Hint 在後續 chunk 存入後保持不變

**前提條件：**
- Session SID 已寫入 hint `"My synthesis."`。

**步驟：**
1. `memory_save(text="New chunk of conversation.", session_id=SID)` — 不帶 `summary`。
2. 取得 session row。

**預期結果：**
- `session_row["hint"] == "My synthesis."` — hint 保留不變。
- `session_row["summary_text"]` 已改變（新 chunk 更新了 centroid）。

**失敗代表什麼：**
- `hint` 被清除或覆蓋為 `None` → chunk 存入路徑錯誤地重設了 hint。
- `summary_text` 未改變 → centroid 自動更新沒有被觸發。

---

### 2c — 只有 chunk 存入時，hint 為 null

**前提條件：**
- 乾淨資料庫。從未傳入明確的 summary。

**步驟：**
1. `memory_save(text="Some content.", session_id=SID)`。
2. 取得 session row。

**預期結果：**
- `session_row["hint"]` 是 `None`（或 schema 中此欄位不存在）。
- `session_row["summary_text"]` 不是 `None`。

**失敗代表什麼：**
- `hint` 有非 null 值 → hint 從不該有的地方被填入。

---

## 場景 3 — deep_search Layer 1 的 Hint 優先邏輯

### 背景

`memory_deep_search` 的 Layer 1 用 embedding 相似度選取 session。當 session 有 `hint` 時，Layer 1 必須用 `hint` 的向量來比對——這是透過在存入 hint 時把 hint embedding 存為 session vector 來實現的。沒有 `hint` 時，fallback 到 `summary_text` 的向量。兩種 session 必須能在同一次搜尋中共存而不出錯。

---

### 3a — 有 hint 的 session：Layer 1 使用 hint embedding

**前提條件：**
- Session A：`hint = "We chose PostgreSQL for its ACID guarantees."`，`summary_text` 是無關主題自動生成的（例如資料庫 schema 討論的 centroid）。
- Session B：無 hint；`summary_text` 描述 PostgreSQL 相關決策。
- 查詢：`"database transaction guarantees"`（與 A 的 hint 高度相關）。

**步驟：**
1. 確保 fake_provider 給查詢和 A 的 hint 高 cosine score（> MIN_SESSION_SCORE）；給查詢和 B 的 summary_text 低 cosine score。
2. 呼叫 `memory_deep_search(query="database transaction guarantees")`。
3. 檢查 `retrieved_from_sessions` 包含哪些 session。

**預期結果：**
- Session A 出現在 `retrieved_from_sessions`（透過 hint 命中）。
- Session B 不出現（summary_text 分數低於門檻）。
- `results` 中至少有一個來自 Session A 的 chunk。

**失敗代表什麼：**
- Session A 沒找到 → Layer 1 用的是 summary_text 而非 hint，hint 優先沒有生效。
- Session B 被找到 → Layer 1 使用了錯誤的欄位或無差別搜尋所有文字。

---

### 3b — 無 hint 的 session：Layer 1 使用 summary_text embedding

**前提條件：**
- Session A：`summary_text` 與查詢高度相關，無 hint。
- 查詢直接對應 `summary_text` 的內容。

**步驟：**
1. 呼叫 `memory_deep_search(query=<相關查詢>)`。
2. 檢查 `retrieved_from_sessions`。

**預期結果：**
- Session A 出現在結果中（透過 summary_text fallback 命中）。
- 沒有因為 hint 為 null 拋出任何例外。

**失敗代表什麼：**
- Session A 沒找到 → hint 為 null 時 fallback 到 summary_text 的邏輯壞了。
- 拋出例外 → null hint 沒有被妥善處理。

---

### 3c — 混合池：查詢匹配 hint 時，有 hint 的 session 排名更高

**前提條件：**
- Session H：`hint = "We optimized the embedding pipeline for throughput."`，`summary_text` 與主題無關（例如資料庫 schema 討論的 centroid）。
- Session S：無 hint，`summary_text = "We discussed embedding pipeline performance."`。
- 查詢：`"embedding throughput optimization"`。
- Fake provider：cosine(查詢, H 的 hint) > cosine(查詢, S 的 summary_text) > MIN_SESSION_SCORE。

**步驟：**
1. 呼叫 `memory_deep_search(query="embedding throughput optimization")`。
2. 收集 `retrieved_from_sessions` 中 session ID 的順序。

**預期結果：**
- 兩個 session 都出現在 `retrieved_from_sessions`。
- Session H 排在 Session S 前面（hint 相似度較高）。
- `results` 中包含來自兩個 session 的 chunk。

**失敗代表什麼：**
- Session H 排名較低 → Layer 1 對 H 用的是 summary_text（不是 hint），產生較差的相似度分數。
- Session S 缺失 → 意外觸發了 global fallback。

---

## 場景 4 — deep_search 回傳值的 next_required_action 欄位

### 背景

`memory_deep_search` 必須永遠在回傳值中帶有 `next_required_action` 欄位，指示 agent 呼叫 `memory_save`，以及一個獨立的 `retrieved_from_sessions` 欄位。

---

### 4a — 每次回應都有 next_required_action

**前提條件：**
- 任何狀態：零個 session、一個 session、多個 session 都可以。

**步驟（三個子情境）：**
1. 在空資料庫呼叫 `memory_deep_search(query="anything")`。
2. 在有一個 session 的 DB 呼叫。
3. 在有十個 session 的 DB 呼叫。

**預期結果（三種情況皆同）：**
- 回傳值是 `dict`。
- 有 `"next_required_action"` key。
- 值是非空字串。
- 值包含子字串 `"memory_save"`（確認指示 agent 呼叫正確的 tool）。
- 值包含 `"session_id"`（確認有提到 current session 的概念）。

**失敗代表什麼：**
- Key 不存在 → 欄位被移除或改名。
- 值沒有提到 `memory_save` → 指示文字被破壞性修改。

---

### 4b — next_required_action 正確描述取得的 chunk 數量

**前提條件：**
- 資料庫有 session，其 chunk 會匹配查詢。

**步驟：**
1. 呼叫 `memory_deep_search(query=<匹配查詢>)`，取得 `results`。
2. 解析 `next_required_action` 中的數字（格式：`"You retrieved N memory chunks"`）。

**預期結果：**
- `next_required_action` 中的 N 等於 `len(results)`。

**失敗代表什麼：**
- 數量不符 → action 文字是靜態的或硬編碼的，沒有從實際結果動態建立。

---

### 4c — retrieved_from_sessions 是獨立的頂層欄位

**前提條件：**
- 至少兩個 session 有 chunk 匹配查詢。

**步驟：**
1. 呼叫 `memory_deep_search(query=<匹配查詢>)`。
2. 檢查回傳值的 keys。

**預期結果：**
- 回傳值剛好有三個頂層 key：`"results"`、`"retrieved_from_sessions"`、`"next_required_action"`。
- `"retrieved_from_sessions"` 是字串列表（UUID）。
- `"retrieved_from_sessions"` **不是**嵌套在 `"results"` 或 `"next_required_action"` 裡面。

**失敗代表什麼：**
- `retrieved_from_sessions` 不存在 → 欄位被刪除或改名。
- `retrieved_from_sessions` 是嵌套的 → 結構被破壞性修改，下游消費者會壞掉。

---

## 場景 5 — Session ID 隔離：當前 Session vs 取得的 Session

### 背景

`memory_deep_search` 之後，agent 用自己的**當前** session ID（不是 `retrieved_from_sessions` 裡的 ID）呼叫 `memory_save`。當前 session 的 centroid 必須更新，來源 session 必須保持不變。

---

### 5a — Centroid 只更新當前 session

**前提條件：**
- Session SOURCE 有 chunk 且已知 `summary_text` 值（快照為 `ST_SOURCE`）。
- Session CURRENT 有一個 chunk。
- `memory_deep_search` 回傳 `retrieved_from_sessions = [SOURCE]`。

**步驟：**
1. Agent 呼叫 `memory_save(text="My synthesis after deep search.", session_id=CURRENT)`。
2. 取得 SOURCE 的 session row，與 `ST_SOURCE` 比對。
3. 取得 CURRENT 的 session row，與存入前的 `summary_text` 比對。

**預期結果：**
- SOURCE 的 `summary_text` 不變（等於 `ST_SOURCE`）。
- CURRENT 的 `summary_text` 已改變（新的合成 chunk 觸發了 centroid 重算）。

**失敗代表什麼：**
- SOURCE `summary_text` 改變 → `memory_save` 寫到了錯誤的 session（用了取得的 session ID）。
- CURRENT 不變 → 存入後 centroid 沒有重算。

---

### 5b — 來源 session 的 hint 不被 agent 的後續存入修改

**前提條件：**
- Session SOURCE 有 `hint = "Original agent synthesis."`。
- Agent 呼叫 `memory_save(text="New chunk.", session_id=CURRENT, summary="New synthesis.")`。

**步驟：**
1. 取得 SOURCE 的 session row。

**預期結果：**
- SOURCE `hint` 仍是 `"Original agent synthesis."`。
- CURRENT `hint` 是 `"New synthesis."`。

**失敗代表什麼：**
- SOURCE hint 被覆蓋 → `memory_save` 的 session 查找用到了取得的 session ID。

---

## 場景 6 — 向下相容性

### 背景

在 hint/centroid 重構前建立的 session，可能有 null 或缺失的 `hint`，以及 null 的 `summary_text`。伺服器不能 crash 也不能靜默丟棄這些 session。

---

### 6a — 有 null summary_text 的 session 能被 memory_sessions 正確回傳

**前提條件：**
- 手動在 `session_summaries` 插入一筆 `summary_text = None`、無 `hint` 欄位的舊格式記錄（模擬 legacy row）。直接用 PyArrow table 透過 `storage._sessions.add(...)` 插入，不帶 hint 欄位。

**步驟：**
1. 呼叫 `memory_sessions()`。

**預期結果：**
- 舊格式 session 出現在回傳列表中。
- 回傳 dict 中的 `summary_text` 欄位是 `None`（不是例外，不是 crash）。
- 其他正常 session 也正確出現。

**失敗代表什麼：**
- `list_sessions()` 期間拋出例外 → `_is_null` 檢查或欄位存取沒有妥善處理 null/缺失。
- 舊格式 session 消失 → 被意外過濾掉。

---

### 6b — memory_deep_search 能妥善處理 null summary_text 和 null hint

**前提條件：**
- 一個舊格式 session：`summary_text = None`，`hint = None`。
- 一個正常 session：有 `summary_text`，無 hint。

**步驟：**
1. 呼叫 `memory_deep_search(query="anything")`。

**預期結果：**
- 沒有拋出任何例外。
- 回傳值有標準的三個頂層 key。
- 舊格式 session 是否出現在 `retrieved_from_sessions` 都可接受（它沒有有意義的摘要可以匹配，預期分數低）。

**失敗代表什麼：**
- Layer 1 期間拋出例外 → null hint/summary_text 在傳入 embedding function 前沒有做 null check。
- `search_sessions` 期間拋出例外 → null vector 導致 LanceDB crash。

---

### 6c — 對舊格式 session 呼叫 memory_save 會回填 summary_text

**前提條件：**
- 舊格式 session SID：`summary_text = None`，`hint = None`。

**步驟：**
1. `memory_save(text="New chunk for old session.", session_id=SID)`。
2. 取得 session row。

**預期結果：**
- `summary_text` 現在不是 null（從這個唯一的新 chunk 計算出 centroid）。
- `hint` 仍是 null。

**失敗代表什麼：**
- `summary_text` 仍是 null → centroid 自動生成在 row 已存在但 summary_text 為 null 時沒有執行 upsert。
- 拋出例外 → 合併有 null 欄位的 row 時發生 schema 衝突。

---

## 場景 7 — 同一 Session 的多次 memory_save

### 背景

對同一個 session 多次呼叫 `memory_save` 必須：(1) 累積 chunk、(2) 每次對**所有** chunk 重新計算 centroid、(3) 在 chunk 存入時保留既有的 `hint`。

---

### 7a — 每次 chunk 存入都對該 session 所有 chunk 重新計算 centroid

**前提條件：**
- 乾淨的 session SID。
- 三個不同的文字：TEXT_1、TEXT_2、TEXT_3。

**步驟：**
1. 存入 TEXT_1 → 快照 session vector 為 V1。
2. 存入 TEXT_2 → 快照 session vector 為 V2。
3. 存入 TEXT_3 → 快照 session vector 為 V3。
4. 用 fake_provider 的已知向量計算每個步驟後的預期 centroid。

**預期結果：**
- V1 == embed(TEXT_1)（單一元素的 centroid 就是它本身）。
- V2 == mean(embed(TEXT_1), embed(TEXT_2))。
- V3 == mean(embed(TEXT_1), embed(TEXT_2), embed(TEXT_3))。
- V1、V2、V3 三者各不相同。

**失敗代表什麼：**
- V2 == V1 → 第二次存入沒有更新 centroid。
- V3 == V2 → 第三次存入的 centroid 計算沒有包含第一個 chunk（只算了最近的，不是全部）。

---

### 7b — Hint 在多次 chunk 存入後保持不變

**前提條件：**
- Session SID。第一次呼叫：`memory_save(text=TEXT_1, session_id=SID, summary="My hint.")`。

**步驟：**
1. `memory_save(text=TEXT_2, session_id=SID)` — 不帶 summary。
2. `memory_save(text=TEXT_3, session_id=SID)` — 不帶 summary。
3. 取得 session row。

**預期結果：**
- `hint == "My hint."` — 兩次純 chunk 存入後保持不變。
- `summary_text` 與步驟 1 後的值不同（centroid 已移動）。

**失敗代表什麼：**
- 步驟 2 或 3 後 `hint` 變成 null → chunk 存入路徑對 hint 欄位寫入了 null。
- `summary_text` 不變 → centroid 沒有更新。

---

### 7c — 再次帶 summary= 呼叫 memory_save 會覆蓋 hint

**前提條件：**
- Session SID 有 `hint = "Old synthesis."`。

**步驟：**
1. `memory_save(text=TEXT_NEW, session_id=SID, summary="New synthesis.")`。
2. 取得 session row。

**預期結果：**
- `hint == "New synthesis."`。
- `summary_text` 反映更新後的 centroid（包含 TEXT_NEW）。

**失敗代表什麼：**
- `hint` 仍是 `"Old synthesis."` → upsert 邏輯在匹配時沒有覆蓋。
- hint 和 summary_text 都存了新的 summary 文字 → hint 被寫入 summary_text 欄位。

---

## 場景 8 — Hook 觸發 vs Agent 觸發

### 背景

Chunk 有兩條來源路徑：(1) `/observe` HTTP endpoint（hook 自動觸發，完全自動），(2) `memory_save` tool（agent 主動觸發）。只有 `memory_save` tool 路徑會觸發 centroid 重算（centroid 邏輯在 `tools.py::memory_save`，不在 `storage.add_chunks`）。只有 agent 路徑（帶 `summary=`）才應該更新 `hint`。

> **實作備注：** observer 路徑直接呼叫 `storage.add_chunks`，繞過 `memory_save`，因此也繞過 centroid 重算。Centroid 會在下一次 agent 呼叫 `memory_save` 時補上。如果這個行為不可接受，修法是把 centroid 重算移進 `storage.add_chunks`（或在 `_process_item` 裡加 post-hook 呼叫）。

---

### 8a — Hook 存入的 chunk 存在於 turn_chunks；下次 agent 存入時 centroid 更新

**前提條件：**
- Session SID。先用 `memory_save`（agent 路徑）存入兩個 chunk：TEXT_A、TEXT_B。
- 快照 session 的 `summary_text` 為 `ST_AFTER_AB`（TEXT_A + TEXT_B 的 centroid）。
- 再用 `observer._process_item({"session_id": SID, "text": TEXT_C, "role": "user", "hook": "prompt-submit"})` 存入一個 chunk（模擬 hook 路徑，直接呼叫以隔離測試）。

**步驟：**
1. 用 `memory_save` 存入 TEXT_A 和 TEXT_B。
2. 快照 session `summary_text` 為 `ST_AFTER_AB`。
3. 透過 observer `_process_item` coroutine 直接處理 TEXT_C（不走 HTTP）。
4. 取得 SID 的 session row。
5. 呼叫 `memory_save(text="trigger recalc", session_id=SID)`（agent 存入）。
6. 再次取得 session row。

**預期結果：**
- 步驟 4 後：`summary_text == ST_AFTER_AB`（centroid 尚未更新——hook 路徑繞過了 centroid 邏輯）。
- 步驟 4 後：`turn_chunks` 中包含 TEXT_C，`source == "hook"`。
- 步驟 6 後：`summary_text` 與 `ST_AFTER_AB` 不同（centroid 現在包含 TEXT_A、TEXT_B、TEXT_C 和觸發用的 chunk）。

**失敗代表什麼：**
- 步驟 4 後 `summary_text` 就改變了 → centroid 在 hook 路徑中被重算（如果是刻意加的可以接受，但需要確認）。
- 步驟 4 後 TEXT_C 不在 `turn_chunks` → `_process_item` 沒有呼叫 `storage.add_chunks`。
- 步驟 6 後 `summary_text` 不變 → 下一次 agent `memory_save` 沒有重算 centroid。

---

### 8b — Hook 存入的 chunk 不更新 hint

**前提條件：**
- Session SID 有 `hint = "Agent-written synthesis."`。

**步驟：**
1. 透過 `_process_item` 為 SID 處理一個 hook 事件（payload 不帶 `summary` 欄位）。
2. 取得 session row。

**預期結果：**
- `hint == "Agent-written synthesis."` — 不變。
- hook 觸發後 `summary_text` 立即不變（centroid 尚未重算；下次 agent 呼叫 `memory_save` 才會更新）。

**失敗代表什麼：**
- `hint` 被清除或改變 → observer 路徑錯誤地寫入了 hint 欄位。

---

## 測試撰寫注意事項

**在測試中存取原始 session row 資料**

需要檢查 `hint`、`vector` 等原始欄位的測試，應呼叫 `storage._sessions.to_pandas()` 並依 `session_id` 過濾。不要只依賴 `memory_sessions()` 的回傳值，因為那個函式只回傳部分欄位。

**Fake provider 相似度設計**

需要相似度排序的測試（場景 3），請設計 fake provider 使包含相同關鍵字的兩個文字產生高 dot-product。簡單做法：建立一個固定的 384 詞彙表，每個詞對應一個基底向量；一個文字的 embedding 是其包含詞的基底向量之和，再正規化。這樣可以在不載入真實模型的情況下，精確控制相似度。

**向量相等的斷言**

獨立計算的 centroid 向量比對，請使用 `numpy.allclose(v1, v2, atol=1e-5)`，因為浮點數平均可能有微小的進位差異。

**Singleton 狀態的測試隔離**

測試之間，請執行：
```python
storage._initialized = False
storage._instance = None
Storage._instance = None
```
然後用新的 `tmp_path` 資料庫重新初始化。否則，在已初始化的測試後執行的測試會重用指向已刪除目錄的 LanceDB 連線。

**模擬舊格式（重構前）的 row**

直接用 PyArrow 插入不帶 `hint` 欄位的舊格式 row，或以 `hint = None` 插入。這要求 schema 把 `hint` 宣告為 nullable（`pa.field("hint", pa.string())` 不帶 `not null` 約束）。撰寫向下相容性測試前請先確認這一點。
