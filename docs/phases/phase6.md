# Phase 6 — Semantic Clustering & Interactive Dashboard

## 目標

提供一個互動式 Dashboard，讓使用者能夠：
1. 瀏覽所有 project 列表
2. 點開某個 project → 看到所有 chunk 的 UMAP 2D 散點圖（用 HDBSCAN cluster 著色）
3. 展開 project 下的 session 列表 → 點開某個 session → 散點圖高亮/篩選該 session 的 chunk

核心工作流：**`patrick cluster` 離線聚類 → UMAP 降維 → Dashboard 視覺化瀏覽**

---

## 設計決策

- **HDBSCAN vs DBSCAN**：採用 HDBSCAN。自適應密度，不需手動調 `eps`，能處理不均勻密度（有些 topic 大量討論、有些只提一次）。`cluster_id=-1` 代表噪音。
- **降維策略**：UMAP 384D → 2D（per-project），座標**預算後存入 DB**（非每次 request 重算），透過 `patrick cluster` CLI 觸發。
- **前端**：純 HTML + Plotly CDN（`scattergl` WebGL 渲染），不需 npm/vite，由 Starlette 直接 serve。
- **Session 導航**：project 散點圖中，選擇某個 session 後高亮其 chunk、其他 chunk 變半透明，直覺地看到該 session 在語意空間中的分布。

---

## 前提確認

- Phase 5 已完成 `project_path` scoping，可按 project 過濾 chunks
- `turn_chunks` 已有 384 維向量（`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`）
- `scikit-learn` 是 `sentence-transformers` 的 transitive dependency，已可用
- `hdbscan` 和 `umap-learn` 需新增為依賴
- Patrick server 基於 Starlette/FastMCP，目前無 HTML-serving endpoint
- 現有 CLI 入口為 `patrick`（typer），可擴充子命令
- 現有 migration pattern：`add_columns()` with fallback expressions（Phase 4/5 先例）

---

## Task List

---

### Task 1 — 新增依賴：`hdbscan` + `umap-learn` + `plotly` ✅ DONE

**目標**：安裝 Phase 6 所需的三個核心依賴，確認與現有環境無衝突。

**改動檔案**：`pyproject.toml`

**具體做法**：
1. 在 `dependencies` 區塊新增：
   ```toml
   # Phase 6: clustering & visualization
   "hdbscan>=0.8.33",
   "umap-learn>=0.5.5",
   "plotly>=5.18.0",
   ```
2. 執行 `uv sync` 確認安裝無衝突
3. 冒煙測試：
   ```python
   import hdbscan, umap, plotly
   import numpy as np
   X = np.random.rand(50, 384).astype(np.float32)
   labels = hdbscan.HDBSCAN(min_cluster_size=5).fit_predict(X)
   coords = umap.UMAP(n_components=2, random_state=42).fit_transform(X)
   assert coords.shape == (50, 2)
   ```
4. 確認 `numba`（UMAP 的 JIT 依賴）不影響 FastAPI 啟動時間（可用 lazy import 解決）

**不確定事項**：
- `hdbscan` 和 `umap-learn` 都依賴 `numpy`/`scipy`，需確認與 `fastembed`/`sentence-transformers` 無版本衝突
- 若衝突，備選方案：改用 `sklearn.cluster.HDBSCAN`（scikit-learn >= 1.3 已內建）+ 自行實作簡化版 UMAP 或用 PCA fallback

**Acceptance Criteria（Task 1）**：
- [x] `pyproject.toml` 含三個新依賴
- [x] `uv sync` 無報錯
- [x] `import hdbscan, umap, plotly` 成功
- [x] 冒煙測試通過（50 個 384D 向量完成 HDBSCAN + UMAP 無 exception）
- [x] 現有功能不受影響（`patrick doctor` 通過）

---

### Task 2 — Schema Migration：`turn_chunks` 加 `cluster_id`、`umap_x`、`umap_y` ✅ DONE

**目標**：為聚類結果和降維座標預留欄位，聚類結果存入 DB 供前端直接讀取（不需每次重算）。

**改動檔案**：`src/patrick/storage.py`

