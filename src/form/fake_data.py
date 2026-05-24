"""Генерация фейковой персоны для подачи заявки в МФО."""
from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import date, timedelta

from faker import Faker

fake_ru = Faker("ru_RU")


@dataclass
class Persona:
    last_name: str
    first_name: str
    middle_name: str
    birth_date: date
    passport_series: str          # 4 цифры
    passport_number: str          # 6 цифр
    passport_issued: date
    passport_issued_by: str
    division_code: str            # 000-000
    address: str
    email: str
    phone: str = ""               # подставится из SmsProvider
    inn: str = ""                 # 12 цифр

    def as_dict(self) -> dict[str, str]:
        return {
            "last_name": self.last_name,
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "fio": f"{self.last_name} {self.first_name} {self.middle_name}",
            "birth_date": self.birth_date.strftime("%d.%m.%Y"),
            "passport_series": self.passport_series,
            "passport_number": self.passport_number,
            "passport_full": f"{self.passport_series} {self.passport_number}",
            "passport_issued": self.passport_issued.strftime("%d.%m.%Y"),
            "passport_issued_by": self.passport_issued_by,
            "division_code": self.division_code,
            "address": self.address,
            "email": self.email,
            "phone": self.phone,
            "inn": self.inn,
        }


def _gender_choice() -> str:
    return random.choice(["male", "female"])


def generate_persona(email_domain: str = "mail.ru") -> Persona:
    g = _gender_choice()
    if g == "male":
        last = fake_ru.last_name_male()
        first = fake_ru.first_name_male()
        middle = fake_ru.middle_name_male()
    else:
        last = fake_ru.last_name_female()
        first = fake_ru.first_name_female()
        middle = fake_ru.middle_name_female()

    today = date.today()
    age_years = random.randint(22, 55)
    birth = today - timedelta(days=age_years * 365 + random.randint(0, 364))

    passport_issued = birth + timedelta(days=random.randint(20 * 365, max(20 * 365 + 1, (today - birth).days - 30)))

    series = "".join(random.choices(string.digits, k=4))
    number = "".join(random.choices(string.digits, k=6))
    division = f"{random.randint(100, 999)}-{random.randint(100, 999)}"

    address = fake_ru.address().replace("\n", ", ")
    issued_by = f"ОВД {fake_ru.city()} района г. {fake_ru.city_name()}"

    translit_last = _translit(last)
    translit_first = _translit(first)
    email = f"{translit_first}.{translit_last}{random.randint(1, 999)}@{email_domain}".lower()

    inn = "".join(random.choices(string.digits, k=12))

    return Persona(
        last_name=last,
        first_name=first,
        middle_name=middle,
        birth_date=birth,
        passport_series=series,
        passport_number=number,
        passport_issued=passport_issued,
        passport_issued_by=issued_by,
        division_code=division,
        address=address,
        email=email,
        inn=inn,
    )


_TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(s: str) -> str:
    out = []
    for ch in s.lower():
        out.append(_TRANSLIT_MAP.get(ch, ch))
    return "".join(out)
