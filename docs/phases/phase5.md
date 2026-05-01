# Phase 5 — Project-Scoped Memory

## 目標

讓 Patrick 的記憶可以依照「工作目錄（project）」過濾，避免跨專案的記憶污染搜尋結果。使用者在 `/Users/andy/project-a` 開的 session，不應該被 `/Users/andy/project-b` 的搜尋撈到。

---

## 前提確認

- Claude Code 的所有 hook（SessionStart、UserPromptSubmit、PostToolUse、Stop）都會在 stdin payload 帶 `cwd` 欄位（common field），官方文件已確認。
- SessionStart hook 是注入 `project_path` 的最佳時機：session 一開始就知道工作目錄，不需要等到 Stop。
- 現有 `_SESSION_SCHEMA` 沒有 `project_path` 欄位；需要 migration。
- **（Task 2 實測確認）** Claude Code 傳來的 `cwd` 已是絕對路徑（無 `~`、無尾斜線），例：`/Users/andy/llm-mem/patrick`。`os.path.realpath(os.path.expanduser(raw))` 策略正確。

---

## Task List

---

### Task 1 — Schema Migration：`session_summaries` 加 `project_path` ✅ DONE

**目標**：讓 `session_summaries` 表有 `project_path` 欄位，可以按工作目錄過濾 session。

**改動檔案**：`src/patrick/storage.py`

**具體做法**：
1. 在 `_SESSION_SCHEMA` 裡新增 `pa.field("project_path", pa.string())`（放在 `summary_status` 之後）
2. 在 `storage.initialize()` 的 Phase 4 migration 區塊之後，新增 Phase 5 migration：
   ```python
   # Phase 5 migration: add project_path
   if "project_path" not in col_names:
       for expr in ("''", "CAST('' AS VARCHAR)", "CAST('' AS TEXT)"):
           try:
               self._sessions.add_columns({"project_path": expr})
               logger.info("Migrated session_summaries: added project_path column")
               break
           except Exception as e:
               logger.warning("add_columns project_path with expr=%s failed: %s", expr, e)
   ```

**Acceptance Criteria（Task 1）**：
- [x] `session_summaries` 表的 schema 包含 `project_path` 欄位
- [x] 新建的資料庫 `project_path` 就存在
- [x] 舊有資料庫升級後，歷史 session 的 `project_path` 為空字串（不報錯）
- [x] `storage.initialize()` 執行不拋出例外

---

### Task 2 — Spike：驗證 `cwd` payload 格式與 normalize 策略 ✅ DONE

**目標**：在實作 hook 之前，確認 Claude Code 傳給 SessionStart hook 的 `cwd` 格式，決定 normalize 策略。

**改動檔案**：`src/patrick/hooks/session_start.py`（臨時 debug，驗完刪除）

**具體做法**：
1. 在 `session_start.py` 暫時加入 debug log，將整個 stdin payload 寫到 `/tmp/patrick_hook_debug.json`：
   ```python
   import json, pathlib
   pathlib.Path("/tmp/patrick_hook_debug.json").write_text(
       json.dumps(data, ensure_ascii=False, indent=2)
   )
   ```
2. 開一個真實 Claude Code session，讓 SessionStart hook 觸發
3. 讀 `/tmp/patrick_hook_debug.json`，確認 `cwd` 的值格式
4. 分別在以下情況各觸發一次：~/project（含 home 縮寫）、/absolute/path（絕對路徑）、/path/with/symlink（若有 symlink）
5. 確認之後，刪除 debug log 程式碼

**Task 2 結論**（實測 `/tmp/patrick_hook_debug.json` 確認）：
- `cwd` 值為絕對路徑（例：`/Users/andy/llm-mem/patrick`）
- **無 `~` 縮寫**：不需要 `expanduser`，但保留無害
- **無尾斜線**
- **已 realpath**：值與 `os.path.realpath()` 結果一致（無 symlink 差異）
- **`source` 欄位**：payload 含 `source`（值為 `"resume"` 或 `"start"`），不影響 cwd 讀取
- **結論**：`os.path.realpath(os.path.expanduser(raw))` 策略正確，`expanduser` 多餘但無害，`realpath` 在有 symlink 環境下仍有保護作用

