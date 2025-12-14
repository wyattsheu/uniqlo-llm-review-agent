"""
UNIQLO 搜尋模組
打包搜尋功能，提供簡潔的接口給主程式使用
"""

import sys
import asyncio
import re
import html
import os
import contextlib
import requests
from dataclasses import dataclass
from typing import List, Literal, Optional
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

# ===== 0. Windows 相容性處理 =====
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)
from bs4 import BeautifulSoup

Region = Literal["jp", "tw"]


def log(step: str, message: str) -> None:
    """輕量級主控台 logger，統一輸出格式。"""
    print(f"[{step}] {message}")


# ===== LLM 設定與工具函式 =====

BASE = os.getenv("OLLAMA_BASE", "https://api-gateway.netdb.csie.ncku.edu.tw/api")
API_KEY = os.getenv("OLLAMA_API_KEY")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")


def _llm(prompt: str) -> Optional[str]:
    if not API_KEY:
        print("[LLM][ERROR] 環境變數 OLLAMA_API_KEY 未設定，跳過 LLM。")
        return None

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
        resp = requests.post(url, headers=headers, json=data, timeout=30)
        print("[LLM] Status code:", resp.status_code)
        if resp.status_code >= 400:
            print("[LLM][ERROR] Response:", resp.text)
        resp.raise_for_status()
        return resp.json().get("response")
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as e:
        print(f"[LLM][ERROR] 呼叫失敗: {e}")
        return None


def _build_jp_keyword_prompt(user_query: str, max_candidates: int = 3) -> str:
    return (
        "你是 UNIQLO 日本站搜尋詞生成器，請把中文直接轉成最接近 UNIQLO 商品名稱的日文關鍵字。\n"
        "規則：\n"
        f"- 僅輸出不超過 {max_candidates} 行，每行一個候選搜尋詞\n"
        "- 由相關度高到低排序\n"
        "- 不要任何說明、標點、編號或前綴，僅輸出詞本身\n"
        "- 如果輸入已是日文，只需微調為更常用的 UNIQLO 搜尋寫法\n"
        "- 優先使用 UNIQLO 常見品名/系列名，例如 ウルトラライトダウン、エアリズム、ワイヤレスブラ\n"
        f"使用者輸入：{user_query}\n"
        "請直接輸出候選搜尋詞："
    )


def _parse_llm_candidates(text: Optional[str], max_candidates: int = 3) -> List[str]:
    if not text:
        return []
    lines = text.strip().splitlines()
    cleaned: List[str] = []
    seen = set()
    for ln in lines:
        cand = ln.strip().strip("-•.。、，, ")
        if not cand:
            continue
        if cand in seen:
            continue
        seen.add(cand)
        cleaned.append(cand)
        if len(cleaned) >= max_candidates:
            break
    return cleaned


# ===== 資料結構 =====


@dataclass
class ProductReviewPage:
    """商品評論頁資訊"""

    region: Region
    keyword: str
    product_url: str
    review_url: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    image_url: Optional[str] = None

    @property
    def product_image_url(self) -> Optional[str]:
        return self.image_url


@dataclass
class SearchResult:
    """搜尋結果資料結構"""

    region: Region
    keyword: str  # 實際用來搜尋的關鍵字（可能經 LLM 修正）
    results: List[ProductReviewPage]  # 首選結果
    total_count: int = 0
    original_keyword: Optional[str] = None  # 使用者原始輸入
    llm_suggestions: Optional[List[str]] = (
        None  # LLM 產生的候選詞（用來顯示「你是不是要找...」）
    )
    llm_used: bool = True  # 是否有使用 LLM 來修正/建議關鍵字

    def __post_init__(self):
        self.total_count = len(self.results)
        if self.original_keyword is None:
            self.original_keyword = self.keyword
        if self.llm_suggestions is None:
            self.llm_suggestions = []


@dataclass
class ProductCard:
    product_url: str
    product_code: Optional[str]
    product_name: Optional[str]
    image_url: Optional[str]


