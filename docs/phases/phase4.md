# Phase 4 — Session Summary

## 目標

每個 session 結束後，自動產生一個可搜尋的 summary，讓 memory_sessions 從「看不懂的截斷文字」變成「一眼能判斷這個 session 在做什麼」。

---

## Summary 的內容

### Regular session
1. **開場**：第一條 `user_prompt`（上限 200 字元，超過截斷加 `…`）
2. **內容**：前 5 條 `assistant_text`（去重，cosine ≥ 0.8 的濾掉）

若 `assistant_text` 不足 5 條，取全部有的；若完全沒有，只用開場。

### Multi-agent session
1. **開場**：`Discussion topic`（從第一條 `user_prompt` 的 system prompt 裡抽；上限 200 字元，超過截斷加 `…`）
2. **內容**：Andy 的 `[broadcast]` 訊息去重後列出（從 `user_prompt` 的 Recent conversation 欄位抽）

判斷是否為 multi-agent session：`user_prompt` 開頭包含 `"You are claude-"` 或 `"participating in a multi-agent discussion room"`。

---

## 資料結構

寫入現有 `session_summaries` 表。需要一次 migration 新增欄位。

新增欄位：

```
opening        str   第一條 user_prompt 或 Discussion topic（上限 200 字元；單獨可用於列表顯示）
body           str   前幾句 assistant_text 或 broadcast 訊息，換行分隔（單獨可用於細節過濾）
session_type   str   "regular" | "multi_agent"
summary_status str   "pending" | "done" | "skipped"（批次處理用）
```

既有欄位（用途改變）：

```
summary_text   str   原本存 centroid 衍生文字，改存 opening + "\n" + body（display 用）
vector         vec   原本是 centroid（chunk 向量平均），改為 embed(opening + body) 的 summary embedding
```

`opening` 和 `body` 分開存的原因：
- `opening` 單獨用於 memory_sessions 列表快速顯示
- `body` 單獨用於未來做更細的過濾（只搜開場、或只搜內容）
- 合在一起只剩黑盒，失去彈性

### 為什麼覆寫 `vector` 而非新增欄位？

- `search_sessions()` 已經綁在 `vector` 欄位做 cosine search，覆寫 = 搜尋路徑零改動
- 兩個向量欄位（centroid + summary_vec）會造成「到底搜哪個」的混淆
- Centroid 被 summary embedding 取代後沒有保留價值——它語義模糊，本來就不準

---

## Hook vs 批次處理

### Hook（Stop 時即時處理）
- 優點：summary 即時可用，不需要另外跑腳本
- 缺點：Stop hook 已經有其他工作（存 assistant_text），再加 summary 會拉長 hook 執行時間；若 hook 失敗，summary 就缺漏

### 批次處理（背景定期跑）
- 優點：不影響 hook 的主流程；失敗可重試；可回補歷史 session
- 缺點：有延遲，session 結束後不會立刻有 summary

### 建議：兩段式

```
Stop hook  → 只做輕量的「標記 session 需要 summary」
背景任務   → 定期掃描沒有 summary 的 session，批次補上
```

具體做法：Stop hook 在 `session_summaries` 寫入一筆 `summary_status = "pending"`，背景任務每隔 N 分鐘（或 patrick server 啟動時）掃描需要處理的 session 並補齊 summary。

**背景任務的掃描邏輯——兩條路徑：**

| 來源 | 條件 | 說明 |
|---|---|---|
| Stop hook 標記 | `session_summaries` 裡 `summary_status = "pending"` | Regular session：Stop hook 正常觸發，寫了 pending 記錄 |
| 無記錄發現 | `turn_chunks` 有該 session_id 但 `session_summaries` 完全沒有對應記錄 | Multi-agent session：Stop hook 不觸發，所以不會有 pending 記錄；backfill 靠「有 chunks 但沒 summary 記錄」來發現 |

這意味著背景任務不能只查 `WHERE summary_status = 'pending'`，必須同時比對 `turn_chunks` 的 distinct session_id 找出完全沒有記錄的 session。

好處：
- hook 不會因為 summary 計算失敗而整個壞掉
- 歷史 session 也可以一次補齊
- multi-agent session 即使 Stop hook 未觸發，backfill 也能自動發現並處理
- 未來改 summary 邏輯，只需要重跑批次，不需要改 hook

---

## 歷史 Session 回補（Backfill）

批次任務天然支援歷史回補，掃描邏輯同上述「兩條路徑」——pending 記錄 + 無記錄的 session，不限時間範圍。

