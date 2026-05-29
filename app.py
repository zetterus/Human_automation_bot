import requests
import logging
import json
import csv
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
import re
import time

# ============== НАСТРОЙКИ ==============
# Замените эти значения на ваши данные для входа.
# РЕКОМЕНДУЕТСЯ использовать переменные окружения для хранения
# конфиденциальных данных вместо того, чтобы хранить их прямо в коде.
LOGIN_EMAIL = os.getenv("HUMAN_UA_EMAIL", "zetter.vitrolic@gmail.com")
LOGIN_PASSWORD = os.getenv("HUMAN_UA_PASSWORD", "Rfrfirf22")
LOG_FILE = "humanua_api.log"  # Файл для логов
# Вывод подробных логов в консоль. Установите в False для продакшена.
DEBUG_MODE = True
SHOW_CONSOLE_LOGS = True
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
RESPONSES_DIR = os.path.join(BASE_DIR, "responses")


# =======================================

class HumanUAAuth:
    def __init__(self, email: str, password: str):
        """
        Инициализация класса для авторизации в Human.UA.
        Args:
            email (str): Электронная почта для входа.
            password (str): Пароль для входа.
        """
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.api_url = "https://api.human.ua/v1"
        self.system_info_scope_id: Optional[int] = None
        self.institution_id: Optional[int] = None
        self.user_id: Optional[int] = None
        self.global_user_id: Optional[int] = None
        self.academic_year_id: Optional[int] = None
        self._setup_logging()
        self._configure_session()

    def get_system_info_scope_id_from_institutions(self) -> bool:
        """Получает обязательный system_info_scope_id из /user/institutions (institutions[0].id)."""
        self.logger.info("Попытка получения system_info_scope_id из /user/institutions...")
        url = f"{self.api_url}/user/institutions"
        response: Optional[requests.Response] = None
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            raw_data = response.json()

            data: Any = raw_data
            if isinstance(data, dict):
                institutions = data.get('institutions')
                if not isinstance(institutions, list):
                    self.logger.error("Поле institutions отсутствует или не является списком.")
                    return False
                data = institutions

            if not isinstance(data, list) or not data:
                self.logger.error("Ответ /user/institutions пуст или не является списком.")
                return False

            first_item = data[0]
            if not isinstance(first_item, dict):
                self.logger.error("Первый элемент institutions не является объектом.")
                return False

            scope_id = self._parse_int(first_item.get('id'))
            if scope_id is None:
                self.logger.error("Не найден institutions[0].id для system_info_scope_id.")
                return False

            self.system_info_scope_id = scope_id
            self.logger.info(f"system_info_scope_id получен: {self.system_info_scope_id}")
            return True
        except requests.exceptions.JSONDecodeError:
            content_type = response.headers.get('Content-Type', 'unknown') if response is not None else 'unknown'
            preview = (response.text[:300] if response is not None else '').replace('\n', ' ')
            self.logger.error(
                f"Endpoint /user/institutions вернул не-JSON ответ. "
                f"status={response.status_code if response is not None else 'unknown'}, "
                f"content-type={content_type}, body-preview={preview}"
            )
            return False
        except requests.RequestException as e:
            self.logger.error(f"Ошибка запроса endpoint /user/institutions: {str(e)}")
            return False

    def _setup_logging(self):
        """Настройка логирования."""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Избегаем дублирования обработчиков при повторном создании класса.
        if self.logger.handlers:
            self.logger.handlers.clear()

        # Обработчик для файла
        file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
        # Пишем построчно, чтобы логи было видно во время выполнения.
        try:
            file_handler.stream.reconfigure(line_buffering=True, write_through=True)
        except Exception:
            pass
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
        self.logger.addHandler(file_handler)

        # Обработчик для консоли
        if SHOW_CONSOLE_LOGS or DEBUG_MODE:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
            self.logger.addHandler(console_handler)

        level_name = "DEBUG" if DEBUG_MODE else "INFO"
        self.logger.info(f"Логирование настроено. Уровень: {level_name}")

    def _configure_session(self):
        """Настройка HTTP-сессии."""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            # Просим не сжимать ответ, чтобы избежать проблем декодирования JSON.
            'Accept-Encoding': 'identity',
            'Accept': 'application/json, text/plain, */*',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json;charset=UTF-8'
        })
        self.logger.info("Сессия HTTP настроена.")

    def login(self) -> bool:
        """
        Авторизация в системе Human.UA с помощью API.
        """
        self.logger.info("Попытка авторизации...")
        try:
            payload = json.dumps({
                "email": self.email,
                "password": self.password,
                "remember_me": True
            })
            response = self.session.post(f"{self.api_url}/auth", data=payload, timeout=10)
            response.raise_for_status()

            if 'authToken' in self.session.cookies:
                self.logger.info("Авторизация прошла успешно.")
                return True
            else:
                self.logger.error("Авторизация прошла успешно, но 'authToken' не найден в cookies.")
                self.logger.debug(f"Полученные cookies: {self.session.cookies.get_dict()}")
                return False
        except requests.exceptions.Timeout:
            self.logger.error("Ошибка авторизации: Истекло время ожидания ответа от сервера.")
            return False
        except requests.RequestException as e:
            self.logger.error(f"Ошибка при авторизации: {e}")
            if e.response is not None:
                self.logger.error(f"Ответ сервера: {e.response.text}")
            return False

    def _parse_int(self, value: Any) -> Optional[int]:
        """Безопасное преобразование значения в int."""
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _academic_year_sort_key(self, year_item: Dict[str, Any]) -> Any:
        """Ключ сортировки academicYears по полю name, например 2025/2026."""
        name = str(year_item.get('name', ''))
        found_years = re.findall(r'\d{4}', name)
        if len(found_years) >= 2:
            return int(found_years[0]), int(found_years[1])
        if len(found_years) == 1:
            return int(found_years[0]), 0
        return 0, 0

    def get_ids_from_system_info(self) -> bool:
        """
        Получение ID из /v1/{scope_id}/system/info:
        - global_user_id: .globalUser.id
        - user_id: .lmsUser.id
        - institution_id: .lmsUser.institution.id
        - academic_year_id: id записи academicYears с максимальным name
        """
        if self.system_info_scope_id is None:
            self.logger.error("system_info_scope_id не установлен. Невозможно запросить /system/info.")
            return False

        url = f"{self.api_url}/{self.system_info_scope_id}/system/info"
        response: Optional[requests.Response] = None
        self.logger.info(f"Попытка получения ID из /{self.system_info_scope_id}/system/info...")
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                self.logger.error(f"Ответ /{self.system_info_scope_id}/system/info не dict, а {type(data).__name__}")
                return False

            self.global_user_id = self._parse_int((data.get('globalUser') or {}).get('id'))
            if self.global_user_id is not None:
                self.logger.info(f"global_user_id получен: {self.global_user_id}")
            else:
                self.logger.warning("global_user_id (.globalUser.id) не найден")

            lms_user = data.get('lmsUser') or {}
            self.user_id = self._parse_int((lms_user or {}).get('id'))
            if self.user_id is not None:
                self.logger.info(f"user_id получен: {self.user_id}")
            else:
                self.logger.warning("user_id (.lmsUser.id) не найден")

            if self.user_id is None:
                self.logger.error("user_id обязателен, но не найден в /system/info")
                return False

            lms_institution = (lms_user or {}).get('institution') or {}
            self.institution_id = self._parse_int((lms_institution or {}).get('id'))
            if self.institution_id is not None:
                self.logger.info(f"institution_id получен: {self.institution_id}")
            else:
                self.logger.warning("institution_id (.lmsUser.institution.id) не найден")

            academic_years = data.get('academicYears')
            if isinstance(academic_years, list) and academic_years:
                latest_year = max(academic_years, key=self._academic_year_sort_key)
                self.academic_year_id = self._parse_int(latest_year.get('id'))
                if self.academic_year_id is not None:
                    self.logger.info(
                        f"academic_year_id получен: {self.academic_year_id} (name={latest_year.get('name')})"
                    )
                else:
                    self.logger.warning("academic_year_id не найден в выбранной записи academicYears")
            else:
                self.logger.warning("academicYears отсутствует или пуст")

            self.logger.info(
                "Итоговые ID из system/info: "
                f"global_user_id={self.global_user_id}, "
                f"user_id={self.user_id}, institution_id={self.institution_id}, "
                f"academic_year_id={self.academic_year_id}"
            )
            return True
        except requests.exceptions.JSONDecodeError:
            content_type = response.headers.get('Content-Type', 'unknown') if response is not None else 'unknown'
            preview = (response.text[:300] if response is not None else '').replace('\n', ' ')
            self.logger.error(
                f"Endpoint /{self.system_info_scope_id}/system/info вернул не-JSON ответ. "
                f"status={response.status_code if response is not None else 'unknown'}, "
                f"content-type={content_type}, body-preview={preview}"
            )
            return False
        except requests.RequestException as e:
            self.logger.error(f"Ошибка запроса endpoint /{self.system_info_scope_id}/system/info: {str(e)}")
            return False


    def initialize_session(self) -> bool:
        """Единая точка инициализации: авторизация + получение всех ID."""
        if not self.login():
            self.logger.error("Авторизация не удалась.")
            return False
        if not self.get_system_info_scope_id_from_institutions():
            self.logger.error("Не удалось получить обязательный system_info_scope_id.")
            return False
        if not self.get_ids_from_system_info():
            self.logger.error("Не удалось получить обязательный user_id из /system/info.")
            return False

        self.logger.info("Сессия успешно инициализирована.")
        return True

    def get_all_classes(self) -> Optional[List[Dict[str, Any]]]:
        """Получение списка всех классов."""
        if not self.user_id:
            self.logger.error("user_id не установлен. Невозможно получить список классов.")
            return None

        self.logger.info("\n--- Запрос списка классов ---")
        try:
            response = self.session.get(
                f"{self.api_url}/{self.user_id}/group/courses/my-courses?expand=members&_limit=987654321",
                timeout=20
            )
            response.raise_for_status()
            classes_list = response.json()

            if not isinstance(classes_list, list):
                self.logger.error("Ответ API имеет неожиданный формат.")
                return None

            if not classes_list:
                self.logger.warning("Список классов пуст. Не найдено ни одного класса.")
                return []

            self.logger.info("Список ваших классов:")
            for cls in classes_list:
                class_id = cls.get('id')
                class_name = cls.get('name')
                class_number = cls.get('number')
                members_count = len(cls.get('members', [])) if cls.get('members') is not None else 0

                # Проверка на наличие важных полей
                if class_id is None or class_name is None:
                    self.logger.warning(f"Пропущен класс с неполными данными: {cls}")
                    continue

                self.logger.info(
                    f"  - ID: {class_id} | Класс: {class_number} | Название: {class_name} | Учеников: {members_count}"
                )
            self.logger.info("---------------------------")
            self.logger.info(f"Найдено классов: {len(classes_list)}")
            return classes_list
        except requests.RequestException as e:
            self.logger.error(f"Ошибка при получении списка классов: {str(e)}")
            return None

    def get_class_members(self, class_id: int) -> Optional[List[Dict[str, Any]]]:
        """
        Получение списка участников для заданного класса с повторными попытками.
        """
        if not self.user_id:
            self.logger.error("user_id не установлен. Невозможно получить участников класса.")
            return None

        url = f"{self.api_url}/{self.user_id}/group/group/{class_id}?expand=members.member.user&_limit=987654321"
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            self.logger.info(f"Запрос участников для класса ID: {class_id}... (Попытка {attempt + 1}/{max_retries})")
            try:
                response = self.session.get(url, timeout=20)
                response.raise_for_status()
                class_data = response.json()
                members_list = class_data.get('members', [])

                if not members_list:
                    self.logger.warning(f"Для класса ID {class_id} не найдено участников.")
                return members_list
            except requests.RequestException as e:
                self.logger.error(f"Ошибка при получении участников для класса (ID: {class_id}): {str(e)}")
                if attempt < max_retries - 1:
                    self.logger.info(f"    Повторная попытка через {retry_delay} секунд...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Экспоненциальная задержка
                else:
                    self.logger.error(
                        f"    Все {max_retries} попыток получения данных для класса {class_id} не удались.")
        return None

    def _sanitize_filename(self, filename: str) -> str:
        """Очистка имени файла от недопустимых символов."""
        return re.sub(r'[\\/:*?"<>|]', '_', filename)

    def save_json_response(self, endpoint_input: str) -> Optional[str]:
        """
        Сохраняет JSON-ответ API по введенному URL или пути.
        Возвращает путь к файлу при успехе, иначе None.
        """
        raw_value = endpoint_input.strip()
        if not raw_value:
            self.logger.error("Пустой адрес запроса.")
            return None

        if raw_value.startswith("http://") or raw_value.startswith("https://"):
            url = raw_value
            endpoint_for_name = raw_value.replace("https://", "").replace("http://", "")
        else:
            endpoint_path = raw_value if raw_value.startswith("/") else f"/{raw_value}"
            url = f"{self.api_url}{endpoint_path}"
            endpoint_for_name = endpoint_path.strip("/")

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_name = self._sanitize_filename(endpoint_for_name or "root")
        os.makedirs(RESPONSES_DIR, exist_ok=True)
        file_path = os.path.join(RESPONSES_DIR, f"{timestamp}_{safe_name}.json")

        self.logger.info(f"Запрос для сохранения JSON: {url}")
        response: Optional[requests.Response] = None
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            payload = response.json()

            with open(file_path, "w", encoding="utf-8") as json_file:
                json.dump(payload, json_file, ensure_ascii=False, indent=2)
                json_file.flush()
                os.fsync(json_file.fileno())

            self.logger.info(f"JSON-ответ сохранен: {file_path}")
            return file_path
        except requests.exceptions.JSONDecodeError:
            text_path = file_path.replace(".json", ".txt")
            body = response.text if response is not None else ""
            with open(text_path, "w", encoding="utf-8") as text_file:
                text_file.write(body)
                text_file.flush()
                os.fsync(text_file.fileno())
            self.logger.error(f"Ответ не JSON. Тело сохранено в: {text_path}")
            return None
        except requests.RequestException as e:
            self.logger.error(f"Ошибка запроса {url}: {e}")
            return None

    def _extract_full_name_from_member(self, member: Dict[str, Any]) -> str:
        """Извлекает ФИО из разных структур member/user в ответах API."""
        candidate_objects = [
            member.get('member'),
            (member.get('member') or {}).get('user') if isinstance(member.get('member'), dict) else None,
            member.get('user')
        ]

        for obj in candidate_objects:
            if not isinstance(obj, dict):
                continue
            first_name = str(obj.get('first_name', '') or '').strip()
            last_name = str(obj.get('last_name', '') or '').strip()
            full_name = f"{last_name} {first_name}".strip()
            if full_name:
                return full_name
        return ""

    def print_classes(self, classes_list: List[Dict[str, Any]]) -> None:
        """Печатает список классов в консоль в удобном виде."""
        print("\n=== Список классов ===")
        for cls in classes_list:
            class_id = cls.get('id')
            class_name = cls.get('name', 'Без названия')
            class_number = cls.get('number', '-')
            print(f"ID: {class_id} | Класс: {class_number} | Название: {class_name}")

    def print_class_members(self, class_id: int, members: List[Dict[str, Any]]) -> None:
        """Печатает список участников выбранного класса."""
        print(f"\n=== Участники класса {class_id} ===")
        if not members:
            print("Участники не найдены.")
            return

        for member in members:
            full_name = self._extract_full_name_from_member(member)
            role_id = member.get('group_role_id', '-')
            user_id = member.get('user_id', '-')
            if full_name:
                print(f"user_id={user_id} | role={role_id} | {full_name}")
            else:
                print(f"user_id={user_id} | role={role_id} | <без имени>")

    def export_all_members_to_csvs_new_logic(self) -> int:
        """
        Получает список классов и сразу экспортирует данные участников
        каждого класса в отдельный CSV-файл.
        """
        classes_list = self.get_all_classes()
        if classes_list is None:
            self.logger.error("Не удалось получить список классов. Экспорт невозможен.")
            return 0

        if not classes_list:
            self.logger.warning("Список классов пуст. Нечего экспортировать.")
            return 0

        self.logger.info("\n--- Запуск экспорта участников классов ---")
        os.makedirs(EXPORT_DIR, exist_ok=True)
        self.logger.info(f"Каталог экспорта: {EXPORT_DIR}")
        total_exported_classes = 0
        total_classes = len(classes_list)
        for index, class_info in enumerate(classes_list, start=1):
            class_id = class_info.get('id')
            class_name = class_info.get('name')
            class_number = class_info.get('number')
            class_subject = ((class_info.get('subject') or {}).get('i18n') or {}).get('name', 'Без предмета')
            members_count = len(class_info.get('members', [])) if class_info.get('members') is not None else 0

            progress_prefix = f"[{index}/{total_classes}]"
            print(f"{progress_prefix} Обработка класса: {class_number}-{class_name} (ID: {class_id})")

            # Проверка, что имя класса существует
            if not class_name or class_id is None:
                self.logger.warning(f"Пропущен класс с неполными данными: {class_info}")
                continue
            if not isinstance(class_id, int):
                self.logger.warning(f"Пропущен класс с невалидным id: {class_info}")
                continue
            class_id_int = class_id

            filename = self._sanitize_filename(f"{class_number}-{class_name}({class_subject}).csv")
            file_path = os.path.join(EXPORT_DIR, filename)

            members = self.get_class_members(class_id_int)
            if members is None:
                self.logger.warning(
                    f"    Не удалось получить данные об участниках класса '{class_name}' (ID: {class_id}). Пропуск."
                )
                continue

            if not members:
                self.logger.info(f"Пропуск класса '{class_name}' (ID: {class_id}) так как список участников пуст.")
                continue

            self.logger.info(
                f"{progress_prefix} Экспорт участников класса '{class_number}-{class_name}' ({members_count} человек) в файл '{file_path}'..."
            )

            fieldnames = ['id', 'user_id', 'group_id', 'group_role_id', 'full_name']

            try:
                with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    exported_rows = 0

                    for member in members:
                        full_name = self._extract_full_name_from_member(member)
                        if not full_name:
                            self.logger.info(
                                f"Пропущен участник без имени/фамилии в классе '{class_name}' (ID: {class_id})."
                            )
                            continue

                        row = {
                            'id': member.get('id'),
                            'user_id': member.get('user_id'),
                            'group_id': member.get('group_id'),
                            'group_role_id': member.get('group_role_id'),
                            'full_name': full_name
                        }
                        writer.writerow(row)
                        exported_rows += 1

                    # Принудительно сбрасываем буфер, чтобы файл точно появился/обновился сразу.
                    csvfile.flush()
                    os.fsync(csvfile.fileno())

                    if exported_rows == 0:
                        self.logger.warning(
                            f"    Для класса '{class_name}' не найдено строк для экспорта (файл создан только с заголовком)."
                        )
                self.logger.info(f"    Экспорт '{class_name}' завершен.")
                print(f"{progress_prefix} Сохранен CSV: {file_path}")
                total_exported_classes += 1
            except Exception as e:
                self.logger.error(f"    Ошибка при записи файла '{file_path}': {e}")
                self.logger.warning(f"    Экспорт '{class_number}-{class_name}' не завершён из-за ошибки.")

        self.logger.info(
            f"Экспорт данных об участниках всех классов завершен. Экспортировано классов: {total_exported_classes}")
        return total_exported_classes


def print_menu() -> None:
    print("\n=== Human.UA API Menu ===")
    print("1) Авторизоваться и инициализировать сессию")
    print("2) Показать список классов")
    print("3) Показать список участников класса")
    print("4) Экспортировать всех учеников в CSV")
    print("5) Сохранить JSON ответа по адресу")
    print("0) Выход")


def run_cli_menu(auth_service: HumanUAAuth) -> None:
    initialized = False
    while True:
        print_menu()
        choice = input("Выберите действие: ").strip()

        if choice == "0":
            auth_service.logger.info("Завершение программы пользователем.")
            break

        if choice == "1":
            initialized = auth_service.initialize_session()
            if initialized:
                print(f"Сессия инициализирована:")
                print(f"  system_info_scope_id={auth_service.system_info_scope_id}")
                print(f"  global_user_id={auth_service.global_user_id}")
                print(f"  institution_id={auth_service.institution_id}")
                print(f"  user_id={auth_service.user_id}")
                print(f"  academic_year_id={auth_service.academic_year_id}")
            else:
                print("Инициализация не удалась. Проверьте файл логов humanua_api.log")
            continue

        if not initialized:
            print("Сначала выполните пункт 1 (инициализация сессии).")
            continue

        if choice == "2":
            classes = auth_service.get_all_classes()
            if classes is not None:
                print(f"Получено классов: {len(classes)}")
                auth_service.print_classes(classes)
            continue

        if choice == "3":
            classes = auth_service.get_all_classes()
            if classes is None:
                print("Не удалось получить список классов.")
                continue
            auth_service.print_classes(classes)

            class_id_raw = input("Введите class_id: ").strip()
            if not class_id_raw.isdigit():
                print("class_id должен быть числом.")
                continue
            members = auth_service.get_class_members(int(class_id_raw))
            if members is None:
                print("Не удалось получить участников.")
            else:
                print(f"Получено участников: {len(members)}")
                auth_service.print_class_members(int(class_id_raw), members)
            continue

        if choice == "4":
            exported = auth_service.export_all_members_to_csvs_new_logic()
            print(f"Экспорт завершён. Успешно обработано классов: {exported}")
            continue

        if choice == "5":
            endpoint_input = input("Введите URL или путь (например /600657/system/info): ").strip()
            saved_file = auth_service.save_json_response(endpoint_input)
            if saved_file:
                print(f"JSON сохранен: {saved_file}")
            else:
                print("Не удалось сохранить JSON. Проверьте логи.")
            continue

        print("Неизвестная опция, попробуйте снова.")


if __name__ == '__main__':
    start_time = datetime.now()
    auth_service = HumanUAAuth(email=LOGIN_EMAIL, password=LOGIN_PASSWORD)
    run_cli_menu(auth_service)
    auth_service.logger.info(f"Общее время выполнения: {datetime.now() - start_time}")
