# Patrick — Phase 2 規劃

> 目標：把 Phase 1 的「能跑通」推進到「找得準」。
> 核心原則：先建尺、再調參。沒有量化就沒有 Phase 2。

---

## 一、Phase 2 範圍（三大主題，按 ROI 排序）

### 1. Eval harness（評估基線）— 必須最先做
沒有這個，後面所有改動都是瞎調。

- **凍結基準集**：挑 30 組具代表性的真實 query（跨語言、跨 session、跨主題），鎖死、後續不得修改。
- **Ground truth**：每組 query 由人工標注「哪些 turn/chunk 是相關的」，存成 JSON/YAML。
- **指標**：
  - `Recall@10`：前 10 筆結果命中 ground truth 的比例
  - `nDCG@10`：考慮排序位置的命中分數
  - `MRR`：第一個正確結果的倒數排名（衡量「第一筆準不準」）
- **跑法**：CLI 指令一條，輸出分數 + 與上次 baseline 的 diff。
- **KPI**：`Recall@10` 從現有 baseline（跑完才知道）提升 ≥ 20%。

### 2. Hybrid Search + Re-rank（檢索品質）— 主菜
純向量檢索有兩個已知痛點：
- **語意漂移**：embedding 相似不等於事實相關
- **關鍵字失準**：專有名詞、ID、錯誤訊息精確匹配時常常漏掉

做法：
- **BM25 + 向量** 並行召回，分數融合（RRF 或 weighted sum）
- **Cross-encoder rerank**：對融合後的 top-N（例如 50）做精排，取 top-10 回傳
- BM25 的 `k1` / `b` 參數用凍結基準集 grid search 定值

### 3. 去重 + Chunking 優化（資料淨化）
- **Session-level cosine 去重**：同 session 內、cosine 相似度 ≥ 0.95 的 turn 視為語意重複，保留最新一筆或合併
- **實體去重**：同一概念被多次提到時，避免召回結果被同義重複洗版
- **Chunking 重切**：目前 turn-level chunk 對長回覆不友善，改為滑動視窗（例如 400 tokens，overlap 80）

### 4. 暫不做（留給 Phase 3）
- **T3 跨 agent 記憶一致性**（原列 Phase 2，移至 Phase 3）
  - **為什麼延後**：一致性的驗收指標（decision-ID Jaccard ≥ 0.9）必須建立在 Phase 2 已產出的穩定 decision-ID 體系 + eval harness 資料之上。在沒有 baseline 數字、沒有穩定 ID schema 之前做，會變成拍腦袋定義「一致」。
  - **前置依賴**：T1（eval harness）、T5（去重後的穩定記憶單元）跑完，累積足夠多的跨 agent 查詢樣本才有資料可量測。
- **T4 Session 結束自動摘要 + 落庫**（原列 Phase 2，移至 Phase 3）
  - **為什麼延後**：摘要驗收要跑「關鍵決策保留率 ≥ 90%」的人工打分，需要凍結基準集 + ground truth 的方法論先成熟。Phase 2 的標注規範與評分流程正是這塊基礎建設,讓 Phase 3 直接沿用同一套尺而不是另造一把。
  - **前置依賴**：T1 的標注流程定型、T7 的 CI 報告框架可複用。
- 知識圖譜 / graph retrieval — 等 re-rank 打到天花板再評估
- 多模態、跨 repo 記憶合併
- LLM-based rerank（成本與延遲都比 cross-encoder 高一個數量級）

---

## 二、交付物清單

| # | 項目 | 驗收 |
|---|------|------|
| 1 | `tests/eval/queries.jsonl` | 30 組 query + ground truth，凍結 |
| 2 | `scripts/eval.py` | 一條指令輸出 Recall@10 / nDCG@10 / MRR |
| 3 | BM25 index + hybrid search API | `memory_search` 支援 `mode=hybrid` |
| 4 | Cross-encoder rerank 模組 | 可開關、可配置 top-N |
| 5 | 去重 pipeline | session 內 cosine ≥ 0.95 合併 |
| 6 | Benchmark 報告 | baseline vs. phase2 對比表 |
| 7 | Re-embedding 計畫 | 開發期清空重建；遷移計畫與 API 成本估算留至正式上線前補 |