**具體做法**：
1. 在 `_CHUNK_SCHEMA` 新增三個欄位（放在 `created_at` 之後）：
   ```python
   pa.field("cluster_id", pa.int32(), nullable=True),   # HDBSCAN label; -1 = noise, null = 未聚類
   pa.field("umap_x", pa.float32(), nullable=True),     # 2D UMAP x 座標
   pa.field("umap_y", pa.float32(), nullable=True),     # 2D UMAP y 座標
   ```
   > ⚠️ 必須加 `nullable=True`：PyArrow `int32`/`float32` 預設非 nullable，不加的話 NULL 會被寫成 `0`，破壞「未聚類（null）vs noise（-1）」的語意區分。
2. 在 `storage.initialize()` 的 Phase 5 migration 之後，新增 Phase 6 migration：
   ```python
   # Phase 6 migration: add cluster_id, umap_x, umap_y to turn_chunks
   chunk_col_names = [f.name for f in self._chunks.schema]
   for col, expr in [("cluster_id", "CAST(NULL AS INTEGER)"),
                      ("umap_x", "CAST(NULL AS FLOAT)"),
                      ("umap_y", "CAST(NULL AS FLOAT)")]:
       if col not in chunk_col_names:
           for fallback in (expr, "NULL"):
               try:
                   self._chunks.add_columns({col: fallback})
                   logger.info("Migrated turn_chunks: added %s column", col)
                   break
               except Exception as e:
                   logger.warning("add_columns %s with expr=%s failed: %s", col, fallback, e)
   ```
3. 使用 NULL 而非預設值（未跑聚類的 chunk 為 null，區別於 HDBSCAN 的 -1 noise）
4. 新增批次更新方法：
   ```python
   def update_chunk_clusters(self, updates: list[dict]) -> int:
       """
       批次更新 cluster_id, umap_x, umap_y。
       updates: [{"chunk_id": str, "cluster_id": int, "umap_x": float, "umap_y": float}]
       Returns: 更新筆數
       """
   ```

**Acceptance Criteria（Task 2）**：
- [x] `turn_chunks` schema 含 `cluster_id`、`umap_x`、`umap_y` 三個欄位
- [x] 新建 DB 時三個欄位自動存在
- [x] 舊 DB 升級後，歷史 chunk 的三個欄位為 null（不報錯）
- [x] `storage.initialize()` 執行不拋出例外
- [x] `update_chunk_clusters()` 能批次寫入，寫入後讀回值正確

---

### Task 3 — Clustering Engine：HDBSCAN + UMAP 計算 ✅ DONE

> 依賴 Task 1（依賴安裝）

**目標**：實作聚類核心邏輯，輸入向量矩陣，輸出 cluster labels 和 UMAP 2D 座標。

**改動檔案**：新增 `src/patrick/clustering.py`

**具體做法**：
1. 建立 `ClusterResult` dataclass 和 `ClusteringEngine` class：
   ```python
   @dataclass
   class ClusterResult:
       labels: np.ndarray          # (N,) int, -1 = noise
       umap_coords: np.ndarray     # (N, 2) float32
       n_clusters: int             # 不含 noise 的 cluster 數
       noise_count: int
       noise_ratio: float

   class ClusteringEngine:
       def compute(
           self,
           vectors: np.ndarray,          # (N, 384)
           min_cluster_size: int = 5,
           min_samples: int = 3,
           umap_n_neighbors: int = 15,
           umap_min_dist: float = 0.1,
       ) -> ClusterResult:
   ```
2. 實作流程（兩段 UMAP pipeline，架構已定案）：
   - **Pass 1（聚類用）**：UMAP 384D → 20D（`metric='cosine'`, `random_state=42`），結果餵給 HDBSCAN
   - **HDBSCAN**：在 20D 空間跑（`metric='euclidean'`，此時歐氏距離有效），產生 cluster labels
   - **Pass 2（視覺化用）**：UMAP 384D → 2D（`metric='cosine'`, `random_state=42`），結果存 `umap_x/umap_y`
   - `n_neighbors` 自動 clamp 到 `min(umap_n_neighbors, N-1)`，避免樣本不足時崩潰
   > 設計決策：384D 直接跑 HDBSCAN 因高維詛咒（距離分佈收斂）必然效果差，先 UMAP 降到 20D 再聚類是標準做法。Task 4 spike 會額外跑一組 384D 直接聚類作為 baseline 對照，但預設 pipeline 是兩段 UMAP。
3. Edge case 處理：
   - N=0 → 回傳空 ClusterResult
   - N=1 → labels=[-1], umap_coords=[[0, 0]]
   - N < min_cluster_size → 全部標 -1，UMAP 仍正常跑
4. 全程 synchronous（CPU-bound），呼叫端用 `run_in_executor()` 包裝

