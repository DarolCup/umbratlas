from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QPushButton

from umbratlas.core import Store, available, pending_reviews, validate_roadmap
from umbratlas.provider import OpenAI
from umbratlas.samples import SAMPLES
from umbratlas.ui import MainWindow, STYLE


class RoadmapTest(unittest.TestCase):
    def test_rejects_cycles_and_unknown_dependencies(self):
        for prerequisites in ([4], [999]):
            roadmap = deepcopy(SAMPLES["cpp"])
            roadmap["topics"][0]["prerequisites"] = prerequisites
            with self.subTest(prerequisites=prerequisites), self.assertRaises(ValueError):
                validate_roadmap(roadmap)

    def test_progress_survives_restart_and_unlocks_both_branches(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "learning.sqlite3"
            store = Store(path)
            identifier = store.add(SAMPLES["cpp"], "cpp")
            self.assertEqual(store.add(SAMPLES["cpp"], "cpp"), identifier)
            topics = SAMPLES["cpp"]["topics"]
            with self.assertRaises(ValueError):
                store.attempt(identifier, topics[1], "blocked", True, "passed")
            started = datetime(2026, 1, 2, tzinfo=timezone.utc)
            store.attempt(identifier, topics[0], "first", False, "missing result", started)
            self.assertFalse(available(topics[1], store.state(identifier)))
            self.assertFalse(pending_reviews(SAMPLES["cpp"], store.state(identifier), started + timedelta(days=2)))
            store.attempt(identifier, topics[0], "second", True, "passed", started)
            store = Store(path)
            self.assertTrue(available(topics[1], store.state(identifier)))
            self.assertTrue(available(topics[2], store.state(identifier)))
            self.assertFalse(available(topics[3], store.state(identifier)))
            next_day = started + timedelta(days=1)
            self.assertEqual(pending_reviews(SAMPLES["cpp"], store.state(identifier), next_day), [topics[0]])
            store.review(identifier, topics[0]["id"], True, next_day)
            self.assertEqual(store.state(identifier)[1]["review_at"], (next_day + timedelta(days=3)).isoformat())
            store.review(identifier, topics[0]["id"], False, next_day)
            self.assertEqual(store.state(identifier)[1]["review_at"], (next_day + timedelta(days=1)).isoformat())


class ProviderTest(unittest.TestCase):
    def simulate(self, content):
        result = {"status": "completed", "output": [{"type": "message", "content": [content]}]}
        response = io.BytesIO(json.dumps(result).encode())
        return patch("umbratlas.provider.build_opener", **{"return_value.open.return_value": response})

    def test_structured_output_and_billing_account(self):
        content = {"type": "output_text", "text": json.dumps({"questions": ["Опыт?", "Цель?", "Время?"]})}
        with self.simulate(content) as opener:
            self.assertEqual(OpenAI("dummy").interview("C++")[0], "Опыт?")
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer dummy")
        self.assertFalse(json.loads(request.data)["store"])
        self.assertEqual(json.loads(request.data)["text"]["format"]["type"], "json_schema")

    def test_search_only_accepts_citations_from_the_tool(self):
        content = {
            "type": "output_text", "text": "См. https://unverified.example",
            "annotations": [
                {"type": "url_citation", "title": "Учебник", "url": "https://docs.example/cpp"},
                {"type": "url_citation", "title": "Дубликат", "url": "https://docs.example/cpp"},
                {"type": "url_citation", "title": "Файл", "url": "file:///etc/passwd"},
            ],
        }
        with self.simulate(content) as opener:
            resources = OpenAI("dummy").resources("C++", SAMPLES["cpp"]["topics"][0], "Русский")
        self.assertEqual([resource["url"] for resource in resources], ["https://docs.example/cpp"])
        self.assertEqual(json.loads(opener.return_value.open.call_args.args[0].data)["tools"][0]["type"], "web_search")


class DesktopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def test_two_sample_maps_and_the_full_offline_learning_loop(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "learning.sqlite3"
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(Store(path), settings)
            window.show()
            self.app.processEvents()
            self.assertEqual(window.pages.currentIndex(), 0)
            next(b for b in window.findChildren(QPushButton) if b.text() == "Основы готовки").click()
            self.assertEqual(window.roadmap["title"], SAMPLES["cooking"]["title"])
            next(b for b in window.findChildren(QPushButton) if b.text() == "Возвращение к C++").click()
            self.assertEqual(window.roadmap["title"], SAMPLES["cpp"]["title"])
            window.select_topic(2)
            self.assertFalse(window.answer.isEnabled())
            window.continue_button.click()
            window.tabs.setCurrentIndex(1)
            window.answer.setPlainText("Выполнил оба вызова. Получил 100 и 0.")

            def confirm_practice():
                dialog = self.app.activeModalWidget()
                dialog.findChild(QCheckBox).setChecked(True)
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

            QTimer.singleShot(0, confirm_practice)
            window.self_check.click()
            self.assertEqual(window.count.text(), "Пройдено 1 из 4")

            def confirm_review():
                dialog = self.app.activeModalWidget()
                buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
                self.assertFalse(buttons["Вспомнил"].isVisible())
                buttons["Показать шпаргалку"].click()
                self.assertTrue(buttons["Вспомнил"].isVisible())
                buttons["Вспомнил"].click()

            QTimer.singleShot(0, confirm_review)
            window.review_button.click()
            self.assertEqual(window.progress[1]["review_step"], 1)
            window.continue_button.click()
            self.assertEqual(window.selected_id, 2)
            window.view_button.click()
            self.assertEqual(window.map_views.currentIndex(), 1)
            window.close()
            restored = MainWindow(Store(path), settings)
            self.assertEqual(restored.selected_id, 2)
            self.assertEqual(restored.count.text(), "Пройдено 1 из 4")
            self.assertEqual(restored.library.count(), 2)
            restored.close()

    def test_worker_delivers_results_on_main_thread(self):
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(Store(Path(directory) / "learning.sqlite3"), settings)
            window.run("Построение", lambda: SAMPLES["cpp"], window.map_created)
            for _ in range(40):
                if window.job is None:
                    break
                QTest.qWait(25)
            self.assertIsNone(window.job)
            self.assertEqual(window.roadmap["title"], SAMPLES["cpp"]["title"])
            window.close()
