# Chunk 機制優化方案

> 作者：claude-opus-4-6 | 日期：2026-04-23
> 背景：目前 patrick-memory 的搜尋召回品質不佳，高分結果語義含量低（例如 "好的，我來用 patrick-memory 查詢一下" 排第一），根本原因在於 chunk 切割未考慮語義邊界，導致 embedding 被噪音稀釋。

---

## 核心問題診斷

1. **Chunk 粒度問題**：按固定 token 數或單一 turn 切割，語義完整性無法保證
2. **Embedding 代表性問題**：原文 embedding 是所有 token 的平均值，混雜了寒暄、工具呼叫、實質討論，導致向量無法精準代表「這段在講什麼」
3. **脈絡斷裂問題**：連續討論同一主題的多個 turn 被拆散，搜尋只能命中片段而非完整脈絡

---

## 分層優化方案

### 第一層：儲存時 — 多層 Chunk 結構

維護兩層索引，不是二選一：

- **Atomic Layer（原子層）**：保持現有的 per-turn chunk，維持原始資料完整性
- **Semantic Layer（語意層）**：自動偵測 topic shift（可用 sentence embedding cosine similarity drop 偵測），把連續討論同一主題的多個 turn 合併成一個 semantic chunk，生成獨立的 summary embedding

```
Session Turn 1 ─┐
Session Turn 2 ──┼── Semantic Chunk A (主題：chunk 優化討論)
Session Turn 3 ─┘
Session Turn 4 ─┐
Session Turn 5 ──┼── Semantic Chunk B (主題：部署計畫)
Session Turn 6 ─┘
```

### 第二層：儲存時 — 自動生成 Semantic Summary

每個 chunk（不論哪層）都應該有一個 LLM 自動生成的 1-2 句 summary：

- 用這個 summary 的 embedding 作為主要搜尋向量
- 直接解決「embedding 是詞彙平均值而非語意核心」的問題
- Summary 會自動過濾噪音，只保留「這段在講什麼」

**範例：**

| 原文 | 自動生成 Summary |
|------|-----------------|
| "好的，我來用 patrick-memory 查詢一下... [工具呼叫] ...結果顯示我們討論了 chunk 優化" | "搜尋記憶以回顧 chunk 優化的討論歷史" |
| "我認為應該用語義分段，因為固定 token 切割會把不相關的內容混在一起..." | "提出以語義邊界取代固定 token 數的 chunk 切割策略" |

### 第三層：檢索時 — 兩階段 Retrieval

- **Phase 1**：先搜 Semantic Layer（大塊），定位主題區域，快速縮小範圍
- **Phase 2**：在命中的 Semantic Chunk 內，搜 Atomic Layer 取回精確的 turn

這樣既有宏觀的主題定位能力，又有微觀的精確回溯能力。

### 第四層：雙向量 Embedding

每個 chunk 同時存兩個向量：

1. **原文 embedding**：保留細節，支援關鍵詞層級的匹配
2. **Summary embedding**：捕捉語意核心，支援意圖層級的匹配

搜尋時對兩個向量分別算相似度，加權合併排序。

---

## 額外建議：結構化 Metadata

儲存時為每個 chunk 自動生成結構化 tag：

- **主題標籤**（例：`chunk-optimization`, `deployment`, `architecture`）
- **實體標籤**（例：`patrick-memory`, `ColBERT`, `embedding`）
- **動作標籤**（例：`proposal`, `decision`, `question`）

搜尋時可先用 tag 做 pre-filter，再用向量排序，大幅降低噪音命中率。

---

## 成本分析與取捨

| 項目 | 寫入成本 | 搜尋品質提升 | 優先級 |
|------|---------|-------------|--------|
| 結構化 Metadata | 低（規則式或輕量 LLM） | 中 | ★★★ 最快落地 |
| 自動 Summary + Summary Embedding | 中（每次存入需 LLM 呼叫） | 高 | ★★★ 核心改善 |
| 多層 Chunk 結構 | 中（需 topic detection） | 高 | ★★☆ 中期目標 |
| 雙向量 Embedding | 低（額外一次 embedding） | 中高 | ★★☆ 搭配 summary 一起做 |
| 兩階段 Retrieval | 低（查詢邏輯改動） | 高 | ★☆☆ 依賴多層結構 |

**關鍵判斷**：所有寫入時的額外成本都是一次性的，換來的是每次搜尋品質的永久提升。這個 trade-off 非常值得。

---

## 與 Overlap 方案的比較

Overlap（chunk 之間保留重疊區域）是常見做法，但我認為**自動 summary 比 overlap 更根本**：

- Overlap 只是緩解邊界切割問題，本質上還是在用原文做 embedding
- Summary 才是讓 embedding 真正代表語意的手段
- 如果已經有了 summary embedding，overlap 的邊際效益就很小了

---

## 建議實施順序

1. **Phase 1**：先加結構化 metadata tag + 自動 summary（最快看到效果）
2. **Phase 2**：實作 semantic layer 的 topic detection 和 chunk 合併
3. **Phase 3**：實作兩階段 retrieval + 雙向量搜尋
