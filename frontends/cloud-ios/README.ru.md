# Kvist Cloud для iPhone и iPad

*(English version: [README.md](README.md))*

Один `WKWebView` со страницей облака для телефона ([`cloud/mobile`](../../cloud/mobile)) и то, чего страница сама не умеет: окно
**Apple Pay** (`PKPaymentAuthorizationController` из PassKit), ссылки в Safari, буфер обмена, вибрация. Фото идут через саму страницу: веб-вью открывает
галерею и камеру для её «+» (камере нужен `NSCameraUsageDescription` в `project.yml`), а фото, перетащенное из другого приложения
(Split View на iPad, между приложениями на iPhone), WebKit отдаёт странице как обычное перетаскивание. Kvist на устройстве не
работает; это окно в облако.

```bash
cd frontends/cloud-ios
swift test                                       # ядро: мост, правила адресов, запрос Apple Pay (работает и на Linux)
brew install xcodegen && xcodegen generate       # macOS: создаёт KvistCloud.xcodeproj
xcodebuild -project KvistCloud.xcodeproj -scheme KvistCloud -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
# сборка для тестового сервера на этом компьютере (обычный HTTP к локальным адресам разрешает App Transport Security):
xcodebuild … KVIST_CLOUD_URL=http://127.0.0.1:8742 build
# сборка для App Store: страница в ней ничего не продаёт (см. cloud/mobile/README.ru.md):
xcodebuild … KVIST_STORE=YES build
```

Продуктовая сборка ходит на `https://cloud.kvist.site`.

## Попробовать на iPhone (без платного аккаунта)

Бери файл `Kvist-Cloud-ios-unsigned.ipa` из релиза GitHub **«Kvist Cloud phone apps (rolling)»** (его собирает
[`cloud-mobile-release.yml`](../../.github/workflows/cloud-mobile-release.yml); при ручном запуске можно выбрать адрес облака). Он
не подписан: программа для сайдлоада подпишет его твоим Apple ID. **Apple Pay в этой сборке не работает** (праву нужен платный
аккаунт разработчика и Merchant ID); вход, чат, тарифы и другие способы оплаты работают.