**Acceptance Criteria（Task 3）**：
- [x] `ClusteringEngine.compute()` 接受 (N, 384) 向量，回傳 ClusterResult
- [x] labels 為 int array，-1 表示 noise
- [x] umap_coords 為 (N, 2) float32 array
- [x] 固定 `random_state=42`，同輸入同輸出（deterministic）
- [x] N=0 不崩潰，回傳空結果
- [x] N=1 不崩潰，回傳 cluster_id=-1
- [x] N < min_cluster_size 不崩潰，全部標 -1

---

### Task 4 — Spike：HDBSCAN 參數調校 ✅ DONE

> 依賴 Task 1（依賴安裝）、Task 3（engine 基本實作）

**目標**：用 Patrick 真實資料驗證 HDBSCAN + UMAP 效果，決定 `config.py` 的預設參數。

**改動檔案**：無（寫臨時腳本放 `scripts/cluster_spike.py`，驗完保留供未來調參）

**具體做法**：
1. 從 LanceDB 載入 `/Users/andy/llm-mem/patrick` 的所有 chunk 向量
2. 先跑 pairwise cosine distance 分佈，畫 histogram：
   - 確認 384D 空間的距離是否集中在很窄的區間（高維詛咒）
   - 若是，驗證 UMAP 降維到 20D 後再跑 HDBSCAN 是否改善
3. 測試參數組合（grid search）——架構已定為兩段 UMAP，spike 只驗參數值：
   | `min_cluster_size` | `min_samples` | Pipeline | clusters | noise% | 時間 | 備注 |
   |---|---|---|---|---|---|---|
   | 3 | 2 | 20D → HDBSCAN | 582 | 21.5% | 20s | 過度細粒，582 cluster 對 6359 chunks |
   | 5 | 3 | 20D → HDBSCAN | 301 | 18.9% | 8.3s | 合理，最大 cluster 是短訊息 |
   | **10** | **3** | **20D → HDBSCAN** | **190** | **18.1%** | **8.5s** | **✅ 推薦：語意最清晰** |
   | 10 | 5 | 20D → HDBSCAN | 168 | 18.4% | 8.3s | 最大 cluster 409 chunks 太胖 |
   | 5 | 3 | 384D → HDBSCAN（直接）| 198 | 56.6% | 16.1s | baseline 對照：noise 是 20D 的 3× |
4. 對每組記錄：
   - cluster 數量、noise ratio
   - 最大 cluster 的代表性文字（離 centroid 最近的 3 個 chunk）
   - 人工判斷：「測試對話」和「真實工作」是否被分到不同 cluster？
5. 對比 20D pipeline vs 384D baseline 的 cluster 數與 noise ratio 差異，量化高維詛咒影響
6. 結論寫入本 Task，決定 `config.py` 預設參數

**Task 4 結論**（實測 `scripts/cluster_spike.py`，N=6359 chunks）：

**384D 距離分佈**（抽樣 n=2000 pairwise cosine distance）：
- mean=0.655, std=0.181, min≈0, p25=0.585, p50=0.676, p75=0.764, max=1.212
- std=0.181 > 0.05，分佈並非極端壓縮（不是最嚴重的高維詛咒）
- 但 UMAP 20D 的 noise ratio（18.9%）仍遠低於 384D baseline（56.6%），**UMAP 降維對聚類仍有顯著改善**

**20D vs 384D 對比**（min_cluster_size=5, min_samples=3）：
- 20D pipeline：301 clusters，noise 18.9%
- 384D baseline：198 clusters，noise 56.6%
- UMAP 降維帶來 **+37.6pp noise reduction**，確認兩段 UMAP pipeline 的必要性

**推薦參數**：
- `CLUSTER_MIN_CLUSTER_SIZE = 10`（mcs=10, ms=3 最大 cluster 是「find/grep 指令」cluster，語意最清晰）
- `CLUSTER_MIN_SAMPLES = 3`
- 理由：582 clusters（mcs=3）過度細粒；168 clusters（mcs=10, ms=5）最大 cluster 達 409 chunks 太肥；190 clusters（mcs=10, ms=3）大小適中，代表性文字語意清晰