# ===== 1. 組搜尋網址 =====


def build_search_url(region: Region, keyword: str) -> str:
    if region == "jp":
        base = "https://www.uniqlo.com/jp/ja/search"
        qs = urlencode({"q": keyword})
    else:  # "tw"
        base = "https://www.uniqlo.com/tw/zh_TW/search.html"
        qs = urlencode({"description": keyword})
    url = f"{base}?{qs}"
    print(f"[STEP 1] 搜尋網址 = {url}")
    return url


# ===== 2. 用 crawl4ai 渲染搜尋頁 =====


async def render_search_page(
    region: Region, search_url: str, page_timeout_ms: Optional[int] = None
) -> str:
    if region == "tw":
        wait_for = (
            "js:() => document.querySelectorAll("
            '\'a[href="/tw/zh_TW/product-detail.html"],'
            'a[href*="/tw/zh_TW/product-detail.html?"]\').length > 0'
        )
    else:
        wait_for = (
            "js:() => document.querySelectorAll("
            "'a[href*=\"/jp/ja/products/\"]').length > 0"
        )

        log("SEARCH-RENDER", f"url={search_url} wait_for={wait_for}")

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,  # 關閉 crawl4ai 詳細日誌，避免噪音
        java_script_enabled=True,
    )

    run_config = CrawlerRunConfig(
        js_code=None,
        wait_for=wait_for,
        cache_mode=CacheMode.BYPASS,
        magic=True,
        page_timeout=(
            page_timeout_ms if page_timeout_ms and page_timeout_ms > 0 else 60000
        ),
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=search_url, config=run_config)

    if not result.success:
        # 若逾時或失敗，但仍有部分 HTML，就盡量使用（回傳能解析多少算多少）
        html_text = result.html or ""
        if not html_text:
            log("SEARCH-ERROR", f"渲染搜尋頁失敗: {result.error_message}")
            return ""
        log("SEARCH-ERROR", f"渲染未成功但取得部分 HTML，長度={len(html_text)}")
    else:
        html_text = result.html or ""
    log("SEARCH-RENDER", f"渲染後 HTML 長度={len(html_text)}")

    return html_text


# ===== 3. 提取商品卡片資訊 =====


def extract_product_urls_from_rendered_html(
    region: Region,
    html_text: str,
    base_url: str,
    max_products: int = 50,
) -> List[str]:
    patterns: List[str] = []

    if region == "tw":
        patterns.append(
            r'href=[\'"](/tw/zh_TW/product-detail\.html\?[^\'"]*productCode=[^\'"&\s]+)[\'"]'
        )
        patterns.append(
            r'(/tw/zh_TW/product-detail\.html\?[^"<>\s]*productCode=[^"&<>\s]+)'
        )
    else:
        patterns.append(r'href=[\'"](/jp/ja/products/[^\'"]+)[\'"]')
        patterns.append(r"(/jp/ja/products/[0-9A-Za-z\-]+/[0-9]+)")

    all_urls: List[str] = []
    seen = set()

    log("SEARCH-URL", f"嘗試 {len(patterns)} 組 regex 抓商品頁 URL")

    for idx, pat in enumerate(patterns, start=1):
        matches = re.findall(pat, html_text)
        log("SEARCH-URL", f"Pattern {idx} 命中數={len(matches)}")
        for sample in matches[:5]:
            print(f"    範例 {idx}: {sample}")

        for m in matches:
            if m.startswith("http"):
                full = m
            else:
                full = urljoin(base_url, m)

            if full not in seen:
                seen.add(full)
                all_urls.append(full)

        if len(all_urls) >= max_products:
            break

    log("SEARCH-URL", f"總共抓到 {len(all_urls)} 個商品網址（去重後）")
    for i, url in enumerate(all_urls[:max_products], start=1):
        print(f"    商品 {i}: {url}")

    if not all_urls:
        log("SEARCH-URL-WARN", "抓不到商品網址，請檢查搜尋結果頁或關鍵字")

    return all_urls[:max_products]


