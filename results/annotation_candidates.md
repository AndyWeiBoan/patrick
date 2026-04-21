# C1 Annotation Candidates — Graded Relevance
> Ground truth annotations for eval harness with **2-level relevance grades**.
> - **Grade 2**: Directly answers/discusses the query topic (primary source)
> - **Grade 1**: Indirectly related (mentions topic in passing, background context)
>
> **Method**: Full DB scan (1,277 chunks, 344 system-prompt chunks excluded) + LLM semantic review.
> Independent of search system to avoid circular bias.

---

## q001 | `patrick project phase 1 goals` ✅ (4×G2 + 1×G1 = 5 total)

> **Query:** `patrick project phase 1 goals` | **Lang:** en | **Category:** project_overview

1. **[G2]** `f359d85d-807d-472a-9789-035c18e5d61d` — 最直接的 Phase 1 完成狀態總報告，逐項列出所有目標
2. **[G2]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 另一份獨立的 Phase 1 核心功能完成清單
3. **[G2]** `e5269299-2328-407d-8f36-58da98c8a752` — Phase 1 設計文件的逐項技術評審
4. **[G2]** `13a08a1b-8223-4a56-9447-2881b76d7fc1` — Phase 1 結構性缺陷分析——反面描述 goals
5. **[G1]** `02ba54f7-6e81-42aa-9b3c-8fd5f76c0283` — Phase 1 進度摘要，只是提到進度而非詳述 goals

**→ Graded IDs:** `{"f359d85d-807d-472a-9789-035c18e5d61d": 2, "57e4618f-0644-4424-9dab-dacc3a31656c": 2, "e5269299-2328-407d-8f36-58da98c8a752": 2, "13a08a1b-8223-4a56-9447-2881b76d7fc1": 2, "02ba54f7-6e81-42aa-9b3c-8fd5f76c0283": 1}`

---

## q002 | `BM25 hybrid search implementation` ✅ (2×G2 + 3×G1 = 5 total)

> **Query:** `BM25 hybrid search implementation` | **Lang:** en | **Category:** technical

1. **[G2]** `002d285e-8c0a-41e6-ba71-999c6999363f` — storage.py 加入 BM25 搜尋功能的程式碼修改
2. **[G2]** `ae63c4b6-e46c-4560-afaa-d5caf3a7a8cd` — storage.py 加入 hybrid search 的結構性修改
3. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — Phase 2 commit 含 hybrid search 但只是 commit message
4. **[G1]** `e7d06f0b-0bfe-4818-8b68-5e7f0de6b275` — phase2.md 里程碑提及 BM25 hybrid 時程規劃
5. **[G1]** `bb9f84d5-cb39-4498-9e06-360a9a193f6b` — 驗證 BM25_K1, BM25_B config 參數存在

**→ Graded IDs:** `{"002d285e-8c0a-41e6-ba71-999c6999363f": 2, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1, "ae63c4b6-e46c-4560-afaa-d5caf3a7a8cd": 2, "e7d06f0b-0bfe-4818-8b68-5e7f0de6b275": 1, "bb9f84d5-cb39-4498-9e06-360a9a193f6b": 1}`

---

## q003 | `LanceDB schema turn_chunks fields` ✅ (1×G2 + 3×G1 = 4 total)

> **Query:** `LanceDB schema turn_chunks fields` | **Lang:** en | **Category:** technical

1. **[G2]** `ae63c4b6-e46c-4560-afaa-d5caf3a7a8cd` — storage.py 修改含 LanceDB schema 定義
2. **[G1]** `a8f8f86c-2eb0-443a-9297-fc5418b247de` — 間接提到 schema 設計考量，非直接列出欄位
3. **[G1]** `002d285e-8c0a-41e6-ba71-999c6999363f` — storage.py 修改涉及 turn_chunks 方法
4. **[G1]** `ae0673e2-705b-4b26-87fe-c13b89071b9a` — git commit 含 storage 修改但無 schema 細節

**→ Graded IDs:** `{"a8f8f86c-2eb0-443a-9297-fc5418b247de": 1, "ae63c4b6-e46c-4560-afaa-d5caf3a7a8cd": 2, "002d285e-8c0a-41e6-ba71-999c6999363f": 1, "ae0673e2-705b-4b26-87fe-c13b89071b9a": 1}`