---

## 三、白話文術語表（怕之後忘記）

### 評估相關

- **凍結基準集（Frozen benchmark set）**
  把測試用的 query 鎖死、不再改動。像固定同一份考卷反覆測不同版本的系統，數字才有可比性。調參過程中絕對不能因為某題跑壞就把它刪掉 — 那叫改考卷作弊。

- **Ground truth（標準答案）**
  人工事先標注「這個 query 的正確答案應該是哪幾份記憶」。後續才能用它去對搜尋結果打分。

- **Recall@10（前 10 命中率）**
  只看搜尋結果的前 10 筆。標準答案裡有 5 筆相關，前 10 命中 3 筆 → Recall@10 = 60%。只關心「有沒有找到」。

- **nDCG@10（排序加權命中率）**
  跟 Recall@10 類似都看前 10 筆，但**相關答案排越前面，分數越高**。衡量「找到 + 排得對」。

- **MRR（Mean Reciprocal Rank，平均倒數排名）**
  看第一個正確答案出現在第幾名。排第 1 就是 1.0，排第 5 就是 0.2。用來衡量「第一筆準不準」。

### 檢索相關

- **BM25**
  經典關鍵字搜尋演算法，類似升級版 TF-IDF。對精確字詞匹配很強（例如搜 error code、人名、ID）。

- **`k1` / `b`（BM25 參數）**
  `k1` 控制「同個字出現多次加多少分」，`b` 控制「長文件是否被懲罰」。需要針對自己的資料調。

- **Embedding（向量）**
  把一段文字壓成一串數字（例如 1536 維），語意相近的字串向量也相近。

- **Cosine similarity（餘弦相似度）**
  兩個向量的夾角有多小。值介於 -1 到 1，越接近 1 越像。我們常拿 0.9 / 0.95 當門檻做去重。

- **Hybrid search（混合搜尋）**
  同時用 BM25（關鍵字）+ 向量（語意）兩路召回，再把分數融合。補彼此的短板。

- **RRF（Reciprocal Rank Fusion）**
  最簡單的分數融合法：各路結果取 `1/(rank+k)` 相加。不需要正規化，兩邊分數不同量綱也能用。

- **Cross-encoder rerank（精排）**
  把 query 和每個候選結果**一起**丟進模型算相關性分數（不同於 bi-encoder 各自 embedding 再算 cosine）。準但慢，所以只對 top-N 做。

- **Top-N / Top-K**
  召回階段多撈一點（N = 50），精排階段取最準的（K = 10）回給使用者。

### 資料處理

- **Chunking（切塊）**
  把長文件切成小段再做 embedding。太大 → 向量稀釋；太小 → 失去上下文。

- **Deduplication（去重）**
  同一概念或同一段話被記多次時，合併或丟棄重複，避免搜尋結果被洗版。

- **Session-level 去重**
  只在同一個對話 session 內做去重，跨 session 的「重複」可能代表主題重要、不應刪。

---

## 四、里程碑

- **M1（Week 1）**：Eval harness 可跑，baseline 數字出爐
  - **主要風險**：ground truth 標注實際耗時會比預估多，跨語言 query 尤其吃時間（詳見風險區）
- **M2（Week 2）**：BM25 + hybrid 接入，跑出 phase2 中期分數
- **M3（Week 3）**：Cross-encoder rerank + 去重 + chunking 重切，跑出 phase2 終版分數
  - **前置作業**：chunking 改動觸發整庫 re-embedding，需在本週排入時段並估算成本（見交付物 #7）
- **M4（Week 4）**：Benchmark 報告、寫成可重現的 CI job

## 五、Phase 2 完成標準（Definition of Done）

