from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

LANGUAGES = {'ru': '🇷🇺 Русский', 'kk': '🇰🇿 Қазақша', 'en': '🇬🇧 English'}
LANG_NAMES = {'ru': 'русском', 'kk': 'казахском', 'en': 'английском'}
CONSENT_RU = 'Я являюсь владельцем этого голоса и даю согласие Google на использование этого голоса для создания модели синтетического голоса.'
PREBUILT = dict([
    ('Kore', 'Уверенный'), ('Puck', 'Живой'), ('Zephyr', 'Яркий'), ('Charon', 'Информативный'),
    ('Fenrir', 'Энергичный'), ('Leda', 'Молодой'), ('Orus', 'Твёрдый'), ('Aoede', 'Лёгкий'),
    ('Callirrhoe', 'Непринуждённый'), ('Autonoe', 'Яркий'), ('Enceladus', 'Придыхание'),
    ('Iapetus', 'Чёткий'), ('Umbriel', 'Спокойный'), ('Algieba', 'Плавный'),
    ('Despina', 'Плавный'), ('Erinome', 'Ясный'), ('Algenib', 'Хрипловатый'),
    ('Rasalgethi', 'Информативный'), ('Laomedeia', 'Бодрый'), ('Achernar', 'Мягкий'),
    ('Alnilam', 'Уверенный'), ('Schedar', 'Ровный'), ('Gacrux', 'Зрелый'),
    ('Pulcherrima', 'Выразительный'), ('Achird', 'Дружелюбный'),
    ('Zubenelgenubi', 'Разговорный'), ('Vindemiatrix', 'Нежный'),
    ('Sadachbia', 'Оживлённый'), ('Sadaltager', 'Рассудительный'), ('Sulafat', 'Тёплый')])
MENU = ['🎙 Добавить голос', '👥 Мои голоса', '🔊 Озвучить текст',
        '🌐 Текст на 3 языках', '🌍 Перевести и озвучить', '💬 Поговорить с ИИ',
        '🎭 Каталог голосов', '🎧 Известные люди: записи',
        '⚙️ Настройки', '❓ Помощь', '❌ Отмена']


def menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in MENU[i:i+2]]
                                         for i in range(0, len(MENU), 2)], resize_keyboard=True)


def inline(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=value) for label, value in row] for row in rows])


def languages(prefix):
    return inline([[(label, f'{prefix}:{code}')] for code, label in LANGUAGES.items()])


def multilingual(nonce):
    rows = [[(LANGUAGES[code], f'multi_choose:{nonce}:{code}')] for code in ('kk', 'ru', 'en')]
    rows.append([('🌐 Все три языка', f'multi_choose:{nonce}:all')])
    rows.append([('❌ Отмена', 'cancel')])
    return inline(rows)


def catalog(page=0):
    page = max(0, min(page, (len(PREBUILT) - 1) // 6))
    items = list(PREBUILT.items())[page * 6:page * 6 + 6]
    rows = [[(f'{name} · {description}', f'voice:b:{name}')] for name, description in items]
    rows.append([('◀', f'catalog:{max(0, page-1)}'),
                 (f'{page+1}/5', 'noop'), ('▶', f'catalog:{min(4, page+1)}')])
    return inline(rows)


def voice_actions(key, custom=False):
    rows = [[('✅ Выбрать', f'select:{key}'), ('▶ Послушать', f'preview:{key}')]]
    if custom:
        rows.append([('🗑 Удалить', f'delete:{key}')])
    return inline(rows)
