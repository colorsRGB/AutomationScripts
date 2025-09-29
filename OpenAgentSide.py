# ===== Импорты =====
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, InvalidElementStateException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import os, time, random, json
import time

# ===== Параметры «серых» карточек (настройка) =====
GREY_WAIT_TIMEOUT   = 4.0   # сколько ждать, что текущая карточка посереет после закрытия
GREY_EXTRA_PAUSE    = 0.7   # дополнительная пауза стабилизации, если карточка таки посерела
TOP_CARD_RECHECK_PAUSE = 0.6  # пауза перед проверкой первой карточки в списке

# ===== Константы (локаторы) =====
# карточки чатов в колонке
ONGOING_CHATS_XPATH = "//app-chat-item/div[contains(@class,'chat-item')]"

# поле ввода: textarea или contenteditable с плейсхолдером
INPUT_LOC = (By.XPATH,
    "//textarea[@placeholder='Type a message']"
    " | //div[@contenteditable='true' and (@placeholder='Type a message' or @data-placeholder='Type a message')]"
)

# твои кнопки
SEND_SPAN_LOC  = (By.XPATH, "//span[normalize-space()='Send']")
CLOSE_SPAN_LOC = (By.XPATH, "//span[normalize-space()='Close']")

# ===== Утилиты =====
def _visible(driver, locator):
    try:
        el = driver.find_element(*locator)
        return el.is_displayed()
    except Exception:
        return False
def _rand_token(k: int = 5):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(k))

def quick_present(driver, locator, timeout=2):
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
        return True
    except TimeoutException:
        return False

def wait_toasts_gone(driver, timeout=6):
    toast = (By.XPATH, "//*[contains(@class,'toast') or contains(@class,'p-toast')]")
    try:
        WebDriverWait(driver, 0.5).until(EC.presence_of_element_located(toast))
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located(toast))
    except TimeoutException:
        pass

def _button_from_span(driver, span_loc):
    """Вернуть <button>, ближайший к указанному span (Send/Close)."""
    span = driver.find_element(*span_loc)
    try:
        return span.find_element(By.XPATH, "ancestor::button[1]")
    except Exception:
        return span.find_element(By.XPATH, "ancestor::*[contains(@class,'p-button')][1]")
# --- альтернативные локаторы для кнопки отправки ---
SEND_BUTTON_CANDIDATES = [
    (By.XPATH, "//span[normalize-space()='Send']/ancestor::button[1]"),
    (By.XPATH, "//button[@type='submit' and .//span[normalize-space()='Send']]"),
    (By.XPATH, "//button[@type='submit' and not(@disabled)]"),
    (By.XPATH, "//button[.//*[contains(@class,'pi-send') or contains(@class,'icon-send')]]"),
]

def _find_send_button(driver):
    """Вернуть первый подходящий видимый/включённый <button> Send по кандидатам."""
    for loc in SEND_BUTTON_CANDIDATES:
        try:
            btn = driver.find_element(*loc)
            if btn.is_displayed():
                return btn
        except Exception:
            continue
    # как последний шанс — старый путь через span
    try:
        return _button_from_span(driver, SEND_SPAN_LOC)
    except Exception:
        return None

def _wait_send_enabled(driver, timeout=4.0, period=0.2) -> bool:
    """Пуллинг: находим кнопку каждый раз заново и проверяем enabled."""
    end = time.time() + timeout
    while time.time() < end:
        btn = _find_send_button(driver)
        if btn:
            try:
                cls  = (btn.get_attribute("class") or "").lower()
                aria = (btn.get_attribute("aria-disabled") or "").lower()
                hard = btn.get_attribute("disabled") is not None
                if not (hard or "p-disabled" in cls or "p-button-loading" in cls or aria == "true"):
                    return True
            except Exception:
                pass
        time.sleep(period)
    return False

def _try_press_enter_to_send(field) -> None:
    """Отправка по Enter — если UI это поддерживает."""
    try:
        field.send_keys(Keys.ENTER)
    except Exception:
        pass

def _is_btn_disabled(btn):
    cls  = (btn.get_attribute("class") or "").lower()
    aria = (btn.get_attribute("aria-disabled") or "").lower()
    hard = btn.get_attribute("disabled") is not None
    return hard or "p-disabled" in cls or "p-button-loading" in cls or aria == "true"

def _is_send_enabled(driver):
    try:
        btn = _button_from_span(driver, SEND_SPAN_LOC)
        return not _is_btn_disabled(btn)
    except Exception:
        return False

def _set_text_and_fire_input(driver, field, text: str):
    """Кладём текст и триггерим input/keyup, чтобы активировать Send."""
    driver.execute_script("""
        const el = arguments[0], val = arguments[1];
        el.focus();
        if (el.isContentEditable) { el.innerText = val; } else { el.value = val; }
        const evOpts = {bubbles:true};
        el.dispatchEvent(new Event('input', evOpts));
        el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'a'}));
    """, field, text)