回補流程：
1. 從 `turn_chunks` 撈出所有 distinct `session_id`
2. 比對 `session_summaries`，找出兩類需要處理的 session：
   - **有 pending 記錄**：regular session，Stop hook 已標記但 summary 尚未產生
   - **完全沒有記錄**：multi-agent session（Stop hook 未觸發，不會有 pending），或任何因故遺漏的 session
3. 對每個 session 執行正常的 summary 產生流程（判斷 session_type → 取 opening → 取 body → embed → 寫入）
4. Multi-agent session 雖然沒有 Stop hook 寫的 pending 標記，但只要 `turn_chunks` 裡有資料就會被步驟 2 發現並處理

首次部署時建議跑一次完整 backfill，把所有歷史 session 一次補齊。

---

## Edge Cases

| 狀況 | 處理方式 |
|---|---|
| `assistant_text` 完全沒有 | summary body 只用 `user_prompt` 前幾條 |
| session 只有 1 條 `user_prompt` | opening = 那條，body 為空 |
| multi-agent session 沒有 broadcast | opening = Discussion topic，body 為空 |
| session 的 `user_prompt` 也是空的 | 跳過，不產生 summary |
| 去重後剩 0 句 | 退回去重前的第一句 |
| `opening` 超過 200 字元 | 截斷至 200 字元，尾端加 `…` |
| `user_prompt` 是超長 system prompt（multi-agent 常見） | 抽 Discussion topic，不取整段 system prompt |

---

## 搜尋整合

**零改動**。既有的兩層搜尋架構不需要修改：

```
query
  │
  ├─ Layer 1：search_sessions() 搜尋 session_summaries.vector
  │           找出最相關的 top N sessions
  │           （vector 內容從 centroid → summary embedding，搜尋程式碼不動）
  │
  └─ Layer 2：在那 N 個 session 的 turn_chunks 裡搜尋
              找出最相關的 chunks（現有的 memory_search 邏輯）
```

改的只有「vector 裡裝的東西」和「怎麼產生它」，`search_sessions()`、`memory_search` MCP tool、Layer 1 → Layer 2 流程全部不動。

---

## 實作進度

### 已完成

- **`summary.py`** — `generate_summary()` 實作完成。Regular session 取 opening + top-5 assistant body；multi-agent session 取 discussion topic + broadcast。Body 以 cosine ≥ 0.8 多樣性篩選去除重複內容。最終用 `provider.embed_async()` 產生 summary embedding，**覆寫** centroid 向量。
- **`server.py`** — `_summary_backfill()` 背景排程器實作完成。每 120 秒掃描一次（`SUMMARY_SCAN_INTERVAL`），cooldown 300 秒（`SUMMARY_COOLDOWN`）。兩條路徑：pending 標記 + stale session 發現。
- **`storage.py`** — `session_summaries` 表已新增 `opening`、`body`、`session_type`、`summary_status` 欄位。`upsert_session_summary()` 支援寫入結構化摘要。`get_sessions_needing_summary()` 實作兩條路徑掃描。`list_sessions()` 支援 `include_body`、`session_type`、`after` 過濾。
- **`observer.py`** — Stop hook 觸發時標記 `summary_status = "pending"`，同時保留即時 centroid 計算作為臨時搜尋錨點。

### 文件更新（2026-04-26）

- **README（EN/ZH）** — Session Summary 改寫為「兩階段流水線」：Stage 1 centroid（即時）→ Stage 2 結構化摘要（背景回填，覆寫 centroid）。去重機制補充兩層說明（SHA-256 + cosine 語義去重）。MCP 工具表更新（`memory_save` 標示已停用、`memory_search` 補上三種模式和 `hook_type`、`memory_sessions` 補上過濾參數）。安裝流程精簡，完整指南抽到 `docs/INSTALL.md` 和 `docs/zh/INSTALL_ZH.md`。
- **`tools.py`** — 清除所有 `memory_deep_search` 過時引用。
- **`docs/phases/phase1.md`** — 修正 port 3112→3141、hook 路徑。
- **`docs/phases/phase2.md`** — 修正 eval 腳本路徑（5 處）。
- **`docs/milestone.md`** — 修正 port、server 技術棧（aiohttp→FastMCP+uvicorn）。
- **檔案結構** — 刪除多餘的頂層 `hooks/`、空的 `data/` 資料夾。`claude_config_example.json` hook 路徑更新。

### 待辦

- 考慮移除 centroid 計算——`generate_summary()` 已可在 stop hook 的 async context 直接呼叫，可省去 `compute_and_upsert_centroid()` 及 6 個呼叫點（約 70 行）。需另開 task 評估。
