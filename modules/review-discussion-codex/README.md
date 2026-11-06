# review-discussion-codex

Адаптер `for = ["review-discussion"]`, `harness = "codex"`.
Контракт объявлен общим модулем в `review-discussion/data/delivery-contract.md`.
Адаптер экспортирует `Adapter(env).send(target,payload) -> queue ID`.

Исполняемый файл выбирается через `REVIEW_CODEX_EXECUTABLE` или текущий PATH.
Вызов — argv `codex queue --thread UUID --message TEXT`, без shell, resume,
fork и запуска нового чата. `CODEX_HOME` наследуется; `REVIEW_TOKEN` удаляется
из окружения queue-процесса. Вход stdin закрыт, timeout 10 секунд, UTF-8.

Успех принимается только при exit 0 и строке receipt с тем же UUID.
stdout/stderr при ошибке не выводятся: в них может оказаться пользовательский
текст или credential. Неопределённый исход timeout — ошибка доставки,
а не доказательство, что queue не был принят. Это часть окна at least once.

Private API Desktop, его UI и отдельный app-server daemon адаптер не использует.
Обычный CLI может воспользоваться встроенным сервером при отсутствии shared
socket; принятие queue не доказывает, что закрытая или unloaded сессия начнёт
ответ. Runtime нужного чата должен быть жив и читать ту же queue DB.
