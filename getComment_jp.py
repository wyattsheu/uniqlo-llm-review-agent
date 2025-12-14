import sys
import asyncio
import json
import os

# ==========================================
# 1. Windows 必備修復
# ==========================================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
import re


def log(step: str, message: str) -> None:
    """輕量級主控台 logger."""
    print(f"[{step}] {message}")


def clean_markdown(markdown_text: str) -> str:
    """清理 Markdown 中的導航、菜單、footer 等不必要內容

    Args:
        markdown_text: 原始 Markdown 文本

    Returns:
        str: 清理後的 Markdown

    Cleaning Strategy:
        1. 找到最後一個「役に立った」後截斷所有內容
        2. 移除導航、按鈕等 UI 元素
        3. 移除追蹤代碼和廣告
        4. 只保留純評論內容
    """
    # 步驟 1: 找到最後一個「役に立った X」的位置並截斷
    # 這是評論區的結束標記
    last_pos = -1
    pattern = r"\*\s*役に立った\s*\d+"

    for match in re.finditer(pattern, markdown_text):
        last_pos = match.end()

    if last_pos > 0:
        # 截斷：只保留到最後一個「役に立った」為止
        markdown_text = markdown_text[:last_pos]

    # 步驟 2: 移除所有「報告」按鈕
    markdown_text = re.sub(r"\s*\*\s*報告\s*", "", markdown_text)

    # 步驟 3: 移除多餘的空行（超過 2 行連續空行的壓縮為 2 行）
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)

    return markdown_text.strip()


def parse_reviews_from_markdown(markdown_text: str) -> list:
    """從 Markdown 中解析評論並結構化

    Args:
        markdown_text: 清理後的 Markdown 文本

    Returns:
        list: 結構化的評論列表，每個評論包含：
            - review_id: 評論 ID
            - title: 評論標題
            - content: 評論內容
            - rating: 評分（暫無，設為 None）
            - date: 評論日期
            - fit_info: 購買資訊（尺寸、顏色、穿著感受）
            - user_info: 用戶資訊（姓名、性別、年齡、身高體重等）
    """
    reviews = []
    review_id = 0

    # 分割評論：以「  * 」開頭的標題為分界點
    # 評論結構：
    #   * 標題
    #   日期
    #   購入サイズ: XX
    #   購入カラー: XX
    #   お客様の着用感: XX
    #   內容
    #   用戶資訊
    #   * 役に立った X

    # 按行分割
    lines = markdown_text.split("\n")

    current_review = None
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # 檢查是否為評論標題（以「  * 」開頭但不是「報告」或「役に立った」）
        if line.startswith("* ") and "役に立った" not in line and "報告" not in line:
            # 保存前一個評論
            if current_review and current_review.get("content"):
                reviews.append(current_review)

            # 開始新評論
            title = line[2:].strip()  # 移除「* 」
            current_review = {
                "review_id": review_id,
                "title": title,
                "content": "",
                "rating": None,
                "date": "",
                "fit_info": "",
                "user_info": "",
            }
            review_id += 1
            i += 1
            continue

        # 如果當前沒有評論在處理，跳過
        if not current_review:
            i += 1
            continue

        # 提取日期（格式：2025/12/07）
        if re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", line):
            current_review["date"] = line
            i += 1
            continue

        # 提取購買尺寸
        if line.startswith("購入サイズ:") or line.startswith("購入サイズ："):
            size_info = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            current_review["fit_info"] += f"購買尺寸：{size_info}"
            i += 1
            continue

        # 提取購買顏色
        if line.startswith("購入カラー:") or line.startswith("購入カラー："):
            color_info = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            if current_review["fit_info"]:
                current_review["fit_info"] += "；"
            current_review["fit_info"] += f"購買顏色：{color_info}"
            i += 1
            continue

        # 提取穿著感受
        if line.startswith("お客様の着用感:") or line.startswith("お客様の着用感："):
            feel_info = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            if current_review["fit_info"]:
                current_review["fit_info"] += "；"
            current_review["fit_info"] += f"穿著感受：{feel_info}"
            i += 1
            continue

        # 檢查是否為用戶資訊行（包含「男性」或「女性」且包含年齡/身高/體重等）
        if ("男性" in line or "女性" in line) and (
            "身長:" in line or "体重:" in line or "歳" in line or "代" in line
        ):
            # 清理用戶資訊，移除可能導致 markdown 刪除線的特殊字符
            user_info = line.replace("~~", "").replace("~", "〜")
            current_review["user_info"] = user_info
            i += 1
            continue

        # 檢查是否為「役に立った」（評論結束標記）
        if "役に立った" in line:
            # 評論結束，保存並重置
            if current_review.get("content"):
                reviews.append(current_review)
            current_review = None
            i += 1
            continue

        # 其他非空行視為評論內容
        if line and not line.startswith("*"):
            # 清理可能導致 markdown 問題的字符
            clean_line = line.replace("~~", "").replace("~", "〜")
            if current_review["content"]:
                current_review["content"] += "\n"
            current_review["content"] += clean_line

        i += 1

    # 保存最後一個評論
    if current_review and current_review.get("content"):
        reviews.append(current_review)

    return reviews


