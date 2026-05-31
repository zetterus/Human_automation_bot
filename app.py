import requests
import logging
import json
import csv
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
import re
import time
from urllib.parse import urlparse

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
REPORTS_DIR = os.path.join(BASE_DIR, "reports")


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
        self.selected_class_ids: Optional[Set[int]] = None
        self.save_all_responses_enabled: bool = False
        self._response_dump_seq: int = 0
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
        self.session.hooks['response'].append(self._response_dump_hook)
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

    def _extract_subject_name(self, cls: Dict[str, Any]) -> str:
        """Безопасно извлекает название предмета класса."""
        subject = cls.get('subject')
        if isinstance(subject, dict):
            i18n = subject.get('i18n')
            if isinstance(i18n, dict):
                name = i18n.get('name')
                if isinstance(name, str):
                    return name
        return "-"

    def _normalize_name_for_match(self, value: Any) -> str:
        """Нормализует строку для устойчивого сравнения названий предметов/галузей."""
        if value is None:
            return ""

        text = str(value).strip().lower()
        if not text:
            return ""

        replacements = {
            '«': ' ',
            '»': ' ',
            '“': ' ',
            '”': ' ',
            '"': ' ',
            '„': ' ',
            'ʼ': '',
            '’': '',
            "'": '',
            '`': '',
            '՚': '',
            '‘': '',
            '–': ' ',
            '—': ' ',
            '-': ' ',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)

        text = text.replace('ё', 'е')
        text = re.sub(r'[^0-9a-zа-яіїєґ\s]', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _match_score(self, left: str, right: str) -> float:
        """Считает score совпадения двух уже нормализованных строк [0..1]."""
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        if left in right or right in left:
            return 0.9

        left_tokens: Set[str] = set(left.split())
        right_tokens: Set[str] = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0

        inter = left_tokens & right_tokens
        if not inter:
            return 0.0

        return len(inter) / max(1, min(len(left_tokens), len(right_tokens)))

    def _pick_industry_id_by_subject(self, industries: Any, class_subject_name: str) -> Optional[int]:
        """Выбирает industry_id по максимально похожему имени предмета."""
        if not isinstance(industries, list):
            self.logger.error(f"Ожидался список industries, но получено: {type(industries).__name__}")
            return None

        target_norm = self._normalize_name_for_match(class_subject_name)
        if not target_norm or target_norm == '-':
            self.logger.warning(f"Пустое/невалидное название предмета класса: '{class_subject_name}'")
            return None

        self.logger.debug(f"Сопоставление industry по предмету: raw='{class_subject_name}', normalized='{target_norm}'")

        best_score = 0.0
        best_id: Optional[int] = None
        best_name = ""

        for idx, item in enumerate(industries):
            if not isinstance(item, dict):
                self.logger.debug(f"industries[{idx}] пропущен: тип {type(item).__name__}")
                continue

            industry_id = self._parse_int(item.get('id'))
            industry_name_raw = item.get('name', '')
            industry_name_norm = self._normalize_name_for_match(industry_name_raw)
            score = self._match_score(target_norm, industry_name_norm)

            self.logger.debug(
                f"industries[{idx}] id={industry_id}, name='{industry_name_raw}', normalized='{industry_name_norm}', score={score:.3f}"
            )

            if industry_id is None:
                continue

            if score > best_score:
                best_score = score
                best_id = industry_id
                best_name = str(industry_name_raw)

        # Порог 0.6: обычно достаточно для устойчивого частичного совпадения.
        if best_id is not None and best_score >= 0.6:
            self.logger.info(
                f"Выбран industry_id={best_id} по предмету '{class_subject_name}' -> '{best_name}' (score={best_score:.3f})"
            )
            return best_id

        self.logger.warning(
            f"Не удалось подобрать industry_id для предмета '{class_subject_name}'. Лучший score={best_score:.3f}, id={best_id}"
        )
        return None

    def _normalize_report_date(self, input_date: str) -> Optional[str]:
        """Проверяет/нормализует дату отчета к формату YYYY-MM-DD HH:MM:SS."""
        raw = input_date.strip()
        if not raw:
            return None

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(raw, fmt)
                if fmt == "%Y-%m-%d":
                    dt = dt.replace(hour=0, minute=0, second=0)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        return None

    def get_selected_classes_status(self) -> str:
        """Текстовый статус текущего выбора классов для массовых операций."""
        if not self.selected_class_ids:
            return "ВСЕ"
        return f"ВЫБРАНО: {len(self.selected_class_ids)}"

    def set_selected_class_ids(self, class_ids: Optional[Set[int]]) -> None:
        """Устанавливает список выбранных class_id (None/empty = все классы)."""
        if not class_ids:
            self.selected_class_ids = None
            self.logger.info("Фильтр классов сброшен: будут использованы все классы.")
            return

        selected_ids = set(class_ids)
        self.selected_class_ids = selected_ids
        selected_str = ", ".join(str(x) for x in sorted(selected_ids))
        self.logger.info(f"Установлен фильтр классов: {selected_str}")

    def _apply_selected_classes_filter(self, classes_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Фильтрует список классов по выбранным class_id."""
        if not self.selected_class_ids:
            self.logger.info("Фильтр классов не задан: используются все классы.")
            return classes_list

        selected = self.selected_class_ids
        filtered: List[Dict[str, Any]] = []
        for cls in classes_list:
            class_id = self._parse_int(cls.get('id'))
            if class_id is not None and class_id in selected:
                filtered.append(cls)

        self.logger.info(
            f"Применен фильтр классов: выбрано={len(selected)}, найдено в текущем списке={len(filtered)}"
        )
        return filtered

    def _get_industries_for_class(self, class_id: int) -> Optional[List[Dict[str, Any]]]:
        """Получает industries для конкретного класса."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно запросить industries.")
            return None

        url = f"{self.api_url}/{self.user_id}/plan/competence-reports-course/{class_id}/industries"
        params = {"expand": "competenceKit.academicYear"}
        self.logger.info(f"Запрос industries для класса {class_id}: {url}")

        try:
            response = self.session.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                self.logger.error(f"Некорректный формат industries для класса {class_id}: {type(data).__name__}")
                return None
            self.logger.info(f"Получено industries для класса {class_id}: {len(data)} шт.")
            return data
        except requests.RequestException as e:
            self.logger.error(f"Ошибка запроса industries для класса {class_id}: {e}")
            return None
        except requests.exceptions.JSONDecodeError:
            self.logger.error(f"Ответ industries для класса {class_id} не JSON")
            return None

    def create_competences_for_all_classes(self, competence_name: str, report_date: str) -> Dict[str, int]:
        """Создает компетенции по всем классам с подробным логированием и устойчивостью к ошибкам."""
        stats = {
            "total": 0,
            "processed": 0,
            "created": 0,
            "skipped": 0,
            "errors": 0,
        }

        if not self.user_id:
            self.logger.error("user_id не установлен. Создание компетенций невозможно.")
            stats["errors"] += 1
            return stats

        classes = self.get_all_classes()
        if classes is None:
            self.logger.error("Не удалось получить список классов для создания компетенций.")
            stats["errors"] += 1
            return stats

        filtered_classes = self._apply_selected_classes_filter(classes)
        stats["total"] = len(filtered_classes)
        if not filtered_classes:
            self.logger.warning("После применения фильтра классов список пуст. Нечего обрабатывать.")
            return stats

        self.logger.info(
            f"Старт массового создания компетенций: классов={len(filtered_classes)}, name='{competence_name}', date='{report_date}'"
        )

        for idx, cls in enumerate(filtered_classes, start=1):
            class_id = self._parse_int(cls.get('id'))
            class_name = str(cls.get('name', 'Без названия'))
            class_number = str(cls.get('number', '-'))
            subject_name = self._extract_subject_name(cls)
            prefix = f"[{idx}/{len(filtered_classes)}]"

            self.logger.info(
                f"{prefix} Обработка класса: id={class_id}, number='{class_number}', name='{class_name}', subject='{subject_name}'"
            )

            try:
                if class_id is None:
                    self.logger.warning(f"{prefix} Пропуск класса: невалидный class_id")
                    stats["skipped"] += 1
                    continue

                stats["processed"] += 1

                industries = self._get_industries_for_class(class_id)
                if industries is None:
                    self.logger.warning(f"{prefix} Пропуск класса {class_id}: не удалось получить industries")
                    stats["errors"] += 1
                    continue

                industry_id = self._pick_industry_id_by_subject(industries, subject_name)
                if industry_id is None:
                    self.logger.warning(
                        f"{prefix} Пропуск класса {class_id}: industry_id не найден для subject='{subject_name}'"
                    )
                    stats["skipped"] += 1
                    continue

                payload = {
                    "date": report_date,
                    "display_in_pdf": 0,
                    "group_id": class_id,
                    "group_type": 1,
                    "index_in_pdf": 0,
                    "industries": [industry_id],
                    "name": competence_name,
                    "type": 1,
                }

                post_url = f"{self.api_url}/{self.user_id}/plan/competence-reports"
                self.logger.info(f"{prefix} POST competence-report для класса {class_id}: {post_url}")
                self.logger.debug(f"{prefix} payload={json.dumps(payload, ensure_ascii=False)}")

                response = self.session.post(post_url, data=json.dumps(payload), timeout=20)
                response.raise_for_status()

                response_payload: Any = None
                try:
                    response_payload = response.json()
                except requests.exceptions.JSONDecodeError:
                    self.logger.warning(f"{prefix} Ответ POST не JSON (status={response.status_code})")

                created_report_id = None
                if isinstance(response_payload, dict):
                    created_report_id = response_payload.get('id')

                self.logger.info(
                    f"{prefix} Успех: создана компетенция для class_id={class_id}, industry_id={industry_id}, report_id={created_report_id}"
                )
                stats["created"] += 1
            except requests.RequestException as e:
                self.logger.error(f"{prefix} Ошибка HTTP для класса {class_id}: {e}")
                stats["errors"] += 1
            except Exception as e:
                self.logger.error(f"{prefix} Непредвиденная ошибка для класса {class_id}: {e}")
                stats["errors"] += 1

        self.logger.info(
            "Итог создания компетенций: "
            f"total={stats['total']}, processed={stats['processed']}, created={stats['created']}, "
            f"skipped={stats['skipped']}, errors={stats['errors']}"
        )
        return stats

    def _get_semester_container_id_for_class(self, class_id: int, semester_number: int) -> Optional[int]:
        """Получает container id для указанного семестра и класса."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно получить semester container id.")
            return None

        url = f"{self.api_url}/{self.user_id}/plan/summative-assessment-container/result-group/course/{class_id}"
        self.logger.info(
            f"Запрос container для класса {class_id}, semester={semester_number}: {url}"
        )
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                self.logger.error(
                    f"Некорректный формат container-ответа для класса {class_id}: {type(payload).__name__}"
                )
                return None

            for item in payload:
                if not isinstance(item, dict):
                    continue
                item_semester = self._parse_int(item.get("semester_number"))
                if item_semester != semester_number:
                    continue
                semestr_id = self._parse_int(item.get("id"))
                if semestr_id is not None:
                    self.logger.info(
                        f"Для класса {class_id} найден semestr_id={semestr_id} (semester={semester_number})"
                    )
                    return semestr_id

            self.logger.warning(
                f"Для класса {class_id} не найден container с semester_number={semester_number}"
            )
            return None
        except requests.RequestException as e:
            self.logger.error(f"Ошибка запроса container для класса {class_id}: {e}")
            return None
        except requests.exceptions.JSONDecodeError:
            self.logger.error(f"Ответ container для класса {class_id} не JSON")
            return None

    def _extract_result_groups_for_student(self, semestr_id: int, student_id: int) -> List[Dict[str, Any]]:
        """Получает result_groups по ученику и семестровому контейнеру."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно получить result_groups ученика.")
            return []

        url = (
            f"{self.api_url}/{self.user_id}/plan/summative-assessment-container/result-group/"
            f"{semestr_id}/student/{student_id}"
        )
        self.logger.info(
            f"Запрос result_groups для student_id={student_id}, semestr_id={semestr_id}: {url}"
        )

        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                self.logger.error(
                    f"Некорректный формат student-result ответа для student_id={student_id}: {type(payload).__name__}"
                )
                return []

            data_items = payload.get("data")
            if not isinstance(data_items, list):
                self.logger.warning(f"student_id={student_id}: поле data отсутствует или не list")
                return []

            result_groups_flat: List[Dict[str, Any]] = []
            for data_idx, data_item in enumerate(data_items):
                if not isinstance(data_item, dict):
                    self.logger.debug(f"student_id={student_id}: data[{data_idx}] пропущен (не dict)")
                    continue
                groups = data_item.get("result_groups")
                if not isinstance(groups, list):
                    self.logger.debug(f"student_id={student_id}: result_groups в data[{data_idx}] не list")
                    continue
                for group_idx, group in enumerate(groups):
                    if isinstance(group, dict):
                        result_groups_flat.append(group)
                    else:
                        self.logger.debug(
                            f"student_id={student_id}: result_groups[{group_idx}] в data[{data_idx}] не dict"
                        )

            self.logger.info(
                f"student_id={student_id}: получено групп результатов: {len(result_groups_flat)}"
            )
            return result_groups_flat
        except requests.RequestException as e:
            self.logger.error(
                f"Ошибка запроса student-result для student_id={student_id}, semestr_id={semestr_id}: {e}"
            )
            return []
        except requests.exceptions.JSONDecodeError:
            self.logger.error(
                f"Ответ student-result не JSON для student_id={student_id}, semestr_id={semestr_id}"
            )
            return []

    def _post_result_group_assessment(
        self,
        semestr_id: int,
        student_id: int,
        result_group_id: int,
        average_score: Any,
    ) -> bool:
        """Отправляет один POST на создание/обновление оценки группы результата."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно отправить result-group assessment.")
            return False

        endpoint_path = f"/{self.user_id}/plan/summative-assessment/result-group/assessment"
        url = f"{self.api_url}{endpoint_path}"

        payload = {
            "endpoint": endpoint_path,
            "entity_type": 12,
            "grade": 3,
            "int_value": average_score,
            "is_final": 1,
            "learner_id": student_id,
            "result_group_id": result_group_id,
            "summative_assessment_container_id": semestr_id,
            "type": 0,
        }

        self.logger.debug(
            "POST result-group assessment payload: "
            f"student_id={student_id}, semestr_id={semestr_id}, result_group_id={result_group_id}, "
            f"int_value={average_score}"
        )

        try:
            response = self.session.post(url, data=json.dumps(payload), timeout=20)
            response.raise_for_status()
            self.logger.info(
                f"Успех POST assessment: student_id={student_id}, result_group_id={result_group_id}, status={response.status_code}"
            )
            return True
        except requests.RequestException as e:
            self.logger.error(
                "Ошибка POST assessment: "
                f"student_id={student_id}, result_group_id={result_group_id}, semestr_id={semestr_id}, error={e}"
            )
            return False

    def _round_score_to_int(self, value: Any) -> Optional[int]:
        """Преобразует score в int с округлением до ближайшего целого (half-up для .5)."""
        if value is None:
            return None

        try:
            numeric = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None

        if numeric >= 0:
            return int(numeric + 0.5)
        return int(numeric - 0.5)

    def _save_failed_students_report(self, failed_students: List[Dict[str, Any]]) -> Optional[str]:
        """Сохраняет CSV-отчет по ученикам, у которых не удалось проставить оценки полностью."""
        if not failed_students:
            return None

        os.makedirs(REPORTS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_path = os.path.join(REPORTS_DIR, f"failed_result_group_students_{timestamp}.csv")
        fieldnames = [
            "class_id",
            "semestr_id",
            "semester_number",
            "student_id",
            "mode",
            "failed_posts",
            "reasons",
        ]

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for row in failed_students:
                    writer.writerow(row)
                csvfile.flush()
                os.fsync(csvfile.fileno())
            self.logger.info(f"Отчет по проблемным ученикам сохранен: {file_path}")
            return file_path
        except Exception as e:
            self.logger.error(f"Не удалось сохранить отчет по проблемным ученикам: {e}")
            return None

    def _save_failed_competence_students_report(self, failed_students: List[Dict[str, Any]]) -> Optional[str]:
        """Сохраняет CSV-отчет по ученикам, у которых не удалось проставить компетенции полностью."""
        if not failed_students:
            return None

        os.makedirs(REPORTS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_path = os.path.join(REPORTS_DIR, f"failed_competence_students_{timestamp}.csv")
        fieldnames = [
            "class_id",
            "semester_number",
            "competence_report_id",
            "student_id",
            "failed_posts",
            "reasons",
        ]

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for row in failed_students:
                    writer.writerow(row)
                csvfile.flush()
                os.fsync(csvfile.fileno())
            self.logger.info(f"Отчет по проблемным выставлениям компетенций сохранен: {file_path}")
            return file_path
        except Exception as e:
            self.logger.error(f"Не удалось сохранить отчет по проблемным компетенциям: {e}")
            return None

    def _extract_result_group_scores_by_position(
        self,
        class_id: int,
        student_id: int,
        semester_number: int,
    ) -> Dict[int, Dict[str, Any]]:
        """Возвращает словарь по position: id/name/int_value из summative result-group для ученика."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно получить result-group для компетенций.")
            return {}

        url = (
            f"{self.api_url}/{self.user_id}/plan/summative-assessment/result-group/"
            f"{class_id}/semester/{semester_number}"
        )
        params = {"student_id": student_id}
        self.logger.info(
            f"Запрос result-group для компетенций: class_id={class_id}, student_id={student_id}, "
            f"semester={semester_number}"
        )

        try:
            response = self.session.get(url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                self.logger.error(
                    f"Некорректный формат ответа result-group для class_id={class_id}, student_id={student_id}: "
                    f"{type(payload).__name__}"
                )
                return {}

            by_position: Dict[int, Dict[str, Any]] = {}
            result_groups_roots = payload.get("resultGroups")
            if isinstance(result_groups_roots, list):
                for root_item in result_groups_roots:
                    if not isinstance(root_item, dict):
                        continue
                    inner_groups = root_item.get("resultGroups")
                    if not isinstance(inner_groups, list):
                        continue
                    for group in inner_groups:
                        if not isinstance(group, dict):
                            continue
                        position = self._parse_int(group.get("position"))
                        result_group_id = self._parse_int(group.get("id"))
                        result_group_name = str(group.get("name", "")).strip()
                        if position is None or result_group_id is None:
                            continue
                        by_position[position] = {
                            "result_group_id": result_group_id,
                            "result_group_name": result_group_name,
                            "result_group_int_value": None,
                        }

            assessment_values_by_result_group_id: Dict[int, int] = {}
            assessments = payload.get("assessments")
            if isinstance(assessments, list):
                for item in assessments:
                    if not isinstance(item, dict):
                        continue
                    result_group_id = self._parse_int(item.get("result_group_id"))
                    assessment_obj = item.get("assessment")
                    int_value = None
                    if isinstance(assessment_obj, dict):
                        int_value = self._round_score_to_int(assessment_obj.get("int_value"))
                    if result_group_id is not None and int_value is not None:
                        assessment_values_by_result_group_id[result_group_id] = int_value

            for position, row in by_position.items():
                result_group_id = self._parse_int(row.get("result_group_id"))
                if result_group_id is None:
                    continue
                row["result_group_int_value"] = assessment_values_by_result_group_id.get(result_group_id)
                self.logger.debug(
                    f"position={position}: result_group_id={row.get('result_group_id')}, "
                    f"name='{row.get('result_group_name')}', int_value={row.get('result_group_int_value')}"
                )

            return by_position
        except requests.RequestException as e:
            self.logger.error(
                f"Ошибка запроса result-group для компетенций: class_id={class_id}, student_id={student_id}, error={e}"
            )
            return {}
        except requests.exceptions.JSONDecodeError:
            self.logger.error(
                f"Ответ result-group для компетенций не JSON: class_id={class_id}, student_id={student_id}"
            )
            return {}

    def _get_competence_report_id_for_class(self, class_id: int) -> Optional[int]:
        """Возвращает competence_report_id для класса (по умолчанию второй элемент, иначе последний)."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно получить competence_report_id.")
            return None

        url = f"{self.api_url}/{self.user_id}/plan/competence-reports-course/{class_id}"
        self.logger.info(f"Запрос competence-reports-course для class_id={class_id}: {url}")

        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                self.logger.warning(f"class_id={class_id}: competence-reports-course пуст или не list")
                return None

            selected_item = payload[1] if len(payload) >= 2 else payload[-1]
            competence_report_id = None
            if isinstance(selected_item, dict):
                competence_report_id = self._parse_int(selected_item.get("id"))

            if competence_report_id is None:
                self.logger.warning(f"class_id={class_id}: не удалось извлечь competence_report_id")
                return None

            self.logger.info(
                f"class_id={class_id}: выбран competence_report_id={competence_report_id} (index={'1' if len(payload) >= 2 else 'last'})"
            )
            return competence_report_id
        except requests.RequestException as e:
            self.logger.error(f"Ошибка запроса competence-reports-course для class_id={class_id}: {e}")
            return None
        except requests.exceptions.JSONDecodeError:
            self.logger.error(f"Ответ competence-reports-course для class_id={class_id} не JSON")
            return None

    def _extract_competence_ids_by_position(
        self,
        competence_report_id: int,
        student_id: int,
    ) -> Dict[int, int]:
        """Возвращает соответствие position -> competence_id из industries[0].children[].competencies[]."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно получить competence_id по позициям.")
            return {}

        url = (
            f"{self.api_url}/{self.user_id}/plan/competence-reports/"
            f"{competence_report_id}/user/{student_id}/assessments"
        )
        self.logger.info(
            f"Запрос competence assessments: competence_report_id={competence_report_id}, student_id={student_id}"
        )

        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                self.logger.error(
                    f"Некорректный формат competence assessments для student_id={student_id}: "
                    f"{type(payload).__name__}"
                )
                return {}

            position_to_competence_id: Dict[int, int] = {}
            industries = payload.get("industries")
            if isinstance(industries, list) and len(industries) > 0:
                first_industry = industries[0]
                if isinstance(first_industry, dict):
                    children = first_industry.get("children")
                    if isinstance(children, list):
                        for child in children:
                            if not isinstance(child, dict):
                                continue
                            competencies = child.get("competencies")
                            if isinstance(competencies, list):
                                for comp in competencies:
                                    if not isinstance(comp, dict):
                                        continue
                                    competence_id = self._parse_int(comp.get("id"))
                                    position = self._parse_int(comp.get("position"))
                                    if competence_id is not None and position is not None:
                                        position_to_competence_id[position] = competence_id
                                        self.logger.debug(
                                            f"student_id={student_id}: competence_id_{position}={competence_id}"
                                        )

            if not position_to_competence_id:
                self.logger.warning(
                    f"student_id={student_id}: competence_id по position не найдены в industries[0].children[].competencies[]"
                )

            self.logger.debug(
                f"student_id={student_id}: итовое маппинг competence_id: {position_to_competence_id}"
            )
            return position_to_competence_id
        except requests.RequestException as e:
            self.logger.error(
                f"Ошибка запроса competence assessments для student_id={student_id}, report={competence_report_id}: {e}"
            )
            return {}
        except requests.exceptions.JSONDecodeError:
            self.logger.error(
                f"Ответ competence assessments не JSON для student_id={student_id}, report={competence_report_id}"
            )
            return {}

    def _post_competence_assessment(
        self,
        competence_report_id: int,
        student_id: int,
        competence_id: int,
        int_value: int,
    ) -> bool:
        """Отправляет POST оценки компетенции для ученика."""
        if self.user_id is None:
            self.logger.error("user_id не установлен. Невозможно отправить competence assessment.")
            return False

        endpoint_path = (
            f"/{self.user_id}/plan/competence-reports/{competence_report_id}/user/{student_id}/assessments"
        )
        url = f"{self.api_url}{endpoint_path}"
        payload = {
            "competence_id": competence_id,
            "endpoint": endpoint_path,
            "grade": 3,
            "int_value": int_value,
            "is_final": 1,
            "learner_id": student_id,
            "type": 0,
        }

        self.logger.debug(
            f"POST competence assessment: student_id={student_id}, report_id={competence_report_id}, "
            f"competence_id={competence_id}, int_value={int_value}"
        )
        try:
            response = self.session.post(url, data=json.dumps(payload), timeout=20)
            response.raise_for_status()
            self.logger.info(
                f"Успех POST competence assessment: student_id={student_id}, competence_id={competence_id}, "
                f"status={response.status_code}"
            )
            return True
        except requests.RequestException as e:
            self.logger.error(
                f"Ошибка POST competence assessment: student_id={student_id}, competence_id={competence_id}, "
                f"report_id={competence_report_id}, error={e}"
            )
            return False

    def set_result_groups_for_selected_classes(
        self,
        semester_number: int,
        dry_run: bool = False,
        score_mode: str = "max",
    ) -> Dict[str, Any]:
        """Проставляет result-group оценки для учеников выбранных классов (или всех, если фильтр не задан)."""
        stats = {
            "total_classes": 0,
            "processed_classes": 0,
            "students_total": 0,
            "students_processed": 0,
            "students_with_failures": 0,
            "posts_sent": 0,
            "posts_failed": 0,
            "posts_planned": 0,
            "skipped_classes": 0,
            "errors": 0,
            "failed_students_report": None,
            "score_mode": score_mode,
        }

        failed_students: List[Dict[str, Any]] = []

        if self.user_id is None:
            self.logger.error("user_id не установлен. Операция невозможна.")
            stats["errors"] += 1
            return stats

        if score_mode not in {"max", "individual"}:
            self.logger.error(f"Некорректный score_mode='{score_mode}'. Доступно: max или individual.")
            stats["errors"] += 1
            return stats

        classes = self.get_all_classes()
        if classes is None:
            self.logger.error("Не удалось получить список классов для проставления result-group.")
            stats["errors"] += 1
            return stats

        filtered_classes = self._apply_selected_classes_filter(classes)
        stats["total_classes"] = len(filtered_classes)
        if not filtered_classes:
            self.logger.warning("После применения фильтра классов список пуст. Нечего обрабатывать.")
            return stats

        self.logger.info(
            f"Старт проставления result-group: semester={semester_number}, classes={len(filtered_classes)}, "
            f"dry_run={dry_run}, score_mode={score_mode}"
        )

        for idx, cls in enumerate(filtered_classes, start=1):
            class_id = self._parse_int(cls.get("id"))
            class_name = str(cls.get("name", "Без названия"))
            class_number = str(cls.get("number", "-"))
            prefix = f"[{idx}/{len(filtered_classes)}]"

            self.logger.info(
                f"{prefix} Класс: id={class_id}, number='{class_number}', name='{class_name}', semester={semester_number}"
            )

            try:
                if class_id is None:
                    self.logger.warning(f"{prefix} Пропуск класса: невалидный class_id")
                    stats["skipped_classes"] += 1
                    continue

                semestr_id = self._get_semester_container_id_for_class(class_id, semester_number)
                if semestr_id is None:
                    self.logger.warning(f"{prefix} Пропуск класса {class_id}: не найден semestr_id")
                    stats["skipped_classes"] += 1
                    continue

                members = self.get_class_members(class_id)
                if members is None:
                    self.logger.warning(f"{prefix} Пропуск класса {class_id}: не удалось получить участников")
                    stats["errors"] += 1
                    continue

                # Берем только учеников (group_role_id=5) с валидным user_id.
                students: List[int] = []
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    role_id = self._parse_int(member.get("group_role_id"))
                    student_id = self._parse_int(member.get("user_id"))
                    if role_id == 5 and student_id is not None:
                        students.append(student_id)

                unique_students = sorted(set(students))
                self.logger.info(
                    f"{prefix} class_id={class_id}: найдено учеников для обработки={len(unique_students)}"
                )
                stats["students_total"] += len(unique_students)
                stats["processed_classes"] += 1

                total_students_in_class = len(unique_students)
                for student_index, student_id in enumerate(unique_students, start=1):
                    student_prefix = f"{prefix} [{student_index}/{total_students_in_class}]"
                    try:
                        student_reasons: List[str] = []
                        student_failed_posts = 0

                        self.logger.info(
                            f"{student_prefix} Обработка ученика student_id={student_id}"
                        )

                        result_groups = self._extract_result_groups_for_student(semestr_id, student_id)
                        if not result_groups:
                            self.logger.warning(
                                f"{student_prefix} student_id={student_id}: result_groups пуст, пропуск ученика"
                            )
                            student_reasons.append("result_groups пуст")
                            failed_students.append({
                                "class_id": class_id,
                                "semestr_id": semestr_id,
                                "semester_number": semester_number,
                                "student_id": student_id,
                                "mode": score_mode,
                                "failed_posts": 0,
                                "reasons": "; ".join(student_reasons),
                            })
                            stats["students_with_failures"] += 1
                            continue

                        stats["students_processed"] += 1

                        max_score: Optional[int] = None
                        if score_mode == "max":
                            score_candidates: List[int] = []
                            for group in result_groups:
                                rounded_score = self._round_score_to_int(group.get("average_score"))
                                if rounded_score is not None:
                                    score_candidates.append(rounded_score)

                            if not score_candidates:
                                self.logger.warning(
                                    f"{student_prefix} student_id={student_id}: не найдено валидных average_score, "
                                    "пропуск ученика"
                                )
                                student_reasons.append("нет валидных average_score")
                                failed_students.append({
                                    "class_id": class_id,
                                    "semestr_id": semestr_id,
                                    "semester_number": semester_number,
                                    "student_id": student_id,
                                    "mode": score_mode,
                                    "failed_posts": 0,
                                    "reasons": "; ".join(student_reasons),
                                })
                                stats["students_with_failures"] += 1
                                continue

                            max_score = max(score_candidates)
                            self.logger.info(
                                f"{student_prefix} student_id={student_id}: выбран максимальный average_score={max_score}; "
                                "будет использован для всех POST по result_groups"
                            )

                        for group in result_groups:
                            result_group_id = self._parse_int(group.get("result_group_id"))
                            if result_group_id is None:
                                self.logger.warning(
                                    f"{student_prefix} student_id={student_id}: пропуск result_group без result_group_id"
                                )
                                stats["posts_failed"] += 1
                                student_failed_posts += 1
                                student_reasons.append("result_group без result_group_id")
                                continue

                            if score_mode == "max":
                                score_to_send = max_score
                            else:
                                score_to_send = self._round_score_to_int(group.get("average_score"))
                                if score_to_send is None:
                                    self.logger.warning(
                                        f"{student_prefix} student_id={student_id}: невалидный average_score "
                                        f"для result_group_id={result_group_id}"
                                    )
                                    stats["posts_failed"] += 1
                                    student_failed_posts += 1
                                    student_reasons.append(
                                        f"invalid average_score result_group_id={result_group_id}"
                                    )
                                    continue

                            stats["posts_planned"] += 1

                            if dry_run:
                                self.logger.info(
                                    f"{student_prefix} DRY-RUN: student_id={student_id}, "
                                    f"result_group_id={result_group_id}, int_value={score_to_send}"
                                )
                                continue

                            ok = self._post_result_group_assessment(
                                semestr_id=semestr_id,
                                student_id=student_id,
                                result_group_id=result_group_id,
                                average_score=score_to_send,
                            )
                            if ok:
                                stats["posts_sent"] += 1
                            else:
                                stats["posts_failed"] += 1
                                student_failed_posts += 1
                                student_reasons.append(f"POST failed result_group_id={result_group_id}")

                        if student_failed_posts > 0:
                            failed_students.append({
                                "class_id": class_id,
                                "semestr_id": semestr_id,
                                "semester_number": semester_number,
                                "student_id": student_id,
                                "mode": score_mode,
                                "failed_posts": student_failed_posts,
                                "reasons": "; ".join(sorted(set(student_reasons))) if student_reasons else "unknown",
                            })
                            stats["students_with_failures"] += 1
                    except Exception as e:
                        self.logger.error(
                            f"{student_prefix} Непредвиденная ошибка при обработке student_id={student_id}: {e}"
                        )
                        failed_students.append({
                            "class_id": class_id,
                            "semestr_id": semestr_id,
                            "semester_number": semester_number,
                            "student_id": student_id,
                            "mode": score_mode,
                            "failed_posts": 0,
                            "reasons": f"exception: {e}",
                        })
                        stats["students_with_failures"] += 1
                        stats["errors"] += 1
            except Exception as e:
                self.logger.error(f"{prefix} Непредвиденная ошибка при обработке класса: {e}")
                stats["errors"] += 1

        report_path = self._save_failed_students_report(failed_students)
        stats["failed_students_report"] = report_path

        self.logger.info(
            "Итог проставления result-group: "
            f"total_classes={stats['total_classes']}, processed_classes={stats['processed_classes']}, "
            f"students_total={stats['students_total']}, students_processed={stats['students_processed']}, "
            f"students_with_failures={stats['students_with_failures']}, posts_planned={stats['posts_planned']}, "
            f"posts_sent={stats['posts_sent']}, posts_failed={stats['posts_failed']}, "
            f"skipped_classes={stats['skipped_classes']}, errors={stats['errors']}, "
            f"score_mode={stats['score_mode']}, report={report_path}"
        )
        return stats

    def set_competence_assessments_for_selected_classes(
        self,
        semester_number: int = 2,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Проставляет оценки компетенций по данным суммативных result-group для учеников выбранных классов."""
        stats = {
            "total_classes": 0,
            "processed_classes": 0,
            "students_total": 0,
            "students_processed": 0,
            "students_with_failures": 0,
            "posts_sent": 0,
            "posts_failed": 0,
            "posts_planned": 0,
            "skipped_classes": 0,
            "errors": 0,
            "failed_students_report": None,
            "semester_number": semester_number,
        }

        failed_students: List[Dict[str, Any]] = []

        if self.user_id is None:
            self.logger.error("user_id не установлен. Операция невозможна.")
            stats["errors"] += 1
            return stats

        classes = self.get_all_classes()
        if classes is None:
            self.logger.error("Не удалось получить список классов для проставления компетенций.")
            stats["errors"] += 1
            return stats

        filtered_classes = self._apply_selected_classes_filter(classes)
        stats["total_classes"] = len(filtered_classes)
        if not filtered_classes:
            self.logger.warning("После применения фильтра классов список пуст. Нечего обрабатывать.")
            return stats

        self.logger.info(
            f"Старт проставления компетенций: semester={semester_number}, classes={len(filtered_classes)}, dry_run={dry_run}"
        )

        for idx, cls in enumerate(filtered_classes, start=1):
            class_id = self._parse_int(cls.get("id"))
            class_name = str(cls.get("name", "Без названия"))
            class_number = str(cls.get("number", "-"))
            prefix = f"[{idx}/{len(filtered_classes)}]"

            self.logger.info(
                f"{prefix} Класс: id={class_id}, number='{class_number}', name='{class_name}', semester={semester_number}"
            )

            try:
                if class_id is None:
                    self.logger.warning(f"{prefix} Пропуск класса: невалидный class_id")
                    stats["skipped_classes"] += 1
                    continue

                competence_report_id = self._get_competence_report_id_for_class(class_id)
                if competence_report_id is None:
                    self.logger.warning(f"{prefix} Пропуск класса {class_id}: не найден competence_report_id")
                    stats["skipped_classes"] += 1
                    continue

                members = self.get_class_members(class_id)
                if members is None:
                    self.logger.warning(f"{prefix} Пропуск класса {class_id}: не удалось получить участников")
                    stats["errors"] += 1
                    continue

                students: List[int] = []
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    role_id = self._parse_int(member.get("group_role_id"))
                    student_id = self._parse_int(member.get("user_id"))
                    if role_id == 5 and student_id is not None:
                        students.append(student_id)

                unique_students = sorted(set(students))
                self.logger.info(
                    f"{prefix} class_id={class_id}: найдено учеников для обработки={len(unique_students)}"
                )
                stats["students_total"] += len(unique_students)
                stats["processed_classes"] += 1

                total_students_in_class = len(unique_students)
                for student_index, student_id in enumerate(unique_students, start=1):
                    student_prefix = f"{prefix} [{student_index}/{total_students_in_class}]"
                    try:
                        student_reasons: List[str] = []
                        student_failed_posts = 0

                        self.logger.info(
                            f"{student_prefix} Обработка ученика student_id={student_id}, report_id={competence_report_id}"
                        )

                        result_group_rows = self._extract_result_group_scores_by_position(
                            class_id=class_id,
                            student_id=student_id,
                            semester_number=semester_number,
                        )
                        if not result_group_rows:
                            self.logger.warning(
                                f"{student_prefix} student_id={student_id}: нет result-group данных для компетенций"
                            )
                            student_reasons.append("empty result-group payload")
                            failed_students.append({
                                "class_id": class_id,
                                "semester_number": semester_number,
                                "competence_report_id": competence_report_id,
                                "student_id": student_id,
                                "failed_posts": 0,
                                "reasons": "; ".join(student_reasons),
                            })
                            stats["students_with_failures"] += 1
                            continue

                        competence_ids_by_position = self._extract_competence_ids_by_position(
                            competence_report_id=competence_report_id,
                            student_id=student_id,
                        )
                        if not competence_ids_by_position:
                            self.logger.warning(
                                f"{student_prefix} student_id={student_id}: не найдены competence_id по позициям"
                            )
                            student_reasons.append("empty competence_id mapping")
                            failed_students.append({
                                "class_id": class_id,
                                "semester_number": semester_number,
                                "competence_report_id": competence_report_id,
                                "student_id": student_id,
                                "failed_posts": 0,
                                "reasons": "; ".join(student_reasons),
                            })
                            stats["students_with_failures"] += 1
                            continue

                        stats["students_processed"] += 1

                        for position in sorted(result_group_rows.keys()):
                            row = result_group_rows[position]
                            competence_id = competence_ids_by_position.get(position)
                            if competence_id is None:
                                self.logger.warning(
                                    f"{student_prefix} student_id={student_id}: нет competence_id для position={position}"
                                )
                                stats["posts_failed"] += 1
                                student_failed_posts += 1
                                student_reasons.append(f"missing competence_id position={position}")
                                continue

                            int_value = self._round_score_to_int(row.get("result_group_int_value"))
                            if int_value is None:
                                self.logger.warning(
                                    f"{student_prefix} student_id={student_id}: нет int_value для position={position}"
                                )
                                stats["posts_failed"] += 1
                                student_failed_posts += 1
                                student_reasons.append(f"missing int_value position={position}")
                                continue

                            stats["posts_planned"] += 1
                            if dry_run:
                                self.logger.info(
                                    f"{student_prefix} DRY-RUN competence POST: student_id={student_id}, "
                                    f"position={position}, competence_id={competence_id}, int_value={int_value}"
                                )
                                continue

                            ok = self._post_competence_assessment(
                                competence_report_id=competence_report_id,
                                student_id=student_id,
                                competence_id=competence_id,
                                int_value=int_value,
                            )
                            if ok:
                                stats["posts_sent"] += 1
                            else:
                                stats["posts_failed"] += 1
                                student_failed_posts += 1
                                student_reasons.append(
                                    f"POST failed competence_id={competence_id} position={position}"
                                )

                        if student_failed_posts > 0:
                            failed_students.append({
                                "class_id": class_id,
                                "semester_number": semester_number,
                                "competence_report_id": competence_report_id,
                                "student_id": student_id,
                                "failed_posts": student_failed_posts,
                                "reasons": "; ".join(sorted(set(student_reasons))) if student_reasons else "unknown",
                            })
                            stats["students_with_failures"] += 1
                    except Exception as e:
                        self.logger.error(
                            f"{student_prefix} Непредвиденная ошибка при обработке student_id={student_id}: {e}"
                        )
                        failed_students.append({
                            "class_id": class_id,
                            "semester_number": semester_number,
                            "competence_report_id": competence_report_id,
                            "student_id": student_id,
                            "failed_posts": 0,
                            "reasons": f"exception: {e}",
                        })
                        stats["students_with_failures"] += 1
                        stats["errors"] += 1
            except Exception as e:
                self.logger.error(f"{prefix} Непредвиденная ошибка при обработке класса: {e}")
                stats["errors"] += 1

        report_path = self._save_failed_competence_students_report(failed_students)
        stats["failed_students_report"] = report_path

        self.logger.info(
            "Итог проставления компетенций: "
            f"total_classes={stats['total_classes']}, processed_classes={stats['processed_classes']}, "
            f"students_total={stats['students_total']}, students_processed={stats['students_processed']}, "
            f"students_with_failures={stats['students_with_failures']}, posts_planned={stats['posts_planned']}, "
            f"posts_sent={stats['posts_sent']}, posts_failed={stats['posts_failed']}, "
            f"skipped_classes={stats['skipped_classes']}, errors={stats['errors']}, "
            f"semester_number={stats['semester_number']}, report={report_path}"
        )
        return stats

    def toggle_request_dumping(self) -> bool:
        """Переключает режим автосохранения всех ответов API в файлы."""
        self.save_all_responses_enabled = not self.save_all_responses_enabled
        state = "включено" if self.save_all_responses_enabled else "выключено"
        self.logger.info(f"Автосохранение ответов API {state}.")
        if self.save_all_responses_enabled:
            os.makedirs(RESPONSES_DIR, exist_ok=True)
            self.logger.info(f"Каталог дампов ответов: {RESPONSES_DIR}")
        return self.save_all_responses_enabled

    def _build_response_dump_path(self, response: requests.Response, is_json: bool) -> str:
        """Формирует путь к файлу дампа ответа API."""
        request = response.request
        request_url = request.url if request is not None and request.url else response.url
        parsed = urlparse(request_url or "")
        endpoint_for_name = parsed.path.strip('/') or "root"
        if parsed.query:
            endpoint_for_name = f"{endpoint_for_name}_q_{parsed.query}"

        method = (request.method if request is not None and request.method else "GET").upper()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        safe_name = self._sanitize_filename(endpoint_for_name)
        self._response_dump_seq += 1
        extension = ".json" if is_json else ".txt"
        filename = (
            f"{timestamp}_{self._response_dump_seq:04d}_{method}_{safe_name}_"
            f"status_{response.status_code}{extension}"
        )
        return os.path.join(RESPONSES_DIR, filename)

    def _dump_response_to_file(self, response: requests.Response) -> Optional[str]:
        """Сохраняет текущий HTTP-ответ в JSON/TXT файл."""
        os.makedirs(RESPONSES_DIR, exist_ok=True)
        request = response.request
        method = (request.method if request is not None and request.method else "GET").upper()
        request_url = request.url if request is not None and request.url else response.url

        try:
            payload = response.json()
            file_path = self._build_response_dump_path(response, is_json=True)
            with open(file_path, "w", encoding="utf-8") as json_file:
                json.dump(payload, json_file, ensure_ascii=False, indent=2)
                json_file.flush()
                os.fsync(json_file.fileno())
        except requests.exceptions.JSONDecodeError:
            file_path = self._build_response_dump_path(response, is_json=False)
            with open(file_path, "w", encoding="utf-8") as text_file:
                text_file.write(response.text)
                text_file.flush()
                os.fsync(text_file.fileno())

        self.logger.info(
            f"Дамп ответа сохранен: {method} {request_url} -> {file_path}"
        )
        return file_path

    def _response_dump_hook(self, response: requests.Response, *args: Any, **kwargs: Any) -> requests.Response:
        """Hook requests.Session: сохраняет каждый ответ, если включен toggle."""
        if not self.save_all_responses_enabled:
            return response

        try:
            self._dump_response_to_file(response)
        except Exception as e:
            self.logger.error(f"Не удалось сохранить дамп ответа: {e}")
        return response

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
            subject_name = self._extract_subject_name(cls)
            print(f"ID: {class_id} | Класс: {class_number} | Название: {class_name} | Предмет: {subject_name}")

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

        filtered_classes = self._apply_selected_classes_filter(classes_list)

        if not filtered_classes:
            self.logger.warning("После применения фильтра классов список пуст. Нечего экспортировать.")
            return 0

        self.logger.info("\n--- Запуск экспорта участников классов ---")
        os.makedirs(EXPORT_DIR, exist_ok=True)
        self.logger.info(f"Каталог экспорта: {EXPORT_DIR}")
        total_exported_classes = 0
        total_classes = len(filtered_classes)
        for index, class_info in enumerate(filtered_classes, start=1):
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


def print_menu(is_autosave_enabled: bool, selected_classes_status: str) -> None:
    print("\n=== Human.UA API Menu ===")
    print("1) Авторизоваться и инициализировать сессию")
    autosave_state = "ВКЛЮЧЕНО" if is_autosave_enabled else "ВЫКЛЮЧЕНО"
    print(f"2) Переключить автосохранение всех ответов API ({autosave_state})")
    print("3) Показать список классов")
    print(f"4) Выбрать классы для массовых операций ({selected_classes_status})")
    print("5) Показать список участников класса")
    print("6) Экспортировать всех учеников в CSV")
    print("7) Создать компетенции")
    print("8) Проставить группы результатов")
    print("9) Выставить компетенции из групп результатов")
    print("0) Выход")


def run_cli_menu(auth_service: HumanUAAuth) -> None:
    initialized = False
    while True:
        print_menu(auth_service.save_all_responses_enabled, auth_service.get_selected_classes_status())
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

        if choice == "2":
            enabled = auth_service.toggle_request_dumping()
            state = "ВКЛЮЧЕНО" if enabled else "ВЫКЛЮЧЕНО"
            print(f"Автосохранение ответов API: {state}")
            if enabled:
                print(f"Файлы сохраняются в: {RESPONSES_DIR}")
            continue

        if not initialized:
            print("Сначала выполните пункт 1 (инициализация сессии).")
            continue

        if choice == "3":
            classes = auth_service.get_all_classes()
            if classes is not None:
                print(f"Получено классов: {len(classes)}")
                auth_service.print_classes(classes)
            continue

        if choice == "4":
            classes = auth_service.get_all_classes()
            if classes is None:
                print("Не удалось получить список классов.")
                continue
            if not classes:
                print("Список классов пуст.")
                continue

            print("\nТекущий список классов:")
            auth_service.print_classes(classes)
            print(
                "\nВведите class_id через запятую (например: 1364654,1364700).\n"
                "Пустой ввод или 'all' сбрасывает фильтр (будут использованы все классы)."
            )
            raw = input("Выбор классов: ").strip()

            if not raw or raw.lower() == "all":
                auth_service.set_selected_class_ids(None)
                print("Фильтр классов сброшен. Используются все классы.")
                continue

            parts = [p.strip() for p in raw.split(',') if p.strip()]
            candidate_ids: Set[int] = set()
            invalid_parts: List[str] = []
            for p in parts:
                if p.isdigit():
                    candidate_ids.add(int(p))
                else:
                    invalid_parts.append(p)

            if invalid_parts:
                print(f"Некорректные значения class_id: {', '.join(invalid_parts)}")
                continue

            existing_ids = {
                int(cls_id) for cls_id in [cls.get('id') for cls in classes]
                if isinstance(cls_id, int)
            }
            missing_ids = sorted([x for x in candidate_ids if x not in existing_ids])
            if missing_ids:
                print(f"Эти class_id не найдены в текущем списке классов: {', '.join(str(x) for x in missing_ids)}")
                continue

            auth_service.set_selected_class_ids(candidate_ids)
            print(f"Выбрано классов: {len(candidate_ids)}")
            continue

        if choice == "5":
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

        if choice == "6":
            exported = auth_service.export_all_members_to_csvs_new_logic()
            print(f"Экспорт завершён. Успешно обработано классов: {exported}")
            continue

        if choice == "7":
            competence_name = input("Введите название компетенции: ").strip()
            if not competence_name:
                print("Название компетенции не может быть пустым.")
                continue

            date_input = input("Введите дату (YYYY-MM-DD или YYYY-MM-DD HH:MM:SS): ").strip()
            normalized_date = auth_service._normalize_report_date(date_input)
            if normalized_date is None:
                print("Некорректная дата. Пример: 2026-05-29 или 2026-05-29 00:00:00")
                continue

            print("Запускаю создание компетенций по всем классам. Подробности в логах...")
            stats = auth_service.create_competences_for_all_classes(
                competence_name=competence_name,
                report_date=normalized_date,
            )
            print(
                "Готово: "
                f"total={stats['total']}, processed={stats['processed']}, created={stats['created']}, "
                f"skipped={stats['skipped']}, errors={stats['errors']}"
            )
            continue

        if choice == "8":
            semester_raw = input("Выберите семестр (1 или 2): ").strip()
            if semester_raw not in {"1", "2"}:
                print("Некорректный семестр. Доступно: 1 или 2.")
                continue

            score_mode_raw = input(
                "Режим оценки: 1 - максимум (одно значение на все группы), "
                "2 - индивидуально (по average_score каждой группы) [1]: "
            ).strip()
            if score_mode_raw in {"", "1"}:
                score_mode = "max"
            elif score_mode_raw == "2":
                score_mode = "individual"
            else:
                print("Некорректный режим. Доступно: 1 или 2.")
                continue

            dry_run_raw = input("Тестовый режим без отправки POST? (y/N): ").strip().lower()
            dry_run = dry_run_raw in {"y", "yes", "д", "да"}

            semester_number = int(semester_raw)
            print(
                f"Запускаю проставление групп результатов для семестра {semester_number}. "
                "Подробности в логах..."
            )
            stats = auth_service.set_result_groups_for_selected_classes(
                semester_number=semester_number,
                dry_run=dry_run,
                score_mode=score_mode,
            )
            print(
                "Готово: "
                f"classes={stats['processed_classes']}/{stats['total_classes']}, "
                f"students={stats['students_processed']}/{stats['students_total']}, "
                f"students_with_failures={stats['students_with_failures']}, "
                f"posts_planned={stats['posts_planned']}, posts_ok={stats['posts_sent']}, posts_failed={stats['posts_failed']}, "
                f"skipped_classes={stats['skipped_classes']}, errors={stats['errors']}, mode={stats['score_mode']}"
            )
            if stats.get("failed_students_report"):
                print(f"Отчет по проблемным ученикам: {stats['failed_students_report']}")
            continue

        if choice == "9":
            semester_raw = input("Выберите семестр для источника оценок (1 или 2) [2]: ").strip()
            if semester_raw == "":
                semester_number = 2
            elif semester_raw in {"1", "2"}:
                semester_number = int(semester_raw)
            else:
                print("Некорректный семестр. Доступно: 1 или 2.")
                continue

            dry_run_raw = input("Тестовый режим без отправки POST? (y/N): ").strip().lower()
            dry_run = dry_run_raw in {"y", "yes", "д", "да"}

            print(
                f"Запускаю выставление компетенций из групп результатов для семестра {semester_number}. "
                "Подробности в логах..."
            )
            stats = auth_service.set_competence_assessments_for_selected_classes(
                semester_number=semester_number,
                dry_run=dry_run,
            )
            print(
                "Готово: "
                f"classes={stats['processed_classes']}/{stats['total_classes']}, "
                f"students={stats['students_processed']}/{stats['students_total']}, "
                f"students_with_failures={stats['students_with_failures']}, "
                f"posts_planned={stats['posts_planned']}, posts_ok={stats['posts_sent']}, posts_failed={stats['posts_failed']}, "
                f"skipped_classes={stats['skipped_classes']}, errors={stats['errors']}, semester={stats['semester_number']}"
            )
            if stats.get("failed_students_report"):
                print(f"Отчет по проблемным ученикам: {stats['failed_students_report']}")
            continue

        print("Неизвестная опция, попробуйте снова.")


if __name__ == '__main__':
    start_time = datetime.now()
    auth_service = HumanUAAuth(email=LOGIN_EMAIL, password=LOGIN_PASSWORD)
    run_cli_menu(auth_service)
    auth_service.logger.info(f"Общее время выполнения: {datetime.now() - start_time}")
