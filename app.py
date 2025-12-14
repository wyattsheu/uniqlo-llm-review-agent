# app.py
import os
import json
import asyncio
import re
from collections import Counter
from typing import Dict, Any, List
import base64
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    Image = None

import requests
import pandas as pd
import streamlit as st

# 你的三個模組
from wedSearch import search_uniqlo
from getComment_tw import scrape_uniqlo_reviews_tw
from getComment_jp import scrape_uniqlo_reviews_jp

# ============================================================
# 0. LLM 呼叫工具（照你提供的版本，加錯誤處理）
# ============================================================

BASE = "https://api-gateway.netdb.csie.ncku.edu.tw/api"
API_KEY = os.getenv("OLLAMA_API_KEY")
MODEL = "gpt-oss:20b"  # 你要換 gemma3:4b 也行


def llm(prompt: str) -> str:
    """呼叫後端 LLM，回傳純文字；失敗時回錯誤訊息字串。"""
    if not API_KEY:
        return "⚠️ OLLAMA_API_KEY 沒有設定，請先在系統環境變數中設定。"

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

    try:
        # ⏱ 把 timeout 拉到 600 秒（10 分鐘）
        resp = requests.post(url, headers=headers, json=data, timeout=600)
    except requests.RequestException as e:
        return f"⚠️ 呼叫 LLM 失敗：{e}"

    if not resp.ok:
        return f"⚠️ LLM 回傳錯誤：HTTP {resp.status_code} - {resp.text}"

    try:
        data = resp.json()
    except ValueError:
        return f"⚠️ LLM 回傳內容不是合法 JSON：{resp.text}"

    return (data.get("response") or "").strip() or "⚠️ LLM 沒有回傳內容"


# ============================================================
# 1. 共用小工具 & 版面風格
# ============================================================


def run_async(coro):
    """在 Streamlit 這種同步環境安全地執行 async 函式。"""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # 假如有 event loop 已存在，就自己開一個
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)


def init_session_state():
    s = st.session_state
    s.setdefault("page", "home")  # home / results / detail
    s.setdefault("search_keyword", "")
    s.setdefault("search_region", "tw")
    s.setdefault("search_result", None)  # SearchResult
    s.setdefault("selected_index", None)  # int
    s.setdefault("review_data", None)  # 統一格式評論 dict
    s.setdefault("summaries", None)  # LLM 整體總結
    s.setdefault("age_stats", None)  # Counter
    s.setdefault("region_stats", None)  # Counter
    s.setdefault("age_fallback_text", "")
    s.setdefault("size_suggestion_text", "")
    s.setdefault("qa_history", [])  # [{q,a},...]
    s.setdefault("review_depth", "標準")  # 評論抓取深度
    s.setdefault("_logo_data_uri", None)


def get_logo_data_uri() -> str:
    """將同資料夾的 uniqlo.png 轉為 data URI，供 HTML img 使用。"""
    if st.session_state.get("_logo_data_uri"):
        return st.session_state["_logo_data_uri"]
    # 取得當前檔案資料夾，拼接圖檔路徑
    here = os.path.dirname(__file__)
    path = os.path.join(here, "uniqlo.png")
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        b = f.read()
    b64 = base64.b64encode(b).decode("ascii")
    uri = f"data:image/png;base64,{b64}"
    st.session_state["_logo_data_uri"] = uri
    return uri