1. Облако по адресу из описания релиза должно уже работать на этой версии, иначе приложение скажет «Нет соединения».
2. На iPhone: Настройки, Конфиденциальность и безопасность, включить **Режим разработчика** (iOS 16+; телефон перезагрузится).
3. На компьютере поставь программу для сайдлоада: [AltStore](https://altstore.io) (macOS и Windows; AltServer живёт в трее и ставит
   по Wi-Fi или кабелю), [Sideloadly](https://sideloadly.io) (macOS и Windows) или на Linux общественные программы вроде
   AltServer-Linux или Sideloader. Я не мог проверить ни одну: следуй их инструкции «установить .ipa».
4. Дай ей `.ipa` и свой Apple ID (подойдёт отдельный; при двухфакторной защите программа попросит пароль приложения).
5. На iPhone: Настройки, Основные, **VPN и управление устройством**, твой Apple ID, **Доверять**. Открой Kvist Cloud.
6. Подпись бесплатного Apple ID действует **7 дней** и разрешает 3 приложения: переустанови (или дай AltStore обновить по Wi-Fi),
   когда истечёт.

Что проверить по порядку: экран входа; «Продолжить через GitHub» и возврат в приложение; принятие условий; лист тарифов (цены); сессию
(отправь сообщение, посмотри, как складывается строка работы); клавиатуру (поле ввода остаётся над ней, последнее сообщение не
закрыто); на iPhone поворот выключен; меню; русский (лист аккаунта, язык); «другим способом». Если что-то не так, пришли скриншот и
что ты нажимал.

**С платным аккаунтом разработчика** (Apple Pay): создай Merchant ID `merchant.site.kvist.cloud` (или поменяй его в
[`KvistCloud.entitlements`](App/KvistCloud.entitlements) и в `APPLE_PAY_MERCHANT_ID` на сервере), `brew install xcodegen`,
`xcodegen generate`, открой `KvistCloud.xcodeproj`, выбери свою команду в Signing и запусти на телефоне. Для стороны кошелька на сервере
ещё нужен `WALLET_ENABLED` с тестовыми ключами Stripe (см. [`cloud/README.ru.md`](../../cloud/README.ru.md)).

## Вход

Страница GitHub, и страница Google за кнопкой «войти через Google», не работают во встроенном браузере, а если открыть их в Safari,
сессия останется в Safari. Поэтому приложение входит через собственную сессию системного браузера (`ASWebAuthenticationSession`,
она делит cookie с Safari), а облако отдаёт приложению короткоживущий подписанный код (`/auth/app/start`,
`kvistcloud://signed-in?code=`); веб-вид меняет его вместе со случайным nonce, который знает только это приложение, на свою сессию
(`/auth/app/exchange`). См. [`appauth.py`](../../cloud/backend/kvist_cloud/appauth.py) и
[`AppSignIn.swift`](Sources/KvistCloudCore/AppSignIn.swift).

## Как страница достаёт до приложения

`WKUserScript`, запускаемый до страницы ([`Bridge.startScript`](Sources/KvistCloudCore/Bridge.swift)), делает `window.KvistHost`
из `WKScriptMessageHandlerWithReply`. Обработчик отвечает **только на сообщение с собственного origin облака из главного фрейма**
([`WebViewController`](App/WebViewController.swift)); страница входа GitHub, которая грузится в том же окне, получает отказ. В
приложении остаются только облако и вход через GitHub ([`Origins`](Sources/KvistCloudCore/Origins.swift)); остальные ссылки идут
в системный браузер.

## Apple Pay

Цену, продавца и сети решает сервер; приложение собирает из его запроса `PKPaymentRequest` и возвращает `PKPaymentToken`
([`ApplePay.swift`](App/ApplePay.swift)). Окно **остаётся открытым**, пока страница не скажет, списал ли сервер токен (`payDone`),
поэтому человек не увидит «Готово» у платежа, который потом отклонят.

Что для этого нужно (в репозитории этого нет):

1. Платный аккаунт Apple Developer и **Merchant ID** (`merchant.site.kvist.cloud` написан в
   [`KvistCloud.entitlements`](App/KvistCloud.entitlements) и в `APPLE_PAY_MERCHANT_ID` на сервере; менять нужно оба места сразу).
2. **Сертификат обработки платежей** для Merchant ID: в панели Stripe (Settings, Apple Pay, iOS) создать запрос на сертификат,
   загрузить его в Apple, а полученный сертификат загрузить в Stripe. Им Stripe расшифровывает токен.
3. Подписать приложение профилем с возможностью Apple Pay (бесплатный Apple ID не подходит).

Неподписанная сборка (CI, симулятор без подписанного профиля) собирается и запускается, но `PKPaymentAuthorizationController.canMakePayments`
отвечает «нет», и страница показывает другие способы оплаты.

**Ревью App Store.** Apple требует In-App Purchase для цифровых товаров и услуг, используемых в приложении (правило 3.1.1); Apple
Pay — для физических товаров и услуг вне него. Часы облачной машины — услуга, которой пользуются из приложения; это серая зона, а
вход через сторонний сервис (GitHub) по правилу 4.8 требует равноценного варианта, не раскрывающего данные. Перед отправкой
прочитай актуальные правила или распространяй вне магазина.

## Что проверено

Swift-ядро и его 14 тестов проходят на Linux. Сторона страницы проверена браузерными тестами с подставным хостом (в WebKit).
**Не проверено:** целевое приложение ([`App/`](App)) написано без Mac и ни разу не компилировалось (первым это сделает job `ios-app`
в CI), а настоящее окно Apple Pay требует устройства, merchant и подписанной сборки. Не проверено и то, что Stripe принимает токен
так, как его передаёт [`wallet.py`](../../cloud/backend/kvist_cloud/wallet.py) (`POST /v1/tokens` с `pk_token`, как делает iOS-библиотека
Stripe): сначала проверь на тестовых ключах Stripe.
