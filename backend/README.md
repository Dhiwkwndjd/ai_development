# Library Backend (Django 5.2 + DRF + MySQL)

Модели, миграции MySQL, REST API (JWT), бизнес-логика бронирований и очереди, журнал действий,
Swagger-документация и тесты. Frontend не реализован. SQLite не используется и не настроена.

## Быстрый старт

```bash

# 1. Python-окружение
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # для mysqlclient нужны libmysqlclient-dev / pkg-config

# 2. Настройки
cp .env.example .env

# 3. Миграции и админ
python manage.py migrate
python manage.py createsuperuser       # создаст пользователя с ролью admin
python manage.py runserver
#   Swagger UI:  http://localhost:8000/api/docs/
#   OpenAPI:     http://localhost:8000/api/schema/
#   Админка:     http://localhost:8000/admin/

# 4. Тесты ограничений (нужны права на CREATE DATABASE для пользователя БД)
python manage.py test
```

> Если используете docker-compose из архива, пользователю `library` может не хватать прав на
> создание тестовой БД. Выдайте: `GRANT ALL ON *.* TO 'library'@'%';` (под root).

## REST API (`/api/v1/`)

Авторизация: `Authorization: Bearer <access>`. Access — JWT (15 мин), refresh — случайный токен
(30 дней), в БД хранится только его SHA-256 хэш. При обмене refresh **ротируется**; повторное
предъявление уже использованного refresh отзывает все токены пользователя.
Пагинация: `?page=`, `?page_size=` (макс. 100). Ошибки — JSON `{"detail": ...}` или словарь полей.

| Метод и путь | Кто | Описание |
|---|---|---|
| `POST /auth/register/` | все | Регистрация (всегда роль reader), возвращает user + токены |
| `POST /auth/login/` | все | Вход по email/паролю |
| `POST /auth/refresh/` | все | Обмен refresh на новую пару |
| `POST /auth/logout/` | все | Отзыв refresh-токена |
| `POST /auth/logout-all/` | авториз. | Отзыв всех токенов пользователя |
| `GET, PATCH /users/me/` | авториз. | Профиль (full_name, phone) |
| `POST /users/me/change-password/` | авториз. | Смена пароля, все сессии завершаются |
| `GET /users/`, `GET /users/{id}/` | librarian, admin | Список/просмотр (`?search=`, `?role=`, `?is_active=`) |
| `POST /users/`, `PATCH /users/{id}/` | admin | Создание сотрудников, смена роли, блокировка |
| `GET /books/`, `GET /books/{id}/` | авториз. | Каталог: `?search=`, `?author=`, `?year=`, `?available=true`; поля `available_copies`, `total_copies`, `queue_length` |
| `POST/PUT/PATCH/DELETE /books/…` | librarian, admin | Управление книгами |
| `GET/POST/PATCH/DELETE /copies/…` | librarian, admin | Экземпляры (`?book=`, `?status=`) |
| `GET /bookings/`, `GET /bookings/{id}/` | авториз. | Читатель — свои, staff — все (`?status=`, `?book=`, `?active=true`, `?user=`) |
| `POST /bookings/` | авториз. | `{"book": id}` — бронь свободного экземпляра (staff может добавить `"user": id`) |
| `POST /bookings/{id}/confirm/` | librarian, admin | created → confirmed (выдача), необязательный `due_date` |
| `POST /bookings/{id}/complete/` | librarian, admin | confirmed → completed (возврат) |
| `POST /bookings/{id}/cancel/` | владелец / staff | → cancelled; читатель — только в статусе created |
| `GET /queue/`, `GET /queue/{id}/` | авториз. | Читатель — свои записи, staff — все (`?book=`, `?active=true`), поле `position` |
| `POST /queue/` | авториз. | `{"book": id}` — встать в очередь (только если нет свободных экземпляров) |
| `DELETE /queue/{id}/` | владелец / staff | Покинуть очередь (статус cancelled) |
| `GET /logs/` | librarian, admin | Журнал: `?user=`, `?action=` (или префикс `booking.`), `?entity_type=`, `?entity_id=`, `?from=`, `?to=` |

### Правила бронирования

- `POST /bookings/` → бронь `created`, экземпляр `reserved`. Подтверждение → `confirmed` / `issued`
  (срок возврата по умолчанию 14 дней). Возврат или отмена → экземпляр `available`.