def parse_tile_body_for_name_image(
    body_html: str, base_url: str
) -> tuple[Optional[str], Optional[str]]:
    """從商品卡片 HTML 抓名稱和圖片"""
    name: Optional[str] = None
    image: Optional[str] = None

    m_alt = re.search(r'alt="([^"]+)"', body_html)
    if m_alt:
        name = html.unescape(m_alt.group(1)).strip()

    if not name:
        texts = re.findall(r">([^<>]{2,})<", body_html)
        for t in texts:
            t_clean = html.unescape(t).strip()
            if not t_clean:
                continue
            if re.match(r"^[0-9\s\.,$NT円¥%]+$", t_clean):
                continue
            name = t_clean
            break

    m_img = re.search(r'<img[^>]+src="([^"]+)"', body_html)
    src = ""
    if m_img:
        src = m_img.group(1).strip()
    else:
        m_srcset = re.search(r'(?:data-srcset|srcset)="([^"]+)"', body_html)
        if m_srcset:
            first = m_srcset.group(1).strip().split()[0]
            src = first

    if src:
        if src.startswith("//"):
            image = "https:" + src
        elif src.startswith("http"):
            image = src
        else:
            image = urljoin(base_url, src)

    return name, image


def find_image_by_token(global_html: str, base_url: str, token: str) -> Optional[str]:
    """用 token 搜尋圖片 URL"""
    pattern = re.compile(
        rf'<img[^>]+(?:src|data-src|data-srcset|srcset)="([^"]*{re.escape(token)}[^"]*)"',
        re.I,
    )
    m = pattern.search(global_html)
    if not m:
        return None

    src = m.group(1).strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http"):
        return src
    return urljoin(base_url, src)


def extract_product_cards_from_rendered_html(
    region: Region,
    html_text: str,
    base_url: str,
    max_products: int = 50,
) -> List[ProductCard]:
    cards: List[ProductCard] = []

    # ---------- TW ==========
    if region == "tw":
        log("SEARCH-CARD", "解析台灣搜尋結果商品卡片")
        soup = BeautifulSoup(html_text, "html.parser")

        anchors = soup.select("li.product-li a.h-a-label")
        log("SEARCH-CARD", f"TW 搜尋結果找到 {len(anchors)} 個 a.h-a-label")

        for idx, a in enumerate(anchors, start=1):
            href = a.get("href")
            if not href:
                continue

            product_url = urljoin(base_url, href)
            product_code = extract_tw_product_code(product_url)

            name_el = a.select_one(".ec-font-sub-title")
            product_name = name_el.get_text(strip=True) if name_el else None

            img_el = a.select_one(".product-background img.picture-img")
            if img_el and img_el.get("src"):
                image_url = urljoin(base_url, img_el["src"])
            else:
                image_url = None

            if not image_url and product_code:
                image_url = (
                    f"https://www.uniqlo.com/tw/hmall/test/"
                    f"{product_code}/main/first/561/1.jpg"
                )
                print(
                    f"[STEP 3][TW][fallback] 商品 {idx} ({product_code}) 沒有 <img>，"
                    f"改用推測圖片 URL: {image_url}"
                )

            cards.append(
                ProductCard(
                    product_url=product_url,
                    product_code=product_code,
                    product_name=product_name,
                    image_url=image_url,
                )
            )

            if len(cards) >= max_products:
                break

        log("SEARCH-CARD", f"TW 卡片解析完成，成功取出 {len(cards)} 筆資料")
        for i, c in enumerate(cards[:5], start=1):
            print(f"    商品 {i}: 名稱={c.product_name!r}")

        # if not cards:
        #     log(
        #         "SEARCH-CARD-WARN",
        #         "TW 卡片解析失敗，改用舊版 regex 只抓 URL，名稱/圖片暫時留空",
        #     )
        #     urls = extract_product_urls_from_rendered_html(
        #         region, html_text, base_url, max_products=max_products
        #     )
        #     for u in urls:
        #         cards.append(
        #             ProductCard(
        #                 product_url=u,
        #                 product_code=extract_tw_product_code(u),
        #                 product_name=None,
        #                 image_url=None,
        #             )
        #         )

        return cards

    # ---------- JP ==========
    print("[STEP 3] JP 使用 regex 解析商品卡片（含名稱與圖片）")
    pattern = re.compile(
        r'<a[^>]+href="(?P<href>/jp/ja/products/(?P<code>[^"/]+)/[^"]*)"[^>]*>'
        r"(?P<body>.*?)</a>",
        re.I | re.S,
    )

    seen = set()

    for m in pattern.finditer(html_text):
        href = m.group("href")
        code = m.group("code")
        body = m.group("body")

        product_url = urljoin(base_url, href)

        if product_url in seen:
            continue
        seen.add(product_url)

        product_name, image_url = parse_tile_body_for_name_image(body, base_url)

        if not image_url and code:
            m_digits = re.search(r"(\d+)", code)
            if m_digits:
                token = m_digits.group(1)
                image_url = find_image_by_token(html_text, base_url, token)

        cards.append(
            ProductCard(
                product_url=product_url,
                product_code=code,
                product_name=product_name,
                image_url=image_url,
            )
        )

        if len(cards) >= max_products:
            break

    if not cards:
        log(
            "SEARCH-CARD-WARN",
            "JP 卡片 regex 解析失敗，改用舊版只抓 URL，名稱/圖片暫時留空",
        )
        urls = extract_product_urls_from_rendered_html(
            region, html_text, base_url, max_products=max_products
        )
        for u in urls:
            m = re.search(r"/products/([^/]+)/", u)
            code = m.group(1) if m else None
            cards.append(
                ProductCard(
                    product_url=u,
                    product_code=code,
                    product_name=None,
                    image_url=None,
                )
            )

    log("SEARCH-CARD", f"JP 解析完成，共得到 {len(cards)} 個商品卡片")
    for i, c in enumerate(cards[:5], start=1):
        print(
            f"    商品 {i}: 名稱={c.product_name!r}, URL={c.product_url}, 圖片={c.image_url}"
        )

    return cards