**Acceptance Criteria（Task 2）**：
- [x] 取得 `cwd` 的實際 payload 值（確認是否含 `~`、是否為絕對路徑、是否含尾斜線）
- [x] 決定 normalize 函式：`os.path.realpath(os.path.expanduser(path))` 或僅 `os.path.expanduser(path)`
- [x] 在本 Task 的結論裡記錄確認結果（更新此文件的「前提確認」段落）
- [x] debug log 程式碼在驗證完後從 `session_start.py` 移除

---

### Task 3 — Hook：`session_start.py` 提取並傳送 `cwd` ✅ DONE

> 依賴 Task 2（需先確認 normalize 策略）

**目標**：SessionStart hook 在通知 server 時，把 `cwd` 一起送過去。

**改動檔案**：`src/patrick/hooks/session_start.py`

**具體做法**：
```python
session_id = data.get("session_id") or data.get("sessionId")
project_path = data.get("cwd", "")   # ← 新增這行

payload = json.dumps({
    "hook": "session-start",
    "session_id": session_id,
    "project_path": project_path,     # ← 新增這行（normalize 在 server 端做）
}).encode()
```

（normalize 在 server 端的 observer.py 統一處理，hook 只負責原樣轉送）

**Acceptance Criteria（Task 3）**：
- [x] `session_start.py` 讀取 `data.get("cwd", "")` 且傳給 `/observe`
- [x] 若 payload 沒有 `cwd`（舊版 Claude Code），graceful fallback 為空字串，不崩潰
- [x] `/observe` endpoint 收到的 JSON body 含 `project_path` 欄位

---

### Task 4 — Spike：確認 `upsert_session_summary()` 的 merge_insert 行為 ✅ DONE

**目標**：在實作 observer 之前，確認 LanceDB `merge_insert` 的 `when_matched_update_all` 是否會覆蓋所有欄位（含後來寫入的 `project_path`），決定 upsert 策略。

**改動檔案**：無（只讀 code 和文件）

**具體做法**：
1. 讀 `storage.py` 裡 `upsert_session_summary()` 的 `merge_insert` 呼叫，確認 `when_matched_update_all()` 的語意
2. 查 LanceDB 官方文件或 source code，確認 `when_matched_update_all` 是否為「覆蓋所有欄位」
3. 若不確定，寫一個小測試腳本驗證：
   ```python
   # 第一次寫入：project_path="A"，其他欄位空
   storage.upsert_session_summary(session_id="test-1", project_path="A")
   # 第二次 upsert：只傳 summary_text，不傳 project_path
   storage.upsert_session_summary(session_id="test-1", summary_text="hello")
   # 讀回來確認 project_path 是 "A" 還是 ""
   result = storage.get_session("test-1")
   assert result["project_path"] == "A", f"Overwritten! Got: {result['project_path']}"
   ```

**Task 4 結論**：`when_matched_update_all` 是全欄覆蓋（full row overwrite）。但 `upsert_session_summary()` 在呼叫 merge_insert 前已做 read-then-merge（storage.py lines 277-307），所以所有欄位（含 `project_path`）均能正確保留。Task 5 採用相同的 read-then-merge pattern，並另加 `upsert_session_project_path()` 方法做 partial update（若 session 已存在則直接用 `table.update()`，否則插入 placeholder row）。

**Acceptance Criteria（Task 4）**：
- [x] 確認 `when_matched_update_all` 的行為（全欄覆蓋 / 部分覆蓋）
- [x] 根據結果決定 Task 5 的實作方式（見 Task 5 不確定事項的兩條路）
- [x] 在本 Task 結論裡記錄確認結果

---

### Task 5 — Observer：處理 `project_path` 並寫入 session ✅ DONE

> 依賴 Task 1（schema 要有欄位）、Task 3（hook 要能傳值）、Task 4（確認 upsert 行為）