- Лимит: 5 активных броней на пользователя; одну книгу нельзя забронировать дважды одновременно.
- Нет свободных экземпляров → `409`, нужно встать в очередь.
- Когда экземпляр освобождается (возврат, отмена, новый экземпляр, возврат из обслуживания), первый
  в очереди получает статус `notified`; «его» экземпляр нельзя забронировать в обход очереди.
- Неподтверждённые брони (48 ч) и уведомления очереди (24 ч) истекают командой
  `python manage.py process_expirations` (запускать по cron). Параметры — `LIBRARY` в `settings.py`.
- Все изменения идут в транзакциях с `SELECT … FOR UPDATE` (нужен MySQL 8.0 / MariaDB 10.6+ из-за
  `SKIP LOCKED`); UNIQUE-ограничения БД — последняя линия защиты от гонок.
- Все значимые действия пишутся в `action_logs`.

## Структура проекта

```
config/            настройки (MySQL, utf8mb4, USE_TZ=True), urls
apps/common/       права доступа, пагинация, аудит-хелпер, исключения
apps/users/        User + auth/users API
apps/catalog/      Book, BookCopy + API
apps/bookings/     Booking, WaitingQueue + services.py (бизнес-логика) + API
apps/tokens/       RefreshToken
apps/audit/        ActionLog + API журнала
```

## Схема данных

Обозначения: **PK** — первичный ключ, **FK** — внешний ключ, **NN** — обязательное (NOT NULL), **NULL** — необязательное.

### users (User)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT AUTO_INCREMENT | NN | **PK** |
| email | VARCHAR(254) | NN | **UNIQUE**, хранится в нижнем регистре |
| password | VARCHAR(128) | NN | хэш пароля |
| full_name | VARCHAR(255) | NN | |
| phone | VARCHAR(32) | NN | по умолчанию `''` |
| role | VARCHAR(16) | NN | `reader` (default) / `librarian` / `admin`; CHECK |
| is_active | BOOL | NN | default true |
| is_staff, is_superuser | BOOL | NN | только для Django Admin |
| last_login | DATETIME(6) | NULL | |
| date_joined, updated_at | DATETIME(6) | NN | |

Индексы: `(role, is_active)`, `(full_name)`, UNIQUE `email`.

### books (Book)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT | NN | **PK** |
| title | VARCHAR(255) | NN | |
| author | VARCHAR(255) | NN | |
| isbn | VARCHAR(17) | NULL | **UNIQUE** (несколько NULL допустимы) |
| publisher | VARCHAR(255) | NN | default `''` |
| publication_year | SMALLINT UNSIGNED | NULL | 1000–9999 |
| language | VARCHAR(32) | NN | default `''` |
| description | TEXT | NN | default `''` |
| created_at, updated_at | DATETIME(6) | NN | |

Индексы: `(title)`, `(author, title)`, UNIQUE `isbn`.

### book_copies (BookCopy)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT | NN | **PK** |
| book_id | BIGINT | NN | **FK → books.id**, `PROTECT` |
| inventory_number | VARCHAR(64) | NN | **UNIQUE** |
| status | VARCHAR(16) | NN | `available` (default), `reserved`, `issued`, `maintenance`, `lost`, `written_off`; CHECK |
| shelf_location | VARCHAR(128) | NN | default `''` |
| notes | TEXT | NN | default `''` |
| acquired_at | DATE | NULL | |
| created_at, updated_at | DATETIME(6) | NN | |

Индексы: `(book_id, status)`, `(status)`, UNIQUE `inventory_number`.
Связь: Book 1 — N BookCopy.

### bookings (Booking)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT | NN | **PK** |
| user_id | BIGINT | NN | **FK → users.id**, `PROTECT` |
| book_copy_id | BIGINT | NN | **FK → book_copies.id**, `PROTECT` |
| status | VARCHAR(16) | NN | `created` (default) / `confirmed` / `completed` / `cancelled`; CHECK |
| processed_by_id | BIGINT | NULL | **FK → users.id**, `SET NULL` (кто обработал) |
| due_date | DATE | NULL | срок возврата |
| comment | TEXT | NN | default `''` |
| created_at | DATETIME(6) | NN | |
| confirmed_at, completed_at, cancelled_at | DATETIME(6) | NULL | |
| updated_at | DATETIME(6) | NN | |
| active_marker | SMALLINT, STORED GENERATED | NULL | 1 для `created`/`confirmed`, иначе NULL (только чтение) |

Ограничение: **UNIQUE `(book_copy_id, active_marker)`** — не более одной активной брони на экземпляр.
Индексы: `(user_id, status)`, `(book_copy_id, status)`, `(status, created_at)`, `(due_date)`.