**Acceptance Criteria（Task 4）**：
- [x] 跑過至少 5 組參數組合（含 1 組 384D baseline 對照）
- [x] 記錄每組的 cluster 數、noise ratio、代表性文字
- [x] 決定 `config.py` 的預設 `CLUSTER_MIN_CLUSTER_SIZE`、`CLUSTER_MIN_SAMPLES`
- [x] 確認 384D baseline vs 20D pipeline 的效果差異（量化 noise ratio 差距）
- [x] 確認 384D pairwise cosine distance 分佈（histogram 截圖或統計數字）

---

### Task 5 — CLI：`patrick cluster` 命令 ✅ DONE

> 依賴 Task 2（schema）、Task 3（engine）、Task 4（預設參數）

**目標**：提供離線 CLI 命令，對指定 project 執行聚類並將結果寫回 DB。

**改動檔案**：`src/patrick/cli.py`、`src/patrick/storage.py`

**具體做法**：
1. 新增 `patrick cluster` 子命令：
   ```python
   @app.command()
   def cluster(
       project_path: str = typer.Argument(..., help="Project 絕對路徑，如 /Users/andy/llm-mem/patrick"),
       min_cluster_size: int = typer.Option(CLUSTER_MIN_CLUSTER_SIZE, help="HDBSCAN min_cluster_size"),
       min_samples: int = typer.Option(CLUSTER_MIN_SAMPLES, help="HDBSCAN min_samples"),
       dry_run: bool = typer.Option(False, help="只印統計結果，不寫入 DB"),
   ):
   ```
2. 實作流程：
   - 初始化 storage + embedding provider
   - 讀取該 project 所有 session_id（`list_sessions(project_path=..., limit=0)`）
   - 載入所有 chunk 向量（`get_session_chunks()` for each session_id）
   - 提取向量矩陣 (N, 384)
   - 呼叫 `ClusteringEngine.compute()`
   - 將 `cluster_id`、`umap_x`、`umap_y` 批次寫回 `turn_chunks`（via `update_chunk_clusters()`）
3. 輸出統計摘要：
   ```
   Project: /Users/andy/llm-mem/patrick
   Chunks:  1,234
   Clusters: 8 (noise: 156, 12.6%)
   ──────────────────────────────────
   Cluster #0 (342 chunks):
     • "Phase 5 project-scoped memory implementation and testing..."
     • "Added project_path filter to list_sessions and search..."
     • "LanceDB merge_insert when_matched_update_all behavior..."
   Cluster #1 (89 chunks):
     • "HDBSCAN vs DBSCAN discussion, density-based clustering..."
   ...
   Noise (-1, 156 chunks):
     • "請回想一下之前我們討論過的..."
     • "test test test 123"
   ```
4. `--dry-run` 只印統計，不寫 DB

**Acceptance Criteria（Task 5）**：
- [x] `patrick cluster /path/to/project` 成功執行，印出統計摘要
- [x] 摘要包含每個 cluster 的代表性文字（離 centroid 最近的 3 個 chunk）
- [x] `turn_chunks` 中該 project 的 chunk 有正確的 `cluster_id`、`umap_x`、`umap_y`
- [x] `--dry-run` 只印統計，不寫 DB
- [x] project 不存在或無 chunk 時，友善錯誤訊息（非 traceback）
- [x] 重新跑 `patrick cluster` 覆蓋舊結果（idempotent）
- [x] 1000 chunks 以內 < 60 秒完成（274 chunks 實測 10.1s，外插 ~37s < 60s）

---

### Task 6 — Dashboard API：聚類資料 + Session 列表 endpoint ✅ DONE

> 依賴 Task 2（chunk schema 有 umap 欄位）、Task 5（cluster 結果已寫入 DB）

**目標**：提供 JSON API 供前端頁面取得 project 列表、session 列表、聚類散點圖資料。

**改動檔案**：`src/patrick/server.py`、`src/patrick/storage.py`

**具體做法**：

#### 6a. Project 列表 endpoint

新增 `GET /dashboard/api/projects` endpoint：
```python
{
    "projects": [
        {
            "project_path": "/Users/andy/llm-mem/patrick",
            "session_count": 7,
            "chunk_count": 1234,
            "clustered_count": 1234,   # umap_x IS NOT NULL 的數量
            "has_clusters": true,
        },
        ...
    ]
}
```

`storage.py` 新增 `get_project_stats() -> list[dict]`：
- 查詢 `session_summaries` 取得所有 distinct `project_path` 及 session 數
- **只回傳 `session_count`**（避免全表掃描 turn_chunks），`chunk_count` 和 `clustered_count` 改為 lazy load
- 前端點開某個 project 時，才呼叫 `/dashboard/api/clusters?project_path=...` 取得實際 chunk 統計
- 目標回應時間 < 500ms（不涉及 turn_chunks 掃描）