# ===== 4. URL → 評論頁 / 貨號 =====


def build_jp_review_url(product_url: str) -> str:
    """組 JP 評論頁 URL"""
    base = product_url.split("?")[0].rstrip("/")
    if base.endswith("/reviews"):
        return base
    return base + "/reviews"


def extract_tw_product_code(product_url: str) -> Optional[str]:
    """從 TW URL 提取商品碼"""
    parsed = urlparse(product_url)
    qs = parse_qs(parsed.query)
    codes = qs.get("productCode")
    if not codes:
        print(f"[STEP 4-TW][WARN] URL 中沒有 productCode: {product_url}")
        return None
    return codes[0]


def build_tw_review_url_from_product(product_url: str) -> Optional[str]:
    """從 TW 商品頁組評論頁 URL"""
    code = extract_tw_product_code(product_url)
    if not code:
        return None
    base = "https://www.uniqlo.com/tw/zh_TW/evaluation.html"
    qs = urlencode({"productCode": code})
    return f"{base}?{qs}"


# ===== 5. 核心搜尋函式 =====


async def find_review_pages_by_keyword(
    region: Region,
    keyword: str,
    max_products: int = 30,
    page_timeout_ms: Optional[int] = None,
) -> List[ProductReviewPage]:
    """
    核心搜尋函式：用關鍵字搜尋商品

    Args:
        region: "jp" 或 "tw"
        keyword: 搜尋關鍵字
        max_products: 最多搜尋多少個商品

    Returns:
        ProductReviewPage 列表（包含商品頁和評論頁資訊）
    """
    print("=" * 80)
    print(f"[PIPELINE] region={region}, keyword={keyword}, max_products={max_products}")
    print("=" * 80)

    search_url = build_search_url(region, keyword)
    html_text = await render_search_page(
        region, search_url, page_timeout_ms=page_timeout_ms
    )
    if not html_text:
        print("[PIPELINE][ERROR] 搜尋頁渲染失敗，停止。")
        return []

    cards = extract_product_cards_from_rendered_html(
        region, html_text, search_url, max_products=max_products
    )

    pages: List[ProductReviewPage] = []

    for c in cards:
        p_url = c.product_url
        if region == "jp":
            review_url = build_jp_review_url(p_url)
        else:
            review_url = build_tw_review_url_from_product(p_url)

        if not review_url:
            print(f"[PIPELINE][WARN] 無法為商品組出評論頁: {p_url}")
            continue

        pages.append(
            ProductReviewPage(
                region=region,
                keyword=keyword,
                product_url=p_url,
                review_url=review_url,
                product_code=c.product_code,
                product_name=c.product_name,
                image_url=c.image_url,
            )
        )

    # print(f"[PIPELINE] 最後得到 {len(pages)} 個 (商品頁, 評論頁) 對應")
    # for i, p in enumerate(pages, start=1):
    #     print(f"  #{i} [{p.region}]")
    #     print(f"    商品名稱: {p.product_name}")
    #     print(f"    商品頁: {p.product_url}")
    #     print(f"    評論頁: {p.review_url}")
    #     print(f"    商品碼: {p.product_code}")
    #     print(f"    圖片: {p.image_url}")
    return pages