1. `scripts/eval.py` 一條指令可重現所有分數
2. Recall@10 相對 Phase 1 baseline 提升 ≥ 20%
3. nDCG@10 相對 Phase 1 baseline 提升 ≥ 15%
4. P95 query latency ≤ 800ms（cross-encoder 打開時；基準硬體：M-series Mac local，cross-encoder 在 CPU 上跑。GPU / 雲端環境需另行標注所用規格並相應調整門檻）
5. 去重後 storage 體積縮減 ≥ 10%（視資料而定，非硬性）
6. 所有新指令與 API 都有寫進 README / ARCHITECTURE

---

## 六、風險與未決

- **Cross-encoder 模型選型**：multilingual-e5-reranker vs. bge-reranker-v2-m3，用 benchmark 決定
- **Ground truth 標注成本（M1 主要風險）**：30 組 × 平均 5 個相關文件 ≈ 150 筆標注，修正為 **1–1.5 人天**（原「半天」過度樂觀）；跨語言 query（中英混雜、程式碼/錯誤訊息）需額外驗證相關性，單筆耗時明顯高於同語言 query。若標注塞車會直接卡 M1 出不了 baseline
- **Re-embedding（Section 3 chunking 改動觸發）**：開發階段直接清庫重建，不做遷移。遷移腳本與 API 成本估算留待正式上線前再補。
- **P95 latency 硬體相依**：800ms 目標綁定基準硬體（M-series Mac local / CPU 推論）。若改跑 GPU 或雲端部署，需重新量測並在報告中標注環境
- **去重門檻**：0.95 vs. 0.9，先用 0.95 保守值，觀察誤殺率
- **Hybrid 融合權重**：RRF 起手，若效果不佳改 weighted sum 並用 grid search

---

## 七、Todo list
> 每項結構：為什麼做 → 怎麼做 → 怎麼驗收 → 為什麼這樣驗。

### T1｜Eval harness（量化基線）
- **為什麼做**：Phase 1 只驗證「能跑通」，沒有數字就無法判斷後續任何改動是進步還是退步。
- **怎麼做**：凍結 30 組 query + 人工 ground truth，`scripts/eval.py` 一鍵輸出 Recall@10 / nDCG@10 / MRR。
- **怎麼驗收**：同一條指令在 CI 跑兩次分數一致；能輸出與上次 baseline 的 diff。
- **為什麼這樣驗**：可重現 + 可比較是「尺」的最低條件，否則所有後續 KPI 都是幻覺。

### T2｜Hybrid Search + Cross-encoder Rerank
- **為什麼做**：純向量有語意漂移、關鍵字失準兩大痛點；rerank 是目前投入產出比最高的品質槓桿。
- **怎麼做**：BM25 + 向量雙路召回 → RRF 融合 top-50 → cross-encoder 精排取 top-10。
- **怎麼驗收**：`mode=hybrid` 下 Recall@10 ≥ baseline + 20%、nDCG@10 ≥ baseline + 15%。
- **為什麼這樣驗**：Recall 管「有沒有找到」，nDCG 管「排序對不對」，兩者同時過關才算真進步。

### T3｜跨 agent 記憶一致性【已移至 Phase 3】
- **原規劃**：以 memory_deep_search 為單一事實來源；為關鍵決策打穩定 ID；驗收 decision-ID Jaccard ≥ 0.9。
- **為什麼不在 Phase 2 做**：驗收門檻（Jaccard ≥ 0.9）需要資料才定得出來 —— 沒有 Phase 2 的 eval harness、穩定 ID schema、跨 agent 查詢樣本,所謂「一致」只是主觀感覺。現在硬做會做出一把沒有刻度的尺。
- **Phase 3 啟動條件**：T1 baseline 出爐 + T5 去重後記憶單元穩定 + 累積 ≥ 30 組跨 agent 查詢樣本。

### T4｜Session 結束自動摘要 + 落庫【已移至 Phase 3】
- **原規劃**：偵測 session 結束 → 結構化摘要 → memory_save；驗收關鍵決策保留率 ≥ 90%。
- **為什麼不在 Phase 2 做**：「保留率 ≥ 90%」要人工打分,但打分規範 = Phase 2 要建的 ground truth 標注方法論。在 Phase 2 同時蓋方法論又用它驗收,等於自己當出題人自己當閱卷者,數字沒公信力。
- **Phase 3 啟動條件**：T1 標注規範定型 + T7 CI 報告框架可複用 + 累積 ≥ 10 個可供離線重跑的歷史 session。

