# MLB Leaderboards

類似 [Baseball Savant Leaderboards](https://baseballsavant.mlb.com/leaderboard/statcast) 的全端應用，以 FastAPI + React + Tailwind CSS 建構，整合 Yahoo Fantasy 球隊擁有權與真實 Statcast 數據。

## 功能特色

- **即時 Statcast 數據**：一鍵從 Baseball Savant 取得當季真實數據，自動排程每日更新
- **多年度歷史排行榜**：Season 下拉選單可切換歷史年度；每次 refresh 自動寫入 SQLite，完整保存整季指標趨勢
- **Fantasy 異動記錄**：Fantasy 持有異動（pickup / drop / trade）以 event 型態記錄
- **打者指標排行榜**：Exit Velocity、xBA、xSLG、xwOBA、xwOBA-diff、Hard Hit Rate、Barrel Rate、Launch Angle、Sprint Speed（共 9 項）
- **投手指標排行榜**：xERA、ERA−xERA、xwOBA Against、Hard Hit% Against、Barrel% Against、Avg EV Against、K/9、BB/9、K-BB%（共 9 項，低分優先指標自動反轉排序）
- **最低樣本數篩選**：模擬 Savant 的「最低 BBE / PA / 跑壘機會」門檻，排除樣本不足的球員
- **百分位視覺化**：仿照 Savant「紅熱」色階，深紅 = 頂尖、深藍 = 末段（投手低分指標同樣以紅色標示最佳）
- **TTL 快取機制**：`limit` 不納入快取鍵，不同筆數請求共享同一快取，最大化命中率
- **`X-Cache-Hit` Header**：每次 API 回應標示資料來源（計算 vs. 快取），方便在 DevTools 觀察
- **Yahoo Fantasy 整合**：一鍵同步聯盟名單，排行榜顯示每位球員（含投手）的 Fantasy 擁有狀態
- **Target 標記**：百分位 ≥ 90 且未被持有的 Free Agent 自動標記為「Target」，方便發現可簽人選

## 目錄結構

```
MLB_Leaderboards/
├── backend/
│   ├── __init__.py
│   ├── main.py          # FastAPI 應用程式（路由定義）
│   ├── config.py        # pydantic-settings — 所有可設定值
│   ├── cache.py         # TTL 快取
│   ├── fetcher.py       # _blocking_fetch()、_compute_leaderboard()、6 個資料來源
│   ├── adapters.py      # DataSourceAdapter Protocol + PybaseballAdapter
│   ├── scheduler.py     # APScheduler 背景排程
│   ├── db.py            # SQLite 讀寫（stat_snapshots / fantasy_events / players）
│   ├── fantasy.py       # Yahoo Fantasy 同步與事件偵測
│   └── data/
│       ├── .gitkeep             # 確保目錄被 Git 追蹤
│       ├── real_data.json        # pybaseball 抓取後產生（gitignore）
│       ├── fantasy_roster.json  # Fantasy 名單快取（gitignore）
│       └── mlb_history.db       # SQLite 歷史資料庫（gitignore）
├── frontend/
│   ├── package.json
│   ├── vite.config.js       # /api proxy → localhost:8000
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── utils/
│       │   └── format.js        # FORMAT_CONFIG、METRIC_LABELS、formatValue、getPercentileStyle
│       └── components/
│           ├── ErrorBoundary.jsx
│           ├── Leaderboard.jsx      # 狀態管理 + 資料 fetch
│           ├── FilterBar.jsx        # Metric / Season / Limit / MinReq + 按鈕
│           ├── LeaderboardTable.jsx # 表格渲染
│           ├── PercentileLegend.jsx # 色階說明
│           └── SchedulerStatus.jsx  # 排程狀態列
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_leaderboard.py
│   ├── test_fetcher.py
│   ├── test_db.py
│   └── test_utils.py
├── .env                     # 環境變數（YAHOO 憑證，gitignore）
├── main.py                  # 便利啟動腳本
└── pyproject.toml
```

## 快速開始

### 前置需求

- Python ≥ 3.11（使用 [uv](https://github.com/astral-sh/uv) 管理依賴）
- Node.js ≥ 18
- Yahoo Fantasy 聯盟（若要使用 Fantasy 同步功能）

### 後端

```bash
# 安裝依賴
uv sync

# 設定環境變數（Fantasy 同步需要）
# 1. 建立 .env 並填入：
#      YAHOO_LEAGUE_ID=469.l.xxxxxx            ← Yahoo Fantasy 聯盟 ID
#      YAHOO_OAUTH2_PATH=/絕對路徑/oauth2.json  ← Yahoo OAuth2 憑證檔路徑
# 2. 首次執行 yahoo-fantasy-agent 登入以產生 oauth2.json
# 3. Fantasy 名單同步後排程每小時自動更新

# 啟動（APScheduler 與 SQLite 初始化隨服務自動完成）
uv run uvicorn backend.main:app --reload --port 8000

# 排程行為（無需額外設定）：
#   Fantasy 名單同步：每 1 小時自動執行
#   Stats 數據更新：每天 ET 10:00（台灣時間約 22:00），僅 3–10 月球季期間
#   首次使用：仍需手動點擊前端「Refresh Stats」取得初始數據
```

可透過 `.env` 覆寫任何排程設定：

```dotenv
CACHE_TTL=300
STATS_REFRESH_HOUR=10
STATS_REFRESH_MINUTE=0
STATS_REFRESH_MONTHS=3-10
STATS_REFRESH_TZ=America/New_York
FANTASY_SYNC_INTERVAL_HOURS=1
```

### 前端

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### 測試

```bash
uv run pytest tests/ -v   # 131 tests
```

## API 文件

互動式 API 文件由 FastAPI 自動生成：`http://localhost:8000/docs`

### 端點

| Method | Path | 說明 |
|--------|------|------|
| `GET` | `/api/v1/leaderboard` | 排行榜資料（含 Fantasy 擁有權，支援歷史年度） |
| `GET` | `/api/v1/metrics` | 所有可用指標 |
| `GET` | `/api/v1/seasons` | SQLite 中有資料的歷史年度（降冪） |
| `GET` | `/api/v1/cache/stats` | 快取狀態（debug） |
| `DELETE` | `/api/v1/cache` | 手動清空快取 |
| `POST` | `/api/v1/data/refresh` | 從 Baseball Savant 抓取真實 Statcast 數據（背景執行） |
| `GET` | `/api/v1/data/status` | 查詢資料來源與 refresh job 進度 |
| `POST` | `/api/v1/fantasy/sync` | 同步 Yahoo Fantasy 聯盟名單 |
| `GET` | `/api/v1/fantasy/status` | 查詢名單同步狀態 |

### `/api/v1/leaderboard` 參數

| 參數 | 型別 | 預設值 | 說明 |
|------|------|--------|------|
| `metric_name` | string | 必填 | 指標名稱，例如 `exit_velocity` |
| `limit` | int | `500` | 回傳筆數（1–2000） |
| `min_requirement` | int | `5` | 最低樣本數門檻（BBE 或 PA，視指標而定） |
| `year` | int | 不填 | 歷史年度（2015–）；不填時回傳當季即時資料 |

當 `year` 有值時，從 SQLite `stat_snapshots` 讀取最新快照；當 `year` 為空時，從 TTL 快取的 `real_data.json` 讀取。

### `/api/v1/leaderboard` 回應範例

```json
{
  "metric_name": "exit_velocity",
  "limit": 500,
  "min_requirement": 5,
  "year": 2026,
  "count": 423,
  "data": [
    {
      "player_id": "592450",
      "player_name": "Aaron Judge",
      "team": "NYY",
      "position": "RF",
      "avg_value": 96.8,
      "sample_size": 245,
      "sample_type": "BBE",
      "rank": 1,
      "percentile": 100,
      "fantasy_team": "Gerbar's Squad",
      "is_owned": true
    }
  ]
}
```

`sample_type` 說明：`"BBE"`（Batted Ball Events）用於 EV 類指標；`"PA"`（Plate Appearances）用於 xStats 與投手指標；`"sprints"` 用於 Sprint Speed。

### `/api/v1/data/status` 回應範例

```json
{
  "source": "real",
  "season": 2026,
  "fetched_at": "2026-04-08T14:30:00Z",
  "refresh_job": {
    "status": "done",
    "started_at": "2026-04-08T14:29:45Z",
    "error": null
  },
  "scheduler": {
    "stats_refresh": { "next_run": "2026-04-09T14:00:00+0000", "schedule": "Daily ET 10:00, Mar–Oct" },
    "fantasy_sync":  { "next_run": "2026-04-08T15:30:00+0000", "schedule": "Every 1 hour" }
  }
}
```

`refresh_job.status` 狀態流程：`idle` → `processing` → `done` / `error`

### `/api/v1/fantasy/sync` 回應範例

```json
{ "synced_at": "2026-04-08T15:00:00Z", "player_count": 312, "events": 3 }
```

`events` 為本次同步偵測到的持有異動筆數（pickup / drop / trade），寫入 `fantasy_events` 資料表。

## 即時數據架構

### 資料來源

#### 打者指標

| 指標 | 來源 | pybaseball 函式 | CSV 欄位 | sample_type |
|------|------|----------------|----------|-------------|
| Exit Velocity | Baseball Savant | `statcast_batter_exitvelo_barrels(year, minBBE=1)` | `avg_hit_speed` | BBE |
| Launch Angle | Baseball Savant | 同上 | `avg_hit_angle` | BBE |
| Hard Hit Rate | Baseball Savant | 同上 | `ev95percent` | BBE |
| Barrel Rate | Baseball Savant | 同上 | `brl_percent` | BBE |
| xBA | Baseball Savant | `statcast_batter_expected_stats(year, minPA=1)` | `est_ba` | PA |
| xSLG | Baseball Savant | 同上 | `est_slg` | PA |
| xwOBA | Baseball Savant | 同上 | `est_woba` | PA |
| xwOBA-diff | Baseball Savant | 同上 | `woba_minus_xwoba_diff` | PA |
| Sprint Speed | Baseball Savant | `statcast_sprint_speed(year, min_opp=1)` | `sprint_speed` | sprints |

#### 投手指標

| 指標 | 來源 | pybaseball 函式 | CSV 欄位 / 計算式 | sample_type | 排序 |
|------|------|----------------|------------------|-------------|------|
| xERA | Baseball Savant | `statcast_pitcher_expected_stats(year, minPA=1)` | `xera` | PA | **ASC** |
| ERA − xERA | Baseball Savant | 同上 | `era_minus_xera_diff` | PA | DESC |
| xwOBA Against | Baseball Savant | 同上 | `est_woba` | PA | **ASC** |
| Hard Hit% Against | Baseball Savant | `statcast_pitcher_exitvelo_barrels(year, minBBE=1)` | `ev95percent` | BBE | **ASC** |
| Barrel% Against | Baseball Savant | 同上 | `brl_percent` | BBE | **ASC** |
| Avg EV Against | Baseball Savant | 同上 | `avg_hit_speed` | BBE | **ASC** |
| K/9 | Baseball Reference | `pitching_stats_bref(year)` | `SO9` | PA (BF) | DESC |
| BB/9 | Baseball Reference | 同上 | `(BB / IP) * 9` | PA (BF) | **ASC** |
| K-BB% | Baseball Reference | 同上 | `(SO - BB) / BF * 100` | PA (BF) | DESC |

樣本數：EV 類指標使用 `attempts`（BBE 數）；xStats 使用 `pa`（打席數）；Sprint Speed 使用 `competitive_runs`；投手 BRef 指標使用 `BF`（面對打者數）。

名稱欄位：Baseball Savant 回傳單一欄 `'last_name, first_name'`（值如 `"Judge, Aaron"`），後端自動還原為 `"Aaron Judge"`。BRef 投手透過 `mlbID`（即 MLBAM ID）join，不需名稱比對。

> **注意**：`pybaseball.pitching_stats()`（FanGraphs 來源）回傳 HTTP 403，因此 K/9、BB/9、K-BB% 改用 Baseball Reference 來源。

### 資料流

```
前端「Refresh Stats」按鈕（Amber）
        ↓
POST /api/v1/data/refresh?year=2026
→ 立即回傳 {"status": "processing"}
        ↓ [Background Task，約 30–45 秒]
Source 1: statcast_batter_exitvelo_barrels()   ← Baseball Savant (EV/LA/HH/Brl)
Source 2: statcast_batter_expected_stats()     ← Baseball Savant (xBA/xSLG/xwOBA)
Source 3: statcast_sprint_speed()              ← Baseball Savant (Sprint Speed + team/pos)
Source 4: statcast_pitcher_expected_stats()    ← Baseball Savant (xERA/ERA-xERA/xwOBA-against)
Source 5: statcast_pitcher_exitvelo_barrels()  ← Baseball Savant (HH%/Brl%/EV against)
Source 6: pitching_stats_bref()                ← Baseball Reference (K/9, BB/9, K-BB%)
        ↓
join by player_id / mlbID（統一使用 MLBAM ID）
        ↓
os.replace(real_data.json.tmp → real_data.json)  ← 原子寫入
_cache.clear()
_write_stat_snapshot()  ← SQLite append（非致命）
_upsert_players()       ← SQLite upsert 球員元資料（非致命）
        ↓
前端 Polling GET /api/v1/data/status 每 2 秒
→ refresh_job.status == "done" → fetchLeaderboard()
```

### 強健性設計

- **空球季保護**：pybaseball 回傳空 DataFrame 時，拋出明確錯誤訊息（如「球季尚未開始」），不覆蓋現有資料
- **原子寫入**：先寫 `.tmp` 再 `os.replace()`，讀取端永遠不會讀到半寫的 JSON
- **並發保護**：第二次 POST refresh 時若仍在執行中，回傳 HTTP 409
- **非致命 fetch**：Sources 3–6（Sprint Speed、投手三來源）各自包在 `try/except` 內；任一失敗只記錄 warning，不中斷整個 refresh
- **非致命 DB 寫入**：SQLite 寫入失敗只記錄 warning，不影響排行榜功能
- **無數據提示**：`real_data.json` 不存在時回傳 HTTP 404，提示用戶點擊「Refresh Stats」

## Yahoo Fantasy 整合

```
.env（YAHOO_LEAGUE_ID, YAHOO_OAUTH2_PATH）
        ↓
前端「Sync Fantasy」按鈕（Purple）
        ↓
POST /api/v1/fantasy/sync
→ 動態 import yahoo-fantasy-agent/player_list.py（零侵入）
→ login() + get_all_rosters()
        ↓
normalize_name() → 建立 _fantasy_index
→ 寫入 fantasy_roster.json + _cache.clear()
        ↓
排行榜：fantasy_team + is_owned + Target badge（pct ≥ 90 且 Free Agent）
```

**`normalize_name()` 一致性**：後端與 `yahoo-fantasy-agent/player_list.py` 採同源邏輯（去重音、移除 Jr./Sr.、移除標點），確保 Fantasy JOIN 無縫接軌。此函式**僅**用於 Fantasy 擁有權對應；Statcast 兩個資料來源均使用 `player_id`（MLBAM ID）做精確 join，不需要名稱比對。

## 資料結構

### `mlb_history.db`（SQLite）

```sql
-- 球員指標歷史：每次 refresh 全量寫入
stat_snapshots(id, snapshot_at, player_id, metric_name, avg_value, sample_size, sample_type)

-- Fantasy 持有異動：僅記錄有變化的事件
fantasy_events(id, event_at, player_name, match_key, event_type, from_team, to_team)
-- event_type: 'pickup'（FA→隊）| 'drop'（隊→FA）| 'trade'（隊→隊）
-- from_team / to_team: NULL 代表 Free Agent

-- 球員元資料：每次 refresh upsert
players(player_id, player_name, team, position, updated_at)
```

### `real_data.json`

```json
{
  "source": "real",
  "season": 2026,
  "fetched_at": "2026-04-08T14:30:00Z",
  "players": [
    {"player_id": "592450", "player_name": "Aaron Judge", "team": "NYY", "position": "RF"}
  ],
  "aggregates": [
    {"player_id": "592450", "metric_name": "exit_velocity", "avg_value": 96.8, "sample_size": 245, "sample_type": "BBE"},
    {"player_id": "592450", "metric_name": "xba",           "avg_value": 0.312, "sample_size": 380, "sample_type": "PA"}
  ]
}
```

## 快取設計

```
Request 進來
  ↓
cache_key = (metric_name, min_requirement)   ← 不含 limit
  ↓
命中且 age < 300s ──→ 直接回傳（O(1)）
  ↓
未命中 ──→ load_data() + 聚合計算 → 存入 dict → 回傳
  ↓
API 層：result[:limit]  ← 此處才套用 limit
```

`limit=50` 與 `limit=500` 共享同一快取條目。Refresh Stats 與 Sync Fantasy 完成後均呼叫 `_cache.clear()`，強制下次請求重算。

## 百分位色階

仿照 Baseball Savant 的「紅熱 (Red Hot)」視覺邏輯：

| 百分位 | 顏色 | 語意 |
|--------|------|------|
| 90–100 | 深紅 `#d22d2d` | 頂尖 |
| 70–89 | 淺紅 `#e06c6c` | 優秀 |
| 40–69 | 淺灰 `#e8e8e8` | 聯盟平均 |
| 20–39 | 淺藍 `#6ba5d9` | 低於平均 |
| 0–19 | 深藍 `#1a4fa0` | 末段 |

## 未來可擴充方向

- **新增打者指標**：在 `backend/fetcher.py` 的 `_METRIC_NAMES` 與對應 column dict 各加一行，以及前端的 `METRIC_LABELS` + `FORMAT_CONFIG` 即可
- **新增投手指標**：同上，另需確認是否加入 `_ASCENDING_METRICS`（低分=優）；同時更新 `db.py` 的 `_ASCENDING_METRICS` frozenset
- **歷史趨勢 API**：基於現有 `stat_snapshots` 資料表，新增 `GET /api/v1/history/stats?player_id=&metric_name=` 端點回傳整季趨勢
- **Fantasy 異動 API**：基於 `fantasy_events` 資料表，新增 `GET /api/v1/history/fantasy?player_name=` 回傳持有記錄
- **分散式快取**：將 `_cache` dict 替換為 Redis，即可支援多 worker 部署
- **投手 team 欄位**：目前純投手（不在 Sprint Speed 名單內）的 `team` 為空字串；可額外呼叫 Savant 球隊查詢端點補齊
