from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from umbratlas.core import Store, validate_roadmap
from umbratlas.samples import SAMPLES
from umbratlas.ui import MainWindow, InterviewDialog


class RetentionTest(unittest.TestCase):
    def test_optional_branches_cannot_block_required_path(self):
        roadmap = deepcopy(SAMPLES['cpp'])
        roadmap['topics'][0]['optional'] = True
        with self.assertRaises(ValueError):
            validate_roadmap(roadmap)
        roadmap['topics'][0]['optional'] = False
        roadmap['topics'][-1]['optional'] = True
        self.assertTrue(validate_roadmap(roadmap)['topics'][-1]['optional'])

    def test_absence_and_answers_survive_restart(self):
        with TemporaryDirectory() as d:
            path = Path(d) / 'db'
            store = Store(path)
            identifier = store.add(SAMPLES['cpp'])
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.assertEqual(store.visit(start), 0)
            store.save_answer(identifier, 1, 'Незаконченное решение')
            restored = Store(path)
            self.assertEqual(restored.visit(start + timedelta(days=42)), 42)
            self.assertEqual(restored.drafts()[identifier, 1], 'Незаконченное решение')
            self.assertEqual(restored.visit(start + timedelta(days=42)), 0)


class EmbeddedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sequential_interview_limit_and_single_window(self):
        with TemporaryDirectory() as d:
            window = MainWindow(Store(Path(d) / 'db'), QSettings(str(Path(d) / 'settings'), QSettings.Format.IniFormat))
            window.show()
            page = InterviewDialog(window, ['Вопрос?'] * 17)
            def answer():
                self.assertFalse(page.isWindow())
                self.assertIsNone(self.app.activeModalWidget())
                self.assertEqual(len([w for w in self.app.topLevelWidgets() if w.isVisible()]), 1)
                page.validate()
                self.assertEqual(page.steps.currentIndex(), 0)
                for i in range(17):
                    self.assertEqual(page.steps.currentIndex(), i)
                    page.editors[i].setPlainText(f'Ответ {i}')
                    page.validate()
            QTimer.singleShot(0, answer)
            self.assertEqual(page.exec(), 1)
            self.assertEqual(len(page.answers()), 17)
            self.assertEqual(window.screen_stack.count(), 1)
            with self.assertRaises(ValueError):
                InterviewDialog(window, ['?'] * 18)
            window.close()

    def test_answer_restored_and_error_inline(self):
        with TemporaryDirectory() as d:
            db = Store(Path(d) / 'db')
            settings = QSettings(str(Path(d) / 'settings'), QSettings.Format.IniFormat)
            window = MainWindow(db, settings)
            window.add_sample('cpp')
            window.answer.setPlainText('Мой черновик')
            window.close()
            restored = MainWindow(db, settings)
            self.assertEqual(restored.answer.toPlainText(), 'Мой черновик')
            restored.show_error('Проверь соединение')
            self.assertEqual(restored.error_banner.text(), 'Проверь соединение')
            self.assertIsNone(self.app.activeModalWidget())
            restored.close()