def apply_style():
    """全站 CSS：米色背景、深色文字、卡片式區塊、美化按鈕。"""
    st.markdown(
        """
        <style>
        :root {
            /* 秋季大地色系 */
            --bg: #f4ecdf;              /* 背景米色 */
            --text: #2a1f1a;            /* 主要深棕文字 */
            --muted: #6f5b4f;           /* 輔助文字 */
            --card: #fbf4ea;            /* 卡片背景（非純白）*/
            --card-border: #e6d5c3;     /* 卡片邊框 */
            --accent: #a25b39;          /* 連結/重點：赭色 */
            --accent-strong: #8c4a2f;   /* 深赭色 */
            --accent-soft: #d9a441;     /* 芥末黃 */
            --focus: #b8874a;           /* 聚焦邊界 */
            --input: #f5ede3;           /* 輸入底色 */
            --shadow: rgba(58, 39, 24, 0.08);
        }

        /* 整體背景與字體顏色（全域預設深色文字，避免白字） */
        .stApp { background-color: var(--bg); color: var(--text); }
        .stApp, .stApp * { color: var(--text) !important; }

        /* 主要內容容器間距 */
        .main .block-container { padding-top: 1.2rem; padding-bottom: 1.8rem; }

        /* 卡片風格容器（移除邊框與陰影，不要框框） */
        .uq-card {
            background-color: transparent; /* 去掉卡片底色，避免像輸入框 */
            border-radius: 14px;
            padding: 1.1rem 1.3rem;
            border: none; /* 移除邊框 */
            box-shadow: none; /* 移除陰影 */
            margin-bottom: 1rem;
        }

        /* 區塊標題：深棕，避免白字 */
        .uq-section-title { font-weight: 700; font-size: 1.1rem; margin-bottom: 0.4rem; color: #5a3d2b; }

        /* 按鈕：棕赭漸層（強制深色文字，完全不使用白字） */
        .stButton>button {
            background: linear-gradient(135deg, #8c5a3c, #c46b3b);
            color: var(--text) !important;
            border-radius: 999px;
            border: 1px solid var(--card-border);
            padding: 0.48rem 1.2rem;
            font-weight: 600;
        }
        .stButton>button:hover { background: linear-gradient(135deg, #75462e, #a6542e); color: var(--text) !important; }

        /* 連結顏色：赭色系 */
        a { color: var(--accent); }
        a:hover { color: var(--accent-strong); text-decoration: underline; }

        /* 輸入、選擇等元件：去白框、提升可讀性與焦點態 */
        input[type="text"], input[type="number"], textarea, select {
            background-color: var(--input) !important;
            color: var(--text) !important;
            border: 1px solid var(--card-border) !important;
            border-radius: 10px !important;
            box-shadow: none !important;
            color-scheme: light !important; /* 避免系統 dark 導致黑底 */
            caret-color: var(--text) !important;
        }
        input[type="text"]:focus, input[type="number"]:focus, textarea:focus, select:focus {
            outline: none !important;
            border-color: var(--focus) !important;
            box-shadow: 0 0 0 2px rgba(184, 135, 74, 0.18) !important;
            background-color: var(--input) !important; /* 聚焦也維持淺底 */
        }

        /* Placeholder 顏色修正：確保不會是白字（涵蓋主要瀏覽器前綴） */
        .stTextInput input::placeholder,
        input::placeholder,
        textarea::placeholder { color: #756355 !important; opacity: 1 !important; }
        .stTextInput input::-webkit-input-placeholder,
        input::-webkit-input-placeholder,
        textarea::-webkit-input-placeholder { color: #756355 !important; opacity: 1 !important; }
        .stTextInput input::-moz-placeholder,
        input::-moz-placeholder,
        textarea::-moz-placeholder { color: #756355 !important; opacity: 1 !important; }
        .stTextInput input:-ms-input-placeholder,
        input:-ms-input-placeholder,
        textarea:-ms-input-placeholder { color: #756355 !important; opacity: 1 !important; }
        .stTextInput input::-ms-input-placeholder,
        input::-ms-input-placeholder,
        textarea::-ms-input-placeholder { color: #756355 !important; opacity: 1 !important; }

        /* 數字輸入（st.number_input）加減按鈕：統一大地色 */
        .stNumberInput button,
        .stNumberInput [role="button"] {
            background: linear-gradient(135deg, #8c5a3c, #c46b3b) !important;
            color: var(--text) !important;
            border: 1px solid var(--card-border) !important;
            border-radius: 8px !important;
            box-shadow: none !important;
        }
        .stNumberInput button:hover,
        .stNumberInput [role="button"]:hover {
            background: linear-gradient(135deg, #75462e, #a6542e) !important;
            color: var(--text) !important;
        }
        /* 覆蓋原生 WebKit spin 按鈕避免黑感；若仍顯示則調淡 */
        input[type=number]::-webkit-outer-spin-button,
        input[type=number]::-webkit-inner-spin-button {
            -webkit-appearance: none;
            margin: 0;
            filter: grayscale(60%) brightness(1.1);
        }
        input[type=number] { -moz-appearance: textfield; } /* Firefox 隱藏 spin */

        /* Select 與 BaseWeb Select（Streamlit selectbox/multiselect）配色統一 */
        select { background-color: var(--input) !important; color: var(--text) !important; }
        [data-baseweb="select"] > div {
            background-color: var(--input) !important;
            color: var(--text) !important;
            border-color: var(--card-border) !important;
            border-radius: 10px !important;
        }
        /* 下拉清單本體 */
        [data-baseweb="select"] div[role="listbox"] {
            background-color: var(--card) !important;
            color: var(--text) !important;
            border: 1px solid var(--card-border) !important;
            box-shadow: 0 8px 24px var(--shadow) !important;
        }
        /* 選項 hover/選取顏色：淺棕底，避免黑藍 */
        [data-baseweb="select"] div[role="option"]:hover {
            background-color: #f0e6d9 !important;
            color: var(--text) !important;
        }
        [data-baseweb="select"] div[role="option"][aria-selected="true"],
        [data-baseweb="select"] div[role="option"][data-focus-visible-added] {
            background-color: #efe3d4 !important;
            color: var(--text) !important;
        }
        /* Focus 外框：改為主題色 */
        [data-baseweb="select"] > div:focus-within {
            box-shadow: 0 0 0 2px rgba(184, 135, 74, 0.18) !important;
            border-color: var(--focus) !important;
        }

        /* Spinner 樣式：降低白感、改為赭棕色轉圈並加微暗底色 */
        [data-testid="stSpinner"] {
            background-color: rgba(42, 31, 26, 0.08); /* 淺淺的深色底 */
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 0.6rem 0.9rem;
            box-shadow: 0 4px 16px var(--shadow);
            color: var(--text) !important; /* 文字與圖示承襲深色 */
        }
        /* 新版 spinner 多為 svg，以 color 控制筆畫顏色 */
        [data-testid="stSpinner"] svg { color: #8c5a3c !important; }
        /* 兼容舊版以邊框作旋轉的實作（確保頂部為赭棕色） */
        .stSpinner > div {
            border-color: #d4c4b3 !important;
            border-top-color: #8c5a3c !important;
        }

        /* Radio / Select 的 label 顏色（深棕） */
        label, .stRadio label, .stSelectbox label { color: #3b2a21 !important; }

        /* caption / 小字：柔和棕色 */
        .stCaption, .stMarkdown small { color: var(--muted); }

        /* code 字串：暖色底、深棕字 */
        code { background-color: #efe3d4; color: #5a3c26; }

        /* 分隔線更柔和 */
        hr { border: none; border-top: 1px solid var(--card-border); }

        /* 頁首含品牌 Logo 與標題 */
        .uq-header {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.6rem 0.4rem 1rem 0.4rem;
        }
        .uq-header img {
            width: 36px; height: 36px;
            border-radius: 6px;
            box-shadow: 0 2px 8px var(--shadow);
        }
        .uq-header .title {
            font-size: 2.2rem;
            font-weight: 700;
            color: var(--text);
            letter-spacing: 0.02em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def unify_tw(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "site": raw.get("site", "uniqlo_tw"),
        "product_url": raw.get("product_url"),
        "reviews": raw.get("reviews", []),
        "review_count": raw.get("review_count", len(raw.get("reviews", []))),
        "raw_markdown": None,
    }


def unify_jp(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "site": raw.get("site", "uniqlo_jp"),
        "product_url": raw.get("product_url"),
        # 直接保留解析好的評論，供前端顯示與統計
        "reviews": raw.get("reviews", []),
        "review_count": raw.get("review_count", len(raw.get("reviews", []))),
        "raw_markdown": raw.get("_raw_markdown", ""),
    }


# ============================================================
# 2. 從 TW/JP 結構化評論抽年齡、地區
# ============================================================

AGE_RANGE_PATTERN = re.compile(r"(\d{2})\s*[~〜\-]\s*(\d{2})\s*歲")
AGE_SINGLE_PATTERN = re.compile(r"(\d{2})\s*歲")


def extract_age_buckets_tw(reviews: List[Dict[str, Any]]) -> Counter:
    buckets = Counter()
    for r in reviews:
        user_info = (r.get("user_info") or "").replace(" ", "")
        if not user_info:
            continue

        m = AGE_RANGE_PATTERN.search(user_info)
        if m:
            bucket = f"{m.group(1)}-{m.group(2)}"
            buckets[bucket] += 1
            continue

        m = AGE_SINGLE_PATTERN.search(user_info)
        if m:
            age = int(m.group(1))
            low = (age // 10) * 10
            high = low + 9
            bucket = f"{low}-{high}"
            buckets[bucket] += 1

    return buckets


def extract_region_buckets_tw(reviews: List[Dict[str, Any]]) -> Counter:
    buckets = Counter()
    REGION_PATTERN = re.compile(r"([^\s·、]+?[縣市])")
    for r in reviews:
        user_info = r.get("user_info") or ""
        m = REGION_PATTERN.search(user_info)
        if m:
            buckets[m.group(1)] += 1
    return buckets


def extract_age_buckets_jp(reviews: List[Dict[str, Any]]) -> Counter:
    """從日本評論中提取年齡統計

    日本評論的年齡格式：
    - 月齡：0 - 6ヶ月、7 - 12ヶ月、13 - 24ヶ月
    - 幼兒：2 - 3歳、4 - 6歳、7 - 9歳
    - 青少年：10 - 14歳、15 - 19歳
    - 成人：20代、30代、40代、50代、60代以上
    """
    buckets = Counter()

    # 日本年齡格式的 Regex 模式
    # 匹配「60代以上」特殊格式
    JP_AGE_60_PLUS = re.compile(r"60代以上")
    # 匹配「20代」、「30代」等年代格式
    JP_DECADE_PATTERN = re.compile(r"(\d{1,2})代")
    # 匹配「0 - 6ヶ月」、「7 - 12ヶ月」等月齡格式（注意有空格）
    JP_MONTH_PATTERN = re.compile(r"(\d+)\s*[-\-]\s*(\d+)\s*[ヶケが]月")
    # 匹配「2 - 3歳」、「4 - 6歳」等歲數區間格式
    JP_AGE_RANGE_PATTERN = re.compile(r"(\d+)\s*[-\-]\s*(\d+)\s*歳")
    # 匹配單一年齡「25歳」（作為後備）
    JP_SINGLE_AGE_PATTERN = re.compile(r"(\d{1,2})歳")

    for r in reviews:
        user_info = r.get("user_info") or ""
        if not user_info:
            continue

        # 1. 優先匹配「60代以上」
        if JP_AGE_60_PLUS.search(user_info):
            buckets["60代以上"] += 1
            continue

        # 2. 匹配「20代」、「30代」等年代格式
        m = JP_DECADE_PATTERN.search(user_info)
        if m:
            decade = m.group(1)
            bucket = f"{decade}代"
            buckets[bucket] += 1
            continue

        # 3. 匹配月齡格式「0 - 6ヶ月」、「7 - 12ヶ月」、「13 - 24ヶ月」
        m = JP_MONTH_PATTERN.search(user_info)
        if m:
            start = m.group(1)
            end = m.group(2)
            bucket = f"{start} - {end}ヶ月"
            buckets[bucket] += 1
            continue

        # 4. 匹配歲數區間「2 - 3歳」、「4 - 6歳」、「7 - 9歳」等
        m = JP_AGE_RANGE_PATTERN.search(user_info)
        if m:
            start = m.group(1)
            end = m.group(2)
            bucket = f"{start} - {end}歳"
            buckets[bucket] += 1
            continue

        # 5. 匹配單一年齡（後備方案，將其歸類到對應區間）
        m = JP_SINGLE_AGE_PATTERN.search(user_info)
        if m:
            age = int(m.group(1))
            # 根據年齡分組到對應區間
            if age < 2:
                bucket = "0 - 6ヶ月"  # 預設嬰兒
            elif age < 4:
                bucket = "2 - 3歳"
            elif age < 7:
                bucket = "4 - 6歳"
            elif age < 10:
                bucket = "7 - 9歳"
            elif age < 15:
                bucket = "10 - 14歳"
            elif age < 20:
                bucket = "15 - 19歳"
            elif age < 30:
                bucket = "20代"
            elif age < 40:
                bucket = "30代"
            elif age < 50:
                bucket = "40代"
            elif age < 60:
                bucket = "50代"
            else:
                bucket = "60代以上"
            buckets[bucket] += 1

            print(
                f"[JP][age] processed {len(reviews)} reviews, buckets={dict(buckets)}"
            )
    return buckets


def extract_region_buckets_jp(reviews: List[Dict[str, Any]]) -> Counter:
    """從日本評論中提取地區統計

    從 user_info 中提取都道府縣資訊
    資料格式範例：
    - "ひろ(東京)男性60代以上体重: 76 - 80kg東京都* 役に立った 2"
    - "たけはる男性60代以上身長: 171 - 175cm体重: 61 - 65kg足のサイズ: 27.0cm岐阜県* 役に立った 0"

    地區資訊通常在「* 役に立った」之前
    """
    buckets = Counter()

    # 日本47都道府縣模式
    # 匹配格式：XXX[都/道/府/県]，且後面通常跟著「*」或「* 役に立った」
    JP_REGION_PATTERN = re.compile(r"([^\s、。！？\d]+?[都道府県])(?:\s*\*|\s*$)")

    for r in reviews:
        user_info = r.get("user_info") or ""
        if not user_info:
            continue

        # 尋找所有匹配的都道府縣
        matches = JP_REGION_PATTERN.findall(user_info)

        # 如果找到多個，優先取最後一個（最接近「* 役に立った」的）
        if matches:
            region = matches[-1]
            buckets[region] += 1

    print(f"[JP][region] processed {len(reviews)} reviews, buckets={dict(buckets)}")
    return buckets


# ============================================================
# 3. LLM 分析工具
# ============================================================


def generate_summaries(
    review_data: Dict[str, Any], product_name: str
) -> Dict[str, str]:
    site = review_data.get("site", "")
    reviews = review_data.get("reviews", [])
    raw_md = review_data.get("raw_markdown", "")

    MAX_REVIEWS = 80
    if site == "uniqlo_tw" and reviews:
        sample = reviews[:MAX_REVIEWS]
        reviews_text = json.dumps(sample, ensure_ascii=False, indent=2)
        source_desc = "以下是台灣 UNIQLO 網站的結構化評論 JSON。"
    else:
        reviews_text = raw_md[:15000]
        source_desc = "以下是 UNIQLO 評論頁面的 Markdown 節錄。"

    prompt = f"""
你是一位專門分析 UNIQLO 商品評論的助理，請「用繁體中文」整理重點。注意資料可能很長，不要忘記請依照下面五個段落輸出。

商品名稱：{product_name}

{source_desc}

請依照下面五個段落輸出，使用清楚的小標題與條列式描述：
一、正面評價重點
二、負面評價重點
三、顏色與實際觀感相關的評論（如：色差、材質觀感）
四、尺寸、版型與穿著感受（偏大/偏小、肩寬、版型特色）
五、主要客群與使用情境（年齡層、典型使用者、常見場景）

規則：
- 嚴禁輸出 JSON；用清楚段落即可。
- 不要捏造精確百分比或不存在的資訊。
- 若資料不足或沒有相關評論，請明確說「資料不足」或「沒有相對應評論」。
- 務必使用繁體中文作為回答語言

以下是原始資料：
--------------------
{reviews_text}
--------------------
"""

    text = llm(prompt)
    return {"full_text": text}


def generate_age_fallback_summary(
    review_data: Dict[str, Any], product_name: str
) -> str:
    site = review_data.get("site", "")
    reviews = review_data.get("reviews", [])
    raw_md = review_data.get("raw_markdown", "")

    if site == "uniqlo_tw" and reviews:
        reviews_text = json.dumps(reviews[:80], ensure_ascii=False, indent=2)
        source_desc = "以下是台灣 UNIQLO 的結構化評論 JSON。"
    else:
        reviews_text = raw_md[:15000]
        source_desc = "以下是 UNIQLO 評論頁面的 Markdown 節錄。"

    prompt = f"""
請根據以下資料，簡要推估購買這件商品的年齡層與地區分佈，務必「用繁體中文」回答。資料考能很長，記得推估購買這件商品的年齡層與地區分佈。

商品名稱：{product_name}

{source_desc}

回答時：
1. 用文字描述主要年齡層（例如：20–30 歲居多）。
2. 若看得出地區差異，也可以附帶描述。
3. 資料不足或沒有相關評論時請直接說明。

規則：
- 嚴禁輸出 JSON；用清楚段落即可。
- 務必使用繁體中文作為回答語言

資料如下：
--------------------
{reviews_text}
--------------------
"""
    return llm(prompt)


def generate_size_suggestion(
    review_data: Dict[str, Any],
    product_name: str,
    height_cm: float,
    weight_kg: float,
    fit_preference: str,
) -> str:
    site = review_data.get("site", "")
    reviews = review_data.get("reviews", [])
    raw_md = review_data.get("raw_markdown", "")

    if site == "uniqlo_tw" and reviews:
        reviews_text = json.dumps(reviews[:80], ensure_ascii=False, indent=2)
        source_desc = "以下是台灣 UNIQLO 的結構化評論 JSON。"
    else:
        reviews_text = raw_md[:15000]
        source_desc = "以下是 UNIQLO 評論頁面的 Markdown 節錄。"

    prompt = f"""
你是一位 UNIQLO 店員，需「用繁體中文」根據評論資料提供尺寸建議。注意以下資料可能會很長，不要忘記根據評論資料提供尺寸建議。

商品名稱：{product_name}
顧客身高：{height_cm} cm
顧客體重：{weight_kg} kg
顧客期望版型：{fit_preference}（例如：合身 / 稍微寬鬆 / 寬鬆）

{source_desc}

請回答：
1. 建議尺寸（若有日本 / 台灣尺碼差異，請註明）。
2. 為什麼選這個尺寸（例如：此商品偏大、偏小、肩寬較窄…）。
3. 如果資料不足或身材在評論裡較少見，請坦白說明並給保守建議。
規則：
- 嚴禁輸出 JSON；用清楚段落即可。
- 務必使用繁體中文作為回答語言

評論資料如下：
--------------------
{reviews_text}
--------------------
"""
    return llm(prompt)


def generate_qa_answer(
    review_data: Dict[str, Any], product_name: str, question: str
) -> str:
    site = review_data.get("site", "")
    reviews = review_data.get("reviews", [])
    raw_md = review_data.get("raw_markdown", "")

    if site == "uniqlo_tw" and reviews:
        reviews_text = json.dumps(reviews[:60], ensure_ascii=False, indent=2)
        source_desc = "以下是台灣 UNIQLO 的部分結構化評論 JSON"
    else:
        reviews_text = raw_md[:10000]
        source_desc = "以下是日本 UNIQLO 評論頁整頁內容的 Markdown 節錄。"

    prompt = f"""
你是一位熟悉 UNIQLO 商品的穿搭顧問，請以「繁體中文」根據實際評論內容回答問題。注意以下資料可能會很長，不要忘記使用者要問的問題；若無相關資訊請如實說明並建議實體試穿。

商品名稱：{product_name}
使用者問題：{question}

{source_desc}

請用自然的繁體中文回答；若評論中沒有相關資訊，請直接說明並建議實體試穿。

資料如下：
--------------------
{reviews_text}
--------------------
"""
    return llm(prompt)


# ============================================================
# 4. Streamlit 頁面
# ============================================================


def page_home():
    # 頁首：品牌 Logo + 標題（以 data URI 確保在任何工作目錄都能顯示）
    logo_uri = get_logo_data_uri()
    img_tag = f'<img src="{logo_uri}" alt="UNIQLO" />' if logo_uri else ""
    st.markdown(
        f'<div class="uq-header">{img_tag}<div class="title">Uniqlo 評論導航器</div></div>',
        unsafe_allow_html=True,
    )

    # --- 搜尋區：裝進卡片 ---
    st.markdown('<div class="uq-card">', unsafe_allow_html=True)
    st.markdown('<div class="uq-section-title">搜尋商品</div>', unsafe_allow_html=True)
    st.write("輸入商品名稱或代碼，選擇地區後按下搜尋。")

    st.session_state.search_keyword = st.text_input(
        "商品關鍵字 / 代碼",
        value=st.session_state.search_keyword,
        placeholder="例：襯衫、牛仔褲、E464918...",
    )

    st.session_state.search_region = st.radio(
        "資料來源地區",
        options=["tw", "jp"],
        format_func=lambda x: "台灣官網" if x == "tw" else "日本官網",
        horizontal=True,
    )

    depth = st.radio(
        "搜尋速度",
        options=["快速", "標準", "完整"],
        index=1,
        horizontal=True,
        help="控制搜尋逾時上限：快速(10s) / 標準(15s) / 完整(20s)",
    )
    timeout_map = {"快速": 2, "標準": 5, "完整": 10}
    search_timeout_seconds = timeout_map.get(depth, 15)
    # 商品數量維持中等預設，若需再加一組選項可另行提供
    max_products = 30

    if st.button("🔍 開始搜尋"):
        keyword = st.session_state.search_keyword.strip()
        if not keyword:
            st.warning("請先輸入關鍵字或商品代碼。")
        else:
            with st.spinner("正在搜尋 UNIQLO 商品，請稍候..."):
                try:
                    result = run_async(
                        search_uniqlo(
                            keyword=keyword,
                            region=st.session_state.search_region,
                            max_products=max_products,
                            search_timeout_seconds=search_timeout_seconds,
                        )
                    )
                except Exception as e:
                    st.error(f"搜尋時發生錯誤：{e}")
                    st.markdown("</div>", unsafe_allow_html=True)
                    return

            if not result or not getattr(result, "results", None):
                st.warning("搜尋不到相關商品，請試試其他關鍵字。")
                st.markdown("</div>", unsafe_allow_html=True)
                return

            st.session_state.search_result = result
            st.session_state.selected_index = 0
            st.session_state.review_data = None
            st.session_state.page = "results"
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    # 說明卡片
    st.markdown('<div class="uq-card">', unsafe_allow_html=True)
    st.markdown('<div class="uq-section-title">小提醒</div>', unsafe_allow_html=True)
    st.write("・台灣站：台灣人的評價較少。  \n・日本站：評論較為充足完整。")
    st.markdown("</div>", unsafe_allow_html=True)


def page_results():
    result = st.session_state.search_result
    if result is None:
        st.warning("目前沒有搜尋結果，請先在主畫面搜尋。")
        if st.button("回主畫面"):
            st.session_state.page = "home"
            # 這裡不用手動 rerun，按鈕本身就會觸發
        return

    # 回主畫面按鈕
    st.button("← 回主畫面", on_click=lambda: go_page("home"))

    st.header("搜尋結果")
    st.caption(
        f"地區：{'台灣官網' if result.region=='tw' else '日本官網'} ｜ "
        f"實際搜尋關鍵字：{result.keyword} ｜ "
        f"共 {len(result.results)} 件商品"
    )

    col_left, col_right = st.columns([1, 2])

    # ---------------- 左側：目前選中的商品 ----------------
    with col_left:
        st.markdown('<div class="uq-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="uq-section-title">目前選中的商品</div>',
            unsafe_allow_html=True,
        )
        idx = st.session_state.selected_index
        if idx is None or idx >= len(result.results):
            st.write("尚未選擇商品，請在右側列表中點選。")
        else:
            p = result.results[idx]
            if getattr(p, "product_image_url", None):
                st.image(p.product_image_url, width="stretch")
            st.write(f"**名稱：** {p.product_name or '（無名稱）'}")
            st.write(f"[商品頁面]({p.product_url})")
            st.write(f"[評論頁面]({p.review_url})")

            # 評論抓取深度選擇
            st.session_state.review_depth = st.radio(
                "評論抓取數量",
                options=["少量", "標準", "大量"],
                index=1,
                horizontal=True,
                key=f"review_depth_{idx}",
                help="TW約每次滾動5-10則，JP約每次3-8則；數量越多耗時越長",
            )

            if st.button("📊 分析這件商品的評論"):
                # 根據選擇的深度決定滾動次數
                depth_choice = st.session_state.review_depth
                if "少量" in depth_choice:
                    scroll_count = 5
                elif "標準" in depth_choice:
                    scroll_count = 15
                else:  # 大量
                    scroll_count = 25

                with st.spinner(
                    f"正在抓取評論，預計 {scroll_count*2}~{scroll_count*3} 秒..."
                ):
                    try:
                        if result.region == "tw":
                            raw = run_async(
                                scrape_uniqlo_reviews_tw(
                                    p.review_url, scroll_count=scroll_count
                                )
                            )
                            review_data = unify_tw(raw)
                        else:
                            raw = run_async(
                                scrape_uniqlo_reviews_jp(
                                    p.review_url, scroll_count=scroll_count
                                )
                            )
                            review_data = unify_jp(raw)
                    except Exception as e:
                        st.error(f"抓取評論時發生錯誤：{e}")
                        st.markdown("</div>", unsafe_allow_html=True)
                        return

                st.session_state.review_data = review_data
                st.session_state.summaries = None
                st.session_state.age_stats = None
                st.session_state.region_stats = None
                st.session_state.age_fallback_text = ""
                st.session_state.size_suggestion_text = ""
                st.session_state.qa_history = []
                st.session_state.page = "detail"
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ---------------- 右側：商品列表 ----------------
    with col_right:
        st.markdown('<div class="uq-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="uq-section-title">商品列表</div>',
            unsafe_allow_html=True,
        )

        for i, p in enumerate(result.results):
            with st.container():
                inner = st.columns([1, 3])
                with inner[0]:
                    if getattr(p, "product_image_url", None):
                        st.image(p.product_image_url, width=140)
                with inner[1]:
                    st.markdown(f"**{p.product_name or '（無名稱）'}**")
                    st.markdown(f"[商品]({p.product_url}) ｜ [評論]({p.review_url})")
                    if st.button("選擇", key=f"select_{i}"):
                        st.session_state.selected_index = i
                        # 這裡不用 st.rerun()，按鈕本身會觸發重跑
            st.markdown("---")
        st.markdown("</div>", unsafe_allow_html=True)


def go_page(name: str):
    """只改 page，不在 callback 裡 rerun，避免 no-op warning。"""
    st.session_state.page = name


def page_detail():
    result = st.session_state.search_result
    review_data = st.session_state.review_data
    idx = st.session_state.selected_index

    if result is None or review_data is None or idx is None:
        st.warning("目前沒有可分析的商品，請先回搜尋結果頁。")
        if st.button("回搜尋結果"):
            go_page("results")
        return

    product = result.results[idx]

    # 導覽列
    nav_left, nav_mid = st.columns([1, 3])
    with nav_left:
        st.button("← 回搜尋結果", on_click=lambda: go_page("results"))
    with nav_mid:
        st.subheader(f"商品分析：{product.product_name or '（無名稱）'}")

    # 顯示地區和評論數
    if review_data["site"] == "uniqlo_tw":
        st.caption(f"地區：台灣官網 ｜ 評論數：{review_data.get('review_count', '—')}")
    elif review_data["site"] == "uniqlo_jp":
        st.caption(f"地區：日本官網 ｜ 評論數：{review_data.get('review_count', '—')}")
    else:
        st.caption("地區：未知")

    # 預先算出年齡 / 地區統計（TW 和 JP 都需要）
    if st.session_state.age_stats is None:
        print(
            f"[page_detail] computing stats for site={review_data['site']}, reviews={len(review_data.get('reviews', []))}"
        )
        if review_data["site"] == "uniqlo_tw":
            ages = extract_age_buckets_tw(review_data["reviews"])
            regions = extract_region_buckets_tw(review_data["reviews"])
        elif review_data["site"] == "uniqlo_jp":
            ages = extract_age_buckets_jp(review_data["reviews"])
            regions = extract_region_buckets_jp(review_data["reviews"])
        else:
            ages = Counter()
            regions = Counter()
        st.session_state.age_stats = ages
        st.session_state.region_stats = regions
        print(
            f"[page_detail] computed age buckets={dict(ages)}, region buckets={dict(regions)}"
        )

    # ---------- A. 上半部：評論總結 + 商品資訊 ----------
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.markdown('<div class="uq-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="uq-section-title">高頻關鍵評論（LLM 總結）</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.summaries is None:
            with st.spinner("正在請 LLM 分析評論重點..."):
                st.session_state.summaries = generate_summaries(
                    review_data, product.product_name or ""
                )
        st.write(st.session_state.summaries.get("full_text", ""))
        st.markdown("</div>", unsafe_allow_html=True)

    with col_right:
        st.markdown('<div class="uq-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="uq-section-title">商品資訊</div>',
            unsafe_allow_html=True,
        )
        if getattr(product, "product_image_url", None):
            st.image(product.product_image_url, width="stretch")
        st.write(f"**名稱：** {product.product_name or '（無名稱）'}")
        st.write(f"[商品頁面]({product.product_url})")
        st.write(f"[評論頁面]({product.review_url})")
        st.markdown("</div>", unsafe_allow_html=True)

    # ---------- B. 尺寸建議區 ----------
    st.markdown('<div class="uq-card">', unsafe_allow_html=True)
    st.markdown('<div class="uq-section-title">尺寸建議</div>', unsafe_allow_html=True)

    col_size_left, col_size_right = st.columns([1, 2])

    with col_size_left:
        height = st.number_input("身高（cm）", min_value=0.0, max_value=250.0, step=1.0)
        weight = st.number_input("體重（kg）", min_value=0.0, max_value=200.0, step=0.5)
        fit_pref = st.selectbox(
            "期望版型",
            ["尚未選擇", "合身", "稍微寬鬆", "寬鬆"],
            index=0,
        )

        if st.button("✨ 取得尺寸建議"):
            if height <= 0 or weight <= 0:
                st.warning("請先輸入有效的身高與體重。")
            elif fit_pref == "尚未選擇":
                st.warning("請先選擇期望版型。")
            else:
                with st.spinner("正在請 LLM 分析合適尺寸..."):
                    text = generate_size_suggestion(
                        review_data,
                        product.product_name or "",
                        height,
                        weight,
                        fit_pref,
                    )
                st.session_state.size_suggestion_text = text

    with col_size_right:
        if st.session_state.size_suggestion_text:
            st.write(st.session_state.size_suggestion_text)
        else:
            st.caption("尺寸建議會顯示在這裡。")

    st.markdown("</div>", unsafe_allow_html=True)

    # ---------- C. 問答區 ----------
    st.markdown('<div class="uq-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="uq-section-title">對此商品提問（LLM 回覆）</div>',
        unsafe_allow_html=True,
    )

    col_q, col_ans = st.columns([1, 2])

    with col_q:
        q = st.text_input("請輸入想問的問題，例如：「這件會不會刺膚？」")
        if st.button("送出問題"):
            if not q.strip():
                st.warning("問題內容不能是空的。")
            else:
                with st.spinner("正在根據評論內容回答你的問題..."):
                    a = generate_qa_answer(
                        review_data, product.product_name or "", q.strip()
                    )
                st.session_state.qa_history.append({"q": q.strip(), "a": a})

    with col_ans:
        if not st.session_state.qa_history:
            st.caption("LLM 回覆會顯示在這裡。")
        else:
            for i, qa in enumerate(reversed(st.session_state.qa_history)):
                st.markdown(f"**Q{i+1}. {qa['q']}**")
                st.write(qa["a"])
                st.markdown("---")

    st.markdown("</div>", unsafe_allow_html=True)

    # ---------- D. 數據視覺化 ----------
    st.markdown('<div class="uq-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="uq-section-title">數據視覺化：購買年齡與地區分佈</div>',
        unsafe_allow_html=True,
    )

    col_age, col_region = st.columns(2)

    with col_age:
        st.markdown("**年齡分佈**")
        ages = st.session_state.age_stats
        # TW 和 JP 都顯示年齡統計圖表
        if ages and sum(ages.values()) > 0:
            df_age = pd.DataFrame(
                {"age_range": list(ages.keys()), "count": list(ages.values())}
            ).sort_values("age_range")
            df_age = df_age.set_index("age_range")
            st.bar_chart(df_age, width="stretch")
        else:
            # 沒有年齡資料時，請 LLM 推估
            if not st.session_state.age_fallback_text:
                with st.spinner("沒有明確年齡資料，正在請 LLM 推估..."):
                    text = generate_age_fallback_summary(
                        review_data, product.product_name or ""
                    )
                st.session_state.age_fallback_text = text
            st.write(st.session_state.age_fallback_text)

    with col_region:
        # 根據地區顯示不同的標題
        if review_data["site"] == "uniqlo_tw":
            st.markdown("**台灣地區分佈**")
        elif review_data["site"] == "uniqlo_jp":
            st.markdown("**日本地區分佈**")
        else:
            st.markdown("**地區分佈**")

        regions = st.session_state.region_stats
        # TW 和 JP 都顯示地區統計圖表
        if regions and sum(regions.values()) > 0:
            df_region = pd.DataFrame(
                {"region": list(regions.keys()), "count": list(regions.values())}
            ).sort_values("count", ascending=False)
            df_region = df_region.set_index("region")
            st.bar_chart(df_region, width="stretch")
        else:
            st.caption("無法從評論中解析足夠的地區資訊。")

    st.markdown("</div>", unsafe_allow_html=True)

    # ---------- E. 評論範例（TW 和 JP 都提供結構化範例） ----------
    if review_data.get("reviews"):
        st.markdown('<div class="uq-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="uq-section-title">精選評論</div>',
            unsafe_allow_html=True,
        )

        examples = review_data["reviews"][:5]
        for i, r in enumerate(examples, start=1):
            title = (r.get("title") or "").strip() or "（無標題）"
            date = (r.get("date") or "").strip()
            user = (r.get("user_info") or "").strip()
            fit_info = (r.get("fit_info") or "").strip()
            content = (r.get("content") or "").strip()
            snippet = content[:180] + ("…" if len(content) > 180 else "")

            st.markdown(f"**評論 {i}**：{title}")
            # 組合 metadata：日期、購買資訊、用戶資訊
            meta_parts = [date]
            if fit_info:
                meta_parts.append(fit_info)
            if user:
                meta_parts.append(user)
            meta = " ｜ ".join([x for x in meta_parts if x])
            if meta:
                st.caption(meta)
            st.write(snippet)
            st.markdown("---")

        st.markdown("</div>", unsafe_allow_html=True)


# ============================================================
# 5. 主入口
# ============================================================


def main():
    # 嘗試以 PIL 讀取本地圖檔作為 page_icon；若無 PIL 則退回 emoji
    page_icon = "🧥"
    if Image is not None:
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "uniqlo.png")
            if os.path.exists(icon_path):
                page_icon = Image.open(icon_path)
        except Exception:
            page_icon = "🧥"
    st.set_page_config(
        page_title="Uniqlo 評論導航器",
        page_icon=page_icon,
        layout="wide",
    )
    apply_style()
    init_session_state()

    page = st.session_state.page
    if page == "home":
        page_home()
    elif page == "results":
        page_results()
    elif page == "detail":
        page_detail()
    else:
        st.session_state.page = "home"
        page_home()


if __name__ == "__main__":
    main()