# ===== 6. 公開 API：主搜尋接口 =====


async def search_uniqlo(
    keyword: str,
    region: Region = "tw",
    max_products: int = 20,
    use_llm_for_jp: bool = True,
    llm_candidates: int = 3,
    search_timeout_seconds: int = 10,
) -> SearchResult:
    """
    【主搜尋接口】搜尋 UNIQLO 商品

    使用範例：
        result = await search_uniqlo("牛仔褲", region="tw", max_products=20)
        for product in result.results:
            print(product.product_name, product.product_url)

    Args:
        keyword (str): 搜尋關鍵字
        region (Region): "tw" (台灣) 或 "jp" (日本)，預設 "tw"
        max_products (int): 最多搜尋多少個商品，預設 20
        use_llm_for_jp (bool): JP 搜尋時是否用 LLM 轉成較像 UNIQLO 的日文關鍵字
        llm_candidates (int): LLM 回傳候選字數量上限
        search_timeout_seconds (int): 搜尋逾時秒數，上限預設 20 秒

    Returns:
        SearchResult: 包含搜尋結果的資料結構
            - region: 搜尋地區
            - keyword: 搜尋關鍵字
            - results: ProductReviewPage 列表
            - total_count: 結果數量
    """
    llm_suggestions: List[str] = []
    llm_used = False
    search_keyword = keyword

    # 先把中文轉成 UNIQLO 風格的日文關鍵字（只在 JP 時啟用）
    if region == "jp" and use_llm_for_jp:
        prompt = _build_jp_keyword_prompt(keyword, max_candidates=llm_candidates)
        llm_reply = _llm(prompt)
        candidates = _parse_llm_candidates(llm_reply, max_candidates=llm_candidates)
        if candidates:
            llm_used = True
            llm_suggestions = candidates
            search_keyword = candidates[0]
            print(f"[LLM] JP 搜尋詞採用：{search_keyword}")

    # 直接根據 UI 指定的逾時，限制渲染頁面的 page_timeout，
    # 這樣即使時間到仍會返回當前可解析的 HTML 結果（有多少算多少）。
    page_timeout_ms = max(1000, int(search_timeout_seconds * 1000))
    try:
        pages = await find_review_pages_by_keyword(
            region, search_keyword, max_products, page_timeout_ms=page_timeout_ms
        )
    except Exception as e:
        log("ERROR", f"搜尋過程發生錯誤: {e}")
        pages = []

    return SearchResult(
        region=region,
        keyword=search_keyword,
        results=pages,
        original_keyword=keyword,
        llm_suggestions=llm_suggestions,
        llm_used=llm_used,
    )