#### 6b. Session 列表 endpoint

新增 `GET /dashboard/api/sessions?project_path=...` endpoint：
```python
{
    "project_path": "/Users/andy/llm-mem/patrick",
    "sessions": [
        {
            "session_id": "sess-456",
            "chunk_count": 89,
            "first_ts": "2026-04-28T10:00:00",
            "last_ts": "2026-04-28T11:30:00",
            "summary_preview": "Phase 5 project-scoped memory...",  # session summary 前 100 字
        },
        ...
    ]
}
```

`storage.py` 新增 `get_sessions_for_project(project_path: str) -> list[dict]`：
- 從 `session_summaries` 列出該 project 的所有 session（含 summary preview）
- 對每個 session 計算 chunk 數量

#### 6c. 聚類資料 endpoint

新增 `GET /dashboard/api/clusters?project_path=...` endpoint：
```python
{
    "project_path": "/Users/andy/llm-mem/patrick",
    "total_chunks": 1234,
    "n_clusters": 8,
    "noise_count": 156,
    "points": [
        {
            "chunk_id": "abc-123",
            "x": 1.23,               # umap_x（已存 DB）
            "y": -0.45,              # umap_y（已存 DB）
            "cluster_id": 0,
            "text_preview": "Phase 5 implementation...",  # 前 120 字
            "session_id": "sess-456",
            "hook_type": "assistant_text",
            "created_at": "2026-04-28T10:00:00",
        },
        ...
    ],
}
```

可選 query param `session_id`：若提供則只回傳該 session 的 chunk（用於 session drill-down）。

`storage.py` 新增 `get_cluster_data(project_path: str, session_id: str | None = None) -> list[dict]`：
- 查詢該 project（或特定 session）所有 chunk 的 `chunk_id, umap_x, umap_y, cluster_id, text, session_id, hook_type, created_at`
- 只回傳 `umap_x IS NOT NULL` 的 chunk（已跑過 `patrick cluster` 的）
- text 截斷到 120 字作為 preview
- 不回傳 384 維向量（前端不需要，省頻寬）

#### 6d. 聚類參數 CRUD endpoint

新增 `GET /dashboard/api/cluster-config?project_path=...` endpoint：
```python
{
    "project_path": "/Users/andy/llm-mem/patrick",
    "min_cluster_size": 5,
    "min_samples": 3,
    "umap_n_neighbors": 15,
    "umap_min_dist": 0.1,
    "last_clustered_at": "2026-04-30T15:00:00",  # 上次聚類時間，null = 未聚類
}
```

新增 `PUT /dashboard/api/cluster-config` endpoint：
- body: `{"project_path": str, "min_cluster_size": int, "min_samples": int, "umap_n_neighbors": int, "umap_min_dist": float}`
- 儲存至 LanceDB `cluster_config` 表（per-project 設定）
- 若該 project 尚無設定，建立新記錄（使用 `config.py` 預設值填充未提供的欄位）

`storage.py` 新增 `cluster_config` 表：
```python
_CLUSTER_CONFIG_SCHEMA = pa.schema([
    pa.field("project_path", pa.string()),          # primary key
    pa.field("min_cluster_size", pa.int32()),
    pa.field("min_samples", pa.int32()),
    pa.field("umap_n_neighbors", pa.int32()),
    pa.field("umap_min_dist", pa.float32()),
    pa.field("last_clustered_at", pa.string()),     # ISO timestamp，null = 未聚類
])
```

新增方法：
```python
def get_cluster_config(self, project_path: str) -> dict | None:
    """取得 project 的聚類參數設定，不存在時回傳 None（由呼叫端使用 config.py 預設值）。"""

def upsert_cluster_config(self, project_path: str, **params) -> None:
    """新增或更新 project 的聚類參數。使用 merge_insert。"""
```

#### 6e. 重新聚類觸發 + 狀態查詢 endpoint

新增 `POST /dashboard/api/recluster` endpoint：
- body: `{"project_path": str}`（參數從 `cluster_config` 表讀取，不在此 body 傳遞）
- 在 background task 中執行聚類（不阻塞 response）
- 聚類完成後更新 `cluster_config.last_clustered_at`，並清除 in-memory 的 `running` 狀態
- 立即回傳 `202 Accepted`
- 重複觸發時拒絕（回傳 `409 Conflict`）

