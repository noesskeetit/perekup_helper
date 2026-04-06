import asyncio
import json
from pathlib import Path

from loguru import logger
from playwright.async_api import Playwright, async_playwright
from playwright_setup import ensure_playwright_installed

logger.add("logs/app.log", rotation="5 MB", retention="5 days", level="DEBUG")

PLAYWRIGHT_STATE_FILE = "storage/own_cookies.json"

# Черный список: куки, которые не участвуют в авторизации и не нужны для работы парсера.
# Фильтрация в целом работает, но может потребовать дополнительной проверки и доработки.
BLACKLIST_COOKIES = {
    # Служебные и трекинговые куки
    '_gid', '_gat',
    'tmr_*', 'tmr_lvid*', 'tmr_detect', 'fpestid',
    '_fbp', '_fbc',
    'ajs_*', 'amplitude_*',
    '__utm*', '__utma', '__utmb', '__utmc', '__utmt', '__utmz',

    # Куки аналитики и метрик
    '_ym_uid', '_ym_counter', '_ym_metrika_enabled',
    '_gaexp', '_gac_*',
    'mp_*_mixpanel',

    # Рекламные идентификаторы
    '_gcl_*', '_gcl_au',
    'IDE', 'test_cookie',

    # Куки, связанные с A/B тестированием
    'ab_test_*', 'exp_*', 'experiment_*',

    # Параметры окружения браузера
    'viewport_width', 'viewport_height',
    'screenResolution', 'colorDepth',
    'pixelRatio', 'timezone',

    # Локальные данные Авито, не влияющие на авторизацию
    'previousSearch', 'search_*', 'favorites_*',
    'viewed_*', 'recently_viewed',
    'location_*', 'city_*',

    # Куки с длинными значениями (чаще всего используются для трекинга)
    '_avif', '_avmc', '_avte', '_avts',
    '__gads', '__gpi',

    'cookiesyncs', 'idt_*', 'rt_*', 'gcfids', 'afp_cookie', 'sn', 'PVID', 'VID', 'XSRF-TOKEN',
    'utid', 'JWT-Cookie', 'adudid', 'BeeAID', 'suuid3', 'ut',
}

# Белый список: куки, необходимые для сохранения авторизации и корректной работы сессии
WHITELIST_COOKIES = {
    'auth',           # Основной флаг авторизации (auth=1)
    'sessid',         # Сессионный токен (JWT)
    'srv_id',         # Идентификатор сервера (affinity)
    '_avisc',         # Внутренняя кука Авито
    'rt',             # Предположительно refresh-токен
    'uid',            # Уникальный идентификатор пользователя
    'user_id',        # Альтернативное имя user ID
    'sid',            # Идентификатор сессии
    'csrf',           # CSRF-токен
    'csrftoken',      # Альтернативное имя CSRF-токена
    'csprefid',       # Настройки безопасности
    'cssid',          # Идентификатор сессии
    'f',              # Fingerprint браузера
    'ft',             # Токен fingerprint
    'sx',             # Сессионные данные (сжатые)
}


def should_keep_cookie(cookie_name: str) -> bool:
    """
    Определяет, следует ли сохранять указанную куку.

    Логика:
    1. Если кука соответствует белому списку — сохраняется.
    2. Если кука соответствует черному списку — отбрасывается.
    3. Все остальные куки по умолчанию не сохраняются.
    """
    # Проверка по белому списку (приоритет)
    for whitelist_pattern in WHITELIST_COOKIES:
        if whitelist_pattern.endswith('*'):
            # Поддержка wildcard-паттернов
            if cookie_name.startswith(whitelist_pattern[:-1]):
                return True
        elif cookie_name == whitelist_pattern:
            return True

    # Проверка по черному списку
    for blacklist_pattern in BLACKLIST_COOKIES:
        if blacklist_pattern.endswith('*'):
            # Поддержка wildcard-паттернов
            if cookie_name.startswith(blacklist_pattern[:-1]):
                return False
        elif cookie_name == blacklist_pattern:
            return False

    # Если кука не входит в белый список — не сохраняем
    return False


async def prompt_user_login(playwright: Playwright):
    ensure_playwright_installed("chromium")
    chromium = playwright.chromium

    # Параметры запуска браузера
    launch_args = {
        "headless": False,
        "chromium_sandbox": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--start-maximized",
        ]
    }

    # Параметры контекста браузера
    context_args = {
        "is_mobile": False,
        "has_touch": False,
        "locale": "ru-RU",
        "no_viewport": True,
    }

    try:
        browser = await chromium.launch(**launch_args)
        context = await browser.new_context(**context_args)
        page = await context.new_page()
    except:
        logger.error("Не удалось запустить браузер в графическом режиме")
        return

    # Открываем страницу авторизации Авито
    await page.goto(url="https://www.avito.ru/#login?authsrc=h", timeout=0)

    # Проверяем, не появилась ли страница с ограничением доступа (например, капча)
    if "Доступ ограничен" in await page.title():
        await page.reload()

    logger.info("Выполните вход в аккаунт в открывшемся окне. Ожидание появления cookie auth=1...")

    # Ожидание успешной авторизации (появление cookie auth=1)
    auth_detected = False
    try:
        while not auth_detected:
            await asyncio.sleep(1)

            cookies = await context.cookies()

            for cookie in cookies:
                if cookie.get('name') == 'auth' and cookie.get('value') == '1':
                    auth_detected = True
                    logger.info("Авторизация успешно обнаружена (cookie auth=1)")
                    break

            # Проверка: пользователь не закрыл браузер вручную
            if not page.context.browser.is_connected():
                logger.error("Браузер был закрыт до завершения авторизации")
                return

    except Exception as e:
        logger.error(f"Ошибка при ожидании авторизации: {e}")
        return

    # Даем время на загрузку всех дополнительных кук после логина
    await asyncio.sleep(5)

    try:
        cookies = await context.cookies()

        # Разделение кук на сохраняемые и игнорируемые
        filtered_cookies = {}
        skipped_cookies = []

        for cookie in cookies:
            cookie_name = cookie['name']

            if should_keep_cookie(cookie_name):
                filtered_cookies[cookie_name] = cookie['value']
                logger.debug(f"Сохранена кука: {cookie_name}")
            else:
                skipped_cookies.append(cookie_name)

        # Подготовка пути и сохранение файла сессии
        state_filepath = Path(PLAYWRIGHT_STATE_FILE)
        state_filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(state_filepath, 'w', encoding='utf-8') as f:
            json.dump({"cookies": filtered_cookies}, f, indent=2, ensure_ascii=False)

        # Ограничение прав доступа к файлу (если поддерживается ОС)
        try:
            state_filepath.chmod(0o600)
        except:
            pass

        await context.close()

        # Логирование итоговой информации
        logger.info(f"Сессия сохранена: {PLAYWRIGHT_STATE_FILE}")
        logger.info(f"Количество сохранённых кук: {len(filtered_cookies)}")
        logger.info(f"Количество пропущенных кук: {len(skipped_cookies)}")

        if skipped_cookies:
            logger.debug(
                f"Пропущенные куки: {', '.join(skipped_cookies[:10])}" +
                ("..." if len(skipped_cookies) > 10 else "")
            )

    except Exception as e:
        logger.error(f"Ошибка при сохранении сессии в {PLAYWRIGHT_STATE_FILE}: {e}")


async def wrapper():
    async with async_playwright() as playwright:
        await prompt_user_login(playwright)


if __name__ == "__main__":
    asyncio.run(wrapper())