**目標**：`/observe` endpoint 收到 `hook=session-start` 時，把 `project_path` 寫進 `session_summaries`。

**改動檔案**：`src/patrick/observer.py`、`src/patrick/storage.py`

**具體做法**：

找到 `_process_item()` 裡處理 `hook == "session-start"` 的段落，改為：
```python
if item.get("hook") == "session-start":
    import os
    raw = item.get("project_path", "")
    # normalize 策略依 Task 2 結論決定
    project_path = os.path.realpath(os.path.expanduser(raw)) if raw else ""
    storage.upsert_session_summary(
        session_id=item["session_id"],
        project_path=project_path,
        # 其他欄位保持 None，不覆蓋後續 summary backfill 寫的內容
    )
    return
```

`upsert_session_summary()` 的策略依 Task 4 結論決定：
- **若 merge_insert 只更新有傳的欄位**：直接傳 `project_path`，其他欄位傳 None 即可
- **若 merge_insert 全欄覆蓋**：改為 read-then-merge：先 `get_session(session_id)` 取現有記錄，merge 後再 upsert，確保 Phase 4 summary backfill 不清空已寫入的 `project_path`

**Acceptance Criteria（Task 5）**：
- [x] Session 開始後，`session_summaries` 裡該 session 的 `project_path` 正確填入
- [x] Phase 4 summary backfill 執行後，`project_path` 不被清空
- [x] 驗證方式：開新 session → 等 2 秒 → 呼叫 `memory_sessions()` → 確認該 session 有正確的 `project_path`

---

### Task 6 — Spike：確認 LanceDB `where()` 的 parameterized query 支援 ✅ DONE

**目標**：在實作 storage filter 之前，確認 LanceDB 是否支援 parameterized filter（避免 SQL injection），分別針對**兩條不同路徑**：(A) `turn_chunks` 的向量搜尋 + filter；(B) `session_summaries` 的純 filter（無向量，`list_sessions()` 用的路徑）。

**改動檔案**：無（只測試和查文件）

**具體做法**：
1. 查 LanceDB 官方文件，搜尋 parameterized filter / bind parameter 相關 API
2. 若有，確認語法（例如 `query.where("project_path = ?", ["value"])` 或其他形式）
3. 若無 parameterized 支援，確認 `table.search().where()` 是否支援 SQL-level filter 疊加向量搜尋，或只能 post-filter
4. 寫一個小測試腳本：
   ```python
   # 測試 1（路徑 A）：where() 能否搭配向量搜尋（turn_chunks 用）
   results = table.search(query_vector).where("project_path = 'test'").limit(5).to_list()
   # 測試 2（路徑 A）：路徑含特殊字元時是否安全（以下測試 injection，不可直接用在 production）
   tricky = "/path/o'brien/project"
   results = table.search(query_vector).where(f"project_path = '{tricky}'").limit(5).to_list()
   # 測試 3（路徑 B）：純 filter 無向量搜尋（list_sessions() 用，scanner / filter API）
   results = table.to_lance().scanner(filter="project_path = 'test'").to_table().to_pydict()
   ```

**Task 6 結論**（從現有 codebase 直接確認，無需另寫腳本）：
- **Parameterized filter**：LanceDB 不支援 `?` bind parameter。
- **路徑 A（vector + filter）**：`search().where(expr, prefilter=True)` 已全程在用（storage.py lines 280, 324 等），confirmed 可行。
- **路徑 B（純 filter 無向量）**：`table.to_pandas(filter=expr)` 已在用（storage.py line 380），confirmed 可行。
- **Task 7 策略**：`list_sessions()` 用 pandas post-filter（`df[df["project_path"] == project_path]`）— 完全 injection-safe，不需字串插值。`search_sessions()` 用 post-filter on result list，同樣 safe。