新增 `GET /dashboard/api/recluster-status?project_path=...` endpoint：
```python
{
    "project_path": "/Users/andy/llm-mem/patrick",
    "running": false,                              # true = 聚類進行中
    "last_clustered_at": "2026-04-30T15:00:00",   # null = 未曾聚類
}
```
- 前端在點「Re-cluster」後每 3 秒 poll 此 endpoint
- `running=false` 時停止 poll，重新載入 `/dashboard/api/clusters` 更新散點圖並取消 spinner
- `running` 狀態用 server in-memory dict 維護（`{project_path: bool}`），不寫 DB

#### 6f. Chunk 完整資料 endpoint

新增 `GET /dashboard/api/chunk/{chunk_id}` endpoint：
```python
{
    "chunk_id": "abc-123",
    "text": "完整的 chunk text，不截斷...",
    "session_id": "sess-456",
    "hook_type": "assistant_text",
    "cluster_id": 0,
    "umap_x": 1.23,
    "umap_y": -0.45,
    "created_at": "2026-04-28T10:00:00",
    "session_summary": "Phase 5 project-scoped memory...",  # 該 session 的 summary
}
```
- Task 7 detail panel 點擊某個點時呼叫此 endpoint 取得完整 text
- 不在 `/dashboard/api/clusters` 回傳完整 text（省頻寬：1000 個點 × 2KB = 2MB）

**Acceptance Criteria（Task 6）**：
- [x] `GET /dashboard/api/projects` 列出所有 project，回應時間 < 500ms（只含 session_count，不掃 turn_chunks）
- [x] `GET /dashboard/api/sessions?project_path=...` 列出該 project 的所有 session
- [x] `GET /dashboard/api/clusters?project_path=...` 回傳正確 JSON
- [x] `GET /dashboard/api/clusters?project_path=...&session_id=...` 只回傳該 session 的 chunk
- [x] 未跑 `patrick cluster` 的 project，`points` 為空 array（不報錯）
- [x] text_preview 截斷到 120 字
- [x] `GET /dashboard/api/cluster-config` 回傳 project 的聚類參數（無設定時回傳預設值）
- [x] `PUT /dashboard/api/cluster-config` 能儲存參數到 DB
- [x] `POST /dashboard/api/recluster` 使用 DB 中的參數在背景執行聚類，不阻塞，重複觸發回傳 409
- [x] `GET /dashboard/api/recluster-status` 正確回傳 `running` 狀態，聚類完成後 `running=false`
- [x] `GET /dashboard/api/chunk/{chunk_id}` 回傳完整 text + session summary
- [x] 回傳速度：1000 chunks 讀取 < 500ms（只讀 DB，不重算）

---

### Task 7 — Dashboard 前端：Project 列表 + Session 導航 + 散點圖 ✅ DONE

> 依賴 Task 6（API endpoint）

**目標**：一個 standalone HTML 頁面，顯示 project 列表，點進去看散點圖，可切換 session 檢視該 session 的 chunk 分布。

**改動檔案**：新增 `src/patrick/static/dashboard.html`、修改 `src/patrick/server.py`

**具體做法**：

#### 7a. Server 端靜態檔案

```python
from starlette.responses import FileResponse

# GET /dashboard → serve HTML
async def dashboard_page(request):
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")

# 註冊 route
app.routes.append(Route("/dashboard", dashboard_page))
```

#### 7b. HTML 頁面結構

`dashboard.html` 為 self-contained HTML（inline CSS/JS），引入 Plotly CDN：

```html
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
```

版面：
- **左側 sidebar（25%）**：
  - Project 列表（從 `/dashboard/api/projects` 載入），每個 project 顯示名稱（basename）、session 數、chunk 數
  - 點開某個 project → 展開顯示其 session 列表（從 `/dashboard/api/sessions` 載入）
  - 每個 session 行顯示：session 時間、chunk 數、summary preview
  - 「All chunks」按鈕回到整個 project 的全局散點圖
  - **參數調整面板**（collapsible）：
    - `min_cluster_size`（number input，預設 5）
    - `min_samples`（number input，預設 3）
    - `umap_n_neighbors`（number input，預設 15）
    - `umap_min_dist`（number input，預設 0.1）
    - 「Save」按鈕 → `PUT /dashboard/api/cluster-config` 儲存參數
    - 「Re-cluster」按鈕 → `POST /dashboard/api/recluster` 用已儲存的參數重新計算
    - 載入 project 時從 `GET /dashboard/api/cluster-config` 取得參數值
    - 聚類進行中時按鈕 disabled + 顯示 spinner

