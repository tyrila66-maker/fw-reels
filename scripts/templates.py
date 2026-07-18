# -*- coding: utf-8 -*-
"""
Шаблоны сценариев без LLM — подстановка города и цен в выверенный каркас.
Фразы на LT/PL/RU/EN написаны так, чтобы TTS звучал чисто (полные предложения,
однозначный языковой контекст). Числа передаются как есть — на экране в титрах
они точные, а озвучка читает их в контексте языка.
"""


def _hook_numbers(frm, to, lang):
    # первая реплика: сильный контраст цен в первые 2 секунды
    return {
        "lithuanian": f"{frm} eurų. O dabar staiga tik {to}. Pažiūrėk į tai.",
        "polish": f"{frm} euro. A teraz nagle tylko {to}. Zobacz to.",
        "russian": f"{frm} евро. А теперь вдруг всего {to}. Смотри.",
        "english": f"{frm} euros. And now suddenly just {to}. Look at this.",
    }[lang]


PRICE_DROP = {
    "lithuanian": [
        None,
        "Skrydžio į {city} kaina kelias dienas tik kilo ir kilo.",
        "Ji atrodė per brangi, ir daugelis tiesiog pasidavė bei nusuko akis.",
        "Bet Flight Price Watch tikrina kainas visą parą ir pagavo tinkamą momentą.",
        "Kaina nukrito iki {to} eurų. Tai reali, gyva šios dienos kaina.",
        "Štai kiek sutaupai vien todėl, kad sekei kainą reikiamu metu.",
        "Tokios kainos ilgai neišsilaiko, todėl svarbu pagauti tą akimirką.",
        "Nori sutaupyti lygiai taip pat? Sek kainas nemokamai. Nuoroda aprašyme.",
    ],
    "polish": [
        None,
        "Cena lotu do {city} przez kilka dni tylko rosła i rosła.",
        "Wyglądała na zbyt wysoką, więc wielu po prostu się poddało.",
        "Ale Flight Price Watch sprawdza ceny całą dobę i złapał właściwy moment.",
        "Cena spadła do {to} euro. To prawdziwa, żywa cena na dziś.",
        "Tyle oszczędzasz tylko dlatego, że śledziłeś cenę w odpowiednim czasie.",
        "Takie ceny długo się nie utrzymują, więc trzeba złapać tę chwilę.",
        "Chcesz zaoszczędzić tak samo? Śledź ceny za darmo. Link w opisie.",
    ],
    "russian": [
        None,
        "Цена на перелёт в {city} несколько дней только росла и росла.",
        "Она казалась слишком дорогой, и многие просто сдавались.",
        "Но Flight Price Watch проверяет цены круглосуточно и поймал нужный момент.",
        "Цена упала до {to} евро. Это реальная, живая цена на сегодня.",
        "Вот сколько экономишь только потому, что следил за ценой вовремя.",
        "Такие цены долго не держатся, поэтому важно поймать момент.",
        "Хочешь сэкономить так же? Следи за ценами бесплатно. Ссылка в описании.",
    ],
    "english": [
        None,
        "The price of a flight to {city} kept climbing for days.",
        "It looked way too expensive, so most people simply gave up.",
        "But Flight Price Watch tracks prices all day and caught the right moment.",
        "It dropped to {to} euros. That is a real, live price for today.",
        "That is how much you save just by watching the price at the right time.",
        "Prices like this never last long, so you have to catch the moment.",
        "Want to save the same way? Track prices for free. Link in the description.",
    ],
}

_VISUALS = [
    "dramatic falling price chart red arrow, numbers dropping fast",
    "{city} famous landmark cinematic, travel destination",
    "person looking at phone disappointed at high price, airport",
    "real Flight Price Watch tracker page screen recording",
    "happy traveler celebrating on phone, relief and joy",
    "{city} beautiful streets and views, sunny travel vibe",
    "airport departure board and planes, sense of urgency",
    "traveler with backpack walking to gate at golden hour",
]

_CAPTION = {
    "lithuanian": "Pigūs skrydžiai iš Vilniaus: {city} ką tik atpigo nuo {frm} iki {to} € 📉\n\n"
                  "Kaina kelias dienas kilo, o Flight Price Watch pagavo momentą, kai ji krito. "
                  "Sek kainas nemokamai: https://flight-watch.onrender.com\n\n"
                  "Persiųsk draugui, su kuriuo skristum į {city} 😉\n"
                  "#pigusskrydziai #{tag} #kelionės #vilnius",
    "polish": "Tanie loty z Warszawy: {city} właśnie potaniało z {frm} do {to} € 📉\n\n"
              "Cena rosła kilka dni, a Flight Price Watch złapał moment spadku. "
              "Śledź ceny za darmo: https://flight-watch.onrender.com\n\n"
              "Wyślij znajomemu, z którym poleciałbyś do {city} 😉\n"
              "#tanieloty #{tag} #podróże #warszawa",
    "russian": "Дешёвые билеты: {city} только что подешевел с {frm} до {to} € 📉\n\n"
               "Цена росла несколько дней, а Flight Price Watch поймал момент падения. "
               "Следи за ценами бесплатно: https://flight-watch.onrender.com\n\n"
               "Перешли другу, с кем полетел бы в {city} 😉\n"
               "#дешевыебилеты #{tag} #путешествия #вильнюс",
    "english": "Cheap flights from Vilnius: {city} just dropped from €{frm} to €{to} 📉\n\n"
               "The price climbed for days, then Flight Price Watch caught the drop. "
               "Track prices free: https://flight-watch.onrender.com\n\n"
               "Send this to the friend you'd fly to {city} with 😉\n"
               "#cheapflights #{tag} #travel #flightdeals",
}


def build_price_drop(city: str, frm, to, lang: str, sourceclip: str = "tracker-live.mp4"):
    """Возвращает (script_dict, caption). lang: lithuanian|polish|russian|english."""
    lang = lang.lower()
    if lang not in PRICE_DROP:
        aliases = {"lt": "lithuanian", "pl": "polish", "ru": "russian", "en": "english"}
        lang = aliases.get(lang, "lithuanian")
    lines_txt = list(PRICE_DROP[lang])
    lines_txt[0] = _hook_numbers(frm, to, lang)
    lines = []
    for i, txt in enumerate(lines_txt):
        line = {
            "index": i,
            "section": ["hook", "problem", "scene", "solution", "scene", "scene", "scene", "cta"][i],
            "text": txt.format(city=city, frm=frm, to=to),
            "visualPrompt": _VISUALS[i].format(city=city),
        }
        if i == 3:
            line["sourceClip"] = sourceclip
            line["clipStartSec"] = 2.0
        lines.append(line)
    script = {"topic": f"Live price drop to {city} ({lang})", "language": lang, "lines": lines}
    tag = "pigusskrydziai" if lang == "lithuanian" else "traveldeals"
    caption = _CAPTION[lang].format(city=city, frm=frm, to=to, tag=tag)
    return script, caption