---

## q004 | `embedding model choice fastembed multilingual` ✅ (3×G2 + 2×G1 = 5 total)

> **Query:** `embedding model choice fastembed multilingual` | **Lang:** en | **Category:** technical

1. **[G2]** `e5269299-2328-407d-8f36-58da98c8a752` — 直接評價 fastembed ONNX 選型的技術評審
2. **[G2]** `d56052e4-d88f-4bce-bb9b-f1b505fef58b` — phase2.md 修改含 cross-encoder 模型選型討論
3. **[G2]** `0d9b844a-d538-418e-bae0-5c4b90f4337c` — embedding.py 的 fastembed wrapper 程式碼
4. **[G1]** `3098c9cb-efb6-42de-819b-6d2c39111e45` — README 提及技術棧含 fastembed + ONNX
5. **[G1]** `40a02f8b-21ea-4a5c-809e-1b7ccdf42cad` — pyproject.toml 加入 fastembed 依賴

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 2, "d56052e4-d88f-4bce-bb9b-f1b505fef58b": 2, "0d9b844a-d538-418e-bae0-5c4b90f4337c": 2, "3098c9cb-efb6-42de-819b-6d2c39111e45": 1, "40a02f8b-21ea-4a5c-809e-1b7ccdf42cad": 1}`

---

## q005 | `cross-encoder rerank latency budget` ✅ (2×G2 + 1×G1 = 3 total)

> **Query:** `cross-encoder rerank latency budget` | **Lang:** en | **Category:** technical

1. **[G2]** `302b0de0-106b-4be0-bc67-d5b2f2afe25e` — P95 latency 800ms 規格的 phase2.md 修改
2. **[G2]** `d56052e4-d88f-4bce-bb9b-f1b505fef58b` — cross-encoder 模型選型與效能討論
3. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit message 提及 rerank 但無 latency 細節

**→ Graded IDs:** `{"302b0de0-106b-4be0-bc67-d5b2f2afe25e": 2, "d56052e4-d88f-4bce-bb9b-f1b505fef58b": 2, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1}`

---

## q006 | `cosine dedup threshold 0.95 rationale` ⚠️ SPARSE (0×G2 + 2×G1 = 2 total)

> **Query:** `cosine dedup threshold 0.95 rationale` | **Lang:** en | **Category:** technical

1. **[G1]** `bb9f84d5-cb39-4498-9e06-360a9a193f6b` — config 驗證含 COSINE_DEDUP_THRESHOLD 但無 rationale
2. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 提及 cosine dedup 但無 0.95 理由

**→ Graded IDs:** `{"bb9f84d5-cb39-4498-9e06-360a9a193f6b": 1, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1}`

---

## q007 | `session summary centroid vector calculation` ✅ (2×G2 + 3×G1 = 5 total)

> **Query:** `session summary centroid vector calculation` | **Lang:** en | **Category:** technical

1. **[G2]** `60a0ca04-8111-4c5d-8fec-0c60028d99c2` — 直接解釋 centroid 是純 numpy 數學，不花 token
2. **[G2]** `e2812299-36d4-490c-9eac-1d406fd4490c` — observer.py 修改 centroid update 觸發邏輯
3. **[G1]** `e5269299-2328-407d-8f36-58da98c8a752` — 技術評審提及 centroid auto summary 為 ✅
4. **[G1]** `f359d85d-807d-472a-9789-035c18e5d61d` — Phase 1 總結提及 LanceDB 雙表 + centroid 計算
5. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 狀態報告提及 centroid session summary（零 token）

**→ Graded IDs:** `{"60a0ca04-8111-4c5d-8fec-0c60028d99c2": 2, "e5269299-2328-407d-8f36-58da98c8a752": 1, "f359d85d-807d-472a-9789-035c18e5d61d": 1, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "e2812299-36d4-490c-9eac-1d406fd4490c": 2}`

---

## q008 | `memory_save disabled reason` ✅ (3×G2 + 2×G1 = 5 total)

> **Query:** `memory_save disabled reason` | **Lang:** en | **Category:** decision

1. **[G2]** `f359d85d-807d-472a-9789-035c18e5d61d` — 直接描述 memory_save 被停掉的原因和 early return
2. **[G2]** `88899ae9-f0d0-484c-9721-a4dba41515ff` — 缺陷分析直接討論 memory_save 被停用的影響
3. **[G2]** `c24bf97e-88be-4d11-b726-39f49a9b2c23` — bug fix 後仍列 memory_save 停用為剩餘缺陷
4. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 狀態報告提及 memory_save 被停掉
5. **[G1]** `02ba54f7-6e81-42aa-9b3c-8fd5f76c0283` — 進度摘要提及 memory_save 停用

**→ Graded IDs:** `{"f359d85d-807d-472a-9789-035c18e5d61d": 2, "88899ae9-f0d0-484c-9721-a4dba41515ff": 2, "c24bf97e-88be-4d11-b726-39f49a9b2c23": 2, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "02ba54f7-6e81-42aa-9b3c-8fd5f76c0283": 1}`

---

## q009 | `eval harness frozen benchmark 30 queries` ✅ (2×G2 + 3×G1 = 5 total)

> **Query:** `eval harness frozen benchmark 30 queries` | **Lang:** en | **Category:** process

1. **[G2]** `e7d06f0b-0bfe-4818-8b68-5e7f0de6b275` — phase2.md M1 里程碑直接描述 eval harness 規劃
2. **[G1]** `acedcad8-9dee-4ac6-90e7-c2dd67749c26` — 寫入 queries.jsonl 的操作記錄——非設計討論
3. **[G1]** `98d5e2b3-b4d3-4e4d-aede-01b82a68efad` — 寫入 eval.py 的操作記錄——非設計討論
4. **[G2]** `69034dd5-1794-41fc-adf1-0d74e253cfa2` — phase2.md C1 ground truth 標注為 M1 第一件事
5. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit message 提及 T1 eval harness 30 frozen queries

**→ Graded IDs:** `{"2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1, "e7d06f0b-0bfe-4818-8b68-5e7f0de6b275": 2, "acedcad8-9dee-4ac6-90e7-c2dd67749c26": 1, "98d5e2b3-b4d3-4e4d-aede-01b82a68efad": 1, "69034dd5-1794-41fc-adf1-0d74e253cfa2": 2}`

---

## q010 | `RRF reciprocal rank fusion formula` ⚠️ SPARSE (1×G2 + 1×G1 = 2 total)

> **Query:** `RRF reciprocal rank fusion formula` | **Lang:** en | **Category:** technical

1. **[G2]** `4b94a79d-e918-4c14-a1bb-b5465ea22f72` — phase2.md 修改直接提及 RRF 策略選擇
2. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 含 hybrid search 但 RRF 只是間接

**→ Graded IDs:** `{"4b94a79d-e918-4c14-a1bb-b5465ea22f72": 2, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1}`

---

## q011 | `patrick 專案 phase 2 計畫` ✅ (2×G2 + 3×G1 = 5 total)

> **Query:** `patrick 專案 phase 2 計畫` | **Lang:** zh | **Category:** project_overview

1. **[G2]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 直接實作 Phase 2 功能
2. **[G2]** `f4ca0624-abdd-4cd9-84d9-c92bd20bce3f` — phase2.md 修改含「暫不做留給 Phase 3」——直接定義 Phase 2 範圍
3. **[G1]** `a8f8f86c-2eb0-443a-9297-fc5418b247de` — Phase 1→2 過渡建議但非 Phase 2 計畫全貌
4. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — Phase 1 狀態總結，提及下一步但非 Phase 2 計畫
5. **[G1]** `02ba54f7-6e81-42aa-9b3c-8fd5f76c0283` — 進度摘要提及 Phase 2 方向

**→ Graded IDs:** `{"a8f8f86c-2eb0-443a-9297-fc5418b247de": 1, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "02ba54f7-6e81-42aa-9b3c-8fd5f76c0283": 1, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 2, "f4ca0624-abdd-4cd9-84d9-c92bd20bce3f": 2}`

---

## q012 | `向量搜尋語意漂移問題` ✅ (2×G2 + 2×G1 = 4 total)

> **Query:** `向量搜尋語意漂移問題` | **Lang:** zh | **Category:** technical

1. **[G2]** `b591ae7b-61ef-48ca-ba56-cbf2bdde71a0` — 直接分析純 cosine 搜尋的限制——語意漂移的具體表現
2. **[G2]** `a8f8f86c-2eb0-443a-9297-fc5418b247de` — 討論時間衰減與語意相關性的取捨
3. **[G1]** `13a08a1b-8223-4a56-9447-2881b76d7fc1` — 結構性缺陷分析但非專門討論語意漂移
4. **[G1]** `c24bf97e-88be-4d11-b726-39f49a9b2c23` — 提及搜尋品質問題但非語意漂移專題

**→ Graded IDs:** `{"b591ae7b-61ef-48ca-ba56-cbf2bdde71a0": 2, "a8f8f86c-2eb0-443a-9297-fc5418b247de": 2, "13a08a1b-8223-4a56-9447-2881b76d7fc1": 1, "c24bf97e-88be-4d11-b726-39f49a9b2c23": 1}`

---

## q013 | `session hook 觸發流程` ✅ (5×G2 + 0×G1 = 5 total)

> **Query:** `session hook 觸發流程` | **Lang:** zh | **Category:** technical

1. **[G2]** `13a08a1b-8223-4a56-9447-2881b76d7fc1` — 直接描述 hook 架構：prompt_submit / post_tool_use / stop
2. **[G2]** `88899ae9-f0d0-484c-9721-a4dba41515ff` — 從搜尋結果反推 hook 記錄的缺陷——展示觸發結果
3. **[G2]** `3a7facce-eada-403f-9298-fbc7416c331f` — 修復 stop hook bug——直接涉及觸發流程細節
4. **[G2]** `17ed5a5b-a388-4ec0-99f2-530fbfbb452f` — 設定 stop hook debug 流程——觸發測試步驟
5. **[G2]** `27a881c0-66ae-43ea-9eaa-aa3dd6ae9541` — 發現 transcript_path 和 stop hook 完整 payload

**→ Graded IDs:** `{"13a08a1b-8223-4a56-9447-2881b76d7fc1": 2, "88899ae9-f0d0-484c-9721-a4dba41515ff": 2, "3a7facce-eada-403f-9298-fbc7416c331f": 2, "17ed5a5b-a388-4ec0-99f2-530fbfbb452f": 2, "27a881c0-66ae-43ea-9eaa-aa3dd6ae9541": 2}`

---

## q014 | `記憶系統去重方法` ✅ (1×G2 + 4×G1 = 5 total)

> **Query:** `記憶系統去重方法` | **Lang:** zh | **Category:** technical

1. **[G2]** `e5269299-2328-407d-8f36-58da98c8a752` — 技術評審直接評價 SHA256 exact dedup 設計
2. **[G1]** `9c863b2b-caa2-4d89-91c2-5cc740fe8487` — 討論記憶系統設計哲學但非去重具體方法
3. **[G1]** `3a7facce-eada-403f-9298-fbc7416c331f` — unique assistant 訊息去重——間接相關
4. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 狀態報告提及 SHA256 dedup 為已完成功能
5. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 含 cosine dedup 但無去重方法細節

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 2, "9c863b2b-caa2-4d89-91c2-5cc740fe8487": 1, "3a7facce-eada-403f-9298-fbc7416c331f": 1, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1}`

---

## q015 | `phase 3 跨 agent 記憶一致性延後原因` ✅ (2×G2 + 1×G1 = 3 total)

> **Query:** `phase 3 跨 agent 記憶一致性延後原因` | **Lang:** zh | **Category:** decision

1. **[G2]** `f4ca0624-abdd-4cd9-84d9-c92bd20bce3f` — phase2.md 直接修改 Phase 3 延後項目
2. **[G2]** `89b3803e-8cac-46f4-9e9a-c79f941ec02b` — T3 跨 agent 一致性延後原因的直接描述
3. **[G1]** `a4e0da00-fe3a-4292-8de1-d48451e5137e` — T7 修改，Phase 3 相關但非跨 agent 一致性

**→ Graded IDs:** `{"f4ca0624-abdd-4cd9-84d9-c92bd20bce3f": 2, "89b3803e-8cac-46f4-9e9a-c79f941ec02b": 2, "a4e0da00-fe3a-4292-8de1-d48451e5137e": 1}`

---

## q016 | `chunk_size 400 tokens overlap 80` ✅ (3×G2 + 2×G1 = 5 total)

> **Query:** `chunk_size 400 tokens overlap 80` | **Lang:** en | **Category:** technical

1. **[G2]** `60a0ca04-8111-4c5d-8fec-0c60028d99c2` — 直接提及 400 token chunk 的設計考量
2. **[G2]** `0d9b844a-d538-418e-bae0-5c4b90f4337c` — embedding.py 修改含 token-aware chunking 實作
3. **[G2]** `7a87226b-50c2-4c23-b1d2-1b20d2982291` — phase2.md re-embedding 因 chunking 改動觸發
4. **[G1]** `87886a19-9f36-4acc-b1f9-050a9c363f84` — 討論 input token 成本但非 chunk size 具體設定
5. **[G1]** `f359d85d-807d-472a-9789-035c18e5d61d` — Phase 1 報告提及 token-aware chunking 已完成

**→ Graded IDs:** `{"60a0ca04-8111-4c5d-8fec-0c60028d99c2": 2, "0d9b844a-d538-418e-bae0-5c4b90f4337c": 2, "87886a19-9f36-4acc-b1f9-050a9c363f84": 1, "7a87226b-50c2-4c23-b1d2-1b20d2982291": 2, "f359d85d-807d-472a-9789-035c18e5d61d": 1}`

---

## q017 | `mcp server tools registration` ✅ (1×G2 + 4×G1 = 5 total)

> **Query:** `mcp server tools registration` | **Lang:** en | **Category:** technical

1. **[G2]** `6e0c0843-b92c-4036-a6be-0756252670ef` — tools.py 修改——直接涉及 MCP tool 定義
2. **[G1]** `e5269299-2328-407d-8f36-58da98c8a752` — 提及 MCP 但非 tools registration 細節
3. **[G1]** `6da2ed85-7a52-4df1-9bdd-692112a2a5dc` — 提及 MCP 原生但非 registration 機制
4. **[G1]** `3098c9cb-efb6-42de-819b-6d2c39111e45` — README 提及 FastMCP 技術棧
5. **[G1]** `f359d85d-807d-472a-9789-035c18e5d61d` — 提及 memory tools 已完成但非 registration 細節

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 1, "6da2ed85-7a52-4df1-9bdd-692112a2a5dc": 1, "6e0c0843-b92c-4036-a6be-0756252670ef": 2, "3098c9cb-efb6-42de-819b-6d2c39111e45": 1, "f359d85d-807d-472a-9789-035c18e5d61d": 1}`