def normalize_reviews(site: str, url: str, markdown_text: str) -> dict:
    """將爬蟲取得的 Markdown 轉換為統一格式

    Args:
        site: 網站標識（例：\"uniqlo_jp\"）
        url: 商品頁面完整 URL
        markdown_text: 爬蟲取得的原始 Markdown 文本

    Returns:
        dict: 統一格式的資料字典，包含：
            - site: 網站標識
            - product_url: 商品 URL
            - review_count: 評論數量
            - reviews: 結構化的評論列表
            - _raw_markdown: 原始 Markdown 內容（用於備份）
    """
    # 解析評論
    reviews = parse_reviews_from_markdown(markdown_text)

    return {
        "site": site,
        "product_url": url,
        "review_count": len(reviews),
        "reviews": reviews,
        "_raw_markdown": markdown_text,  # 保留原始 markdown 供備用
    }


async def scrape_uniqlo_reviews_jp(
    url: str,
    scroll_count: int = 15,
) -> dict:
    """爬取日本 Uniqlo 商品評論資料

    使用 Markdown 提取模式適配日本版複雜的評論結構
    透過 JavaScript 自動滾動加載更多評論

    Args:
        url: Uniqlo JP 評論頁面 URL
              範例：https://www.uniqlo.com/jp/ja/products/E464918-000/00/reviews
        scroll_count: 自動滾動次數（預設 15）
                     每次滾動間隔 2 秒，建議範圍 10-30

    Returns:
        dict: 評論資料字典，包含：
            - site: \"uniqlo_jp\"
            - product_url: 傳入的商品 URL
            - review_count: 評論總數（待 LLM 解析）
            - reviews: 評論列表（待 LLM 解析）
            - _raw_markdown: 原始 Markdown（用於 LLM 結構化解析）

    Process:
        1. 初始化瀏覽器設定（無頭模式）
        2. 執行 JS 滾動腳本加載更多評論
        3. 提取完整頁面 Markdown
        4. 保存原始 Markdown 到本地檔案
        5. 返回統一格式的資料結構

    Note:
        - JP 版本評論結構複雜，無法用簡單 CSS selector 提取
        - 建議將返回資料中的 _raw_markdown 傳給 LLM
        - LLM 可提取：評論 ID、標題、內容、評分、使用者資訊等

    Performance:
        - 預期耗時：(scroll_count * 2) + 10 秒
        - 預設 scroll_count=15：約 40 秒
    """

    browser_config = BrowserConfig(
        headless=True,  # 無頭模式：不顯示瀏覽器視窗
        verbose=False,  # 關閉詳細日誌，避免重複初始化訊息
        java_script_enabled=True,  # 啟用 JS：執行動態載入腳本
    )

    # ================================
    # JavaScript 滾動腳本：自動加載更多評論
    # ================================
    # 流程：
    #   1. 監聽 scrollTo 事件，每次滾動到頁面底部
    #   2. 尋找並點擊 \"もっと\" 或「さらに表示」按鈕
    #   3. 重複 scroll_count 次，每次間隔 2 秒
    #   4. 設置 window.crawl_complete = true 通知爬蟲完成
    js_scroll_script = f"""
    (async () => {{
        console.log("🔴 [JP] 開始滾動...");
        window.crawl_complete = false;
        
        const scrolls = {scroll_count};
        const delay = 2000;
        
        for (let i = 0; i < scrolls; i++) {{
            window.scrollTo(0, document.body.scrollHeight);

            const btns = document.querySelectorAll('button, a[role="button"]'); 
            for(let btn of btns) {{
                const text = (btn.innerText || "").trim();
                if (
                    text.includes('もっと') ||
                    text.includes('さらに表示') ||
                    text.toLowerCase().includes('load more')
                ) {{
                    console.log("點擊按鈕:", text);
                    btn.click();
                }}
            }}

            console.log(`🔄 滾動進度 ${{i+1}}/${{scrolls}}`);
            await new Promise(resolve => setTimeout(resolve, delay));
        }}
        
        console.log("🟢 [JP] 滾動完成！");
        window.crawl_complete = true;
    }})();
    """

    run_config = CrawlerRunConfig(
        js_code=js_scroll_script,  # 執行滾動腳本
        wait_for="js:() => window.crawl_complete === true",  # 等待 JS 完成信號
        cache_mode=CacheMode.BYPASS,  # 禁用快取，每次重新爬取
        magic=True,  # 自動優化提取
        page_timeout=120000,  # 超時時間：120 秒
        # 排除不必要的網頁元素，只保留評論內容
        excluded_tags=["nav", "header", "footer", "script", "style", "iframe"],
        # 只提取評論區域（可選：如果知道具體的 CSS selector 可以啟用）
        # css_selector='[class*="review"], [class*="comment"]',
    )

    log("JP-START", f"啟動爬蟲 url={url} scrolls={scroll_count}")
    expected_time = scroll_count * 2 + 10
    log(
        "JP-INFO",
        f"預期耗時約 {expected_time} 秒（滾動 {scroll_count} 次×2 秒 + 10 秒初始化）",
    )

    # ================================
    # 執行爬蟲：非同步上下文管理器確保資源正確釋放
    # ================================
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)

        # 檢查爬蟲是否成功
        if not result.success:
            log("JP-ERROR", f"爬蟲失敗: {result.error_message}")
            return {
                "site": "uniqlo_jp",
                "product_url": url,
                "review_count": 0,
                "reviews": [],
            }

        # 檢查是否成功提取 Markdown
        if not result.markdown or not result.markdown.raw_markdown:
            log(
                "JP-ERROR",
                "無法提取頁面內容（Markdown 為空）；可能 JS 執行失敗 / 載入超時 / 無評論",
            )
            return {
                "site": "uniqlo_jp",
                "product_url": url,
                "review_count": 0,
                "reviews": [],
            }

        markdown_text = result.markdown.raw_markdown

        # ================================
        # 清理 Markdown：移除不必要的內容
        # ================================
        # 移除常見的導航、菜單、footer 標記
        markdown_text = clean_markdown(markdown_text)

        markdown_size_mb = len(markdown_text) / (1024 * 1024)
        log(
            "JP-OK",
            f"成功提取 markdown 長度={len(markdown_text)} bytes ({markdown_size_mb:.2f} MB)",
        )

        # ================================
        # 解析並結構化評論資料
        # ================================
        data = normalize_reviews("uniqlo_jp", url, markdown_text)

        log("JP-OK", f"解析評論數={data['review_count']}")

        return data