**Acceptance Criteria（Task 6）**：
- [x] 確認 LanceDB 是否支援 parameterized filter（有/無）
- [x] 確認 `search().where()` 能否搭配向量搜尋（SQL-level 或只能 post-filter）——**路徑 A**
- [x] 確認 `scanner(filter=...)` 或同等的純 filter API 是否可用（無向量，回傳全部符合的 rows）——**路徑 B**，這是 `list_sessions()` 謂詞下推的必要條件
- [x] 根據結果決定 Task 7 的 filter 實作方式（parameterized / escape / post-filter）
- [x] 在本 Task 結論裡記錄確認結果

---

### Task 7 — Storage：查詢支援 `project_path` 過濾 ✅ DONE

> 依賴 Task 1（schema）、Task 6（確認 LanceDB filter API）

**目標**：`list_sessions()` 和 `search_sessions()` 支援可選的 `project_path` 參數，傳入時只回傳符合的 session。

**改動檔案**：`src/patrick/storage.py`

**具體做法**（依 Task 6 結論選擇一種）：

> ⚠️ **目標是真正的謂詞下推**：`list_sessions()` 目前用 `to_pandas()` 全表載入再 post-filter，`session_summaries` 層沒有 pushdown。Task 7 的目標是讓 `project_path` filter 在 LanceDB 層就執行，**不走 `to_pandas()`**，只 materialize 符合條件的 rows。Task 6 spike 結論決定能不能做到。

`list_sessions()` 加參數（回傳型別是 `dict`，含 `sessions` key）：
```python
def list_sessions(
    self,
    limit: int = 50,
    offset: int = 0,
    include_body: bool = False,
    session_type: str | None = None,
    after: str | None = None,
    project_path: str | None = None,   # ← 新增
) -> dict:  # {"sessions": [...], "total": int, "limit": int, "offset": int}
    ...
    if project_path:
        # 選項 A（若 Task 6 確認 LanceDB 支援純 filter API）：
        #   改用 LanceDB 原生 filter，不呼叫 to_pandas()，避免全表掃描
        #   依 Task 6 確認的參數化語法實作，⚠️ 不可用字串插值（f"...{project_path}..."）以避免 SQL injection
        #   例：self._sessions.to_lance().scanner(filter=...).to_table().to_pandas()（實際語法依 Task 6 結論）
        # 選項 B（若 Task 6 確認 LanceDB 不支援純 filter，fallback）：
        #   pandas post-filter，仍有全表掃描，但正確性不受影響
        df = df[df["project_path"] == project_path]
```

`search_sessions()` 加參數：
```python
def search_sessions(
    self,
    query_vector: list[float],
    top_k: int = TOP_K_SESSIONS,
    project_path: str | None = None,   # ← 新增
) -> list[dict]:
    ...
    # 在 cosine search 後 post-filter
    if project_path:
        results = [r for r in results if r.get("project_path") == project_path]
```

**Acceptance Criteria（Task 7）**：
- [x] `list_sessions(project_path="/path/a")` 只回傳 `project_path` 為 `/path/a` 的 session
- [x] `list_sessions()` 不傳 `project_path` 時行為與現在完全一致（backward compatible）
- [x] `search_sessions(project_path=...)` 同上
- [x] 空字串 `project_path=""` 不被當成有效 filter（等同 `None`，傳空字串時回傳全部）
- [x] 無 SQL injection 風險（parameterized 或 post-filter，不用字串插值）
- [x] 驗證方式：塞兩個 project 的 session → 分別 filter → 確認各自隔離（`/llm-mem/patrick` 回 7 筆 vs 全局 575 筆）

---

### Task 8 — MCP Tools：`memory_sessions` 和 `memory_search` 加 `project_path` 參數 ✅ DONE

> 依賴 Task 5（observer 寫入）、Task 7（storage 過濾）

**目標**：Claude 可以直接帶著 project 路徑呼叫工具，拿到 project-scoped 的記憶。

**改動檔案**：`src/patrick/tools.py`

**具體做法**：

