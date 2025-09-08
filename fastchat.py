import os
import asyncio
import random
import string
from contextlib import asynccontextmanager
from typing import Optional

from playwright.async_api import (
    async_playwright,
    TimeoutError as PWTimeout,
    Page,
    Frame,
)

# ====================== НАСТРОЙКИ ======================
WIDGET_URL = "https://redirect.test.vivai.ai/30a8e3fa-8ed6-4b6a-a547-5445037e5414"

# Селекторы: обновляйте при смене билдов/классов виджета
SEL_IFRAME = 'iframe'                 # можно уточнить: iframe[title="Chat Widget"]
SEL_WIDGET_BUTTON = ".key-1qn0tbk"    # кнопка открытия
SEL_INPUT_USERID = "#userId"
SEL_BTN_START = ".key-t91e19"         # Start chat
SEL_INPUT_MESSAGE = ".key-jml02v"
SEL_SEND_ICONS = "svg.key-b44e5x"     # как правило, 2-я иконка — отправка
SEL_CLOSE_BUTTON = ".key-1cfsorn"     # для закрытия
TOTAL_CHATS = 200         # всего сессий
CONCURRENCY = 50         # одновременных сессий
MESSAGE_TEXT = "Test message"
TIMEOUT_MS = 15000       # мс ожиданий на действия
RETRIES = 2              # доп. попытки на один чат (в сумме 1 + RETRIES)
POST_SEND_PAUSE_MS = 300 # пауза после отправки

# Необязательный прокси: задайте в окружении PLAYWRIGHT_PROXY
PLAYWRIGHT_PROXY = os.getenv("PLAYWRIGHT_PROXY")

# ==================== ВСПОМОГАТЕЛЬНО ====================

def rnd_suffix(n: int = 6) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

async def get_widget_frame(page: Page) -> Frame:
    """
    Дожидаемся появления iframe и получаем его Frame.
    Иногда element виден, но frame ещё не прикреплён — добавлен мини-луп.
    """
    iframe_el = await page.wait_for_selector(SEL_IFRAME, state="visible", timeout=TIMEOUT_MS)
    frame = await iframe_el.content_frame()
    if frame is None:
        for _ in range(20):
            await asyncio.sleep(0.05)
            frame = await iframe_el.content_frame()
            if frame is not None:
                break
    if frame is None:
        raise RuntimeError("Не удалось получить content_frame из iframe")
    return frame

async def run_chat_flow(frame: Frame, index: int) -> None:
    # открыть виджет
    await frame.click(SEL_WIDGET_BUTTON, timeout=TIMEOUT_MS)

    # userId
    await frame.fill(SEL_INPUT_USERID, f"Test {index}-{rnd_suffix()}", timeout=TIMEOUT_MS)

    # старт
    await frame.click(SEL_BTN_START, timeout=TIMEOUT_MS)

    # === несколько сообщений подряд (3–7) ===
    count = random.randint(3, 7)
    for j in range(count):
        msg = f"{MESSAGE_TEXT} #{index}.{j+1}"

        await frame.click(SEL_INPUT_MESSAGE, timeout=TIMEOUT_MS)
        await frame.fill(SEL_INPUT_MESSAGE, msg, timeout=TIMEOUT_MS)

        await frame.wait_for_selector(SEL_SEND_ICONS, timeout=TIMEOUT_MS)
        icons = await frame.query_selector_all(SEL_SEND_ICONS)
        target = icons[1] if len(icons) > 1 else icons[0]
        await target.click()

        await frame.wait_for_timeout(POST_SEND_PAUSE_MS)

    # === закрытие пока закомментировано ===
    # try:
    #     await frame.click(SEL_CLOSE_BUTTON, timeout=3000)
    #     print(f"[OK] Chat #{index} closed")
    # except Exception:
    #     print(f"[WARN] Chat #{index}: кнопка закрытия не найдена")

    print(f"[OK] Chat #{index}: отправлено {count} сообщений")

async def one_chat(context, index: int) -> bool:
    page = await context.new_page()
    page.set_default_timeout(TIMEOUT_MS)
    try:
        await page.goto(WIDGET_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        frame = await get_widget_frame(page)
        await run_chat_flow(frame, index)
        await page.wait_for_timeout(POST_SEND_PAUSE_MS)
        return True
    except PWTimeout:
        print(f"[TIMEOUT] Chat #{index}")
        return False
    except Exception as e:
        print(f"[ERR] Chat #{index}: {e}")
        return False
    finally:
        await page.close()

@asynccontextmanager
async def launch_browser():
    pw = await async_playwright().start()

    launch_kwargs = {"headless": True}
    if PLAYWRIGHT_PROXY:
        launch_kwargs["proxy"] = {"server": PLAYWRIGHT_PROXY}

    browser = await pw.chromium.launch(**launch_kwargs)
    try:
        yield browser
    finally:
        await browser.close()
        await pw.stop()

async def run():
    sem = asyncio.Semaphore(CONCURRENCY)
    success = 0

    async with launch_browser() as browser:
        async def worker(i: int):
            nonlocal success
            async with sem:
                context = await browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 1366, "height": 900},
                )
                try:
                    for attempt in range(1, RETRIES + 2):
                        ok = await one_chat(context, i)
                        if ok:
                            success += 1
                            break
                        await asyncio.sleep(0.25 * attempt)
                finally:
                    await context.close()

        tasks = [asyncio.create_task(worker(i)) for i in range(1, TOTAL_CHATS + 1)]
        await asyncio.gather(*tasks)

    print(f"\nDone. Success: {success}/{TOTAL_CHATS}")

if __name__ == "__main__":
    asyncio.run(run())
