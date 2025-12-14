import sys
import asyncio
import json
import os

# ==========================================
# 1. 環境設定與相容性修復
# ==========================================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    JsonCssExtractionStrategy,
)


def log(step: str, message: str) -> None:
    """輕量級主控台 logger."""
    print(f"[{step}] {message}")


def normalize_reviews(site: str, url: str, raw_reviews: list) -> dict:
    """將原始評論資料統一轉換為 LLM 友善格式

    用途：標準化 TW 版爬蟲提取的評論資料，便於後續 LLM 處理
    特性：清理空值、標準化欄位、統一資料類型

    Args:
        site: 網站標識（例：\"uniqlo_tw\"）
        url: 商品頁面完整 URL
        raw_reviews: 原始評論列表（來自 CSS 提取結果）
                    每筆評論包含：title, date, rating, fit_info, content, user_info

    Returns:
        dict: 標準化後的評論資料，包含：
            - site: 網站標識
            - product_url: 商品 URL
            - review_count: 評論總筆數
            - reviews: 標準化評論列表，每筆包含：
                * review_id: 評論序號（0-based）
                * title: 評論標題（已去除多餘空格）
                * content: 評論內容（已去除多餘空格）
                * rating: 評分（轉換為 float，無效值為 None）
                * date: 發表日期（已去除多餘空格）
                * fit_info: 版型資訊（已去除多餘空格）
                * user_info: 使用者資訊（已去除多餘空格）

    Data Cleaning:
        - 移除前後空格、多餘換行
        - 將 \"null\" 字符串視為 None
        - 嘗試將評分轉為 float，轉換失敗則設為 None
        - 空字符串轉為 None 方便 LLM 處理

    Note:
        此函式用於 TW 版爬蟲（CSS Selector 提取）
        JP 版爬蟲需要不同的處理邏輯
    """
    normalized = []
    for idx, r in enumerate(raw_reviews):
        raw_rating = r.get("rating")
        try:
            rating = float(raw_rating) if raw_rating not in (None, "", "null") else None
        except (ValueError, TypeError):
            rating = None

        # 清理文字內容，避免 Markdown 刪除線問題
        def clean_text(text):
            """清理文字，避免 markdown 刪除線語法問題"""
            if not text:
                return None
            # 移除 ~~ (markdown 刪除線語法)
            text = text.replace("~~", "")
            # 將半形 ~ 轉為全形 〜
            text = text.replace("~", "〜")
            return text.strip() or None

        normalized.append(
            {
                "review_id": idx,
                "title": clean_text(r.get("title")),
                "content": clean_text(r.get("content")),
                "rating": rating,
                "date": clean_text(r.get("date")),
                "fit_info": clean_text(r.get("fit_info")),
                "user_info": clean_text(r.get("user_info")),
            }
        )

    return {
        "site": site,
        "product_url": url,
        "review_count": len(normalized),
        "reviews": normalized,
    }


# ==========================================
# 2. 核心爬蟲邏輯（TW 版）
# ==========================================
async def scrape_uniqlo_reviews_tw(
    url: str,
    scroll_count: int = 20,
) -> dict:
    """
    爬取 Uniqlo 商品頁面的評論資料（TW 版）
    """

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,  # 關閉詳細日誌，避免重複初始化訊息
        java_script_enabled=True,
    )

    # TW 版本滾動腳本（保持原樣）
    js_scroll_script = f"""
    (async () => {{
        console.log("🔴 [TW] 初始化滾動腳本...");
        window.crawl_complete = false;
        
        const scrolls = {scroll_count};
        const delay = 1500;
        
        for (let i = 0; i < scrolls; i++) {{
            window.scrollTo(0, document.body.scrollHeight);
            
            const btns = document.querySelectorAll('.h-btn, button'); 
            for (let btn of btns) {{
                const text = (btn.innerText || "").trim();
                if (text.includes('更多') || text.toLowerCase().includes('more')) {{
                    btn.click();
                }}
            }}

            console.log(`🔄 滾動進度 ${{i+1}}/${{scrolls}}`);
            await new Promise(resolve => setTimeout(resolve, delay));
        }}
        
        console.log("🟢 [TW] 滾動完成，亮綠燈！");
        window.crawl_complete = true;
    }})();
    """

    # TW 版本的 CSS Schema
    schema = {
        "name": "Uniqlo Reviews",
        "baseSelector": "li.comment-item",
        "fields": [
            {"name": "title", "selector": ".comment-item-header-title", "type": "text"},
            {"name": "date", "selector": ".comment-item-header-date", "type": "text"},
            {
                "name": "rating",
                "selector": ".h-comment-star input",
                "type": "attribute",
                "attribute": "value",
            },
            {"name": "fit_info", "selector": ".comment-item-fit", "type": "text"},
            {"name": "content", "selector": ".comment-item-detail", "type": "text"},
            {"name": "user_info", "selector": ".comment-item-info", "type": "text"},
        ],
    }

    run_config = CrawlerRunConfig(
        js_code=js_scroll_script,
        wait_for="js:() => window.crawl_complete === true",
        extraction_strategy=JsonCssExtractionStrategy(schema),
        cache_mode=CacheMode.BYPASS,
        magic=True,
        page_timeout=120000,
    )

    log("TW-START", f"啟動 TW 爬蟲 url={url} scrolls={scroll_count}")

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)

        if not result.success:
            log("TW-ERROR", f"爬取失敗: {result.error_message}")
            return {
                "site": "uniqlo_tw",
                "product_url": url,
                "review_count": 0,
                "reviews": [],
            }

        if not result.extracted_content:
            log(
                "TW-WARN",
                "沒有抽到評論資料，可能 CSS selector 變更 / 頁面結構改變 / 滾動不足",
            )
            return {
                "site": "uniqlo_tw",
                "product_url": url,
                "review_count": 0,
                "reviews": [],
            }

        raw_reviews = json.loads(result.extracted_content)
        data = normalize_reviews("uniqlo_tw", url, raw_reviews)
        log("TW-DONE", f"成功抓取 {data['review_count']} 筆評論")
        return data


# ==========================================
# 3. 主程式入口
# ==========================================
if __name__ == "__main__":
    target_url = (
        "https://www.uniqlo.com/tw/zh_TW/evaluation.html?productCode=u0000000051828"
    )

    tw_data = asyncio.run(scrape_uniqlo_reviews_tw(target_url, scroll_count=20))

    out_file = "uniqlo_tw_reviews.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(tw_data, f, ensure_ascii=False, indent=2)

    print(f"💾 TW 資料已存檔至: {out_file}")

    if tw_data["review_count"] == 0:
        print("\n⚠️ 未抓到評論。建議嘗試：")
        print("   1. 增加 scroll_count（例如改成 40）")
        print("   2. 檢查 CSS selector 是否還有效")
        print("   3. 用 headless=False 查看實際頁面結構")