`memory_sessions()` 加參數：
```python
async def memory_sessions(
    limit: int = 50,                   # 已有，保留（分頁）
    offset: int = 0,                   # 已有，保留（分頁）
    session_type: str | None = None,
    after: str | None = None,
    include_body: bool = False,
    project_path: str | None = None,   # ← 新增
) -> dict:
    sessions = storage.list_sessions(
        limit=limit,                   # 已有，保留
        offset=offset,                 # 已有，保留
        include_body=include_body,
        session_type=session_type,
        after=after,
        project_path=project_path,     # ← 傳下去
    )
```

`memory_search()` 的 project_path 過濾方式（兩層）：
1. 用 `list_sessions(project_path=project_path, limit=0)` 拿到該 project **所有** session_id（`limit=0` = 不限筆數，確保不受預設 50 筆截斷），從回傳的 `result["sessions"]` 取出 session_id list
2. 再對 `search_chunks()`、`search_chunks_hybrid()`、`search_chunks_with_recency()` **三條 code path 都傳入 `session_ids`**（三個函式都已支援此參數）。不能只改其中一條，否則 `mode="hybrid"` 或 `use_recency=True` 時過濾會靜默失效。
3. 若 project_path 傳了但 `list_sessions` 回傳空（新 project 或歷史資料空白），fallback 到全域搜尋並在結果裡標注 `"project_filter_applied": false`（注意：此欄位名稱全文統一使用 `project_filter_applied`）

> ⚠️ 不可用 `search_sessions(project_path=...)`——那是向量搜尋只回傳 top-K 語義相關的 session，會漏掉 project 裡和 query 不相關的 session 的 chunks。

**Acceptance Criteria（Task 8）**：
- [x] `memory_sessions(project_path="/path")` 只回傳該 project 的 session 列表
- [x] `memory_search(query="...", project_path="/path")` 只在該 project 的所有 session chunks 裡搜尋
- [ ] project 有超過 50 個 session 時，搜尋結果不遺漏（驗證 `limit=0` 生效）⚠️ 待驗證（目前 `/llm-mem/patrick` 只有 7 筆 session，無法驗此條）
- [x] 不傳 `project_path` 時行為與現在完全一致（backward compatible）
- [x] 回傳格式含 `"project_filter_applied"` 欄位（bool），告知 Claude 過濾是否生效
- [x] MCP tool docstring 說明 `project_path` 的用途和格式（絕對路徑）

**效能備注**：若 Task 7 選擇**選項 B**（pandas post-filter fallback），`list_sessions(limit=0)` 仍有全表掃描 session_summaries 的問題；選項 A（LanceDB 原生 filter）可避免此問題。目前 session 數量下兩種選項都沒有明顯效能問題；若未來 project session 數超過數百，可評估改為在 turn_chunks 表直接加 project_path join filter，省去中間 session_id 清單。

---

## 任務依賴關係

```
Task 1 (schema) ──────────────────────────────┐
    └── Task 5 (observer) ──────────────────┐  │
            ↑                               ↓  │
Task 2 (spike: cwd format)              Task 8 (MCP tools)
    └── Task 3 (hook) ──→ Task 5             ↑
                                             │
Task 4 (spike: upsert 行為) ──→ Task 5       │
                                             │
Task 6 (spike: LanceDB filter) ──→ Task 7 (storage filter) ──┘
                                      ↑
                               Task 1 (schema)
```

**建議執行順序**：Task 1、Task 2、Task 4、Task 6 互不依賴，可全部並行 → Task 3（依賴 Task 2）→ Task 5（依賴 Task 1、3、4）→ Task 7（依賴 Task 1、6）→ Task 8（依賴 Task 5、7）

---

## 整體 Acceptance Criteria

所有 Task 完成後，以下情境必須通過：

1. **隔離測試**：在兩個不同目錄各開一個 session，各說幾句話，然後：
   - `memory_sessions(project_path="/path/a")` → 只看到 project-a 的 session
   - `memory_search(query="...", project_path="/path/a")` → 只搜到 project-a 的內容
   - 不帶 `project_path` 的搜尋 → 行為與 Phase 4 完全相同

2. **歷史資料不爛**：升級後跑一次 server，所有舊 session 仍可正常搜尋，只是 `project_path` 為空。

