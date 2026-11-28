# Публикация телефонных приложений Kvist Cloud

*(English version: [cloud-mobile-store.md](cloud-mobile-store.md))*

Что уже делает код и что может сделать только владелец аккаунтов разработчика. Приложения —
[`cloud-android`](cloud-android) и [`cloud-ios`](cloud-ios); магазинные сборки делает
[`cloud-mobile-store.yml`](../.github/workflows/cloud-mobile-store.yml), запуск вручную.

## Сделано в коде

| Правило магазина | Где |
| --- | --- |
| В магазинной сборке нет покупок и отсылок к ним (Apple 3.1.3(f), политика платежей Google Play) | `KvistHost.store`, [`cloud/mobile`](../cloud/mobile/README.ru.md) |
| Удаление аккаунта внутри приложения (Apple 5.1.1(v), удаление аккаунта в Google Play) | лист аккаунта; [`account.py`](../cloud/backend/kvist_cloud/account.py), стирание через 90 дней |
| Вход через Apple рядом с GitHub (Apple 4.8) | [`apple.py`](../cloud/backend/kvist_cloud/apple.py), [`AppleSignIn.swift`](cloud-ios/App/AppleSignIn.swift) |
| Политика конфиденциальности доступна из приложения | `/privacy`, ссылка в листе аккаунта |
| Подписанные пронумерованные сборки для магазинов | `cloud-mobile-store.yml` (ключ загрузки Play, ключ App Store Connect API) |

Программа продавцов Google Play открыта не во всех странах, а Play Billing без неё не работает, поэтому магазинные сборки ничего не
продают, а тарифы покупаются на сайте. Нативная оплата может появиться позже через Freedom Pay.

## Ещё открыто

- **Демо-аккаунт для ревьюеров.** Оба магазина входят в приложение с данными, которые ты им даёшь. Аккаунт для проверки
  ([`review.py`](../cloud/backend/kvist_cloud/review.py)) с тарифом открывается только по ссылке в браузере. Приложениям нужен свой
  вход: например, поле для кода доступа на экране входа.
- Ничего ещё не запускалось ни на устройстве, ни в магазинах; workflow для магазинов не запускался ни разу.

## Google Play

1. Зарегистрироваться в [Play Console](https://play.google.com/console) как частное лицо ($25, проверка личности). Новый личный
   аккаунт должен провести **закрытое тестирование минимум с 12 тестировщиками в течение 14 дней**, прежде чем откроется продакшен.
2. Создать приложение: название **Kvist Cloud**, пакет `site.kvist.cloud`, бесплатное.
3. Сделать ключ загрузки: `keytool -genkeypair -v -keystore upload.jks -alias upload -keyalg RSA -keysize 4096 -validity 10000`.
   Файл и пароли держать вне репозитория. Свой ключ подписи хранит Google (Play App Signing).
4. Секреты репозитория: `ANDROID_UPLOAD_KEYSTORE_BASE64` (`base64 -w0 upload.jks`), `ANDROID_UPLOAD_KEYSTORE_PASSWORD`,
   `ANDROID_UPLOAD_KEY_ALIAS`, `ANDROID_UPLOAD_KEY_PASSWORD`.
5. Запустить workflow (platform `android`), скачать артефакт `.aab` и один раз загрузить его **вручную** (API Google не умеет
   создавать первый релиз).
6. Для следующих загрузок из workflow: сервисный аккаунт Google Cloud с доступом к приложению в Play Console (Пользователи и
   разрешения), его JSON-ключ — в секрет `PLAY_SERVICE_ACCOUNT_JSON`.
7. Страница в магазине и формы, которые просит Play:
   - **Безопасность данных:** собираются имя, email, id пользователя (аккаунт), фото, которые человек прикладывает, действия в
     приложении (сессии); цель — работа приложения и управление аккаунтом; шифрование при передаче; удаление можно запросить в
     приложении.
   - **Адрес для удаления аккаунта:** `https://cloud.kvist.site/m/` (лист аккаунта, «Удалить аккаунт») и политика конфиденциальности.
   - Анкета **возрастного рейтинга** (инструмент для разработчиков: без насилия, без чата между пользователями).
   - **Доступ к приложению:** демо-аккаунт (см. «Ещё открыто»).

## App Store

1. Вступить в [Apple Developer Program](https://developer.apple.com/programs/enroll/) ($99 в год).
2. Certificates, Identifiers and Profiles: App ID `site.kvist.cloud` с **Sign in with Apple** (и Apple Pay, только если он
   понадобится); ключ с **Sign in with Apple** для сервера (`APPLE_TEAM_ID`, `APPLE_KEY_ID`, `.p8`, см.
   [`cloud/README.ru.md`](../cloud/README.ru.md#вход-через-apple)); Services ID для кнопки на сайте, если она нужна.
3. App Store Connect: новое приложение, bundle id `site.kvist.cloud`, название Kvist Cloud, бесплатное. Users and Access,
   Integrations: **ключ App Store Connect API** с ролью App Manager.
4. Секреты репозитория: `APPLE_TEAM_ID`, `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8_BASE64` (`base64 -w0 AuthKey_XXXX.p8`).
5. Включить `APPLE_SIGNIN_ENABLED` на сервере, затем запустить workflow (platform `ios`): он архивирует магазинную сборку,
   подписывает её автоматической подписью и загружает в TestFlight.
6. Информация для App Review:
   - **Заметки** (по-английски): "Kvist Cloud is the companion app of a paid web service (cloud machines for a coding agent).
     Plans are bought on the website; the app sells nothing and does not link to purchases (Guideline 3.1.3(f)). Sign in with
     Apple is offered next to GitHub. Account deletion: account sheet, Delete account."
   - **Демо-аккаунт:** см. «Ещё открыто».
   - **Конфиденциальность в App Store:** контактные данные (имя, email), идентификаторы (id пользователя), пользовательский
     контент (фото и сообщения, которые человек отправляет), данные об использовании; всё связано с аккаунтом, ничего не
     используется для отслеживания.