### T5｜去重 + Chunking 重切
- **為什麼做**：turn-level chunk 對長回覆切得太碎；同 session 重複 turn 會洗版搜尋結果。
- **怎麼做**：滑動視窗 400 tokens / overlap 80；session 內 cosine ≥ 0.95 合併保留最新。
- **怎麼驗收**：storage 縮減 ≥ 10% 且 Recall@10 不退步（硬約束：品質不能為了體積讓步）。
- **為什麼這樣驗**：去重很容易誤殺有效資訊，必須同時盯體積**和**品質兩個指標，單看任一個都會走偏。

### T6｜Latency & 成本預算
- **為什麼做**：cross-encoder 開下去延遲會跳、re-embedding 會燒 API 費用，沒預算線會失控。
- **怎麼做**：P95 latency ≤ 800ms（M-series Mac / CPU 基準硬體）；re-embedding 列明 chunk 數 × 單價 × rate limit。
- **怎麼驗收**：benchmark 報告同時附 latency 直方圖與 API 花費明細，超標需附 fallback 計畫。
- **為什麼這樣驗**：P95 比 P50 更能反映「最差使用者體驗」；金額明列可避免決策者拿「差不多」含混過關。

### T7｜Benchmark 報告 + 可重現 CI
- **為什麼做**：分數若只能在某人筆電跑出來，等於沒有；Phase 3 要能站在 Phase 2 的肩膀上。
- **怎麼做**：CI job 拉凍結 query set、跑 eval、產出 Markdown 報告與 JSON artifact。
- **怎麼驗收**：任意乾淨環境 clone → 一條指令 → 分數與報告上 commit 數值一致（±容忍誤差）。
- **為什麼這樣驗**：可重現性是科學化調參的前提，不可重現的 20% 提升沒有價值。

### T8｜`turn_chunks` 補 `hook_type` 欄位（資料品質）
- **為什麼做**：目前 `turn_chunks` 只有 `source: "hook"` 標記，所有 hook 觸發的寫入（tool call read file、bash 執行、assistant text）全混在一起，查詢時無法過濾掉 tool call 雜訊。這會讓召回結果夾帶大量低語意價值的 chunk，直接拖低 Recall@10 / nDCG@10。
- **怎麼做**：schema 新增 `hook_type: str` 欄位，寫入時依來源標記（例如 `"assistant_text"` / `"tool_call_read"` / `"tool_call_bash"` / `"tool_result"` 等），`memory_search` / BM25 / hybrid 查詢時可加 `hook_type filter`。
- **怎麼驗收**：(a) 新寫入的 chunk 都有非空 `hook_type`；(b) `memory_search(hook_type="assistant_text")` 能成功過濾，結果不含 tool call chunk；(c) eval harness 加 filter 後 Recall@10 不退步（基線：T1 凍結數字）。
- **為什麼這樣驗**：過濾功能正確性 + 召回品質不退步，兩者缺一都代表改動無效甚至有害。
- **狀態**：🔲 待實作（2026-04-23 識別）

---

## 八、Code Review 發現的問題與修正計畫

> 基於 commit `f84c4d7` 實作 review。
> 分級：🔴 Critical（直接阻擋 KPI 或 baseline）｜🟠 High（影響 KPI 歸因）｜🟡 Medium（影響量測準確度）
> 每條統一格式：位置 → 問題描述 → 白話文 → 解法 → 白話文 → 狀態。
> 原「反對意見（Review of the Review）」章節的 R1/R2/R3 已併入對應條目（C4/C6/C2）的解法段，作為最終採用的穩妥版本，不再獨立列出。

---

### 執行優先序

