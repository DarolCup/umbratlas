"""Roadmap validation, prerequisites, and local progress."""

from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

REVIEWS = (1, 3, 7, 14, 30)


def now():
    return datetime.now(timezone.utc)


def clean_text(value, limit=3500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Модель вернула пустой или слишком длинный текст.")
    return value.strip()


def web_url(value):
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
    except (TypeError, ValueError, AttributeError):
        return False


def validate_roadmap(raw):
    try:
        title = clean_text(raw["title"], 140)
        summary = clean_text(raw["summary"], 1500)
        topics = raw["topics"]
        if not isinstance(topics, list) or not 1 <= len(topics) <= 30:
            raise ValueError("Карта должна содержать от 1 до 30 тем.")
        result = []
        ids = set()
        for topic in topics:
            identifier = topic["id"]
            if type(identifier) is not int or identifier < 1 or identifier in ids:
                raise ValueError("Идентификаторы тем должны быть уникальными положительными числами.")
            ids.add(identifier)
            prerequisites = topic["prerequisites"]
            if not isinstance(prerequisites, list) or any(type(p) is not int for p in prerequisites):
                raise ValueError("Связи между темами должны ссылаться на идентификаторы тем.")
            result.append({
                "id": identifier,
                "prerequisites": list(dict.fromkeys(prerequisites)),
                "title": clean_text(topic["title"], 140),
                "lesson": clean_text(topic["lesson"]),
                "practice": clean_text(topic["practice"]),
                "criteria": clean_text(topic["criteria"]),
                "cheatsheet": clean_text(topic["cheatsheet"]),
                "recall": clean_text(topic["recall"], 1200),
            })
        if any(set(t["prerequisites"]) - ids for t in result):
            raise ValueError("В карте есть ссылка на отсутствующую тему.")
        roadmap = {"title": title, "summary": summary, "topics": result}
        stages(roadmap)
        return roadmap
    except (KeyError, TypeError) as error:
        raise ValueError("Модель вернула неполную карту.") from error


def stages(roadmap):
    """Group topics by the depth of their longest prerequisite chain."""
    topics = {topic["id"]: topic for topic in roadmap["topics"]}
    depths = {}
    visiting = set()

    def depth(identifier):
        if identifier in visiting:
            raise ValueError("Зависимости между темами образуют цикл.")
        if identifier not in depths:
            visiting.add(identifier)
            depths[identifier] = 1 + max(
                (depth(p) for p in topics[identifier]["prerequisites"]), default=-1
            )
            visiting.remove(identifier)
        return depths[identifier]

    groups = defaultdict(list)
    for identifier, topic in topics.items():
        groups[depth(identifier)].append(topic)
    return [groups[index] for index in sorted(groups)]


def available(topic, progress):
    return all(progress.get(p, {}).get("completed_at") for p in topic["prerequisites"])


def pending_reviews(roadmap, progress, moment=None):
    moment = (moment or now()).isoformat()
    return sorted(
        (
            topic for topic in roadmap["topics"]
            if (due := progress.get(topic["id"], {}).get("review_at")) and due <= moment
        ),
        key=lambda topic: progress[topic["id"]]["review_at"],
    )


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS roadmaps (
                    id INTEGER PRIMARY KEY, body TEXT NOT NULL, sample TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS interview_draft (
                    id INTEGER PRIMARY KEY CHECK(id = 1), body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topic_state (
                    roadmap_id INTEGER NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
                    topic_id INTEGER NOT NULL,
                    completed_at TEXT, review_at TEXT, review_step INTEGER NOT NULL DEFAULT 0,
                    feedback TEXT NOT NULL DEFAULT '', resources TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (roadmap_id, topic_id)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY,
                    roadmap_id INTEGER NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
                    topic_id INTEGER NOT NULL, time TEXT NOT NULL,
                    answer TEXT NOT NULL, passed INTEGER NOT NULL, feedback TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    def add(self, roadmap, sample=None, *, finish_interview=False):
        body = json.dumps(validate_roadmap(roadmap), ensure_ascii=False)
        with self.connection() as db:
            if sample:
                db.execute("INSERT OR IGNORE INTO roadmaps(body, sample) VALUES (?, ?)", (body, sample))
                return db.execute("SELECT id FROM roadmaps WHERE sample = ?", (sample,)).fetchone()[0]
            identifier = db.execute("INSERT INTO roadmaps(body) VALUES (?)", (body,)).lastrowid
            if finish_interview:
                db.execute("DELETE FROM interview_draft WHERE id = 1")
            return identifier

    def interview_draft(self):
        with self.connection() as db:
            row = db.execute("SELECT body FROM interview_draft WHERE id = 1").fetchone()
            return json.loads(row["body"]) if row else None

    def save_interview(self, goal, questions=None, answers=None):
        with self.connection() as db:
            db.execute("""
                INSERT INTO interview_draft(id, body) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET body = excluded.body
            """, (json.dumps([goal, questions, answers], ensure_ascii=False),))

    def all(self):
        with self.connection() as db:
            return [(r["id"], json.loads(r["body"])) for r in db.execute(
                "SELECT id, body FROM roadmaps ORDER BY id DESC"
            )]

    def state(self, roadmap_id):
        with self.connection() as db:
            return {
                row["topic_id"]: dict(row) | {"resources": json.loads(row["resources"])}
                for row in db.execute("SELECT * FROM topic_state WHERE roadmap_id = ?", (roadmap_id,))
            }

    def save_resources(self, roadmap_id, topic_id, resources):
        with self.connection() as db:
            db.execute("""
                INSERT INTO topic_state(roadmap_id, topic_id, resources) VALUES (?, ?, ?)
                ON CONFLICT(roadmap_id, topic_id) DO UPDATE SET resources = excluded.resources
            """, (roadmap_id, topic_id, json.dumps(resources, ensure_ascii=False)))

    def attempt(self, roadmap_id, topic, answer, passed, feedback, moment=None):
        moment = moment or now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            completed = {
                r[0]: {"completed_at": r[1]} for r in db.execute(
                    "SELECT topic_id, completed_at FROM topic_state WHERE roadmap_id = ?", (roadmap_id,)
                )
            }
            if not available(topic, completed):
                raise ValueError("Сначала заверши предыдущие темы.")
            db.execute("""
                INSERT INTO attempts(roadmap_id, topic_id, time, answer, passed, feedback)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (roadmap_id, topic["id"], moment.isoformat(), answer, passed, feedback))
            db.execute("""
                INSERT INTO topic_state(roadmap_id, topic_id, completed_at, review_at, feedback)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(roadmap_id, topic_id) DO UPDATE SET
                    completed_at = COALESCE(topic_state.completed_at, excluded.completed_at),
                    review_at = COALESCE(topic_state.review_at, excluded.review_at),
                    feedback = excluded.feedback
            """, (
                roadmap_id, topic["id"], moment.isoformat() if passed else None,
                (moment + timedelta(days=REVIEWS[0])).isoformat() if passed else None,
                feedback,
            ))

    def review(self, roadmap_id, topic_id, remembered, moment=None):
        moment = moment or now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("""
                SELECT review_step FROM topic_state WHERE roadmap_id = ? AND topic_id = ?
                AND completed_at IS NOT NULL
            """, (roadmap_id, topic_id)).fetchone()
            if state is None:
                raise ValueError("Сначала заверши практику по теме.")
            step = min(state[0] + 1, len(REVIEWS) - 1) if remembered else 0
            due = moment + timedelta(days=REVIEWS[step])
            db.execute("""
                UPDATE topic_state SET review_step = ?, review_at = ?
                WHERE roadmap_id = ? AND topic_id = ?
            """, (step, due.isoformat(), roadmap_id, topic_id))
