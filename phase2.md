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