3. **Graceful fallback**：`project_path` filter 找不到 session 時，不崩潰，有明確說明（fallback 或 empty result）。

4. **不引入 regression**：現有 `memory_search`、`memory_sessions`、`memory_deep_search` 的測試（若有）全部通過。

---

## 已知問題清單（Code Review 後整理）

> 以下問題由 code review 發現，尚未修復。

### 🔴 P1 — AC 未達標（功能缺陷）

**Issue 1：`memory_sessions` 缺少 `project_filter_applied` 欄位** ✅ FIXED
- Task 8 AC 要求：「回傳格式含 `project_filter_applied` 欄位（bool）」
- 實際狀況：`memory_sessions()` 直接 return `storage.list_sessions()` 的結果，沒有這個欄位
- `memory_search` 三條 code path 都有此欄位，`memory_sessions` 沒有，不一致
- **修復方式**：`tools.py` 的 `memory_sessions()` 改為先把 `list_sessions()` 結果存入 `result`，再附加 `result["project_filter_applied"] = bool(project_path)`，最後才 return。`bool(project_path)` 在傳入空字串或 None 時為 `False`，傳入非空路徑時為 `True`，語意正確。

**Issue 2：`memory_search` 回傳型別不一致** ✅ FIXED
- `mode="vector"` + 不傳 `project_path` → 回傳 `list[dict]`
- `mode="vector"` + 傳 `project_path` → 回傳 `dict`
- `mode="hybrid"` 或 `use_recency=True` → 無論有無 `project_path`，**永遠**回傳 `dict`
- signature 雖已標 `-> list[dict] | dict`，但呼叫端必須判斷型別，容易踩雷
- **修復方式**：vector-only path 統一改為回傳 `dict`，格式與 hybrid/recency 一致（含 `results`、`mode`、`latency_ms`、`project_filter_applied` 四個 key）。同時補上 `latency_ms`（此前 vector path 缺少此欄位）。signature 改為 `-> dict`，移除 union type。

### 🟡 P2 — 假設未驗證

**Issue 3：Task 2 Spike 未執行，normalize 策略是假設** ✅ FIXED
- Task 3 文件明示「依賴 Task 2」，但 Task 2（確認 `cwd` payload 格式）從未正式執行
- `observer.py` 的 `os.path.realpath(os.path.expanduser(raw))` 是假設，非驗證結論
- 若 Claude Code 傳來的 `cwd` 已是 realpath（無 `~`、無 symlink），現行邏輯無誤；若有差異則 project filter 會靜默失效
- **已修復**：實測確認 `cwd` 已是絕對路徑（`/Users/andy/llm-mem/patrick`），無 `~`、無尾斜線。策略正確，結論已補記於「前提確認」與 Task 2 結論。

### 🟡 P3 — 效能目標未達（已知，有意為之）

**Issue 4：`list_sessions` 仍是全表掃描**
- Task 7 原本設目標「謂詞下推，不走 `to_pandas()` 全表掃描」
- 實際依 Task 6 結論選擇 **Option B**（pandas post-filter）：仍先 `to_pandas()` 全表載入再過濾
- 屬有意為之的 fallback，但文件描述目標（「只 materialize 符合條件的 rows」）與實作不符
- 現況可接受（session 量小）；若未來 session 數超過數百，需評估改為 LanceDB 原生 filter

### 🔵 P4 — 驗收狀態

**Issue 5：所有 Task 的 Acceptance Criteria checkbox 全部未勾選**
- 每個標記 ✅ DONE 的 Task，其 AC 項目全部仍是 `[ ]`
- 代表只有 code 存在，尚未跑過端到端驗收
- 建議：依照整體 Acceptance Criteria 的四個情境逐一驗收後補勾

---

## 不在本 Phase 範圍

- `memory_deep_search` 的 project_path 過濾（架構更複雜，先做 `memory_search`）
- 跨 project 的聯合搜尋（`project_path` list 而非單一值）
- project alias / display name（只用 raw path）
- `CwdChanged` hook 追蹤 mid-session 目錄切換