if __name__ == "__main__":
    target_url = "https://www.uniqlo.com/jp/ja/products/E464918-000/00/reviews"

    print("=" * 60)
    print("開始爬取 Uniqlo JP 評論資料")
    print("=" * 60)

    jp_data = asyncio.run(scrape_uniqlo_reviews_jp(target_url, scroll_count=15))

    print("\n" + "=" * 60)
    print("解析結果摘要")
    print("=" * 60)
    print(f"網站: {jp_data['site']}")
    print(f"商品 URL: {jp_data['product_url']}")
    print(f"評論總數: {jp_data['review_count']}")

    if jp_data["reviews"]:
        print(f"\n前 3 筆評論預覽：")
        print("-" * 60)
        for i, review in enumerate(jp_data["reviews"][:3], 1):
            print(f"\n[評論 {i}]")
            print(f"  標題: {review['title']}")
            print(f"  日期: {review['date']}")
            print(
                f"  內容: {review['content'][:50]}..."
                if len(review["content"]) > 50
                else f"  內容: {review['content']}"
            )
            print(f"  購買資訊: {review['fit_info']}")
            print(
                f"  用戶資訊: {review['user_info'][:80]}..."
                if len(review["user_info"]) > 80
                else f"  用戶資訊: {review['user_info']}"
            )

    # 可選：保存到 JSON 檔案（方便除錯）
    save_to_file = input("\n是否保存為 JSON 檔案？(y/n): ").strip().lower()
    if save_to_file == "y":
        out_file = "uniqlo_jp_reviews.json"
        # 移除 _raw_markdown 以減少檔案大小
        save_data = {k: v for k, v in jp_data.items() if k != "_raw_markdown"}
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"💾 資料已儲存為: {out_file}")

    print("\n✅ 完成！")
