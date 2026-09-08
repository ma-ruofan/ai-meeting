"""SQLite is the source of truth. Every visible mutation also advances a durable event sequence."""

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def uid():
    return uuid.uuid4().hex


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meetings(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
 created REAL NOT NULL, invite_expires REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ready',
 revision INTEGER NOT NULL DEFAULT 0, seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS members(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 name TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS utterances(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 text TEXT NOT NULL, speaker TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
 source TEXT NOT NULL, match_json TEXT, version INTEGER NOT NULL DEFAULT 1,
 request_key TEXT NOT NULL, created REAL NOT NULL, UNIQUE(meeting_id,request_key));
CREATE TABLE IF NOT EXISTS answers(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 member_id TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
 citations TEXT NOT NULL, trace TEXT NOT NULL, revision INTEGER NOT NULL,
 mode TEXT NOT NULL, status TEXT NOT NULL, spoken INTEGER NOT NULL,
 request_key TEXT NOT NULL, created REAL NOT NULL, UNIQUE(meeting_id,request_key));
CREATE TABLE IF NOT EXISTS decisions(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 answer_id TEXT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
 text TEXT NOT NULL, kind TEXT NOT NULL, confirmed_by TEXT NOT NULL, created REAL NOT NULL,
 UNIQUE(answer_id));
CREATE TABLE IF NOT EXISTS summaries(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 data TEXT NOT NULL, revision INTEGER NOT NULL, mode TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS voiceprints(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 member_id TEXT NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
 name TEXT NOT NULL, embedding TEXT NOT NULL, model TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 status TEXT NOT NULL, filename TEXT NOT NULL, request_key TEXT NOT NULL,
 error TEXT, created REAL NOT NULL, UNIQUE(meeting_id,request_key));
CREATE TABLE IF NOT EXISTS events(
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, type TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(meeting_id,seq));
CREATE INDEX IF NOT EXISTS transcript_meeting ON utterances(meeting_id,start);
"""


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / "meetings.sqlite3"
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute(
                "UPDATE jobs SET status='failed',error='服务重启，音频已保留，可重试' WHERE status IN ('queued','running')"
            )
            db.execute("UPDATE meetings SET status='ready' WHERE status='recording'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def event(db, meeting_id, kind, revision=False):
        db.execute(
            "UPDATE meetings SET seq=seq+1,revision=revision+? WHERE id=?", (int(revision), meeting_id)
        )
        db.execute(
            "INSERT INTO events SELECT id,seq,?,? FROM meetings WHERE id=?", (kind, time.time(), meeting_id)
        )

    def create(self, title, name):
        mid, member, token = uid(), uid(), secrets.token_urlsafe(32)
        code = secrets.token_hex(4).upper()
        with self.connect() as db:
            db.execute(
                "INSERT INTO meetings(id,title,code,created,invite_expires) VALUES(?,?,?,?,?)",
                (mid, title, code, time.time(), time.time() + 86400),
            )
            db.execute(
                "INSERT INTO members VALUES(?,?,?,?,?,?)",
                (member, mid, name, "host", digest(token), time.time()),
            )
            self.event(db, mid, "meeting.created")
        return {"meeting_id": mid, "token": token, "member_id": member, "role": "host", "title": title}

    def join(self, code, name):
        with self.connect() as db:
            meeting = db.execute(
                "SELECT * FROM meetings WHERE code=? AND invite_expires>? AND status!='ended'",
                (code.upper(), time.time()),
            ).fetchone()
            if not meeting:
                raise ValueError("会议码无效、已结束或邀请已过期")
            if (
                db.execute("SELECT count(*) FROM members WHERE meeting_id=?", (meeting["id"],)).fetchone()[0]
                >= 12
            ):
                raise ValueError("个人版每场最多 12 位参会者")
            token, member = secrets.token_urlsafe(32), uid()
            db.execute(
                "INSERT INTO members VALUES(?,?,?,?,?,?)",
                (member, meeting["id"], name, "guest", digest(token), time.time()),
            )
            self.event(db, meeting["id"], "member.joined")
        return {
            "meeting_id": meeting["id"],
            "token": token,
            "member_id": member,
            "role": "guest",
            "title": meeting["title"],
        }

    def add_attendee(self, mid, name):
        # No login credential is issued for people attending without a device.
        ident = uid()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            meeting = db.execute("SELECT status FROM meetings WHERE id=?", (mid,)).fetchone()
            if not meeting or meeting["status"] == "ended":
                raise ValueError("会议已结束或不存在")
            if db.execute("SELECT count(*) FROM members WHERE meeting_id=?", (mid,)).fetchone()[0] >= 12:
                raise ValueError("个人版每场最多 12 位参会者")
            if db.execute("SELECT id FROM members WHERE meeting_id=? AND name=?", (mid, name)).fetchone():
                raise ValueError("已有同名参会者，请添加称呼以便区分")
            db.execute(
                "INSERT INTO members VALUES(?,?,?,?,?,?)",
                (ident, mid, name, "attendee", digest(secrets.token_urlsafe(32)), time.time()),
            )
            self.event(db, mid, "member.added_by_host")
        return {"id": ident, "name": name, "role": "attendee"}

    def attendee(self, mid, ident):
        with self.connect() as db:
            row = db.execute(
                "SELECT id,name,role FROM members WHERE meeting_id=? AND id=? AND role='attendee'",
                (mid, ident),
            ).fetchone()
            return dict(row) if row else None

    def auth(self, mid, token):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM members WHERE meeting_id=? AND token_hash=?", (mid, digest(token))
            ).fetchone()
            return dict(row) if row else None

    def snapshot(self, mid):
        with self.connect() as db:
            # A read transaction keeps transcript, revision and event cursor coherent.
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
            if not row:
                raise ValueError("会议已删除")
            result = {"meeting": dict(row)}
            for table in ("members", "utterances", "answers", "decisions", "summaries", "jobs"):
                columns = "id,name,role,created" if table == "members" else "*"
                order = "start,created" if table == "utterances" else "created"
                result[table] = [
                    dict(x)
                    for x in db.execute(
                        f"SELECT {columns} FROM {table} WHERE meeting_id=? ORDER BY {order}", (mid,)
                    )
                ]
            result["voiceprints"] = [
                dict(x)
                for x in db.execute(
                    "SELECT id,member_id,name,enabled,created FROM voiceprints WHERE meeting_id=?", (mid,)
                )
            ]
        for item in result["answers"]:
            item["spoken"] = bool(item["spoken"])
            for field in ("citations", "trace"):
                item[field] = json.loads(item[field])
            item["stale"] = item["revision"] != result["meeting"]["revision"]
        for item in result["summaries"]:
            item["data"] = json.loads(item["data"])
            item["stale"] = item["revision"] != result["meeting"]["revision"]
        for item in result["utterances"]:
            item["match"] = json.loads(item.pop("match_json") or "null")
        for item in result["jobs"]:
            item.pop("filename", None)
        return result

    def add_utterance(self, mid, text, speaker, start, end, source, key, match=None):
        with self.connect() as db:
            old = db.execute(
                "SELECT id FROM utterances WHERE meeting_id=? AND request_key=?", (mid, key)
            ).fetchone()
            if old:
                return old[0]
            ident = uid()
            db.execute(
                "INSERT INTO utterances VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ident, mid, text, speaker, start, end, source, encode(match), 1, key, time.time()),
            )
            self.event(db, mid, "utterance.added", revision=True)
            return ident

    def edit(self, mid, ident, text, speaker, version):
        with self.connect() as db:
            count = db.execute(
                "UPDATE utterances SET text=?,speaker=?,version=version+1,source='edited' WHERE id=? AND meeting_id=? AND version=?",
                (text, speaker, ident, mid, version),
            ).rowcount
            if not count:
                raise ValueError("记录已被修改，请刷新后重试")
            self.event(db, mid, "utterance.edited", revision=True)

    def set_status(self, mid, status):
        with self.connect() as db:
            db.execute("UPDATE meetings SET status=? WHERE id=?", (status, mid))
            self.event(db, mid, "meeting.status")

    def save_answer(self, mid, member, question, result, revision, mode, spoken, key):
        with self.connect() as db:
            old = db.execute(
                "SELECT id FROM answers WHERE meeting_id=? AND request_key=?", (mid, key)
            ).fetchone()
            if old:
                return old[0]
            ident = uid()
            db.execute(
                "INSERT INTO answers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    mid,
                    member,
                    question,
                    result["answer"],
                    encode(result["citations"]),
                    encode(result["trace"]),
                    revision,
                    mode,
                    result["status"],
                    int(spoken),
                    key,
                    time.time(),
                ),
            )
            self.event(db, mid, "answer.added")
            return ident

    def confirm(self, mid, answer_id, member, text, kind):
        with self.connect() as db:
            answer = db.execute(
                "SELECT * FROM answers WHERE id=? AND meeting_id=?", (answer_id, mid)
            ).fetchone()
            if not answer:
                raise ValueError("回答不存在")
            rev = db.execute("SELECT revision FROM meetings WHERE id=?", (mid,)).fetchone()[0]
            if answer["revision"] != rev:
                raise ValueError("回答依据已变化，请重新提问后采纳")
            db.execute(
                "INSERT OR IGNORE INTO decisions VALUES(?,?,?,?,?,?,?)",
                (uid(), mid, answer_id, text, kind, member, time.time()),
            )
            self.event(db, mid, "answer.confirmed")

    def save_summary(self, mid, data, revision, mode):
        with self.connect() as db:
            db.execute(
                "INSERT INTO summaries VALUES(?,?,?,?,?,?)",
                (uid(), mid, encode(data), revision, mode, time.time()),
            )
            self.event(db, mid, "summary.added")

    def enroll(self, mid, member, name, vector, model):
        with self.connect() as db:
            db.execute("DELETE FROM voiceprints WHERE member_id=? AND meeting_id=?", (member, mid))
            db.execute(
                "INSERT INTO voiceprints VALUES(?,?,?,?,?,?,?,?)",
                (uid(), mid, member, name, encode(vector), model, 1, time.time()),
            )
            self.event(db, mid, "voiceprint.enrolled")

    def voice_candidates(self, mid):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM voiceprints WHERE meeting_id=? AND enabled=1", (mid,))
            ]

    def voice_change(self, mid, member, enabled=None):
        with self.connect() as db:
            if enabled is None:
                db.execute("DELETE FROM voiceprints WHERE meeting_id=? AND member_id=?", (mid, member))
            else:
                db.execute(
                    "UPDATE voiceprints SET enabled=? WHERE meeting_id=? AND member_id=?",
                    (int(enabled), mid, member),
                )
            self.event(db, mid, "voiceprint.changed")

    def add_job(self, mid, filename, key):
        with self.connect() as db:
            old = db.execute(
                "SELECT id FROM jobs WHERE meeting_id=? AND request_key=?", (mid, key)
            ).fetchone()
            if old:
                return old[0], False
            ident = uid()
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                (ident, mid, "queued", filename, key, None, time.time()),
            )
            self.event(db, mid, "job.queued")
            return ident, True

    def job(self, mid, ident):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND meeting_id=?", (ident, mid)).fetchone()
            return dict(row) if row else None

    def update_job(self, mid, ident, status, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,error=? WHERE id=? AND meeting_id=?", (status, error, ident, mid)
            )
            self.event(db, mid, "job.updated")

    def delete(self, mid):
        with self.connect() as db:
            db.execute("DELETE FROM meetings WHERE id=?", (mid,))