# ===== Драйвер с включёнными performance-логами/CDP =====
def make_driver():
    options = Options()
    options.add_argument("--incognito")

    # включаем performance-логи и browser-логи
    options.set_capability("goog:loggingPrefs", {
        "performance": "ALL",
        "browser": "ALL"
    })

    driver = webdriver.Chrome(service=Service(), options=options)

    # включаем CDP-сеть (для чтения тел XHR)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass

    driver.set_window_size(1550, 838)
    return driver

def _clear_perf_logs(driver):
    try:
        _ = driver.get_log("performance")
    except Exception:
        pass

def _find_ws_or_xhr_with_token(driver, token: str, timeout: int = 60) -> bool:
    """
    Ждём появления токена в WebSocket кадрах или теле XHR-ответа.
    Возвращает True/False.
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            entries = driver.get_log("performance")
        except Exception:
            entries = []

        for e in entries:
            try:
                m = json.loads(e["message"])["message"]
            except Exception:
                continue

            method = m.get("method", "")

            # WebSocket
            if method == "Network.webSocketFrameReceived":
                payload = (m.get("params", {})
                            .get("response", {})
                            .get("payloadData", "") or "")
                if token in payload:
                    return True

            # XHR
            if method == "Network.responseReceived":
                params = m.get("params", {})
                resp   = params.get("response", {})
                url    = (resp.get("url") or "")
                # подстрой при необходимости список ключевых фрагментов:
                if any(k in url for k in ["agent-events", "events", "hub", "chat"]):
                    req_id = params.get("requestId")
                    if req_id:
                        try:
                            body = driver.execute_cdp_cmd(
                                "Network.getResponseBody", {"requestId": req_id}
                            )
                            if token in (body.get("body") or ""):
                                return True
                        except Exception:
                            pass
        time.sleep(0.3)
    return False

# ===== >>> добавлено для серых карточек =====
def _card_is_grey(card) -> bool:
    """Эвристика «серой» карточки: класс, бэйдж или текст."""
    try:
        cls = (card.get_attribute("class") or "").lower()
        if "closed-item-light" in cls or "closed" in cls:
            return True
        # бэйдж Closed
        if card.find_elements(By.XPATH, ".//span[contains(@class,'badge') and contains(translate(., 'CLOSED', 'closed'),'closed')]"):
            return True
        # текстовая эвристика
        text = (card.text or "").lower()
        if "closed" in text:
            return True
    except Exception:
        pass
    return False

def _is_selected(card) -> bool:
    try:
        return "selected" in ((card.get_attribute("class") or "").lower())
    except Exception:
        return False

def _wait_card_turns_grey(card, timeout=GREY_WAIT_TIMEOUT) -> bool:
    """Ждём, что переданная карточка станет серой (после закрытия)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if _card_is_grey(card):
                return True
        except Exception:
            # карточку могли пересоздать — считаем, что обновилась/исчезла
            return True
        time.sleep(0.2)
    return False
# ===== <<< добавлено для серых карточек =====

# ===== Отправка сообщений (подтверждение по сетевому событию) =====
def send_messages(driver, wait, count: int) -> int:
    sent = 0
    for i in range(count):
        token = _rand_token()
        msg = f"Automated message #{i+1} [{token}]"

        # поле ввода
        field = wait.until(EC.presence_of_element_located(INPUT_LOC))
        wait.until(EC.element_to_be_clickable(INPUT_LOC))
        driver.execute_script("arguments[0].scrollIntoView({block:'nearest'});", field)
        driver.execute_script("arguments[0].click();", field)

        # очистка + ввод
        try:
            field.send_keys(Keys.CONTROL, "a"); field.send_keys(Keys.DELETE)
        except InvalidElementStateException:
            driver.execute_script(
                "if(arguments[0].isContentEditable){arguments[0].innerText=''}else{arguments[0].value=''};",
                field
            )
        _set_text_and_fire_input(driver, field, msg)

        # --- 1) ждём коротко кнопку Send; если не активна — пробуем отправку по Enter
        if not _wait_send_enabled(driver, timeout=3.0):
            # иногда нужно «шевельнуть» инпут, чтобы включить валидацию
            try:
                _set_text_and_fire_input(driver, field, msg + " ")
                _set_text_and_fire_input(driver, field, msg)
            except Exception:
                pass

            # ещё раз подождать кнопку
            if not _wait_send_enabled(driver, timeout=2.0):
                # план Б: отправка по Enter
                _try_press_enter_to_send(field)
                # если отправилось — в логах появится наш токен; перейдём к подтверждению
                # иначе ниже ещё попробуем кликом (на случай, если кнопка всё-таки ожила)

        # --- 2) попытка нажать кнопку (если она есть и включена)
        btn = _find_send_button(driver)
        if btn:
            try:
                driver.execute_script("arguments[0].click();", btn)
            except Exception:
                # как запасной вариант — обычный click()
                try:
                    btn.click()
                except Exception:
                    pass
        else:
            # кнопки не нашли — надеемся, что Enter уже сработал
            pass

        # --- 3) подтверждаем по WebSocket/XHR
        _clear_perf_logs(driver)
        if _find_ws_or_xhr_with_token(driver, token, timeout=60):
            sent += 1
            print(f"   ✓ отправлено: {sent}/{count}")
            time.sleep(0.3)
            continue

        # --- 4) ре-трай: снова «пнуть» инпут и ещё раз нажать
        try:
            _set_text_and_fire_input(driver, field, msg + " ")
            _set_text_and_fire_input(driver, field, msg)
            # попробовать ещё раз кнопку
            btn = _find_send_button(driver)
            if btn:
                driver.execute_script("arguments[0].click();", btn)
            else:
                _try_press_enter_to_send(field)

            if _find_ws_or_xhr_with_token(driver, token, timeout=10):
                sent += 1
                print(f"   ✓ отправлено (retry): {sent}/{count}")
                time.sleep(0.3)
                continue
        except Exception:
            pass

        print(f"   ⚠️ не получили сетевое подтверждение для [{token}] — прерываю цикл")
        break

    return sent