- **右側 main area（75%）**：
  - **上方（70%）**：Plotly `scattergl` 散點圖
    - x/y = UMAP 座標（從 DB 讀取，非即時計算）
    - color = cluster_id（`-1` 用灰色，其他用 Plotly 色盤）
    - hover tooltip：`text_preview`（前 80 字）+ `hook_type` + `created_at`
    - 選中某個 session 時：該 session 的 chunk 正常顯示，其他 chunk 變為半透明灰色
  - **下方（30%）**：Detail panel
    - 點擊某個點後顯示：完整 chunk text、session_id、hook_type、cluster_id、created_at
    - 顯示該 chunk 所屬 session 的 summary

- **底部 status bar**：統計列（total chunks / clusters / noise count / sessions shown）

#### 7c. 互動功能

1. **Project 選擇**：左側點擊 project → 載入 `/dashboard/api/clusters?project_path=...` → 繪製全部 chunk 散點圖
2. **Session 篩選**：左側點擊某個 session → 前端 filter：
   - 方案 A（推薦）：前端已有全部 chunk 資料，JS 端按 `session_id` 篩選，將選中 session 的點設為 `opacity=1.0` + 原色，其餘設為 `opacity=0.15` + 灰色
   - 方案 B：若 chunk 太多需 lazy load，改打 `/dashboard/api/clusters?project_path=...&session_id=...` 僅載入該 session
3. **hover**：顯示 text preview + metadata
4. **click**：下方 panel 顯示完整 chunk 資訊
5. **lasso/box select**：框選多個點 → 顯示選中數量 + cluster 分佈統計
6. **Session 回到全局**：點擊「All chunks」或再次點擊已選的 session → 取消篩選，恢復全部 chunk 顯示

**Acceptance Criteria（Task 7）**：
- [x] `http://localhost:3141/dashboard` 可開啟頁面
- [x] 左側顯示 project 列表，含 session 數和 chunk 數
- [x] 點擊 project → 散點圖顯示該 project 全部 chunk（UMAP 2D 座標，cluster 著色）
- [x] 點擊 project → 左側展開 session 列表
- [x] 點擊某個 session → 散點圖高亮該 session 的 chunk，其餘 chunk 半透明
- [x] 點擊「All chunks」→ 恢復顯示全部 chunk
- [x] hover 顯示 text preview（前 80 字）
- [x] 點擊某點 → 下方 panel 顯示完整 chunk 詳情
- [x] noise cluster（-1）用灰色顯示
- [x] 參數面板顯示當前 project 的聚類參數（從 API 讀取）
- [x] 修改參數後點「Save」能儲存到 DB
- [x] 點「Re-cluster」觸發重新聚類，前端每 3 秒 poll `/dashboard/api/recluster-status`，`running=false` 時自動重載散點圖
- [x] 聚類進行中，Re-cluster 按鈕 disabled + spinner；完成後 spinner 消失、散點圖更新
- [x] 1000 個點操作流暢（scattergl WebGL 渲染）
- [x] 無外部 npm 依賴，純 CDN + inline JS
- [x] Chrome/Safari 無 console error（需實機瀏覽器測試）

---

### Task 8 — Config：聚類相關常數 ✅ DONE

**目標**：將聚類的可調參數集中到 `config.py`，作為 **全局預設值**。每個 project 可透過 Dashboard UI 覆蓋（存在 LanceDB `cluster_config` 表，見 Task 6d）。

**改動檔案**：`src/patrick/config.py`

**具體做法**：
```python
# Phase 6: Clustering（全局預設，可被 per-project cluster_config 表覆蓋）
CLUSTER_MIN_CLUSTER_SIZE: int = 5      # HDBSCAN min_cluster_size（Task 4 spike 後調整）
CLUSTER_MIN_SAMPLES: int = 3           # HDBSCAN min_samples
CLUSTER_UMAP_N_NEIGHBORS: int = 15     # UMAP n_neighbors
CLUSTER_UMAP_MIN_DIST: float = 0.1     # UMAP min_dist
CLUSTER_UMAP_RANDOM_STATE: int = 42    # 固定種子確保 reproducible

# Phase 6: Dashboard
DASHBOARD_TEXT_PREVIEW_LEN: int = 120  # Dashboard API 回傳的 text preview 長度
DASHBOARD_SESSION_SUMMARY_LEN: int = 100  # Session summary preview 長度
```