### waiting_queue (WaitingQueue)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT | NN | **PK**, определяет порядок очереди |
| user_id | BIGINT | NN | **FK → users.id**, `PROTECT` |
| book_id | BIGINT | NN | **FK → books.id**, `PROTECT` |
| status | VARCHAR(16) | NN | `waiting` (default) / `notified` / `fulfilled` / `cancelled` / `expired`; CHECK |
| queued_at | DATETIME(6) | NN | момент постановки |
| notified_at, closed_at | DATETIME(6) | NULL | |
| updated_at | DATETIME(6) | NN | |
| active_marker | SMALLINT, STORED GENERATED | NULL | 1 для `waiting`/`notified`, иначе NULL |

- **Порядок постановки**: по `id` (AUTO_INCREMENT, выдаётся БД атомарно, строго возрастает —
  не зависит от часов app-серверов). Выборка: `WaitingQueue.objects.for_book(book)`;
  позиция записи: `entry.position`.
- **Запрет повторной активной записи**: **UNIQUE `(user_id, book_id, active_marker)`**.
  Пока запись активна (`waiting`/`notified`), вторая на ту же книгу невозможна;
  после закрытия (`fulfilled`/`cancelled`/`expired`) пользователь может встать снова.

Индексы: `(book_id, active_marker)`, `(user_id, status)`, `(status, queued_at)`.

### refresh_tokens (RefreshToken)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT | NN | **PK** |
| user_id | BIGINT | NN | **FK → users.id**, `CASCADE` |
| token_hash | VARCHAR(64) | NN | **UNIQUE**, SHA-256 hex; сам токен не хранится |
| created_at | DATETIME(6) | NN | |
| expires_at | DATETIME(6) | NN | |
| revoked_at | DATETIME(6) | NULL | |
| last_used_at | DATETIME(6) | NULL | |
| replaced_by_id | BIGINT | NULL | **FK (1:1) → refresh_tokens.id**, `SET NULL` — цепочка ротации |
| user_agent | VARCHAR(255) | NN | default `''` |
| ip_address | VARCHAR(39) | NULL | IPv4/IPv6 |

Индексы: `(user_id, revoked_at)`, `(expires_at)`, UNIQUE `token_hash`.

### action_logs (ActionLog)
| Поле | Тип | Обяз. | Примечание |
|---|---|---|---|
| id | BIGINT | NN | **PK** |
| user_id | BIGINT | NULL | **FK → users.id**, `SET NULL` |
| action | VARCHAR(64) | NN | код, напр. `booking.created` |
| entity_type | VARCHAR(64) | NN | default `''` |
| entity_id | BIGINT | NULL | мягкая ссылка без FK |
| details | JSON | NN | default `{}` |
| ip_address | VARCHAR(39) | NULL | |
| user_agent | VARCHAR(255) | NN | default `''` |
| created_at | DATETIME(6) | NN | |

Индексы: `(created_at)`, `(user_id, created_at)`, `(action, created_at)`, `(entity_type, entity_id)`.

## Связи

```
User 1 ─── N Booking           (user)          PROTECT
User 1 ─── N Booking           (processed_by)  SET NULL
User 1 ─── N WaitingQueue                      PROTECT
User 1 ─── N RefreshToken                      CASCADE
User 1 ─── N ActionLog                         SET NULL
Book 1 ─── N BookCopy                          PROTECT
Book 1 ─── N WaitingQueue                      PROTECT
BookCopy 1 ─ N Booking                         PROTECT
RefreshToken 1 ─ 1 RefreshToken (replaced_by)  SET NULL
```

## Почему «маркер активности»

MySQL не поддерживает частичные уникальные индексы, а Django на MySQL игнорирует
`UniqueConstraint(condition=...)`. Поэтому используется `GeneratedField` (STORED):
для активных записей значение равно 1, для остальных — NULL, а UNIQUE-индекс MySQL допускает
любое число NULL. Ограничение работает на уровне БД и защищает от гонок при параллельных запросах.
Требуется MySQL 8.0.13+ / MariaDB 10.6+.

## Проверено

Миграции применены, а 73 теста (ограничения БД, все эндпоинты, права ролей, ротация токенов, жизненный
цикл брони, очередь, истечение сроков, параллельные запросы на последний экземпляр) пройдены на
MariaDB 10.11 (MySQL-совместимая); сценарий также прогнан живым HTTP-сервером. На MySQL 8.0 проект не запускался в этой среде;
GeneratedField и CHECK-ограничения в нём поддерживаются штатно.