- [x] 1st｜C1（ground truth 標注）— M1 第一件事
- [x] 2nd｜Med2（eval README 補說明）— M1 標注開始前
- [x] 3rd｜H3（recency sort 移除/移後）— M1 baseline 量測前
- [x] 4th｜C2（Phase 1 baseline 凍結，用臨時 branch 法）— C1 + H3 完成後立即
- [x] 5th｜C6（CI quality gate，硬卡 == 30）— C1 完成後
- [x] 6th｜C3（BM25 cache）— M2 前
- [x] 7th｜C4（BM25 中文 tokenizer：jieba + char fallback）— M2 前
- [x] 8th｜C5（expand 移到 rerank 後）— M2 前
- [x] 9th｜H1（reindex CLI）— M3 前
- [x] 10th｜H4（死常數清除或實作 weighted RRF）— M3 grid search 前
- [x] 11th｜Med1（P95 off-by-one）— 順手
- [x] 12th｜H2（T6 補交付：latency 分布 + 成本腳本）— M4 前

---

### 🔴 Critical（直接阻擋 baseline 或 KPI，M1/M2 前必修）

#### C1｜Ground truth 全部為空，eval 跑不起來
- **位置**：`tests/eval/queries.jsonl`
- **問題描述**：30 組 `relevant_chunk_ids` 全為 `[]`，`eval.py:135` 偵測到沒有 annotated query 即 early-return，M1 baseline 根本跑不出來。
- **白話文**：考卷發下去了但標準答案欄全空，閱卷程式看到沒答案就直接放棄打分，整個 M1 baseline 卡住。
- **解法**：人力完成 150 筆標注（30 query × 平均 5 relevant chunk），預估 1–1.5 人天（見 §六風險區）。
- **解法白話文**：找人把 30 題的標準答案一題一題寫進去，寫完才有得比。
- **狀態**：✅ 完成（30 組 query 全部標注 relevant_chunk_ids，2026-04-21）

#### C2｜Phase 1 baseline 從未凍結進 repo（含 schema 相容性處理）
- **位置**：`results/`（目錄空）
- **問題描述**：`results/phase1_baseline.json` 不存在。若等到 M1 才生，storage 已經跑過 dedup + 新 chunking，資料分布已改變，所謂 +20% KPI 變成跟新環境自己比的偽命題。另外直接 `git checkout 8fe1dbf` 跑 Phase 2 的 `eval.py` 有 **schema 相容性風險**——eval.py 是 Phase 2 新增，Phase 1 commit 的 storage 欄位可能對不上，跑起來要嘛直接爆，要嘛跑出來的數字是錯的。
- **白話文**：「進步 20%」要跟「以前」比，但「以前」的分數從沒留底；而且現在的資料庫已經不是以前的樣子，再量等於自己騙自己。另外，直接把新考試程式搬回舊資料庫，程式和資料庫規格對不上，會出事。
- **解法**：採最穩妥做法——在 Phase 1 commit（`8fe1dbf`）上建一個臨時 branch，把 Phase 2 的 `scripts/eval.py` cherry-pick 過去並驗證可跑後，用該 branch 跑出 baseline，再把 `results/phase1_baseline.json` commit 回 main。baseline JSON 必須內嵌 **commit hash / eval.py 版本 / storage schema 版本** 三欄以供追溯。
- **解法白話文**：回到 Phase 1 那個時空，但把 Phase 2 的考試程式搬過去先試跑一次確定能跑，跑完把分數鎖進檔案並標明「這是用哪個版本在哪個資料庫結構下跑的」，以後才能追。
- **狀態**：✅ 完成（results/phase1_baseline.json 已凍結，Recall@10=0.2789, nDCG@10=0.2899，2026-04-21）

#### C3｜BM25 index 每次 query 都重建
- **位置**：`storage.py:337-373, 386`
- **問題描述**：每次 `search_chunks_bm25` 都做全表 `to_pandas()` + 全量 tokenize + 新建 `BM25Okapi`，corpus 成長後直接違反 P95 ≤ 800ms。
- **白話文**：每次搜尋都把整個書櫃重新整理一遍才開始找書，書一多就卡死。
- **解法**：`Storage` 加 `_bm25_cache: tuple | None = None`，只在 `add_chunks` / `cosine_dedup_session` 後 invalidate，其餘 query 直接吃 cache。
- **解法白話文**：書櫃整理一次就記住，之後直接查；只有新增/刪書時才重整。
- **狀態**：✅ 完成（C3 cache + invalidation 實作，2026-04-21）