---

## q018 | `session_summaries Layer 1 coarse filter` ✅ (3×G2 + 2×G1 = 5 total)

> **Query:** `session_summaries Layer 1 coarse filter` | **Lang:** en | **Category:** technical

1. **[G2]** `e5269299-2328-407d-8f36-58da98c8a752` — 直接評價兩層索引設計：粗篩 + 細撈
2. **[G2]** `60a0ca04-8111-4c5d-8fec-0c60028d99c2` — 解釋 centroid 在 Layer 1 的角色
3. **[G2]** `ae63c4b6-e46c-4560-afaa-d5caf3a7a8cd` — storage.py 修改含 session_summaries 表邏輯
4. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 提及兩層向量索引為已完成功能
5. **[G1]** `3098c9cb-efb6-42de-819b-6d2c39111e45` — README 提及兩層 cosine 搜索

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 2, "60a0ca04-8111-4c5d-8fec-0c60028d99c2": 2, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "3098c9cb-efb6-42de-819b-6d2c39111e45": 1, "ae63c4b6-e46c-4560-afaa-d5caf3a7a8cd": 2}`

---

## q019 | `text_hash SHA256 exact dedup` ✅ (1×G2 + 3×G1 = 4 total)

> **Query:** `text_hash SHA256 exact dedup` | **Lang:** en | **Category:** technical

1. **[G2]** `e5269299-2328-407d-8f36-58da98c8a752` — 技術評審直接評價 SHA256 exact dedup
2. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 提及 SHA256 dedup 為已完成功能
3. **[G1]** `3098c9cb-efb6-42de-819b-6d2c39111e45` — README 提及 SHA-256 dedup
4. **[G1]** `9c863b2b-caa2-4d89-91c2-5cc740fe8487` — 討論去重哲學但非 SHA256 具體機制

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 2, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "3098c9cb-efb6-42de-819b-6d2c39111e45": 1, "9c863b2b-caa2-4d89-91c2-5cc740fe8487": 1}`