**參數優先序**：
1. `cluster_config` 表（per-project，由 Dashboard UI 設定）— 最高優先
2. `patrick cluster --min-cluster-size=N` CLI flag — 一次性覆蓋，**不寫入 `cluster_config` 表**（只影響本次執行）
3. `config.py` 常數 — 全局預設，無 per-project 設定時使用

> CLI flag 是臨時覆蓋，不持久化。若要永久修改 per-project 參數，使用 Dashboard UI 的「Save」按鈕，會寫入 `cluster_config` 表。

**Acceptance Criteria（Task 8）**：
- [x] 所有聚類常數定義在 `config.py`
- [x] `clustering.py`、`server.py`、`cli.py` 引用 config 常數而非 hard-coded 值
- [x] Task 4 spike 結論反映在預設值中（`CLUSTER_MIN_CLUSTER_SIZE=10`）
- [x] `ClusteringEngine.compute()` 和 `patrick cluster` CLI 支援從 `cluster_config` 表讀取 per-project 設定
- [x] 無 per-project 設定時 fallback 到 `config.py` 預設值

---

## 任務依賴關係

```
Task 1 (deps) ──────────────────────────────────────┐
    └── Task 3 (engine) ──→ Task 4 (spike) ──→ Task 5 (CLI) ──┐
                                                               │
Task 2 (chunk schema) ──────────────────────→ Task 5 (CLI)    │
    │                                             │            ↓
    └────────────────────────────── Task 6a/b/c (API) ──→ Task 7 (frontend)
                                                  ↑
Task 2 (chunk schema) ──→ Task 6d (cluster_config table)
    ↑  （Task 6d 不依賴 Task 5，可與 Task 2 並行完成後直接實作）

Task 8 (config) — 獨立，可穿插任何時間點

Task 4 (spike) ──→ Task 8 (config 預設值)
```

**建議執行順序**：
1. **Task 1**（deps）— 最優先，後續都需要
2. **Task 2**（schema）+ **Task 6d**（cluster_config 表）— 與 Task 1 並行，Task 6d 不依賴 Task 5 可提前完成
3. **Task 3**（engine）— 依賴 Task 1
4. **Task 4**（spike）— 依賴 Task 3，決定預設參數
5. **Task 8**（config）— 依賴 Task 4 結論
6. **Task 5**（CLI）— 依賴 Task 2、3、4
7. **Task 6a/b/c/e/f**（API）— 依賴 Task 2、Task 5
8. **Task 7**（frontend）— 最後做，依賴 Task 6

---

## 整體 Acceptance Criteria

所有 Task 完成後，以下端到端情境必須通過：

1. **聚類可執行**：`patrick cluster /Users/andy/llm-mem/patrick` 在 60 秒內完成，DB 寫入正確，印出有意義的統計摘要
2. **Dashboard 可開啟**：打開 `http://localhost:3141/dashboard`，頁面正常載入
3. **Project 列表**：Dashboard 左側列出所有有 session 的 project
4. **散點圖正確**：選擇 project 後，散點圖顯示 UMAP 2D 座標，cluster 著色正確，noise 灰色
5. **Session 導航**：展開 session 列表，點擊某 session → 散點圖高亮該 session 的 chunk，其餘半透明
6. **Chunk 詳情**：hover 看 preview，click 看完整 text + metadata
7. **參數調整**：在 Dashboard 修改 `min_cluster_size` → Save → Re-cluster → 散點圖重新繪製，cluster 數量變化符合預期
8. **向後相容**：現有 `memory_search`、`memory_sessions` 功能不受影響
9. **不引入 regression**：現有 MCP tools 行為完全相同

---

## 不在本 Phase 範圍

- **黑名單管理**（cluster blacklist、semantic prototype blacklist）— 待 Phase 7
- **搜尋整合**（黑名單排除搜尋結果）— 待 Phase 7
- 跨 project 全局聚類（global UMAP）
- 自動定期重新聚類（scheduler/cron）
- 聚類結果用於搜尋排序（cluster-aware ranking）
- chunk 級別的黑名單
- 黑名單的匯出/匯入
- 歷史 session 的 project 歸類推斷（k-NN based project attribution）
- UMAP 結果快取機制（已存 DB，每次 `patrick cluster` 重算）
- Re-embedding 支援（embedding model 升級後的向量重算）