#### C4｜BM25 中文 tokenizer 失效（詞級斷詞，非 char-level）
- **位置**：`storage.py:371`
- **問題描述**：`.lower().split()` 對沒有空格的中文整句只切出一個 token，30 組基準裡 4 組中文 + 5 組中英混雜的 BM25 貢獻趨近於零，hybrid 對中文退化成 pure vector，T2「補關鍵字失準」對中文全無效。
- **白話文**：中文沒有空格，現在的切詞靠空格切，整句中文被當成一個超長單字，關鍵字搜尋對中文幾乎沒動作。
- **解法**：引入 `jieba`（或 `lac` / `ckip-transformers`）做**詞級斷詞**，char-level `list(text)` 只留作 fallback（套件載入失敗時）。詞典可擴充專案常見專有名詞。**不可只做 char-level split**——成語、複合詞、錯誤訊息會被拆散，IDF 被單字稀釋，等於只回血一半。驗收時中文 query 的 BM25 top-10 命中率須明顯高於 char-level baseline。
- **解法白話文**：裝一個真的懂中文的切詞工具（jieba），讓它把句子切成有意義的詞；萬一工具壞了才退回到一個字一個字切當備援。
- **狀態**：✅ 完成（jieba 詞級斷詞 + char fallback 實作，2026-04-21）

#### C5｜Context expansion 在 rerank 之前，latency 爆表
- **位置**：`tools.py:215-228`
- **問題描述**：hybrid recall(50) → `_expand_context`（50×5=250 chunks）→ 全數餵給 cross-encoder，CPU 推論時間暴增 5 倍，直接違反 T6 P95 ≤ 800ms。
- **白話文**：先把候選從 50 個擴成 250 個，再拿去做最貴的那一步，等於叫最貴的工人做最多的活。
- **解法**：順序改為 hybrid recall(50) → cross-encoder rerank 取 top-K → 對 rerank 結果才做 sibling expansion。
- **解法白話文**：先讓貴工人在 50 個裡挑出前 10，再擴充上下文，工作量降為 1/5。
- **狀態**：✅ 完成（rerank 移至 expand 前，2026-04-21）

#### C6｜CI 永遠綠，T7 quality gate 形同虛設（門檻硬卡 == 30）
- **位置**：`.github/workflows/eval.yml:23, 46, 53`
- **問題描述**：job 層和兩個 eval step 層全設 `continue-on-error: true`，eval.py exit code 1 時 CI 照樣綠燈，品質退步無人把關。
- **白話文**：CI 裝了警報器但電線被剪掉，燈永遠亮綠，出事也不叫。
- **解法**：加 annotated queries 數量檢查 step，**硬卡 `== 30`**（與 C1「M1 前必做」一致；不可放水到 `>= 20`，否則自打臉：C1 要求 30 筆全標，CI 卻允許 1/3 未標通過，兩處矛盾）。C1 完成後同步移除所有 `continue-on-error: true`。
  ```yaml
  - name: Verify annotated queries
    run: |
      python -c "
      import json; lines = open('tests/eval/queries.jsonl').readlines()
      annotated = sum(1 for l in lines if json.loads(l).get('relevant_chunk_ids'))
      assert annotated == 30, f'Only {annotated}/30 annotated — CI blocked'
      "
  ```
- **解法白話文**：加一道關卡——30 題沒全部標完就不讓過；同時把那個假裝成功的開關關掉。
- **狀態**：✅ 完成（eval.yml 已移除 continue-on-error，硬卡 annotated == 30，2026-04-21）

---

### 🟠 High（影響 KPI 歸因，M3/M4 前必修）

