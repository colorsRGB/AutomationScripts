from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def start_chat(index):
    options = webdriver.ChromeOptions()
    options.add_argument("--incognito")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 10)

    try:
        driver.get("https://ww-host.test.vivai.ai/74f5097d-e80c-4493-b989-91b57d592106")
        driver.set_window_size(1550, 838)

        # Переключение в iframe
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe")))

        # Клик по иконке
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".key-1qn0tbk"))).click()

        # Ввод userId
        user_id_input = wait.until(EC.visibility_of_element_located((By.ID, "userId")))
        user_id_input.send_keys(f"Test {index}")

        # Клик по Start chat
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".key-t91e19"))).click()

        # Ввод сообщения
        message_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".key-jml02v")))
        message_input.send_keys(f"Test {index}")

        # Нажатие на кнопку отправки (второй svg)
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "svg.key-b44e5x")) > 1)
        driver.find_elements(By.CSS_SELECTOR, "svg.key-b44e5x")[1].click()

        print(f"[{index}] Успешно отправлено")



    except Exception as e:
        print(f"[{index}] Ошибка: {e}")
    finally:
        driver.quit()

# Запуск чатов с Test 1 по Test 10
for i in range(0, 20000):
    start_chat(i)
