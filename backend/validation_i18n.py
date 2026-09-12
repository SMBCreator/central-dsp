"""Русские сообщения для ошибок валидации Pydantic/FastAPI (audit F-01 / C6).

По умолчанию FastAPI отдаёт 422 с английскими msg («Field required», «String
should have at least 3 characters») и полем `input` — эхом присланных данных
(в т.ч. пароля при ошибке формы логина). Обработчик:

* переводит msg по `type` ошибки, подставляя параметры из `ctx`;
* сохраняет форму `{"detail": [{"loc", "msg", "type"}]}`, на которую опирается
  frontend (lib/api.ts extractErrorDetail собирает «поле: сообщение»);
* не возвращает `input`, `ctx`, `url`.

Неизвестные типы оставляют исходный msg — лучше английское сообщение, чем потеря
информации.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_BY_TYPE: dict[str, str] = {
    "missing": "Обязательное поле",
    "extra_forbidden": "Неизвестное поле",
    "string_type": "Ожидается строка",
    "string_too_short": "Минимум {min_length} символов",
    "string_too_long": "Максимум {max_length} символов",
    "string_pattern_mismatch": "Недопустимый формат",
    "int_type": "Введите целое число",
    "int_parsing": "Введите целое число",
    "int_from_float": "Введите целое число",
    "float_type": "Введите число",
    "float_parsing": "Введите число",
    "decimal_parsing": "Введите число",
    "decimal_type": "Введите число",
    "bool_type": "Ожидается значение да/нет",
    "bool_parsing": "Ожидается значение да/нет",
    "list_type": "Ожидается список",
    "dict_type": "Ожидается объект",
    "model_type": "Ожидается объект",
    "model_attributes_type": "Ожидается объект",
    "too_short": "Минимум {min_length} элементов",
    "too_long": "Максимум {max_length} элементов",
    "greater_than": "Значение должно быть больше {gt}",
    "greater_than_equal": "Значение должно быть не меньше {ge}",
    "less_than": "Значение должно быть меньше {lt}",
    "less_than_equal": "Значение должно быть не больше {le}",
    "multiple_of": "Значение должно быть кратно {multiple_of}",
    "finite_number": "Введите конечное число",
    "enum": "Недопустимое значение. Допустимые: {expected}",
    "literal_error": "Недопустимое значение. Допустимые: {expected}",
    "uuid_type": "Некорректный идентификатор",
    "uuid_parsing": "Некорректный идентификатор",
    "date_parsing": "Некорректная дата",
    "date_type": "Некорректная дата",
    "date_from_datetime_parsing": "Некорректная дата",
    "datetime_parsing": "Некорректная дата/время",
    "datetime_type": "Некорректная дата/время",
    "datetime_from_date_parsing": "Некорректная дата/время",
    "time_parsing": "Некорректное время",
    "url_parsing": "Некорректный URL",
    "url_type": "Некорректный URL",
    "url_scheme": "Недопустимая схема URL",
    "json_invalid": "Некорректный JSON",
    "json_type": "Некорректный JSON",
}

_VALUE_ERROR_PREFIX = "Value error, "
_ASSERTION_PREFIX = "Assertion failed, "


def localize_message(err: dict[str, Any]) -> str:
    etype = str(err.get("type") or "")
    msg = str(err.get("msg") or "")
    ctx = err.get("ctx") or {}
    if etype == "value_error":
        # Пользовательские валидаторы уже пишут по-русски; снимаем технический префикс.
        body = msg[len(_VALUE_ERROR_PREFIX):] if msg.startswith(_VALUE_ERROR_PREFIX) else msg
        if "email address" in body.lower():
            return "Некорректный email"
        return body or "Некорректное значение"
    if etype == "assertion_error":
        return msg[len(_ASSERTION_PREFIX):] if msg.startswith(_ASSERTION_PREFIX) else msg
    template = _BY_TYPE.get(etype)
    if not template:
        return msg or "Некорректное значение"
    try:
        # ctx у pydantic содержит python-объекты (Decimal, list) — форматируем строкой.
        return template.format(**{k: _fmt(v) for k, v in ctx.items()})
    except (KeyError, IndexError):
        return msg or template


def _fmt(v: Any) -> str:
    if isinstance(v, (list, tuple, set, frozenset)):
        return ", ".join(str(x) for x in v)
    return str(v)


def localize_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"loc": list(e.get("loc") or []), "msg": localize_message(e), "type": e.get("type")}
        for e in errors
    ]


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": localize_errors(list(exc.errors()))})
