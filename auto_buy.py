import argparse
import sys
import time
import os
from typing import Optional

from playwright.sync_api import Playwright, sync_playwright, BrowserContext, Page
try:
    from PIL import Image, ImageOps
    import pytesseract
except Exception:
    Image = None
    pytesseract = None


HOMEPAGE = "https://ticket-training.onrender.com/"


def normalize_digits(value: str) -> str:
    # Keep only digits for fuzzy matching prices like NT$2,800 -> 2800
    return "".join(ch for ch in value if ch.isdigit())


def log(msg: str) -> None:
    print(f"[auto-buy] {msg}")


def save_debug(page: Page, name: str) -> None:
    try:
        page.screenshot(path=f"{name}.png", full_page=True)
    except Exception:
        pass
    try:
        html = page.content()
        with open(f"{name}.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass


def agree_terms_if_present(page: Page) -> None:
    """Try to tick the terms/consent checkbox on the captcha page."""
    try:
        # Prefer role checkbox
        cb = page.get_by_role("checkbox").first
        cb.wait_for(state="attached", timeout=500)
        try:
            checked = cb.is_checked()
        except Exception:
            checked = False
        if not checked:
            cb.scroll_into_view_if_needed()
            cb.check(timeout=800)
            log("已勾選條款核取方塊 (role=checkbox)")
            return
    except Exception:
        pass

    # Try by label text
    for label_text in [
        "我已詳細閱讀且同意",
        "會員服務條款",
        "同意",
    ]:
        try:
            el = page.get_by_label(label_text, exact=False)
            el.first.check(timeout=800)
            log("已勾選條款核取方塊 (by label)")
            return
        except Exception:
            continue

    # Fallback: click the checkbox preceding the consent text
    try:
        consent = page.get_by_text("會員服務條款", exact=False).first
        consent.scroll_into_view_if_needed()
        consent.locator("xpath=preceding::input[@type='checkbox'][1]").first.check(timeout=800)
        log("已勾選條款核取方塊 (fallback preceding checkbox)")
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automate ticket training flow")
    parser.add_argument("--seconds", type=int, default=3, help="Countdown seconds before clicking buy")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--slowmo", type=int, default=0, help="Slow motion delay in ms for debugging")
    parser.add_argument("--timeout", type=int, default=15000, help="Default timeout for actions (ms)")
    parser.add_argument("--price", type=str, default="", help="Target price text (e.g. 2800 or NT$2,800)")
    parser.add_argument("--quantity", type=int, default=0, help="Desired ticket quantity (e.g. 2)")
    parser.add_argument("--tesseract", type=str, default="", help="Path to tesseract.exe (Windows)")
    return parser.parse_args()


def click_if_visible(page: Page, text: str, timeout_ms: int) -> bool:
    try:
        locator = page.get_by_role("button", name=text)
        locator.first.wait_for(state="visible", timeout=timeout_ms)
        locator.first.click()
        return True
    except Exception:
        # Try text selector fallback
        try:
            page.get_by_text(text, exact=True).first.click(timeout=timeout_ms)
            return True
        except Exception:
            return False


def dismiss_adblock_and_disclaimers(page: Page, timeout_ms: int) -> None:
    # Close AdBlock notification if present
    log("嘗試關閉提示/覆蓋層…")
    click_if_visible(page, "我已閱讀並了解", timeout_ms=1000)
    click_if_visible(page, "確認", timeout_ms=1000)
    # Some variants may have a simple close button
    click_if_visible(page, "重新整理", timeout_ms=500)


def start_countdown_and_buy(page: Page, seconds: int, timeout_ms: int) -> None:
    # Some versions have a direct purchase table with [立即訂購]
    # Try the homepage flow first
    try:
        log("尋找倒數輸入框並嘗試填入秒數…")
        # If an input for seconds exists, set it
        inputs = page.locator("input").all()
        for input_box in inputs:
            try:
                placeholder = input_box.get_attribute("placeholder")
            except Exception:
                placeholder = None
            # Heuristically pick the first number input
            input_type = input_box.get_attribute("type")
            if input_type in ("number", None) or (placeholder and "秒" in placeholder):
                input_box.fill(str(seconds))
                break
        # Click 開始倒數計時 if exists
        clicked = click_if_visible(page, "開始倒數計時", timeout_ms=1000)
        if clicked:
            # Wait for countdown to finish
            log(f"已點擊開始倒數，等待 {seconds} 秒…")
            time.sleep(max(0, seconds - 1))
            # Polling small delay to mimic human timing
            time.sleep(1.2)
        # Click 立即購票 or 立即訂購
        log("嘗試點擊『立即購票/立即訂購』…")
        if not click_if_visible(page, "立即購票", timeout_ms=1000):
            click_if_visible(page, "立即訂購", timeout_ms=2000)
    except Exception:
        pass


def wait_for_user_prep_and_countdown(seconds: int) -> None:
    log("請在瀏覽器完成步驟 1-4：勾選『我已閱讀並了解』→ 點『確認』→ 輸入秒數 → 點『開始倒數計時』。")
    input("[auto-buy] 完成後按 Enter 繼續，我會等倒數秒數再自動購票… ")
    time.sleep(max(0, seconds))


def double_click_buy(page: Page) -> None:
    # 5-6: click buy twice
    log("步驟 5-6：嘗試連點兩次『立即購票/立即訂購』…")
    for _ in range(2):
        clicked = click_if_visible(page, "立即購票", timeout_ms=1200)
        if not clicked:
            click_if_visible(page, "立即訂購", timeout_ms=1200)
        page.wait_for_timeout(400)


def select_price_and_quantity(page: Page, price_text: str, quantity: int, timeout_ms: int) -> None:
    # 7: select seat by price
    log(f"步驟 7：嘗試依價格選位 price='{price_text}'…")
    if price_text:
        wanted_digits = normalize_digits(price_text)
        try:
            # Prefer the category section header h4 (e.g., 2880區), then first .seat-item under that category
            header = page.locator("h4").filter(has_text=price_text)
            if header.count() > 0:
                header.first.scroll_into_view_if_needed()
                category = header.first.locator("xpath=ancestor::*[@class='category'][1]")
                seat_first = category.locator(".seat-item").first
                seat_first.scroll_into_view_if_needed()
                seat_first.click(timeout=timeout_ms)
                log("已在價位區塊內點擊第一筆座位項目")
            else:
                # fallback: any price text then nearest .seat-item
                any_price = page.get_by_text(price_text, exact=False).first
                any_price.scroll_into_view_if_needed()
                parent = any_price.locator("xpath=ancestor::*[self::div or self::li][1]")
                try:
                    parent.locator(".seat-item").first.click(timeout=timeout_ms)
                    log("已點擊相鄰座位項目")
                except Exception:
                    any_price.click(timeout=timeout_ms)
                    log("已直接點擊含價格文字元素")
        except Exception:
            # Fallback: click any element containing digits (e.g., 2800)
            if wanted_digits:
                page.locator(f":text-matches('.*{wanted_digits}.*')").first.click(timeout=timeout_ms)
                log("已點擊包含目標數字的元素")

    page.wait_for_timeout(300)

    # 8: select quantity from dropdowns
    log(f"步驟 8：嘗試選擇張數 quantity={quantity}…")
    if quantity and quantity > 0:
        try:
            # Prefer selects that contain the quantity option
            selects = page.locator("select").all()
            for sel in selects:
                options = sel.locator("option").all()
                values = [opt.get_attribute("value") for opt in options]
                texts = []
                for opt in options:
                    try:
                        texts.append(opt.inner_text().strip())
                    except Exception:
                        texts.append("")
                target = str(quantity)
                if target in (values or []) or target in texts:
                    try:
                        sel.select_option(target)
                        log("已透過 value 選擇張數")
                    except Exception:
                        # Try by index
                        sel.select_option(label=target)
                        log("已透過 label 選擇張數")
                    break
        except Exception:
            pass


def fill_captcha_and_confirm(page: Page, timeout_ms: int) -> None:
    # 9: input captcha (we will prompt user to read it and type here)
    log("步驟 9：請查看頁面驗證碼…")
    # Try OCR first if available
    user_code = ""
    if pytesseract and Image:
        try:
            log("OCR 啟用：pytesseract 與 PIL 可用，嘗試定位驗證碼圖片…")
            # 先保存一張全頁截圖以利除錯
            try:
                page.screenshot(path="_before_captcha.png", full_page=True)
            except Exception:
                pass
            # Find the captcha input
            input_candidates = [
                page.get_by_placeholder("驗證碼"),
                page.locator("input[name*='captcha' i]"),
                page.locator("input[aria-label*='驗證碼']"),
                page.get_by_label("驗證碼", exact=False),
            ]
            target_input = None
            for cand in input_candidates:
                try:
                    target_input = cand.first
                    target_input.wait_for(state="visible", timeout=800)
                    break
                except Exception:
                    target_input = None
            if target_input is None:
                log("找不到驗證碼輸入框，跳過 OCR")
            # Try to find the nearest image to the input (common layout)
            captcha_img = None
            try:
                if target_input is not None:
                    target_input.scroll_into_view_if_needed()
                    inp_box = target_input.bounding_box()
                    imgs = page.locator("img").all()
                    min_d = None
                    for img_loc in imgs:
                        try:
                            b = img_loc.bounding_box()
                        except Exception:
                            b = None
                        if not b or not inp_box:
                            continue
                        # distance between centers
                        dx = (b["x"] + b["width"] / 2) - (inp_box["x"] + inp_box["width"] / 2)
                        dy = (b["y"] + b["height"] / 2) - (inp_box["y"] + inp_box["height"] / 2)
                        d2 = dx*dx + dy*dy
                        if min_d is None or d2 < min_d:
                            min_d = d2
                            captcha_img = img_loc
                # If not found, fallback to any image with alt/title including 驗證
                if captcha_img is None:
                    captcha_img = page.locator("img[alt*='驗證'], img[title*='驗證']").first
            except Exception:
                pass

            crop_by_box = None
            if captcha_img is not None:
                # 直接用元素截圖，避免全頁截圖後再裁切造成誤差
                try:
                    captcha_img.scroll_into_view_if_needed()
                    captcha_img.screenshot(path="_captcha_crop.png")
                    if Image:
                        bw_src = Image.open("_captcha_crop.png").convert("L")
                        base = bw_src
                        crop_by_box = None  # 已直接取得元素截圖
                    else:
                        crop_by_box = captcha_img.bounding_box()
                except Exception:
                    box = captcha_img.bounding_box()
                    if box:
                        crop_by_box = box
            # If no image box, fall back to an area near the input box (left side rectangle)
            if crop_by_box is None and target_input is not None:
                try:
                    ib = target_input.bounding_box()
                    if ib:
                        crop_by_box = {
                            "x": max(0, ib["x"] - 220),
                            "y": max(0, ib["y"] - 10),
                            "width": 200,
                            "height": max(40, int(ib["height"]))
                        }
                        log("使用輸入框附近區域作為驗證碼截圖後備方案")
                except Exception:
                    pass

            if crop_by_box is not None:
                tmp_path = "_captcha_full.png"
                # 一律輸出全頁截圖
                page.screenshot(path=tmp_path, full_page=True)
                im = Image.open(tmp_path)
                left = max(0, int(crop_by_box["x"]) - 2)
                top = max(0, int(crop_by_box["y"]) - 2)
                right = int(crop_by_box["x"] + crop_by_box["width"]) + 2
                bottom = int(crop_by_box["y"]) + int(crop_by_box["height"]) + 2
                base = im.crop((left, top, right, bottom)).convert("L")
                # 參考你的程式：嘗試旋轉、反相與二值化門檻
                candidates = []
                for angle in (-6, -4, -2, 0, 2, 4, 6):
                    img_rot = base.rotate(angle, expand=True, fillcolor=255)
                    # 反相
                    inv = ImageOps.invert(img_rot)
                    # 二值化（門檻 115 與 160 各試一次）
                    for th in (115, 160):
                        bw = inv.point(lambda p, t=th: 255 if p > t else 0, mode="1")
                        # 保存最後一張以便除錯
                        bw.save("_captcha_crop.png")
                        raw = pytesseract.image_to_string(bw, config='--psm 7')
                        text = "".join(ch for ch in raw if ch.isalnum())
                        candidates.append(text)
                # 選最長且字元數量最多的候選
                user_code = max(candidates, key=lambda s: (len(s), s), default="")
                if user_code:
                    log(f"OCR 取得驗證碼：{user_code}")
                else:
                    log("OCR 未辨識出文字，將回退為人工輸入")
            else:
                log("找不到可截圖的驗證碼區域 (圖片或備援區域)")
            if captcha_img is None:
                log("找不到驗證碼圖片節點")
        except Exception:
            log("OCR 嘗試發生例外，將回退為人工輸入")

    if not user_code:
        user_code = input("[auto-buy] 請輸入頁面上的驗證碼後按 Enter： ").strip()
    captcha_focused = None
    if user_code:
        # Try common captcha inputs
        filled = False
        for locator in [
            page.get_by_placeholder("驗證碼"),
            page.locator("input[name*='captcha' i]"),
            page.locator("input[aria-label*='驗證碼']"),
            page.get_by_label("驗證碼", exact=False),
        ]:
            try:
                target = locator.first
                target.scroll_into_view_if_needed()
                target.fill(user_code, timeout=800)
                captcha_focused = target
                filled = True
                break
            except Exception:
                continue
        if not filled:
            try:
                target = page.locator("input").first
                target.scroll_into_view_if_needed()
                target.fill(user_code, timeout=800)
                captcha_focused = target
            except Exception:
                pass

    # 10: click confirm quantity
    log("步驟 10：嘗試點擊『確認張數/確認/確定/送出』…")
    # Ensure terms checkbox is ticked if present
    agree_terms_if_present(page)
    # Try pressing Enter on captcha field first
    try:
        if captcha_focused is not None:
            captcha_focused.press("Enter")
            page.wait_for_timeout(400)
            log("已在驗證碼欄位按下 Enter")
    except Exception:
        pass

    confirm_labels = [
        "確認張數",
        "確認數量",
        "確認",
        "確定",
        "送出",
        "下一步",
        "下一步驟",
    ]
    # Try role-based buttons first
    for label in confirm_labels:
        if click_if_visible(page, label, timeout_ms=1000):
            log(f"已點擊：{label}")
            break
    else:
        # Try generic text and submit inputs
        try:
            el = page.get_by_text("確認張數", exact=False).first
            el.scroll_into_view_if_needed()
            el.click(timeout=800)
            log("已以文字選擇器點擊『確認張數』")
        except Exception:
            try:
                # Scroll to bottom to surface buttons
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(200)
                # Query multiple button types
                candidates = page.locator(
                    "button, a, input[type='submit'], input[type='button']"
                ).filter(
                    has_text=None
                )
                # Prefer elements whose text/value includes target keywords
                keyword_xpath = (
                    "xpath=|//*[self::button or self::a or self::input]["
                    "contains(normalize-space(.),'確認張數') or "
                    "contains(normalize-space(.),'確認') or "
                    "contains(normalize-space(.),'確定') or "
                    "contains(normalize-space(.),'送出') or "
                    "contains(normalize-space(.),'下一步')]")
                btn = page.locator(keyword_xpath).first
                btn.scroll_into_view_if_needed()
                btn.click(timeout=1200)
                log("已點擊關鍵字匹配之按鈕/連結/輸入元件")
            except Exception:
                pass

    # If still not progressed, save a screenshot to help diagnose
    try:
        save_debug(page, "step10_confirm_not_found")
        log("若仍未前進，已保存 step10_confirm_not_found.(png|html)")
    except Exception:
        pass

    # Final fallback: try submitting the first visible form
    try:
        log("嘗試以程式提交表單 (fallback)…")
        form = page.locator("form").first
        form.scroll_into_view_if_needed()
        try:
            # Prefer native submit buttons
            submit_btn = form.locator("button[type='submit'], input[type='submit']").first
            submit_btn.click(timeout=800)
            log("已按下表單提交按鈕")
        except Exception:
            # Programmatic submit
            page.evaluate(
                "(f)=>{try{f.requestSubmit?f.requestSubmit():f.submit()}catch(e){}}",
                form
            )
            log("已以程式方式提交表單")
    except Exception:
        pass
        try:
            page.screenshot(path="step10_confirm_not_found.png", full_page=True)
            log("未找到確認按鈕，已截圖 step10_confirm_not_found.png")
        except Exception:
            pass


def navigate_purchase_flow(page: Page, timeout_ms: int) -> None:
    # The practice site frequently uses step-by-step pages resembling tixcraft/拓元
    # We navigate by common action buttons and form choices.
    common_next_labels = [
        "下一步",
        "下一步驟",
        "下一頁",
        "我同意",
        "我已閱讀並同意",
        "確認",
        "確定",
        "送出",
        "開始購票",
        "同意並繼續",
    ]

    # Resilient loop with a cap of steps to avoid infinite loops
    for _ in range(20):
        # Seat/quantity selections: try to activate selects and choose first non-disabled option
        try:
            select_boxes = page.locator("select").all()
            for select_box in select_boxes:
                try:
                    is_disabled = select_box.is_disabled()
                except Exception:
                    is_disabled = False
                if not is_disabled:
                    # Choose the first option that's not disabled and not placeholder
                    options = select_box.locator("option").all()
                    for opt in options:
                        try:
                            disabled = opt.is_disabled()
                            value = opt.get_attribute("value")
                        except Exception:
                            disabled = True
                            value = None
                        if not disabled and value and value not in ("", "0", "-1"):
                            select_box.select_option(value)
                            break
        except Exception:
            pass

        # Try common action buttons
        progressed = False
        for label in [
            "加入購物車",
            "立即結帳",
            "下一步",
            "下一步驟",
            "我同意",
            "我已閱讀並同意",
            "確認",
            "確定",
            "送出",
            "前往下一步",
            "同意並繼續",
            "我要購票",
        ]:
            if click_if_visible(page, label, timeout_ms=800):
                progressed = True
                page.wait_for_timeout(400)
                break

        # Also try links styled as buttons
        if not progressed:
            try:
                link = page.get_by_role("link", name="下一步")
                link.first.click(timeout=500)
                progressed = True
                page.wait_for_timeout(300)
            except Exception:
                pass

        # If nothing clicked this iteration, try to click any visible primary button
        if not progressed:
            try:
                buttons = page.locator("button").all()
                for btn in buttons:
                    try:
                        text = btn.inner_text().strip()
                    except Exception:
                        text = ""
                    if text and any(k in text for k in common_next_labels):
                        btn.click(timeout=500)
                        progressed = True
                        page.wait_for_timeout(300)
                        break
            except Exception:
                pass

        # Exit condition: detect success or end page cues
        try:
            success_texts = [
                "訂單建立",
                "購票完成",
                "已完成",
                "本次最佳紀錄",
            ]
            body_text = page.content()
            if any(s in body_text for s in success_texts):
                return
        except Exception:
            pass

        # If still not progressed, break to avoid loop
        if not progressed:
            break


def run(playwright: Playwright, headless: bool, slowmo: int, seconds: int, timeout_ms: int, price: str, quantity: int) -> None:
    browser = playwright.chromium.launch(headless=headless, slow_mo=slowmo)
    context: BrowserContext = browser.new_context()
    page: Page = context.new_page()
    page.set_default_timeout(timeout_ms)

    # Navigate to homepage
    log("開啟首頁…")
    page.goto(HOMEPAGE, wait_until="domcontentloaded")

    # Let user complete steps 1-4 manually, but still try to dismiss overlays if helpful
    dismiss_adblock_and_disclaimers(page, timeout_ms)

    # Wait for user to click start countdown, then sleep seconds
    wait_for_user_prep_and_countdown(seconds)

    # 5-6: double click buy
    double_click_buy(page)

    # If a direct progress path exists, also try navigating to it
    try:
        # In many versions, /progress is the main purchase practice flow
        if "progress" not in page.url:
            log("導向 /progress 練習頁…")
            page.goto(HOMEPAGE + "progress", wait_until="load")
    except Exception:
        pass

    # 7-8: price and quantity
    select_price_and_quantity(page, price, quantity, timeout_ms)

    # 9-10: captcha and confirm
    fill_captcha_and_confirm(page, timeout_ms)

    # Keep the browser open for inspection if headful
    if not headless:
        log("步驟 11：流程暫停於此，請檢查頁面。視窗會保持開啟 10 分鐘或直到你關閉。")
        page.wait_for_timeout(600000)

    context.close()
    browser.close()


if __name__ == "__main__":
    args = parse_args()
    try:
        # Configure tesseract path if provided
        if args.tesseract:
            os.environ["TESSERACT_EXECUTABLE"] = args.tesseract
        elif "TESSERACT_EXECUTABLE" not in os.environ and os.name == "nt":
            # Try common default install path
            default_path = r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
            if os.path.exists(default_path):
                os.environ["TESSERACT_EXECUTABLE"] = default_path
        # Apply to pytesseract if available
        if 'pytesseract' in globals() and pytesseract:
            exe = os.environ.get("TESSERACT_EXECUTABLE")
            if exe:
                pytesseract.pytesseract.tesseract_cmd = exe
        with sync_playwright() as p:
            run(
                p,
                headless=args.headless,
                slowmo=args.slowmo,
                seconds=args.seconds,
                timeout_ms=args.timeout,
                price=args.price,
                quantity=args.quantity,
            )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