# ===== Закрытие чата (с ожиданием «поседения») =====
def _is_checked(box_div):
    cls = (box_div.get_attribute("class") or "").lower()
    if "p-highlight" in cls or "checked" in cls:
        return True
    try:
        parent = box_div.find_element(By.XPATH, "./ancestor::p-checkbox[1]")
        return (parent.get_attribute("aria-checked") or "").lower() == "true"
    except Exception:
        return False

def _ensure_checkbox(driver, wait, box_loc, should_be_checked: bool):
    box = wait.until(EC.presence_of_element_located(box_loc))
    if _is_checked(box) != should_be_checked:
        driver.execute_script("arguments[0].click();", box)
        WebDriverWait(driver, 5).until(lambda d: _is_checked(box) == should_be_checked)

def _pick_first_reason_if_needed(driver, wait):
    label_loc = (By.XPATH, "//p-dropdown//span[contains(@class,'p-dropdown-label')]")
    if not quick_present(driver, label_loc, 2):
        return
    label = driver.find_element(*label_loc)
    if "select" in (label.text or "").strip().lower():
        trigger_loc = (By.XPATH, "//p-dropdown//div[contains(@class,'p-dropdown') and contains(@class,'p-component')]")
        trig = wait.until(EC.element_to_be_clickable(trigger_loc))
        driver.execute_script("arguments[0].click();", trig)
        first_opt = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//div[contains(@class,'p-dropdown-items-wrapper')]//li[@role='option'][1]")
        ))
        driver.execute_script("arguments[0].click();", first_opt)
        WebDriverWait(driver, 5).until(lambda d: "select" not in driver.find_element(*label_loc).text.lower())

def close_chat(driver, wait):
    """Close → Yes → Submit → OK → ждём серую карточку → пауза стабилизации."""
    close_btn = _button_from_span(driver, CLOSE_SPAN_LOC)
    driver.execute_script("arguments[0].click();", close_btn)

    yes_box_loc    = (By.XPATH, "//p-checkbox[./label[normalize-space()='Yes']]//div[contains(@class,'p-checkbox-box')] | //label[normalize-space()='Yes']/preceding::p-checkbox[1]//div[contains(@class,'p-checkbox-box')]")
    no_box_loc     = (By.XPATH, "//p-checkbox[./label[normalize-space()='No']]//div[contains(@class,'p-checkbox-box')]  | //label[normalize-space()='No']/preceding::p-checkbox[1]//div[contains(@class,'p-checkbox-box')]")
    submit_btn_loc = (By.XPATH, "//span[normalize-space()='Submit']")
    ok_btn_loc     = (By.XPATH, "//button[normalize-space()='OK']")

    # сохраним ссылку на текущую выбранную карточку (для ожидания «поседения»)
    selected_card = None
    try:
        all_cards = driver.find_elements(By.XPATH, ONGOING_CHATS_XPATH)
        for c in all_cards:
            if _is_selected(c):
                selected_card = c
                break
    except Exception:
        pass

    # Yes/No
    _ensure_checkbox(driver, wait, yes_box_loc, True)
    _ensure_checkbox(driver, wait, no_box_loc,  False)

    # Сабмит
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable(submit_btn_loc)))

    # OK после сабмита
    try:
        ok_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(ok_btn_loc))
        driver.execute_script("arguments[0].click();", ok_btn)
        WebDriverWait(driver, 5).until(EC.invisibility_of_element_located(ok_btn_loc))
    except TimeoutException:
        pass

    # ждём пока инпут исчезнет
    try:
        WebDriverWait(driver, 5).until(EC.invisibility_of_element_located(INPUT_LOC))
    except TimeoutException:
        pass

    # >>> добавлено: ждём, что карточка посереет, и даём небольшую паузу стабилизации
    if selected_card is not None:
        if _wait_card_turns_grey(selected_card, timeout=GREY_WAIT_TIMEOUT):
            time.sleep(GREY_EXTRA_PAUSE)
    # <<< добавлено

    time.sleep(0.2)  # общая микро-пауза