#### H1｜Chunking 改動沒觸發整庫重建
- **位置**：`config.py:25`，`cli.py`（無 reindex 指令）
- **問題描述**：`CHUNK_OVERLAP 50→80` 只影響新寫入，舊 chunks 仍是 50 overlap。M3 終版分數量的是新舊混合 corpus，無法歸因到 chunking 改動。交付物 #7 寫「開發期清空重建」，但 CLI 沒對應指令。
- **白話文**：規格改了但舊資料沒重切，量出來的分數是新舊混著跑的，分不清到底是哪個改動造成的。
- **解法**：`cli.py` 新增 `patrick reindex --wipe`（drop + recreate LanceDB tables），M3 跑 eval 前強制執行，benchmark 報告標注執行的 commit hash。
- **解法白話文**：寫個一鍵「清庫重建」指令，M3 前按一下，整庫都用新規格切過。
- **狀態**：✅ 完成（patrick reindex --wipe 實作，含 drop tables + scan transcripts + re-chunk + re-embed，2026-04-21）

#### H2｜T6 未交付：無 latency histogram，無成本估算腳本
- **位置**：`scripts/eval.py`（T6 對應腳本缺失）
- **問題描述**：T6 要求 latency 直方圖 + re-embedding chunk 數 × 單價明細，`eval.py` 只輸出 `p95_latency_ms` 純量，沒有分布資料，也沒有成本估算腳本。
- **白話文**：只給一個「最慢的 5% 多慢」的數字，沒有分布圖；也沒算過整庫重建要花多少 API 費用。
- **解法**：(a) `eval.py --output` JSON 加 `latency_distribution` 分桶欄位；(b) 新增 `scripts/estimate_reembed_cost.py`（chunk count × embedding 單價 × rate limit）。
- **解法白話文**：補兩樣——延遲分布圖、重建費用試算表。
- **狀態**：✅ 完成（eval.py 加 latency_distribution 分桶 + 新增 scripts/estimate_reembed_cost.py 本地推論耗時估算，2026-04-21）

#### H3｜Recency sort 在 rerank 之前，vector baseline 的 nDCG 被污染
- **位置**：`tools.py:219`（Phase 1 舊行為，但影響 Phase 2 eval 量測）
- **問題描述**：vector-only 模式最終輸出按時間排序而非相似度，nDCG@10 被人為壓低。hybrid 因 rerank 可覆蓋 sort，對比數字虛胖，nDCG +15% KPI 歸因不乾淨。Recall@10 不受影響（只看集合命中）。附注：eval 的 `search_direct` 刻意不跑 sibling expansion，量的是 retrieval 層，這是設計而非 bug。
- **白話文**：baseline 被偷偷打了折，Phase 2 分數虛胖，兩者相減看起來進步很多，其實一半是假的。
- **解法**：recency sort 移到 `formatted[:top_k]` truncation 之後，或移到 rerank 之後，避免污染 eval 量測的排序分數。
- **解法白話文**：把「按時間排」這一步挪到最後，不要干擾打分。
- **狀態**：✅ 完成（recency sort 移至 top_k truncation/rerank 之後，2026-04-21）

#### H4｜`BM25_WEIGHT` / `VECTOR_WEIGHT` 是死常數
- **位置**：`config.py:41-42`
- **問題描述**：`search_chunks_hybrid` 的 RRF fusion 只用 `RRF_K`，完全沒讀這兩個 weight，誤導後續調參人員以為 fusion 有加權。
- **白話文**：config 裡放了兩個旋鈕看起來可以調，實際上沒接線，轉了沒用。
- **解法**：擇一——(a) 刪除這兩行；(b) 實作 weighted RRF：`BM25_WEIGHT / (rrf_k + bm25_rank + 1) + VECTOR_WEIGHT / (rrf_k + vec_rank + 1)`。若 M3 要跑 grid search 建議選 (b)。
- **解法白話文**：要嘛拆掉那兩個假旋鈕，要嘛接上線讓它真的能調。
- **狀態**：✅ 完成（實作 weighted RRF：BM25_WEIGHT/(k+br+1) + VECTOR_WEIGHT/(k+vr+1)，2026-04-21）

---

### 🟡 Medium（影響量測準確度，順手或文件層級）

