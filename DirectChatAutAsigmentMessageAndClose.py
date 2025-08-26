from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    StaleElementReferenceException,
    ElementNotInteractableException,
)
from selenium.webdriver.common.keys import Keys
import time


# ---------- helpers ----------

def wait_toasts_gone(driver, timeout=6):
    """Ждём исчезновения любых тост-уведомлений."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located(

                (By.CSS_SELECTOR, "p-toast, .p-toast, .toast-title, .p-toast-message")
            )
        )
    except TimeoutException:
        pass


def quick_present(driver, locator, timeout=1.5):
    """Проверяем наличие элемента, быстро, с таймаутом по умолчанию 1.5с."""
    try:
        return WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
    except TimeoutException:
        return None




def is_checked(box_el):
    """Определяет, выбран ли чекбокс/радиокнопка PrimeNG."""
    cls = (box_el.get_attribute("class") or "")
    aria = (box_el.get_attribute("aria-checked") or "")
    try:
        holder = box_el.find_element(
            By.XPATH, "ancestor::*[contains(@class,'p-checkbox') or contains(@class,'p-radiobutton')][1]"
        )
        holder_cls = holder.get_attribute("class") or ""
    except Exception:
        holder_cls = ""
    return (
        aria == "true"
        or "p-checkbox-checked" in cls
        or "p-radiobutton-checked" in cls
        or "p-checkbox-checked" in holder_cls
        or "p-radiobutton-checked" in holder_cls
    )


def select_yes_in_modal(driver):
    """
    В модалке Chat closure выбирает 'Yes' (по label), если Submit ещё не активна.
    Ожидаем не состояние чекбокса, а именно активацию кнопки Submit.
    """
    # найдём саму кнопку Submit (для проверки состояния)
    try:
        submit_btn = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[.//span[normalize-space()='Submit']]")
            )
        )
    except TimeoutException:
        return  # модалки нет — ничего не делаем

    def submit_enabled(el):
        if el is None:
            return False
        cls = (el.get_attribute("class") or "")
        return (el.get_attribute("disabled") in (None, "", "false")) and ("p-disabled" not in cls)

    # если уже активна — ничего не трогаем (во избежание снятия галки)
    if submit_enabled(submit_btn):
        return

    # иначе один раз нажмём по label "Yes"
    try:
        label_yes = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//label[normalize-space()='Yes']"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", label_yes)
        try:
            label_yes.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            driver.execute_script("arguments[0].click();", label_yes)
    except TimeoutException:
        return

    # дождёмся активации Submit (повторно берём элемент на случай перерисовки)
    WebDriverWait(driver, 5).until(lambda d: submit_enabled(
        d.find_element(By.XPATH, "//button[.//span[normalize-space()='Submit']]")
    ))


# ---------- NEW: работа с «закрытыми» чатами ----------

def is_closed_chat(chat_el) -> bool:
    """
    Чат считается закрытым, если на контейнере есть класс closed-item или closed-item-light.
    """
    cl = (chat_el.get_attribute("class") or "")
    return ("closed-item" in cl) or ("closed-item-light" in cl)


def remove_closed_chip(chat_el, driver):
    """
    У «закрытых» чатов слева есть крестик. Попробуем кликнуть по нему, чтобы убрать запись из списка.
    """
    try:
        btn = chat_el.find_element(By.CSS_SELECTOR, ".close-icon i.pi-times")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        btn.click()
        return True
    except Exception:
        return False


# ---------- core ----------

def process_one_chat(driver, wait, chats_xpath) -> bool:
    """Обрабатывает один чат из списка Ongoing — самый первый НЕ закрытый."""
    all_items = driver.find_elements(By.XPATH, chats_xpath)
    if not all_items:
        return False

    # оставляем только незакрытые
    open_items = [el for el in all_items if not is_closed_chat(el)]

    # если все видимые — закрытые: попробуем их убрать крестиком и обновить список
    if not open_items:
        removed = False
        for el in all_items:
            removed = remove_closed_chip(el, driver) or removed
        # после очистки перечитаем список
        all_items = driver.find_elements(By.XPATH, chats_xpath)
        open_items = [el for el in all_items if not is_closed_chat(el)]
        if not open_items:
            return False  # нечего обрабатывать

    first = open_items[0]
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first)
    try:
        first.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", first)

    # Assign to me (если есть)
    try:
        assign_btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Assign to me']"))
        )
        try:
            assign_btn.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", assign_btn)
    except TimeoutException:
        pass

    # Отправка сообщения
    textarea = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "textarea.form-control")))
    wait.until(lambda d: textarea.is_enabled() and (textarea.get_attribute("readonly") in (None, "", "false")))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", textarea)
    textarea.click()
    textarea.send_keys(Keys.CONTROL, "a")
    textarea.send_keys(Keys.BACKSPACE)
    textarea.send_keys("Тестовое сообщение")
    time.sleep(30)
    send_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Send']")))
    try:
        send_btn.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", send_btn)

    time.sleep(0.3)

    # Закрыть чат
    close_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Close']")))
    try:
        close_btn.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", close_btn)

    # Выбор "Yes", если ещё не выбран
    select_yes_in_modal(driver)

    # Нажать Submit
    try:
        submit_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Submit']]"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit_btn)
        submit_btn.click()
    except TimeoutException:
        pass

    # OK в SweetAlert
    try:
        ok_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.swal2-confirm.btn-success"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ok_btn)
        ok_btn.click()
    except TimeoutException:
        pass

    # Ждём, что в верху списка появился другой элемент (тот закрыли/исчез)
    try:
        WebDriverWait(driver, 6).until(
            lambda d: not d.find_elements(By.XPATH, chats_xpath) or d.find_elements(By.XPATH, chats_xpath)[0] is not first
        )
    except TimeoutException:
        pass

    return True


# ---------- main test ----------

def test_login():
    driver = webdriver.Chrome()
    driver.set_window_size(1550, 838)
    wait = WebDriverWait(driver, 15)

    # карточки чатов слева
    ONGOING_CHATS_XPATH = (
        "//div[contains(@class,'scroll-container') and contains(@class,'flex-column')]"
        "//div[contains(@class,'chat-item-content')]"
    )

    try:
        print("1) Логин…")
        driver.get("https://gpt3.uat.vivai.ai/auth/login?returnUrl=%2Fdashboard")
        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys("vladimir.t")
        wait.until(EC.presence_of_element_located((By.NAME, "password"))).send_keys("6&5>8x#2N")
        wait.until(EC.element_to_be_clickable((By.ID, "kt_sign_in_submit"))).click()

        print("2) Chats → Direct…")
        wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@title='Chats']"))).click()
        wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@title='Direct']"))).click()
        time.sleep(0.3)

        print("3) Открываю меню аватара…")
        wait_toasts_gone(driver, timeout=6)
        avatar_loc = (By.XPATH, "//app-agent-avatar//span[contains(@class,'p-avatar-text')]")
        avatar = wait.until(EC.element_to_be_clickable(avatar_loc))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", avatar)
        avatar.click()

        print("4) Do not accept chats → Accepting chats…")
        if quick_present(driver, (By.XPATH, "//*[normalize-space()='Do not accept chats']")):
            driver.find_element(By.XPATH, "//*[normalize-space()='Do not accept chats']").click()
        if quick_present(driver, (By.XPATH, "//div[normalize-space()='Accepting chats']")):
            driver.find_element(By.XPATH, "//div[normalize-space()='Accepting chats']").click()

        print("4.1) Закрываю меню…")
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        driver.execute_script("document.body.click();")

        print("5) Обрабатываю чаты…")
        processed = 0
        while True:
            # есть ли вообще элементы в колонке
            any_items = driver.find_elements(By.XPATH, ONGOING_CHATS_XPATH)
            if not any_items:
                break

            ok = process_one_chat(driver, wait, ONGOING_CHATS_XPATH)
            if not ok:
                # возможно остались только закрытые — попробуем убрать их крестиком и проверим ещё раз
                removed = False
                for el in driver.find_elements(By.XPATH, ONGOING_CHATS_XPATH):
                    if is_closed_chat(el):
                        removed = remove_closed_chip(el, driver) or removed
                if not removed:
                    break  # обрабатывать нечего
                else:
                    continue

            processed += 1
            print(f"   Готово для {processed} чата(ов).")

        print("Все чаты обработаны. ✅")

    finally:
        driver.quit()


if __name__ == "__main__":
    test_login()
