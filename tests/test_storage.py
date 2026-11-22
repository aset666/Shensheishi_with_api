from app.storage import Store
from app.bot import VoiceBot, InputError
from app.config import Config
import pytest


def test_persistence_and_owner_isolation(tmp_path):
    path = tmp_path / 'db.sqlite3'
    db = Store(path)
    first = db.add_voice(1, 'voice_first', 'First', 'kk')
    second = db.add_voice(2, 'voice_second', 'Second', 'ru')
    db.select(1, f'v:{first}')
    db.language(1, 'en')
    db.set_session(1, 'clone_consent', {'name': 'Новый', 'language': 'kk'})
    db.close()
    db = Store(path)
    assert db.user(1)['language'] == 'en'
    assert db.session(1)[1]['name'] == 'Новый'
    assert db.voice(2, first) is None
    assert db.delete_voice(2, first) is False
    assert db.voice(1, first) is not None
    assert db.delete_voice(1, first) is True
    assert db.user(1)['selected_voice'] == 'b:Kore'
    assert db.voice(2, second) is not None
    db.close()


def test_daily_limits_and_history_bound(tmp_path):
    db = Store(tmp_path / 'db')
    assert db.reserve(1, 2, 3)
    assert db.reserve(1, 2, 3)
    assert not db.reserve(1, 2, 3)
    assert db.reserve(2, 2, 3)
    assert not db.reserve(3, 2, 3)
    assert db.usage(1) == 2
    for i in range(20):
        db.add_turn(1, f'q{i}', f'a{i}')
    assert len(db.history(1)) == 12
    assert db.history(1)[0]['text'] == 'q14'
    assert db.history(2) == []
    db.clear_history(1)
    assert db.history(1) == []
    db.close()
