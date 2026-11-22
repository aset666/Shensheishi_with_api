"""SQLite: user isolation, durable workflows and atomic request limits."""
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'ru',
          selected_voice TEXT NOT NULL DEFAULT 'b:Kore');
        CREATE TABLE IF NOT EXISTS voices(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          remote_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          sample_language TEXT NOT NULL, created REAL NOT NULL,
          consent_language TEXT NOT NULL DEFAULT 'ru', expires TEXT);
        CREATE TABLE IF NOT EXISTS sessions(
          user_id INTEGER PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL,
          updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          role TEXT NOT NULL, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS usage(
          day TEXT NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
          PRIMARY KEY(day,user_id));
        CREATE TABLE IF NOT EXISTS previews(
          voice_key TEXT NOT NULL, language TEXT NOT NULL, file_id TEXT NOT NULL,
          PRIMARY KEY(voice_key,language));
        ''')

    def close(self):
        self.db.close()

    def user(self, uid):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO users(id) VALUES(?)', (uid,))
        return dict(self.db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())

    def language(self, uid, lang):
        if lang not in {'ru', 'en', 'kk'}:
            raise ValueError('Неподдерживаемый язык.')
        self.user(uid)
        with self.db:
            self.db.execute('UPDATE users SET language=? WHERE id=?', (lang, uid))

    def select(self, uid, key):
        self.user(uid)
        with self.db:
            self.db.execute('UPDATE users SET selected_voice=? WHERE id=?', (key, uid))

    def session(self, uid):
        row = self.db.execute('SELECT * FROM sessions WHERE user_id=?', (uid,)).fetchone()
        if not row:
            return '', {}
        return row['state'], json.loads(row['data'])

    def set_session(self, uid, state, data=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)',
                            (uid, state, json.dumps(data or {}, ensure_ascii=False), time.time()))

    def clear_session(self, uid):
        with self.db:
            self.db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))

    def expire_sessions(self, cutoff):
        rows = self.db.execute('SELECT user_id FROM sessions WHERE updated<?', (cutoff,)).fetchall()
        with self.db:
            self.db.execute('DELETE FROM sessions WHERE updated<?', (cutoff,))
        return [r['user_id'] for r in rows]

    def add_voice(self, uid, remote, name, lang, expires=None):
        with self.db:
            cur = self.db.execute('''INSERT INTO voices
                (user_id,remote_id,name,sample_language,created,expires) VALUES(?,?,?,?,?,?)''',
                (uid, remote, name, lang, time.time(), expires))
        return cur.lastrowid

    def voices(self, uid):
        return [dict(r) for r in self.db.execute('SELECT * FROM voices WHERE user_id=? ORDER BY id DESC', (uid,))]

    def voice(self, uid, vid):
        row = self.db.execute('SELECT * FROM voices WHERE id=? AND user_id=?', (vid, uid)).fetchone()
        return dict(row) if row else None

    def delete_voice(self, uid, vid):
        row = self.voice(uid, vid)
        if not row:
            return False
        with self.db:
            self.db.execute('DELETE FROM voices WHERE id=? AND user_id=?', (vid, uid))
            self.db.execute('DELETE FROM previews WHERE voice_key=?', (f'v:{vid}',))
            self.db.execute("UPDATE users SET selected_voice='b:Kore' WHERE id=? AND selected_voice=?",
                            (uid, f'v:{vid}'))
        return True

    def reserve(self, uid, user_limit, total_limit):
        day = datetime.now(timezone.utc).date().isoformat()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            own = self.db.execute('SELECT count FROM usage WHERE day=? AND user_id=?', (day, uid)).fetchone()
            total = self.db.execute('SELECT COALESCE(SUM(count),0) FROM usage WHERE day=?', (day,)).fetchone()[0]
            if (own and own[0] >= user_limit) or total >= total_limit:
                self.db.rollback()
                return False
            self.db.execute('''INSERT INTO usage VALUES(?,?,1)
              ON CONFLICT(day,user_id) DO UPDATE SET count=count+1''', (day, uid))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def usage(self, uid):
        day = datetime.now(timezone.utc).date().isoformat()
        row = self.db.execute('SELECT count FROM usage WHERE day=? AND user_id=?', (day, uid)).fetchone()
        return row[0] if row else 0

    def history(self, uid):
        rows = self.db.execute('SELECT role,text FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12', (uid,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_turn(self, uid, prompt, answer):
        with self.db:
            self.db.executemany('INSERT INTO history(user_id,role,text) VALUES(?,?,?)',
                                [(uid, 'user', prompt), (uid, 'model', answer)])
            self.db.execute('''DELETE FROM history WHERE user_id=? AND id NOT IN
              (SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12)''', (uid, uid))

    def clear_history(self, uid):
        with self.db:
            self.db.execute('DELETE FROM history WHERE user_id=?', (uid,))

    def preview(self, key, lang):
        row = self.db.execute('SELECT file_id FROM previews WHERE voice_key=? AND language=?', (key, lang)).fetchone()
        return row[0] if row else None

    def cache_preview(self, key, lang, fid):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO previews VALUES(?,?,?)', (key, lang, fid))
