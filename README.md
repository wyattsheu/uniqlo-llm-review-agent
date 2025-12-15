# UNIQLO 評論洞察 Agent（TW/JP）— State Machine + 動態爬蟲 + LLM（Streamlit UI）

本專案是一個以**狀態機（State Machine）**做 deterministic 控制的 Agent：  
使用者輸入關鍵字與地區（台灣 / 日本），系統會自動搜尋 UNIQLO 商品、抓取**動態**評論頁（CSR），並透過 **LLM** 將評論（含日文）用**中文**摘要與回答提問。  
UI 以 **Streamlit** 提供互動式操作。

---

## 目錄

- [專案動機](#專案動機)
- [功能特色](#功能特色)
- [專案目錄結構](#專案目錄結構)
- [環境需求與安裝](#環境需求與安裝)
- [LLM 模型介紹（三個模型）](#llm-模型介紹三個模型)
- [API Key 與環境變數設定（OLLAMA_API_KEY）](#api-key-與環境變數設定ollama_api_key)
- [LLM 呼叫方式（requests 範例）](#llm-呼叫方式requests-範例)
- [如何執行（Streamlit）](#如何執行streamlit)
- [輸出資料格式（統一 JSON）](#輸出資料格式統一-json)
- [系統架構與執行流程（State Diagram）](#系統架構與執行流程state-diagram)
- [常見問題](#常見問題)
- [使用注意事項](#使用注意事項)

---

## 專案動機

UNIQLO **台灣站**的評論常常「寥寥無幾」，不夠支撐尺寸/版型判斷；  
相對地 **日本站**評論通常更豐富（身形資訊、穿著情境、偏大偏小、材質感受等），但語言門檻高、閱讀耗時。

因此本專案提供一條路徑：

**中文輸入 →（必要時）抓取日文評論 → LLM 用中文統整 → 使用者提問 → LLM 用中文回答**

讓使用者能快速「讀懂日本評論的重點」，不必自己翻譯、慢慢滑。

---

## 功能特色

- **TW/JP 分流**：根據使用者選擇的地區，走不同的搜尋與評論頁路徑（TW/JP 網站結構不同）
- **動態評論頁抓取**：支援 CSR / 需滾動載入的評論頁（scroll / load more）
- **統一輸出格式（LLM-friendly JSON）**：把 TW/JP 的結果整理成相同欄位，便於 LLM 後處理
- **LLM 中文摘要與 QA**：
  - 將評論統整為中文（優缺點、尺寸建議、適合季節/用途等）
  - 讓使用者針對評論內容提問（例如：偏大嗎？會不會透？適合夏天嗎？）

---

## 專案目錄結構

你的 repo 目前結構如下（以此為準）：

```text
├── app.py
├── getComment_jp.py
├── getComment_tw.py
├── uniqlo.png
└── wedSearch.py
```

建議各檔案角色（對照理解）：

- `app.py`：Streamlit UI 入口（收集使用者輸入、顯示結果、呼叫後端函式）
- `wedSearch.py`：搜尋頁處理（組搜尋 URL、渲染/解析商品清單）
- `getComment_tw.py`：台灣站評論抓取
- `getComment_jp.py`：日本站評論抓取
- `uniqlo.png`：UI 圖示/Logo

---

## 環境需求與安裝

- Python：建議 **3.10+**
- 主要套件：
  - `streamlit`（UI）
  - `requests`（呼叫 LLM API）
  - `python-dotenv`（讀取 `.env`）
  - 你的動態爬取依賴（依你實作可能是 Playwright / crawl4ai 等）

範例安裝：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

> 若你的爬蟲依賴 Playwright，請記得再安裝瀏覽器核心：  
> `playwright install chromium`

---

## LLM 模型介紹（三個模型）

本專案支援以下三個模型（你目前的三個選項）：

### 1) `gpt-oss:120b`

- **定位**：最強、最穩的主力模型（偏向高品質推理與摘要）
- **優點**：
  - 中文摘要更完整、更會抓「尺寸/版型」線索
  - 問答通常更準（尤其是評論內容很長或很雜時）
- **代價**：
  - 較慢、資源需求最高（RAM/VRAM、CPU/GPU 負載更高）
- **推薦用途**：最後產出、需要高品質總結與嚴謹 QA

### 2) `gpt-oss:20b`

- **定位**：品質與速度的平衡款
- **優點**：
  - 多數摘要與 QA 已足夠好
  - 明顯比 120b 快、硬體壓力更小
- **推薦用途**：一般使用、日常查評論、Demo 展示（穩定 + 不太慢）

### 3) `gemma3:4b`

- **定位**：輕量、快速、成本最低
- **優點**：
  - 速度快、硬體需求低
  - 適合做「快速預覽」或 UI 即時互動
- **限制**：
  - 長文本摘要可能較不完整
  - 對細節推理（偏大偏小的綜合判斷）可能較弱
- **推薦用途**：先看大方向、或在硬體有限的環境跑 UI

> ⚠️ 注意：實際速度/品質也會受到你的硬體與模型量化設定影響。  
> 建議你在 UI 提供模型下拉選單：預設 `gpt-oss:20b`，需要更準再切 `gpt-oss:120b`，要快就切 `gemma3:4b`。

---

## API Key 與環境變數設定（OLLAMA_API_KEY）

你希望程式可用以下方式讀取 Key：

```python
API_KEY = os.getenv("OLLAMA_API_KEY")
```

因此建議使用 `.env` 管理環境變數（**不要**把 `.env` commit 到 GitHub）。

### 1) 建立 `.env.example`（要放進 repo）

```env
# LLM Gateway / Ollama API
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=
OLLAMA_MODEL=gpt-oss:20b
```

### 2) 建立 `.env`（不要 commit）

把 `.env.example` 複製成 `.env` 後填入 key：

```bash
# macOS/Linux
cp .env.example .env
# Windows PowerShell
copy .env.example .env
```

`.env` 範例：

```env
OLLAMA_BASE_URL=https://<YOUR_BASE>
OLLAMA_API_KEY=<YOUR_API_KEY>
OLLAMA_MODEL=gpt-oss:20b
```

### 3) 在程式中載入 `.env`

在 `app.py` 或你的 LLM client 檔案中加上：

```python
from dotenv import load_dotenv
load_dotenv()
```

---

## LLM 呼叫方式（requests 範例）

你的 LLM 呼叫方式如下（依你提供的 code）：

```python
import os
import requests

BASE = os.getenv("OLLAMA_BASE_URL")
API_KEY = os.getenv("OLLAMA_API_KEY")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")

def call_llm(prompt: str) -> str:
    url = f"{BASE}/generate"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
    }
    resp = requests.post(url, headers=headers, json=data, timeout=120)
    resp.raise_for_status()
    # 依你的 gateway 回傳格式調整
    return resp.json().get("response", "")
```

---

## 如何執行（Streamlit）

本專案 UI 是 Streamlit，因此執行方式是：

```bash
streamlit run app.py
```

啟動後在瀏覽器中：

1. 選擇地區（TW / JP）
2. 輸入關鍵字（中文或日文）
3. 選擇模型（120b / 20b / 4b）
4. 點擊搜尋 → 顯示商品與評論摘要
5. 輸入問題 → 取得 LLM 回答

---

## 輸出資料格式（統一 JSON）

> 重要：TW/JP 統一 schema，後續丟給 LLM 比較穩。

範例（簡化）：

```json
{
  "meta": {
    "region": "jp",
    "keyword": "ジーンズ",
    "model": "gpt-oss:20b"
  },
  "products": [
    {
      "product_code": "E464918-000",
      "product_url": "https://www.uniqlo.com/jp/ja/products/E464918-000/00",
      "review_url": "https://www.uniqlo.com/jp/ja/products/E464918-000/00/reviews"
    }
  ],
  "reviews": [
    {
      "rating": 5,
      "title": "履き心地が良い",
      "body": "…",
      "date": "2025-xx-xx"
    }
  ],
  "llm": {
    "summary_zh": "…中文統整…",
    "qa_zh": "…針對使用者問題回答…"
  }
}
```

---

# 系統架構與執行流程（State Diagram）

本專案的核心是「**狀態機控制流程**」，而不是把流程寫在 prompt 裡。  
LLM 只在特定狀態（摘要、QA）被呼叫，其餘流程（搜尋、渲染、解析、錯誤處理）都由程式 deterministic 決定下一步。

![alt text](state.png)

## 常見問題

- **Q: 為什麼一定要分 TW/JP？**  
  A: 因為兩邊的評論量與頁面結構不同；JP 通常更豐富，TW 有些商品評論很少。分流能提高可用性與準確性。

- **Q: LLM 失敗會怎樣？**  
  A: 建議做 fallback：至少把「爬到的評論原文/結構化資料」先顯示，並提示使用者檢查 `OLLAMA_API_KEY` 或 API 服務狀態。

- **Q: 網站改版抓不到怎麼辦？**  
  A: 更新 `wedSearch.py`、`getComment_tw.py`、`getComment_jp.py` 中的 selector 與解析規則。

---

## 使用注意事項

- 建議限制抓取頻率，避免對網站造成負擔
- 僅供課程/學術用途示範
- `.env` 請勿上傳 GitHub（務必放入 `.gitignore`）
