import asyncio
import threading
import time
import os
from pathlib import Path

import flet as ft
from loguru import logger

from dto import AvitoConfig
from integrations.notifications.factory import build_notifier
from lang import *
from load_config import save_avito_config, load_avito_config
from parser_cls import AvitoParse
from utils import prompt_user_login


def main(page: ft.Page):
    page.title = 'AviPars'
    page.window.icon = str(Path(__file__).parent / "assets" / "logo.ico")
    page.theme_mode = ft.ThemeMode.DARK
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.window.width = 1400
    page.window.height = 900
    page.window.min_width = 900
    page.window.min_height = 600
    page.padding = 0

    page.window.center()

    is_run = False
    stop_event = threading.Event()
    current_theme = ft.ThemeMode.DARK

    def set_up():
        """Загружает настройки из config.toml и применяет к интерфейсу"""
        try:
            config = load_avito_config("config.toml")
        except Exception as err:
            logger.error(f"Ошибка при загрузке конфига: {err}")
            return

        url_input.value = "\n".join(config.urls or [])
        tg_chat_id.value = "\n".join(config.tg_chat_id or [])
        tg_token.value = config.tg_token or ""
        vk_token.value = config.vk_token or ""
        vk_user_id.value = "\n".join(config.vk_user_id or [])
        count_page.value = str(config.count)
        keys_word_white_list.value = "\n".join(config.keys_word_white_list or [])
        keys_word_black_list.value = "\n".join(config.keys_word_black_list or [])
        max_price.value = str(config.max_price)
        min_price.value = str(config.min_price)
        geo.value = config.geo or ""
        proxy.value = config.proxy_string or ""
        proxy_change_ip.value = config.proxy_change_url or ""
        pause_general.value = config.pause_general or 60
        pause_between_links.value = config.pause_between_links or 5
        max_age.value = config.max_age or 0
        seller_black_list.value = "\n".join(config.seller_black_list or [])
        ignore_ads_in_reserv.value = config.ignore_reserv
        ignore_promote_ads.value = config.ignore_promotion
        max_count_of_retry.value = config.max_count_of_retry or 5
        one_time_start.value = config.one_time_start
        one_file_for_link.value = config.one_file_for_link
        parse_views.value = config.parse_views
        save_xlsx.value = config.save_xlsx
        use_webdriver.value = config.use_webdriver
        use_bypass_api.value = config.use_bypass_api
        cookies_api_key.value = config.cookies_api_key
        use_own_account.value = config.use_own_cookies
        parse_phone.value = config.parse_phone
        proxy_notifier.value = config.proxy_notifier

        page.update()

    def toggle_theme(e):
        nonlocal current_theme
        if page.theme_mode == ft.ThemeMode.DARK:
            page.theme_mode = ft.ThemeMode.LIGHT
            current_theme = ft.ThemeMode.LIGHT
            theme_btn.icon = ft.icons.LIGHT_MODE
            theme_btn.tooltip = "Темная тема"
        else:
            page.theme_mode = ft.ThemeMode.DARK
            current_theme = ft.ThemeMode.DARK
            theme_btn.icon = ft.icons.DARK_MODE
            theme_btn.tooltip = "Светлая тема"
        page.update()

    def to_int_safe(value, default=0):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def save_config():
        """Сохраняет настройки в TOML"""
        config = {"avito": {
            "tg_token": tg_token.value or "",
            "tg_chat_id": tg_chat_id.value.splitlines() if tg_chat_id.value else [],
            "vk_token": vk_token.value or "",
            "vk_user_id": vk_user_id.value.splitlines() if vk_user_id.value else [],
            "urls": url_input.value.splitlines() if url_input.value else [],
            "count": to_int_safe(count_page.value, 1),
            "keys_word_white_list": keys_word_white_list.value.splitlines() if keys_word_white_list.value else [],
            "keys_word_black_list": keys_word_black_list.value.splitlines() if keys_word_black_list.value else [],
            "seller_black_list": seller_black_list.value.splitlines() if seller_black_list.value else [],
            "max_price": to_int_safe(max_price.value, 99999999),
            "min_price": to_int_safe(min_price.value, 0),
            "geo": geo.value or "",
            "proxy_string": proxy.value or "",
            "proxy_change_url": proxy_change_ip.value or "",
            "pause_general": to_int_safe(pause_general.value, 3),
            "pause_between_links": to_int_safe(pause_between_links.value, 1),
            "max_age": to_int_safe(max_age.value, 0),
            "max_count_of_retry": to_int_safe(max_count_of_retry.value, 5),
            "ignore_reserv": ignore_ads_in_reserv.value,
            "ignore_promotion": ignore_promote_ads.value,
            "one_time_start": one_time_start.value,
            "one_file_for_link": one_file_for_link.value,
            "parse_views": parse_views.value,
            "save_xlsx": save_xlsx.value,
            "use_webdriver": use_webdriver.value,
            "use_bypass_api": use_bypass_api.value,
            "cookies_api_key": cookies_api_key.value,
            "use_own_cookies": use_own_account.value,
            "parse_phone": parse_phone.value,
            "proxy_notifier": proxy_notifier.value,
        }}

        save_avito_config(config)
        logger.debug("Настройки сохранены в config.toml")

    def close_dlg(e):
        dlg_modal_proxy.open = False
        page.update()

    def logger_console_init():
        logger.add(logger_console_widget, format="{time:HH:mm:ss} - {message}")

    def logger_console_widget(message):
        MAX_LOG_LINES = 500
        console_widget.controls.append(
            ft.Text(
                message.rstrip(),
                size=12,
                color=ft.colors.GREEN,
            )
        )

        if len(console_widget.controls) > MAX_LOG_LINES:
            console_widget.controls.pop(0)

        page.update()

    def telegram_log_test(e):
        """Тестирование отправки уведомлений"""
        logger.info("Проверка настроек уведомлений")

        try:
            config = AvitoConfig(
                tg_token=tg_token.value,
                tg_chat_id=tg_chat_id.value.split(),
                proxy_notifier=proxy_notifier.value,
                urls=[]
            )

            notifier = build_notifier(config=config)
            notifier.notify(message="Тестовое сообщение")

        except Exception as err:
            logger.error(f"Ошибка при проверке Telegram: {err}")

    def vk_log_test(e):
        """Тестирование отправки уведомлений VK"""
        logger.info("Проверка настроек VK")

        try:
            config = AvitoConfig(
                vk_token=vk_token.value,
                vk_user_id=vk_user_id.value.splitlines(),
                urls=[]
            )

            notifier = build_notifier(config=config)
            notifier.notify(message="Тестовое сообщение от парсера AviPars")

        except Exception as err:
            logger.error(f"Ошибка при проверке VK: {err}")

    dlg_modal_proxy = ft.AlertDialog(
        modal=True,
        title=ft.Text("Помощь по разделу:"),
        content=ft.Container(
            content=ft.Text(PROXY_PANEL_HELP, size=14),
            width=600,
            height=600,
            padding=10
        ),
        actions=[
            ft.TextButton("Купить прокси",
                          on_click=lambda e: page.launch_url(PROXY_LINK)),
            ft.TextButton("Зарегистрироваться на spfa.ru",
                          on_click=lambda e: page.launch_url(SPFA_LINK)),
            ft.TextButton("Отмена", on_click=close_dlg),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        on_dismiss=lambda e: print("Modal dialog dismissed!"),
    )

    def open_dlg_modal(e):
        page.overlay.append(dlg_modal_proxy)
        dlg_modal_proxy.open = True
        page.update()

    def on_click_use_own_cookies(e):
        cookies_exist = os.path.exists("storage/own_cookies.json")

        account_login_btn.text = (
            "Cookies уже есть" if cookies_exist else
            "Войти в аккаунт (обязательно)" if use_own_account.value else
            "Войти в аккаунт (опционально)"
        )
        page.update()

    async def btn_prompt_user_login_handler(e):
        await prompt_user_login.wrapper()
        page.update()
        await asyncio.sleep(2)
        on_click_use_own_cookies(None)
        logger.info("update")

    def start_parser(e):
        nonlocal is_run
        result_proxy = check_string()
        result_own_cookies = check_own_cookies()
        if not result_proxy or not result_own_cookies:
            return
        logger.info("Старт")
        stop_event.clear()
        save_config()
        console_widget.height = 300
        start_btn.visible = False
        stop_btn.visible = True
        is_run = True
        page.update()
        while is_run and not stop_event.is_set():
            run_process()
            if not is_run:
                return
            logger.info("Пауза между повторами")
            for _ in range(int(pause_general.value if pause_general.value else 300)):
                time.sleep(1)
                if not is_run:
                    logger.info("Завершено")
                    start_btn.text = "Старт"
                    start_btn.disabled = False
                    page.update()
                    return
            if one_time_start.value:
                stop_event.set()
                page.window.close()

    def stop_parser(e):
        nonlocal is_run
        stop_event.set()
        logger.debug("Стоп")
        is_run = False
        stop_btn.visible = False
        start_btn.visible = True
        start_btn.text = "Останавливаюсь..."
        start_btn.disabled = True
        page.update()

    def check_own_cookies():
        if use_own_account.value and not os.path.exists("storage/own_cookies.json"):
            dlg_modal = ft.AlertDialog(
                modal=True,
                title=ft.Text("Не найден cookies"),
                content=ft.Text(NOT_FOUND_OWN_COOKIES),
                actions=[
                    ft.TextButton("Понятно", on_click=lambda e: page.close(dlg_modal)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: print("Окно закрыто"),
            )
            page.open(dlg_modal)
            return False
        return True

    def check_string():
        if proxy.value and ("proxy.site" not in proxy.value or "@" not in proxy.value):
            dlg_modal = ft.AlertDialog(
                modal=True,
                title=ft.Text("Проблемы с прокси"),
                content=ft.Text(UNSUPPORT_PROXY),
                actions=[
                    ft.TextButton("Купить совместимые прокси",
                                  on_click=lambda e: page.launch_url(PROXY_LINK)),
                    ft.TextButton("Понятно", on_click=lambda e: page.close(dlg_modal)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: print("Окно закрыто"),
            )
            page.open(dlg_modal)
            return False
        return True

    def check_api_key_exist(e):
        if parse_phone.value and not cookies_api_key.value:
            parse_phone.value = False
            dlg_modal = ft.AlertDialog(
                modal=True,
                title=ft.Text("Не заполнен api ключ"),
                content=ft.Text(NEED_TO_INSERT_API_KEY),
                actions=[
                    ft.TextButton("Зарегистрироваться на spfa",
                                  on_click=lambda e: page.launch_url(SPFA_LINK)),
                    ft.TextButton("Понятно", on_click=lambda e: page.close(dlg_modal)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: print("Окно закрыто"),
            )
            page.open(dlg_modal)
            return False
        return True

    def run_process():
        config = load_avito_config("config.toml")
        parser = AvitoParse(config, stop_event=stop_event)
        parsing_thread = threading.Thread(target=parser.parse)
        parsing_thread.start()
        parsing_thread.join()
        start_btn.disabled = False
        start_btn.text = "Старт"
        page.update()

    def create_section_card(title: str, content: list[ft.Control], icon: str = ""):
        """Создает красивую карточку-секцию"""
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(icon, size=20, color=ft.colors.GREEN_400) if icon else ft.Container(),
                            ft.Text(title, size=14, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(height=1),
                    ft.Column(content, spacing=10),
                ],
                spacing=10,
            ),
            padding=15,
            border_radius=10,
            bgcolor=ft.colors.SURFACE_VARIANT,
            border=ft.border.all(1, ft.colors.OUTLINE)
        )

    # Инициализация элементов интерфейса
    url_input = ft.TextField(
        label="Вставьте начальную ссылку или ссылки (Enter между значениями)",
        multiline=True,
        min_lines=3,
        max_lines=10,
        expand=True,
        tooltip=URL_INPUT_HELP,
        text_size=12,
    )
    min_price = ft.TextField(label="Мин. цена", expand=True, text_size=12, height=40, tooltip=MIN_PRICE_HELP)
    max_price = ft.TextField(label="Макс. цена", expand=True, text_size=12, height=40, tooltip=MAX_PRICE_HELP)
    keys_word_white_list = ft.TextField(
        label="Ключевые слова (Enter между значениями)",
        multiline=True,
        min_lines=2,
        max_lines=8,
        expand=True,
        tooltip=KEYWORD_INPUT_HELP,
        text_size=12,
    )
    keys_word_black_list = ft.TextField(
        label="Исключающие слова (Enter между значениями)",
        multiline=True,
        min_lines=2,
        max_lines=8,
        expand=True,
        tooltip=KEYWORD_BLACK_INPUT_HELP,
        text_size=12,
    )
    count_page = ft.TextField(label="Кол-во страниц", expand=True, tooltip=COUNT_PAGE_HELP, text_size=12, height=40)
    pause_general = ft.TextField(label="Пауза между повторами (сек.)", expand=True, text_size=12, height=40, tooltip=PAUSE_GENERAL_HELP)
    pause_between_links = ft.TextField(label="Пауза между ссылками (сек.)", text_size=12, height=40, expand=True, tooltip=PAUSE_BETWEEN_LINKS_HELP)
    max_age = ft.TextField(label="Макс. возраст объявления (сек.)", text_size=12, height=40, expand=True, tooltip=MAX_AGE_HELP)
    max_count_of_retry = ft.TextField(label="Макс. повторов", text_size=12, height=40, expand=True, tooltip=MAX_COUNT_OF_RETRY_HELP)
    tg_token = ft.TextField(label="Telegram Token", text_size=12, height=50, expand=True, tooltip=TG_TOKEN_HELP)
    tg_chat_id = ft.TextField(label="Chat ID (Enter между значениями)", multiline=True, expand=True, text_size=12, height=50, tooltip=TG_CHAT_ID_HELP)
    proxy_notifier = ft.TextField(label="Прокси для Telegram", multiline=False, expand=True, text_size=12, height=50, tooltip=PROXY_NOTIFIER_HELP)
    btn_test_tg = ft.ElevatedButton(text="Тест Telegram", disabled=False, on_click=telegram_log_test, expand=True, tooltip=BTN_TEST_TG_HELP)
    vk_token = ft.TextField(label="VK Token", text_size=12, height=50, expand=True, tooltip="Токен доступа VK API")
    vk_user_id = ft.TextField(label="VK User ID (Enter между значениями)", multiline=True, expand=True, text_size=12, height=50, tooltip="ID пользователей VK")
    btn_test_vk = ft.ElevatedButton(text="Тест VK", disabled=False, on_click=vk_log_test, expand=True, tooltip="Тестовое сообщение в VK")
    proxy = ft.TextField(label="Прокси (username:password@site:port)", expand=True, tooltip=PROXY_HELP, password=True, can_reveal_password=True)
    proxy_change_ip = ft.TextField(label="URL для смены IP (мобильные прокси)", expand=True, tooltip=PROXY_CHANGE_IP_HELP)
    proxy_btn_panel_help = ft.FilledButton(text="Справка по прокси", on_click=open_dlg_modal, expand=True, tooltip=PROXY_BTN_HELP_HELP)

    proxy_help_icon = ft.IconButton(icon=ft.icons.HELP_OUTLINE, tooltip="Справка по прокси", icon_size=20)
    cookies_api_key = ft.TextField(label="API ключ spfa.ru", password=True, can_reveal_password=True, expand=True)
    use_bypass_api = ft.Checkbox("Использовать spfa сервис", value=False)
    bypass_api_key_help_icon = ft.IconButton(icon=ft.icons.HELP_OUTLINE, tooltip="Что такое api-key", icon_size=20)

    use_own_account = ft.Checkbox("Использовать свой аккаунт", value=False, on_change=on_click_use_own_cookies)

    if os.path.exists("storage/own_cookies.json"):
        btn_text = "Cookies готовы"
    else:
        btn_text = "Войти в аккаунт"

    account_login_btn = ft.ElevatedButton(text=btn_text, icon=ft.icons.LOGIN, on_click=btn_prompt_user_login_handler, expand=True, tooltip=PROMPT_USER_LOGIN_HELP)
    account_login_btn_help_icon = ft.IconButton(icon=ft.icons.HELP_OUTLINE, tooltip="Использование своего аккаунта", icon_size=20)

    geo = ft.TextField(label="Город", expand=True, text_size=12, height=40, tooltip=GEO_HELP)
    seller_black_list = ft.TextField(label="Черный список продавцов (Enter между значениями)", multiline=True, min_lines=2, max_lines=8, expand=True, tooltip=BLACK_LIST_OF_SELLER_HELP, text_size=12)
    
    start_btn = ft.FilledButton("СТАРТ", icon=ft.icons.PLAY_ARROW_ROUNDED, expand=True, on_click=start_parser)
    stop_btn = ft.OutlinedButton("СТОП", icon=ft.icons.STOP_ROUNDED, expand=True, on_click=stop_parser, visible=False, style=ft.ButtonStyle(bgcolor=ft.colors.RED_400))
    
    console_widget = ft.ListView(expand=True, spacing=2, auto_scroll=True, height=300)

    ignore_ads_in_reserv = ft.Checkbox(label="Игнорировать резервы", value=True, tooltip=IGNORE_RESERV_HELP)
    ignore_promote_ads = ft.Checkbox(label="Игнорировать продвинутые", value=False)
    one_time_start = ft.Checkbox(label="Выключить после завершения", value=False, tooltip=ONE_TIME_START_HELP)
    one_file_for_link = ft.Checkbox(label="Отдельный файл для каждой ссылки", value=False, tooltip=ONE_FILE_FOR_LINK_HELP)
    parse_views = ft.Checkbox(label="Парсить просмотры", value=False, tooltip=PARSE_VIEWS_HELP)
    parse_phone = ft.Checkbox(label="Парсить телефоны", value=False, on_change=check_api_key_exist, tooltip=PARSE_PHONE_HELP)
    save_xlsx = ft.Checkbox(label="Сохранять в Excel", value=True, tooltip=SAVE_XLSX_HELP)
    use_webdriver = ft.Checkbox(label="Использовать браузер", value=True, tooltip=USE_WEBDRIVER_HELP)

    # Кнопка переключения темы
    theme_btn = ft.IconButton(icon=ft.icons.DARK_MODE, tooltip="Светлая тема", on_click=toggle_theme, icon_size=24)

    # Заголовок
    app_header = ft.Container(
        content=ft.Row(
            [
                ft.Column([
                    ft.Text("AviPars", size=36, weight=ft.FontWeight.BOLD, color=ft.colors.GREEN_400),
                    ft.Text("Профессиональный парсер объявлений", size=12, opacity=0.6),
                ], spacing=0),
                ft.Container(expand=True),
                theme_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=20,
        ),
        padding=20,
        border=ft.border.only(bottom=ft.BorderSide(2, ft.colors.GREEN_400))
    )

    # Создание вкладок
    tabs_content = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tab_alignment=ft.TabAlignment.START,
        expand=True,
        tabs=[
            # TAB 1: Основное
            ft.Tab(
                text="Основное",
                icon=ft.icons.TUNE_ROUNDED,
                content=ft.Container(
                    content=ft.Column(
                        [
                            create_section_card("Источники данных", [url_input], ft.icons.LINK),
                            ft.Row([
                                ft.Container(expand=True, content=create_section_card("Минимальная цена", [min_price])),
                                ft.Container(expand=True, content=create_section_card("Максимальная цена", [max_price])),
                            ], spacing=10, expand=True),
                            create_section_card("Количество страниц", [count_page], ft.icons.PAGES),
                        ],
                        spacing=10,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=15,
                    expand=True,
                )
            ),

            # TAB 2: Фильтры
            ft.Tab(
                text="Фильтры",
                icon=ft.icons.FILTER_ALT_ROUNDED,
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row([
                                ft.Container(expand=True, content=create_section_card("Ключевые слова", [keys_word_white_list], ft.icons.CHECK)),
                                ft.Container(expand=True, content=create_section_card("Исключающие слова", [keys_word_black_list], ft.icons.BLOCK)),
                            ], spacing=10, expand=True),
                            create_section_card("Черный список продавцов", [seller_black_list], ft.icons.PERSON_OFF),
                            ft.Row([
                                ft.Container(expand=True, content=create_section_card("Город", [geo], ft.icons.LOCATION_ON)),
                                ft.Container(expand=True, content=create_section_card("Макс. возраст (сек.)", [max_age])),
                            ], spacing=10, expand=True),
                            create_section_card("Дополнительные фильтры", [
                                ft.Row([ignore_ads_in_reserv, ignore_promote_ads], spacing=20),
                            ]),
                        ],
                        spacing=10,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=15,
                    expand=True,
                )
            ),

            # TAB 3: Уведомления
            ft.Tab(
                text="Уведомления",
                icon=ft.icons.NOTIFICATIONS_ACTIVE_ROUNDED,
                content=ft.Container(
                    content=ft.Column(
                        [
                            create_section_card("Telegram", [
                                ft.Row([tg_token, tg_chat_id], spacing=10),
                                ft.Row([proxy_notifier], spacing=10),
                                btn_test_tg,
                            ], ft.icons.SEND),
                            ft.Divider(height=20),
                            create_section_card("VK", [
                                ft.Row([vk_token, vk_user_id], spacing=10),
                                btn_test_vk,
                            ], ft.icons.CHAT),
                        ],
                        spacing=10,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=15,
                    expand=True,
                )
            ),

            # TAB 4: Прокси
            ft.Tab(
                text="Прокси",
                icon=ft.icons.ROUTER_ROUNDED,
                content=ft.Container(
                    content=ft.Column(
                        [
                            create_section_card("Сторонний сервис (spfa.ru)", [
                                ft.Row([use_bypass_api, cookies_api_key, bypass_api_key_help_icon], spacing=10),
                            ], ft.icons.CLOUD),
                            create_section_card("Мобильные/серверные прокси", [
                                ft.Row([proxy, proxy_change_ip, proxy_help_icon], spacing=10),
                            ], ft.icons.PHONE_ANDROID),
                            create_section_card("Свой аккаунт", [
                                ft.Row([use_own_account, account_login_btn, account_login_btn_help_icon], spacing=10),
                            ], ft.icons.PERSON),
                            ft.Container(margin=ft.margin.only(top=10)),
                            ft.Row([proxy_btn_panel_help], alignment=ft.MainAxisAlignment.CENTER),
                        ],
                        spacing=10,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=15,
                    expand=True,
                )
            ),

            # TAB 5: Параметры
            ft.Tab(
                text="Параметры",
                icon=ft.icons.SETTINGS_ROUNDED,
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row([
                                ft.Container(expand=True, content=create_section_card("Пауза повторов (сек.)", [pause_general])),
                                ft.Container(expand=True, content=create_section_card("Пауза ссылок (сек.)", [pause_between_links])),
                            ], spacing=10),
                            create_section_card("Макс. повторов", [max_count_of_retry]),
                            create_section_card("Поведение парсера", [
                                ft.Row([one_time_start, one_file_for_link], spacing=20),
                                ft.Row([parse_views, save_xlsx, use_webdriver], spacing=20),
                            ]),
                        ],
                        spacing=10,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=15,
                    expand=True,
                )
            ),

            # TAB 6: Запуск
            ft.Tab(
                text="Запуск",
                icon=ft.icons.PLAY_CIRCLE_ROUNDED,
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(
                                content=ft.Column(
                                    [
                                        ft.Row([
                                            ft.Icon(ft.icons.TERMINAL, size=20, color=ft.colors.GREEN_400),
                                            ft.Text("Консоль парсера", size=14, weight=ft.FontWeight.BOLD),
                                        ], spacing=10),
                                        ft.Divider(height=1),
                                        console_widget,
                                    ],
                                    spacing=10,
                                    expand=True,
                                ),
                                padding=15,
                                border_radius=10,
                                bgcolor=ft.colors.SURFACE_VARIANT,
                                border=ft.border.all(1, ft.colors.OUTLINE),
                                expand=True,
                            ),
                            ft.Row([start_btn, stop_btn], spacing=10, expand=True),
                        ],
                        spacing=15,
                        expand=True,
                    ),
                    padding=15,
                    expand=True,
                )
            ),
        ]
    )

    def start_page():
        page.add(
            ft.Column(
                [
                    app_header,
                    tabs_content,
                ],
                spacing=0,
                expand=True,
            )
        )

    set_up()
    start_page()
    logger_console_init()


ft.app(
    target=main,
    assets_dir="assets",
)
