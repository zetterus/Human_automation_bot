import requests
import logging
import json
import csv
from datetime import datetime
from typing import Dict, Any, List, Optional
import re
import time

# ============== НАСТРОЙКИ ==============
# Замените эти значения на ваши данные для входа.
# РЕКОМЕНДУЕТСЯ использовать переменные окружения для хранения
# конфиденциальных данных вместо того, чтобы хранить их прямо в коде.
LOGIN_EMAIL = "YOUR_EMAIL"
LOGIN_PASSWORD = "YOUR_PASSWORD"
LOG_FILE = "humanua_api.log"  # Файл для логов
# Вывод подробных логов в консоль. Установите в False для продакшена.
DEBUG_MODE = False


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
        # Установка institution_id в None для проверки его получения
        self.institution_id: Optional[int] = None
        self._setup_logging()
        self._configure_session()

    def _setup_logging(self):
        """Настройка логирования."""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Обработчик для файла
        file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)  # Логировать в файл начиная с INFO
        self.logger.addHandler(file_handler)

        # Обработчик для консоли
        if DEBUG_MODE:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(logging.DEBUG)  # Логировать в консоль начиная с DEBUG
            self.logger.addHandler(console_handler)

        self.logger.info("Логирование настроено. Уровень: INFO")

    def _configure_session(self):
        """Настройка HTTP-сессии."""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Encoding': 'gzip, deflate, br',
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

    def get_institution_id(self) -> bool:
        """Получение ID организации пользователя."""
        self.logger.info("Попытка получения ID организации...")
        try:
            response = self.session.get(f"{self.api_url}/user/institutions", timeout=10)
            response.raise_for_status()
            profile_data = response.json()
            if 'institutions' in profile_data and profile_data['institutions']:
                self.institution_id = profile_data['institutions'][0]['id']
                self.logger.info(f"ID организации успешно получен: {self.institution_id}")
                return True
            else:
                self.logger.error("Не удалось найти ID организации в данных профиля.")
                return False
        except requests.RequestException as e:
            self.logger.error(f"Ошибка при получении ID организации: {str(e)}")
            return False

    def get_all_classes(self) -> Optional[List[Dict[str, Any]]]:
        """Получение списка всех классов."""
        if not self.institution_id:
            self.logger.error("ID организации не установлен. Невозможно получить список классов.")
            return None

        self.logger.info("\n--- Запрос списка классов ---")
        try:
            response = self.session.get(
                f"{self.api_url}/{self.institution_id}/group/courses/my-courses?expand=members&_limit=987654321",
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
        if not self.institution_id:
            self.logger.error("ID организации не установлен. Невозможно получить участников класса.")
            return None

        url = f"{self.api_url}/{self.institution_id}/group/group/{class_id}?expand=members.member.user&_limit=987654321"
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

    def export_all_members_to_csvs_new_logic(self):
        """
        Получает список классов и сразу экспортирует данные участников
        каждого класса в отдельный CSV-файл.
        """
        classes_list = self.get_all_classes()
        if classes_list is None:
            self.logger.error("Не удалось получить список классов. Экспорт невозможен.")
            return

        if not classes_list:
            self.logger.warning("Список классов пуст. Нечего экспортировать.")
            return

        self.logger.info("\n--- Запуск экспорта участников классов ---")
        total_exported_classes = 0
        for class_info in classes_list:
            class_id = class_info.get('id')
            class_name = class_info.get('name')
            class_number = class_info.get('number')
            class_subject = class_info.get('subject').get('i18n').get('name')
            members_count = len(class_info.get('members', [])) if class_info.get('members') is not None else 0

            # Проверка, что имя класса существует
            if not class_name or class_id is None:
                self.logger.warning(f"Пропущен класс с неполными данными: {class_info}")
                continue

            filename = self._sanitize_filename(f"{class_number}-{class_name}({class_subject}).csv")

            if members_count == 0:
                self.logger.info(f"Пропуск класса '{class_name}' (ID: {class_id}) так как нет учеников.")
                continue

            members = self.get_class_members(class_id)
            if members is None:
                self.logger.warning(
                    f"    Не удалось получить данные об участниках класса '{class_name}' (ID: {class_id}). Пропуск."
                )
                continue

            self.logger.info(
                f"Экспорт участников класса '{class_number}-{class_name}' ({members_count} человек) в файл '{filename}'..."
            )

            fieldnames = ['id', 'user_id', 'group_id', 'group_role_id', 'full_name']

            try:
                with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()

                    for member in members:
                        # Проверка на наличие необходимых данных об ученике
                        if 'member' in member and member["group_role_id"] == 5:
                            user_data = member['member']
                            # Проверка, что у ученика есть имя или фамилия
                            if 'first_name' in user_data or 'last_name' in user_data:
                                full_name = f"{user_data.get('last_name', '')} {user_data.get('first_name', '')}".strip()
                                row = {
                                    'id': member.get('id'),
                                    'user_id': member.get('user_id'),
                                    'group_id': member.get('group_id'),
                                    'group_role_id': member.get('group_role_id'),
                                    'full_name': full_name
                                }
                            else:
                                self.logger.info(
                                    f"Пропущен участник без имени/фамилии в классе '{class_name}' (ID: {class_id})."
                                )
                        else:
                            self.logger.warning(
                                f"Пропущен участник с неполными данными в классе '{class_name}' (ID: {class_id})."
                            )
                self.logger.info(f"    Экспорт '{class_name}' завершен.")
                total_exported_classes += 1
            except Exception as e:
                self.logger.error(f"    Ошибка при записи файла '{filename}': {e}")
                self.logger.warning(f"    Экспорт '{class_number}-{class_name}' не завершён из-за ошибки.")

        self.logger.info(
            f"Экспорт данных об участниках всех классов завершен. Экспортировано классов: {total_exported_classes}")


if __name__ == '__main__':
    start_time = datetime.now()

    auth_service = HumanUAAuth(email=LOGIN_EMAIL, password=LOGIN_PASSWORD)

    if auth_service.login():
        if auth_service.get_institution_id():
            auth_service.logger.info("Программа успешно авторизовалась и получила ID организации.")
            auth_service.export_all_members_to_csvs_new_logic()
            auth_service.logger.info(f"Общее время выполнения: {datetime.now() - start_time}")
        else:
            auth_service.logger.error("Не удалось получить ID организации. Программа будет завершена.")
    else:
        auth_service.logger.error("Авторизация не удалась. Программа будет завершена.")