# ===== Основная функция =====
def start_chat(index: int = 0):
    driver = make_driver()
    wait = WebDriverWait(driver, 10)
    start_time = time.time()  # отметка старта
    try:
        print("1) Логин…")
        url = os.getenv("VIVAI_URL", "")
        driver.get(url)

        username = os.getenv("VIVAI_USER", "")
        password = os.getenv("VIVAI_PASS", "")
        if not username or not password:
            raise RuntimeError("Нет логина/пароля в переменных окружения VIVAI_USER / VIVAI_PASS")

        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(username)
        wait.until(EC.presence_of_element_located((By.NAME, "password"))).send_keys(password)
        wait.until(EC.element_to_be_clickable((By.ID, "kt_sign_in_submit"))).click()

        print("2) Chats → Direct…")
        wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@title='Chats']"))).click()
        wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@title='Direct']"))).click()
        time.sleep(0.3)

        print("3) Открываю меню аватара…")
        wait_toasts_gone(driver, timeout=6)
        avatar_loc = (By.XPATH, "//app-agent-avatar//span[contains(@class,'p-avatar-text')]")
        avatar = wait.until(EC.element_to_be_clickable(avatar_loc))
        driver.execute_script("arguments[0].click();", avatar)

        print("4) Do not accept chats → Accepting chats…")
        time.sleep(5.3)
        if quick_present(driver, (By.XPATH, "//div[contains(text(),'Do not accept chats')]")):
            driver.find_element(By.XPATH, "//div[contains(text(),'Do not accept chats')]").click()
        if quick_present(driver, (By.XPATH, "//div[contains(text(),'Accepting chats')]")):
            driver.find_element(By.XPATH, "//div[contains(text(),'Accepting chats')]").click()
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

        print("5) Обхожу чаты по очереди…")
        processed = 0

        while True:
            try:
                cards = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.XPATH, ONGOING_CHATS_XPATH))
                )
            except TimeoutException:
                print("Список чатов не загрузился ❌")
                break

            # >>> добавлено: отбрасываем серые/закрытые/выбранные карточки
            candidate = None
            for c in cards:
                if _is_selected(c) or _card_is_grey(c):
                    continue
                # дополнительная текстовая эвристика (если вдруг класс не прогрузился)
                try:
                    if "closed" in ((c.text or "").lower()):
                        continue
                except Exception:
                    pass
                candidate = c
                break
            # <<< добавлено

            if not candidate:
                # >>> добавлено: дайте списку обновиться и перепроверьте «первую» карточку
                time.sleep(TOP_CARD_RECHECK_PAUSE)
                try:
                    cards = driver.find_elements(By.XPATH, ONGOING_CHATS_XPATH)
                    if cards:
                        first = cards[0]
                        if not _is_selected(first) and not _card_is_grey(first) and "closed" not in ((first.text or "").lower()):
                            candidate = first
                except Exception:
                    pass
                # <<< добавлено

            if not candidate:
                print("Нет доступных чатов. Готово ✅")
                break

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", candidate)
            driver.execute_script("arguments[0].click();", candidate)

            try:
                WebDriverWait(driver, 8).until(EC.presence_of_element_located(INPUT_LOC))
            except TimeoutException:
                print("Не удалось открыть чат — пропускаю…")
                continue

            n = random.randint(3, 7)
            print(f"Отправляю {n} сообщений…")
            sent = send_messages(driver, wait, n)

            if sent == n:
                print("Закрываю чат…")
                close_chat(driver, wait)
                wait_toasts_gone(driver, timeout=6)
                processed += 1
                print(f"✔️ Обработано: {processed}")
            else:
                print(f"❌ Отправлено только {sent}/{n}. Чат НЕ закрываю — следующий.")
                continue

    finally:
        elapsed = time.time() - start_time
        minutes, seconds = divmod(int(elapsed), 60)
        print(f"⏱ Время выполнения: {minutes} мин {seconds} сек")
        driver.quit()

# ===== Точка входа =====
if __name__ == "__main__":
    start_chat(index=0)