---

## q020 | `observer.py batch worker asyncio queue` ✅ (2×G2 + 1×G1 = 3 total)

> **Query:** `observer.py batch worker asyncio queue` | **Lang:** en | **Category:** technical

1. **[G2]** `c24bf97e-88be-4d11-b726-39f49a9b2c23` — 描述 stop hook 大量批次寫入的問題
2. **[G2]** `e2812299-36d4-490c-9eac-1d406fd4490c` — observer.py 修改 centroid trigger 邏輯
3. **[G1]** `e5269299-2328-407d-8f36-58da98c8a752` — 提及 run_in_executor offload 但非 batch worker

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 1, "c24bf97e-88be-4d11-b726-39f49a9b2c23": 2, "e2812299-36d4-490c-9eac-1d406fd4490c": 2}`

---

## q021 | `benchmark KPI Recall@10 nDCG MRR definition` ⚠️ SPARSE (0×G2 + 2×G1 = 2 total)

> **Query:** `benchmark KPI Recall@10 nDCG MRR definition` | **Lang:** en | **Category:** metrics

1. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 提及 Recall@10/nDCG@10/MRR 但無定義
2. **[G1]** `bb9f84d5-cb39-4498-9e06-360a9a193f6b` — config 驗證但無 KPI 定義

**→ Graded IDs:** `{"2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1, "bb9f84d5-cb39-4498-9e06-360a9a193f6b": 1}`

---

## q022 | `re-embedding cost estimate production migration` ✅ (3×G2 + 1×G1 = 4 total)

> **Query:** `re-embedding cost estimate production migration` | **Lang:** en | **Category:** process

1. **[G2]** `918557a8-c2d9-4a99-a3d5-b4c741e2e329` — phase2.md re-embedding 成本估算修改
2. **[G2]** `35c18afa-9ac0-4a7c-aecb-70f8437f1c9e` — phase2.md re-embedding 計畫修改
3. **[G2]** `7a87226b-50c2-4c23-b1d2-1b20d2982291` — phase2.md re-embedding 因 chunking 觸發
4. **[G1]** `769168f3-2d77-4f25-ac16-377b1e8311b8` — Benchmark 報告修改，間接涉及 re-embedding

**→ Graded IDs:** `{"918557a8-c2d9-4a99-a3d5-b4c741e2e329": 2, "35c18afa-9ac0-4a7c-aecb-70f8437f1c9e": 2, "7a87226b-50c2-4c23-b1d2-1b20d2982291": 2, "769168f3-2d77-4f25-ac16-377b1e8311b8": 1}`

---

## q023 | `TOP_K_SESSIONS TOP_K_CHUNKS config values` ✅ (2×G2 + 1×G1 = 3 total)

> **Query:** `TOP_K_SESSIONS TOP_K_CHUNKS config values` | **Lang:** en | **Category:** technical

1. **[G2]** `1b853088-ac02-4014-a6b7-db827188c15a` — tools.py 修改含 TOP_K_CHUNKS 參數使用
2. **[G2]** `9f6b1f57-1bd7-4b05-9a65-e8a429356302` — tools.py 修改含 memory_deep_search TOP_K 參數
3. **[G1]** `87886a19-9f36-4acc-b1f9-050a9c363f84` — 討論 top-k retrieval 概念但非 config 值

**→ Graded IDs:** `{"1b853088-ac02-4014-a6b7-db827188c15a": 2, "9f6b1f57-1bd7-4b05-9a65-e8a429356302": 2, "87886a19-9f36-4acc-b1f9-050a9c363f84": 1}`

---

## q024 | `memory_deep_search two layer retrieval` ✅ (3×G2 + 2×G1 = 5 total)

> **Query:** `memory_deep_search two layer retrieval` | **Lang:** en | **Category:** technical

1. **[G2]** `88899ae9-f0d0-484c-9721-a4dba41515ff` — 從搜尋結果分析 deep search 品質問題
2. **[G2]** `6e0c0843-b92c-4036-a6be-0756252670ef` — tools.py 修改含 memory_deep_search 實作
3. **[G2]** `9f6b1f57-1bd7-4b05-9a65-e8a429356302` — tools.py 修改含 deep_search 兩層邏輯
4. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 提及 memory_deep_search 為已完成功能
5. **[G1]** `f359d85d-807d-472a-9789-035c18e5d61d` — 提及 memory_deep_search 為已完成功能

**→ Graded IDs:** `{"57e4618f-0644-4424-9dab-dacc3a31656c": 1, "f359d85d-807d-472a-9789-035c18e5d61d": 1, "88899ae9-f0d0-484c-9721-a4dba41515ff": 2, "6e0c0843-b92c-4036-a6be-0756252670ef": 2, "9f6b1f57-1bd7-4b05-9a65-e8a429356302": 2}`

---

## q025 | `patrick CLI init start setup doctor commands` ✅ (2×G2 + 3×G1 = 5 total)

> **Query:** `patrick CLI init start setup doctor commands` | **Lang:** en | **Category:** usage

1. **[G2]** `25c40645-dc7d-48e9-941b-46913f4cd59d` — 直接列出 CLI 指令：patrick init → start → setup
2. **[G2]** `c4bc7b7c-76dd-4f38-8a65-e24fee927f4d` — 指出 README 用錯 CLI 指令名稱
3. **[G1]** `f359d85d-807d-472a-9789-035c18e5d61d` — Phase 1 報告提及 CLI 已完成
4. **[G1]** `2b30b443-d9a3-482c-9170-c0facc3b2f45` — git commit 含 CLI 文件修正
5. **[G1]** `3098c9cb-efb6-42de-819b-6d2c39111e45` — README 含 CLI 使用說明

**→ Graded IDs:** `{"25c40645-dc7d-48e9-941b-46913f4cd59d": 2, "c4bc7b7c-76dd-4f38-8a65-e24fee927f4d": 2, "f359d85d-807d-472a-9789-035c18e5d61d": 1, "2b30b443-d9a3-482c-9170-c0facc3b2f45": 1, "3098c9cb-efb6-42de-819b-6d2c39111e45": 1}`

---

## q026 | `bge-reranker vs mmarco cross-encoder comparison` ✅ (1×G2 + 3×G1 = 4 total)

> **Query:** `bge-reranker vs mmarco cross-encoder comparison` | **Lang:** en | **Category:** decision

1. **[G2]** `d56052e4-d88f-4bce-bb9b-f1b505fef58b` — phase2.md 直接列出 reranker 模型比較
2. **[G1]** `302b0de0-106b-4be0-bc67-d5b2f2afe25e` — P95 latency 規格涉及 cross-encoder 但非比較
3. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 含 rerank 但無模型比較
4. **[G1]** `bb9f84d5-cb39-4498-9e06-360a9a193f6b` — config 驗證 RERANK_MODEL 值

**→ Graded IDs:** `{"d56052e4-d88f-4bce-bb9b-f1b505fef58b": 2, "302b0de0-106b-4be0-bc67-d5b2f2afe25e": 1, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1, "bb9f84d5-cb39-4498-9e06-360a9a193f6b": 1}`

---

## q027 | `fastembed ONNX runtime CPU inference` ✅ (2×G2 + 3×G1 = 5 total)

> **Query:** `fastembed ONNX runtime CPU inference` | **Lang:** en | **Category:** technical

1. **[G2]** `e5269299-2328-407d-8f36-58da98c8a752` — 技術評審直接評價 fastembed ONNX 120MB 冷啟動快
2. **[G2]** `0d9b844a-d538-418e-bae0-5c4b90f4337c` — embedding.py 修改含 fastembed wrapper 實作
3. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 狀態報告提及 fastembed ONNX 為已完成功能
4. **[G1]** `3098c9cb-efb6-42de-819b-6d2c39111e45` — README 提及 fastembed + ONNX 技術棧
5. **[G1]** `40a02f8b-21ea-4a5c-809e-1b7ccdf42cad` — pyproject.toml 加入 fastembed 依賴

**→ Graded IDs:** `{"e5269299-2328-407d-8f36-58da98c8a752": 2, "57e4618f-0644-4424-9dab-dacc3a31656c": 1, "3098c9cb-efb6-42de-819b-6d2c39111e45": 1, "0d9b844a-d538-418e-bae0-5c4b90f4337c": 2, "40a02f8b-21ea-4a5c-809e-1b7ccdf42cad": 1}`

---

## q028 | `session end stop hook centroid update` ✅ (4×G2 + 1×G1 = 5 total)

> **Query:** `session end stop hook centroid update` | **Lang:** en | **Category:** technical

1. **[G2]** `e2812299-36d4-490c-9eac-1d406fd4490c` — observer.py 修改 stop hook centroid 觸發邏輯
2. **[G2]** `27a881c0-66ae-43ea-9eaa-aa3dd6ae9541` — 發現 stop hook transcript_path 和 payload 結構
3. **[G2]** `c24bf97e-88be-4d11-b726-39f49a9b2c23` — stop hook bug fix——路徑修正 + 批次寫入
4. **[G2]** `17ed5a5b-a388-4ec0-99f2-530fbfbb452f` — stop hook debug 設定——觸發測試流程
5. **[G1]** `71ae170c-2640-40ca-8f84-3d76da37ca2b` — 確認修復後資料庫有 assistant 文字

**→ Graded IDs:** `{"e2812299-36d4-490c-9eac-1d406fd4490c": 2, "27a881c0-66ae-43ea-9eaa-aa3dd6ae9541": 2, "c24bf97e-88be-4d11-b726-39f49a9b2c23": 2, "17ed5a5b-a388-4ec0-99f2-530fbfbb452f": 2, "71ae170c-2640-40ca-8f84-3d76da37ca2b": 1}`

---

## q029 | `T4 session summary deferred phase 3 reason` ✅ (1×G2 + 3×G1 = 4 total)

> **Query:** `T4 session summary deferred phase 3 reason` | **Lang:** en | **Category:** decision

1. **[G1]** `60a0ca04-8111-4c5d-8fec-0c60028d99c2` — 解釋 zero token 原則——間接相關但非 T4 延後的直接原因
2. **[G2]** `f4ca0624-abdd-4cd9-84d9-c92bd20bce3f` — phase2.md 直接將 T4 移至 Phase 3
3. **[G1]** `89b3803e-8cac-46f4-9e9a-c79f941ec02b` — T3 跨 agent 移至 Phase 3——相關但非 T4
4. **[G1]** `57e4618f-0644-4424-9dab-dacc3a31656c` — 狀態報告提及 Phase 1 完成情況

**→ Graded IDs:** `{"60a0ca04-8111-4c5d-8fec-0c60028d99c2": 1, "f4ca0624-abdd-4cd9-84d9-c92bd20bce3f": 2, "89b3803e-8cac-46f4-9e9a-c79f941ec02b": 1, "57e4618f-0644-4424-9dab-dacc3a31656c": 1}`

---

## q030 | `BM25 k1 b parameter grid search tuning` ✅ (3×G2 + 2×G1 = 5 total)

> **Query:** `BM25 k1 b parameter grid search tuning` | **Lang:** en | **Category:** technical

1. **[G2]** `bb9f84d5-cb39-4498-9e06-360a9a193f6b` — 直接驗證 BM25_K1, BM25_B config 參數
2. **[G2]** `002d285e-8c0a-41e6-ba71-999c6999363f` — storage.py 含 BM25 實作——使用 k1/b 參數
3. **[G2]** `4b94a79d-e918-4c14-a1bb-b5465ea22f72` — phase2.md 提及 RRF + grid search 策略
4. **[G1]** `e7d06f0b-0bfe-4818-8b68-5e7f0de6b275` — 里程碑提及 BM25 + hybrid 時程
5. **[G1]** `2d0e87ee-4aa9-4deb-9526-fbb19b0a9185` — commit 含 hybrid search 但無 grid search 細節

**→ Graded IDs:** `{"bb9f84d5-cb39-4498-9e06-360a9a193f6b": 2, "002d285e-8c0a-41e6-ba71-999c6999363f": 2, "4b94a79d-e918-4c14-a1bb-b5465ea22f72": 2, "e7d06f0b-0bfe-4818-8b68-5e7f0de6b275": 1, "2d0e87ee-4aa9-4deb-9526-fbb19b0a9185": 1}`

---