#### Med1｜P95 計算 off-by-one
- **位置**：`eval.py:185`
- **問題描述**：`latencies[int(0.95 * len(latencies)) - 1]` → 30 queries 時取 index 27（P93），非 P95。
- **白話文**：宣稱量 P95 其實量到 P93，小偏差但會讓報告失準。
- **解法**：改為 `float(np.percentile(latencies, 95))`（numpy 已為間接依賴）。
- **解法白話文**：用 numpy 內建的百分位函數，不要自己算。
- **狀態**：✅ 完成（改用 np.percentile，2026-04-21）

#### Med2｜eval path 與 production path 差異未說明
- **位置**：`tests/eval/README.md`
- **問題描述**：eval 的 `search_direct` 不走 `_expand_context`，production 的 `memory_deep_search` 走。差異合理（eval 量 retrieval 層，expansion 是 presentation layer），但文件未明文化，ground truth 標注者容易誤解。
- **白話文**：評測用的搜尋跟線上用的搜尋流程不一樣，這是故意的，但沒寫清楚，標注的人會標錯。
- **解法**：`tests/eval/README.md` 加一段說明：ground truth 標注的是 retrieval top-K 的 chunk_id，**不含 sibling expansion**；eval 刻意不跑 expand 以獨立量測檢索品質。
- **解法白話文**：在 README 加一段「標準答案只標原始檢索結果，不要標擴充的上下文」。
- **狀態**：✅ 完成（README 補充 eval vs production 差異說明，2026-04-21）

---



 2026-04-22 14:32:25.783  [owner] Andy
    [broadcast] 如果我提供兩個機制
    1 查詢的方式多一種 是 針對時間 時間越接近 權重越高
    2 每個session 應該要提煉出tag 可以讓 往下或是fallback 查詢時 能查到更多東西？

  2026-04-22 14:32:39.405  claude-claude-sonnet-0022486  (claude-sonnet-4-6)
    兩個機制都實用，但我對它們的優先級判斷和 Opus 可能不同：

    機制2（Session Tag）價值更高，因為它解決的是 vocabulary mismatch 的根本問題——tag
是橋接「意圖描述詞」和「技術術語」的索引層，一次建好終身受益，不依賴查詢者事後猜詞。

    機制1（時間權重）是輔助，對「最近在做什麼」這類問題確實有效，但有盲區：如果某個關鍵技術決策是三個月前定的，時間權重反而會壓低它，造成重要舊記憶被忽略。建議時間權重
是可選 flag（`recent=true`），不是預設行為。

    實作優先序建議：先上 Tag 機制，讓每 session 結束時提煉 3-5 個技術 tag 存入
metadata；再上時間加權作為查詢參數，兩者組合才能覆蓋「最近的泛查」和「跨時間的精確召回」兩種場景。
  (13615ms)

  2026-04-22 14:32:44.611  claude-claude-opus-4-6-ae49e6c  (claude-opus-4-6)
    ## 對這兩個機制的評價

    兩個都該做，但優先順序和效果不同。

    機制 1（時間衰減加權）：立即有效，優先做。 這直接解決了我們第一輪查詢失敗的核心場景——用戶問「最近在做什麼」，這本質上就是個時間查詢，不是語義查詢。目前純靠 cosine
similarity 排序，一個三個月前用詞完全匹配的記憶會排在昨天高度相關的記憶前面，這是反直覺的。時間加權讓 `memory_search` 在模糊查詢時自然偏向近期結果，符合人類回憶的
recency bias。實現上也簡單——score = α·semantic_similarity + (1-α)·time_decay，一個參數就搞定。

    機制 2（session 提煉 tag）：長期價值更大，但要設計好。 Tag 等於建了一個倒排索引層，讓查詢可以先 filter 再
rank，這是正確方向。但關鍵問題是：誰負責提煉、什麼時候提煉、tag 的詞彙表怎麼控制？ 如果靠 agent 在 session 結束時自動摘要打 tag，tag
品質會參差不齊；如果用固定分類體系（project name + phase + tech stack），則更穩定但需要前期規劃。建議用半結構化方式——強制填 project name，其餘 tag 自由生成。

    兩者搭配的查詢流程： 先用 tag 過濾（project=cartograph），再用時間加權語義排序，這樣即使查詢詞模糊，也能精準命中。

