# Kvist Cloud для Android

*(English version: [README.md](README.md))*

Один WebView со страницей облака для телефона ([`cloud/mobile`](../../cloud/mobile)) и то, чего страница сама не умеет: окно
**Google Pay** (`PaymentsClient` от Google), ссылки в системном браузере, буфер обмена, вибрация на нажатие, кнопка «назад», выбор фото и камера за «+» на странице, а ещё фото, перетащенные
в приложение из другого (WebView не отдаёт странице файлы другого приложения, поэтому приложение читает их само и передаёт в
`window.kvistDrop`). Kvist на телефоне не работает; это окно в облако.

```bash
cd frontends/cloud-android                      # JDK 21 и ANDROID_HOME с платформой 36
./gradlew :app:testDebugUnitTest :app:assembleDebug
# сборка для тестового сервера на этом компьютере (эмулятор видит его как 10.0.2.2); обычный HTTP разрешён только в ней:
./gradlew :app:assembleDebug -PkvistCloudUrl=http://10.0.2.2:8742
# сборка для Google Play: страница в ней ничего не продаёт (см. cloud/mobile/README.ru.md):
./gradlew :app:assembleRelease -PkvistStore=true
```

Продуктовая сборка ходит на `https://cloud.kvist.site` и отказывается от обычного HTTP (CI проверяет итоговый манифест).

## Попробовать на Android

Бери `Kvist-Cloud-android.apk` из релиза GitHub **«Kvist Cloud phone apps (rolling)»**
([`cloud-mobile-release.yml`](../../.github/workflows/cloud-mobile-release.yml); при ручном запуске можно выбрать адрес облака).

1. Облако по адресу из описания релиза должно уже работать на этой версии, иначе приложение скажет «Нет соединения».
2. Скачай APK на телефон, открой, разреши «установку из неизвестных источников» для браузера или файлового менеджера, установи. Или с
   компьютера: `adb install -r Kvist-Cloud-android.apk` (с включённой отладкой по USB).
3. APK подписан отладочным ключом машины сборки, каждый раз другим: чтобы обновить, сначала удали старую сборку.
4. Google Pay: страница предлагает его, только если на сервере включён `WALLET_ENABLED`, а в Google Pay на телефоне есть карта; окно
   работает в среде **TEST** Google, пока сервер не переведён в `PRODUCTION`, так что настоящая карта не списывается.

Что проверить: вход через GitHub и возврат; условия; тарифы; сессию; клавиатуру (поле ввода над ней); кнопку «назад» (закрывает лист,
потом меню, потом ведёт на стартовый экран, потом выходит); русский; «другим способом» (открывает браузер).

## Вход

Страница GitHub (и Google за ней) не работает во встроенном браузере, поэтому вход делает Custom Tab с сессией браузера, а облако
отдаёт приложению короткоживущий код через `kvistcloud://signed-in?code=` (intent-filter у activity); веб-вид меняет его вместе со
случайным nonce, который знает только это приложение, на свою сессию. Серверная часть та же, что для iOS:
[`appauth.py`](../../cloud/backend/kvist_cloud/appauth.py), [`AppSignIn.kt`](app/src/main/java/site/kvist/cloud/AppSignIn.kt).

## Как страница достаёт до приложения

`WebViewCompat.addWebMessageListener` создаёт канал сообщений, который существует **только для собственного origin облака**, а
скрипт, запускаемый до страницы ([`Bridge.START_SCRIPT`](app/src/main/java/site/kvist/cloud/Bridge.kt)), делает из него
`window.KvistHost`. Страница входа GitHub, которая грузится в том же окне, не получает ничего. В приложении остаются только
облако и вход через GitHub ([`Origins`](app/src/main/java/site/kvist/cloud/Origins.kt)); остальные ссылки открываются в системном
браузере, и наружу уходят только `http(s)` и `mailto:`.

## Google Pay

Цену, продавца и шлюз решает сервер (`transactionInfo`, `merchantInfo`, токенизация `PAYMENT_GATEWAY` для Stripe); приложение
только открывает окно с этим запросом и возвращает `paymentMethodData.tokenizationData.token`
([`GooglePay.kt`](app/src/main/java/site/kvist/cloud/GooglePay.kt)). Среду называет сервер: `TEST`, пока не заданы
`GOOGLE_PAY_ENVIRONMENT=PRODUCTION` и `GOOGLE_PAY_MERCHANT_ID`.

Чтобы выйти в боевой режим (здесь не сделано, нужны твои аккаунты):

1. Зарегистрироваться в [Google Pay & Wallet Console](https://pay.google.com/business/console) и получить merchant ID.
2. Опубликовать приложение (или закрытое тестирование) в Google Play и отправить интеграцию на доступ к боевому режиму со
   скриншотами оплаты, как просит [чеклист Google](https://developers.google.com/pay/api/android/guides/test-and-deploy/integration-checklist).
3. На сервере задать `GOOGLE_PAY_ENVIRONMENT=PRODUCTION`, `GOOGLE_PAY_MERCHANT_ID` и боевые ключи Stripe.

**Политика Play.** Google Play требует Google Play Billing для цифровых товаров, продаваемых внутри приложения. Часы облачной
машины — услуга, которой пользуются из приложения; это серая зона: перед публикацией прочитай актуальную политику платежей или
продавай вне приложения (так работают другие способы оплаты на странице). При установке APK напрямую этого вопроса нет.

## Что проверено

Kotlin собирается, его 14 юнит-тестов проходят (сообщения моста, правила адресов, JSON Google Pay). Сторона страницы проверена
браузерными тестами с подставным хостом. **Не проверено:** приложение на устройстве или в эмуляторе и настоящее окно Google Pay
(нужны аккаунт Google, Play services и, в бою, merchant). Первое, что стоит сделать с телефоном: открыть приложение, войти, открыть
тариф и убедиться, что кнопка Google Pay появилась и окно TEST открывается.
