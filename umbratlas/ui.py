"""Qt desktop window and the learning workflow."""

from datetime import datetime
from functools import partial
import os
from pathlib import Path
import sqlite3

from PySide6.QtCore import QObject, QRunnable, QSettings, QStandardPaths, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .core import Store, available, pending_reviews, stages, web_url
from .graph import MapView
from .provider import ApiError, DEFAULT_MODEL, OpenAI
from .samples import SAMPLES

DATA = Qt.ItemDataRole.UserRole
ASSETS = Path(__file__).resolve().parent.parent / "assets"
STYLE = """
QWidget { background: #121c22; color: #edf2f1; font-family: Inter, sans-serif; font-size: 14px; }
QFrame#sidebar, QFrame#detail, QFrame#top { background: #18242b; }
QFrame#sidebar { border-right: 1px solid #2b3b42; }
QFrame#detail { border-left: 1px solid #2b3b42; }
QFrame#top { border-bottom: 1px solid #2b3b42; }
QLabel, QCheckBox { background: transparent; }
QLabel#brand { color: #b5efda; font-size: 21px; font-weight: 750; }
QLabel#hero { font-size: 32px; font-weight: 700; }
QLabel#heading { font-size: 24px; font-weight: 700; }
QLabel#subheading { font-size: 17px; font-weight: 680; }
QLabel#muted, QLabel#section { color: #a3b2b9; }
QLabel#section { font-size: 13px; font-weight: 650; }
QLabel#accent { color: #9ce1c3; font-weight: 650; }
QPushButton, QLineEdit, QPlainTextEdit {
    background: #1b2a32; border: 1px solid #3a5057; border-radius: 7px; padding: 8px 12px;
}
QPushButton:hover { background: #294047; border-color: #709e90; }
QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus { border: 1px solid #9ce1c3; }
QPushButton#accent { color: #112822; background: #9ce1c3; border-color: #9ce1c3; font-weight: 700; }
QPushButton#accent:hover { background: #b7f2db; }
QPushButton:disabled, QPushButton#accent:disabled { color: #869399; background: #26343a; border-color: #39484d; }
QListWidget { background: #18242b; border: 1px solid transparent; outline: 0; }
QListWidget::item { padding: 9px 8px; border-radius: 7px; }
QListWidget::item:selected { color: #b5efda; background: #2b413c; }
QListWidget::item:hover:!selected { background: #22333b; }
QTabWidget::pane { border: 0; border-top: 1px solid #34484d; }
QTabBar::tab { background: #18242b; color: #a3b2b9; padding: 9px 10px; }
QTabBar::tab:selected { color: #b5efda; border-bottom: 2px solid #9ce1c3; }
QScrollArea { border: 0; background: #18242b; }
QWidget#tabBody { background: #18242b; }
QStatusBar { background: #18242b; color: #a3b2b9; }
QSplitter::handle { background: #2b3b42; }
"""


def label(text="", style="", parent=None):
    item = QLabel(text, parent)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    item.setObjectName(style)
    return item


def button(text, action, primary=False):
    item = QPushButton(text)
    if primary:
        item.setObjectName("accent")
    item.clicked.connect(action)
    return item


def scroll_panel():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    content.setObjectName("tabBody")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(5, 16, 8, 16)
    layout.setSpacing(10)
    scroll.setWidget(content)
    return scroll, layout


def block(layout, heading):
    layout.addWidget(label(heading, "section"))
    text = label()
    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(text)
    return text


class Signals(QObject):
    success = Signal(object)
    failure = Signal(str)


class Task(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = Signals()

    def run(self):
        try:
            self.signals.success.emit(self.operation())
        except (ApiError, ValueError) as error:
            self.signals.failure.emit(str(error))
        except Exception:
            self.signals.failure.emit("Не удалось обработать ответ. Повтори запрос.")


class ConnectionDialog(QDialog):
    def __init__(self, parent, key, model, language):
        super().__init__(parent)
        self.setWindowTitle("Подключение OpenAI")
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.key = QLineEdit(key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-…")
        self.model = QLineEdit(model)
        self.language = QLineEdit(language)
        self.model.setMaxLength(120)
        self.language.setMaxLength(100)
        form.addRow("API-ключ", self.key)
        form.addRow("Модель", self.model)
        form.addRow("Язык материалов", self.language)
        form.addRow(label("Ключ хранится только до закрытия приложения. Запросы оплачиваются в твоём аккаунте OpenAI.", "muted"))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        form.addRow(actions)


class GoalDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Новая карта")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Чему хочешь научиться?", "heading"))
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText("Например, хочу вернуться к C++ и написать консольное приложение.")
        self.goal.setFixedHeight(100)
        layout.addWidget(self.goal)
        layout.addWidget(label("ИИ задаст несколько вопросов о цели и твоём опыте.", "muted"))
        self.actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.actions.button(QDialogButtonBox.StandardButton.Ok).setText("Получить вопросы")
        self.actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.actions.accepted.connect(self.validate)
        self.actions.rejected.connect(self.reject)
        layout.addWidget(self.actions)

    def validate(self):
        if 3 <= len(self.goal.toPlainText().strip()) <= 1000:
            self.accept()
        else:
            QMessageBox.information(self, "Цель", "Опиши цель текстом длиной от 3 до 1000 символов.")


class InterviewDialog(QDialog):
    def __init__(self, parent, questions, answers=None):
        super().__init__(parent)
        self.setWindowTitle("Стартовая диагностика")
        self.resize(600, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Перед построением карты", "heading"))
        scroll, form = scroll_panel()
        self.editors = []
        for index, question in enumerate(questions):
            form.addWidget(label(question))
            editor = QPlainTextEdit(answers[index] if answers else "")
            editor.setPlaceholderText("Твой ответ")
            editor.setFixedHeight(74)
            form.addWidget(editor)
            self.editors.append(editor)
        form.addStretch()
        layout.addWidget(scroll)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("Построить карту")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        actions.accepted.connect(self.validate)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def answers(self):
        return [editor.toPlainText().strip() for editor in self.editors]

    def validate(self):
        answers = self.answers()
        if not any(answers) or any(len(answer) > 2400 for answer in answers):
            QMessageBox.information(self, "Ответы", "Ответь хотя бы на один вопрос. Лимит — 2400 символов на ответ.")
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, store, settings=None):
        super().__init__()
        self.store = store
        self.settings = settings if settings is not None else QSettings()
        self.key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = self.settings.value("model", DEFAULT_MODEL, str)
        self.language = self.settings.value("language", "Русский", str)
        self.pool = QThreadPool(self)
        self.job = None
        self.on_result = None
        self.roadmaps = {}
        self.roadmap_id = None
        self.selected_id = None
        self.progress = {}
        self.drafts = {}
        self.interview_draft = None
        self.setWindowTitle(f"Umbratlas {__version__}")
        self.setMinimumSize(880, 610)
        self.resize(1260, 800)
        self.build()
        self.reload_library(self.settings.value("active_roadmap", 0, int))

    def build(self):
        shell = QWidget()
        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(shell)
        top = QFrame(objectName="top")
        bar = QHBoxLayout(top)
        bar.setContentsMargins(19, 11, 20, 11)
        logo = QLabel()
        logo.setPixmap(QIcon(str(ASSETS / "umbratlas.png")).pixmap(31, 31))
        bar.addWidget(logo)
        bar.addWidget(label("Umbratlas", "brand"))
        bar.addStretch()
        self.connection_status = label(style="muted")
        bar.addWidget(self.connection_status)
        bar.addWidget(button("Настройки ИИ", self.configure))
        root.addWidget(top)

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        root.addLayout(main, 1)

        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(206)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(13, 19, 13, 17)
        nav.setSpacing(11)
        nav.addWidget(button("＋  Новая карта", self.new_map, True))
        self.resume_interview = button("К диагностике", self.open_interview)
        self.resume_interview.hide()
        nav.addWidget(self.resume_interview)
        nav.addWidget(label("Мои карты", "section"))
        self.library = QListWidget()
        self.library.setWordWrap(True)
        self.library.setAccessibleName("Мои карты")
        self.library.currentItemChanged.connect(self.change_map)
        nav.addWidget(self.library, 1)
        nav.addWidget(label("Примеры без ключа", "section"))
        nav.addWidget(button("Возвращение к C++", partial(self.add_sample, "cpp")))
        nav.addWidget(button("Основы готовки", partial(self.add_sample, "cooking")))
        main.addWidget(sidebar)

        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        welcome = QWidget()
        intro = QVBoxLayout(welcome)
        intro.setContentsMargins(46, 24, 46, 24)
        intro.setSpacing(14)
        intro.addStretch()
        intro.addWidget(label("Карта твоего обучения", "hero"))
        intro.addWidget(label("Опиши цель, ответь на вопросы и получи маршрут с практикой и шпаргалками.", "muted"))
        intro.addSpacing(10)
        intro.addWidget(button("Создать карту", self.new_map, True), alignment=Qt.AlignmentFlag.AlignLeft)
        intro.addWidget(button("Открыть пример C++", partial(self.add_sample, "cpp")), alignment=Qt.AlignmentFlag.AlignLeft)
        intro.addStretch()
        self.pages.addWidget(welcome)

        workspace = QWidget()
        self.pages.addWidget(workspace)
        columns = QVBoxLayout(workspace)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        header = QVBoxLayout()
        header.setContentsMargins(23, 18, 23, 13)
        header.setSpacing(5)
        self.map_title = label(style="heading")
        self.summary = label(style="muted")
        header.addWidget(self.map_title)
        header.addWidget(self.summary)
        actions = QHBoxLayout()
        self.count = label(style="accent")
        actions.addWidget(self.count)
        actions.addStretch()
        self.continue_button = button("Продолжить", self.continue_map, True)
        self.review_button = button("Повторить", self.open_review)
        actions.addWidget(self.review_button)
        actions.addWidget(self.continue_button)
        header.addLayout(actions)
        columns.addLayout(header)

        splitter = QSplitter()
        columns.addWidget(splitter, 1)
        left = QWidget()
        left.setMinimumWidth(256)
        center = QVBoxLayout(left)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        view_actions = QHBoxLayout()
        view_actions.setContentsMargins(18, 6, 12, 6)
        view_actions.addWidget(label("Маршрут", "section"))
        view_actions.addStretch()
        self.view_button = button("Список", self.toggle_map_view)
        view_actions.addWidget(self.view_button)
        center.addLayout(view_actions)
        self.map_views = QStackedWidget()
        self.map_view = MapView()
        self.map_view.chosen.connect(self.select_topic)
        self.topic_list = QListWidget()
        self.topic_list.setAccessibleName("Темы маршрута")
        self.topic_list.setWordWrap(True)
        self.topic_list.currentItemChanged.connect(self.select_from_list)
        self.map_views.addWidget(self.map_view)
        self.map_views.addWidget(self.topic_list)
        center.addWidget(self.map_views, 1)
        splitter.addWidget(left)
        splitter.addWidget(self.build_details())
        splitter.setSizes([550, 475])
        splitter.setChildrenCollapsible(False)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_map)
        self.statusBar().showMessage("Готово")
        self.update_connection()

    def build_details(self):
        panel = QFrame(objectName="detail")
        panel.setMinimumWidth(343)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        details = QVBoxLayout(panel)
        details.setContentsMargins(15, 12, 13, 8)
        details.setSpacing(7)
        self.topic_status = label(style="accent")
        self.topic_title = label(style="subheading")
        details.addWidget(self.topic_status)
        details.addWidget(self.topic_title)

        self.tabs = QTabWidget()
        details.addWidget(self.tabs)
        lesson, layout = scroll_panel()
        self.tabs.addTab(lesson, "Изучение")
        self.lesson = block(layout, "Что изучить")
        layout.addSpacing(5)
        layout.addWidget(label("Материалы", "section"))
        self.resource_list = QListWidget()
        self.resource_list.setWordWrap(True)
        self.resource_list.setMaximumHeight(164)
        self.resource_list.setAccessibleName("Найденные материалы")
        self.resource_list.itemActivated.connect(self.open_resource)
        layout.addWidget(self.resource_list)
        self.no_resources = label("Для этой темы ещё нет сохранённых ссылок.", "muted")
        layout.addWidget(self.no_resources)
        row = QHBoxLayout()
        self.search_button = button("Найти материалы", self.search)
        self.link_button = button("Открыть ссылку", self.open_selected_resource)
        self.resource_list.currentItemChanged.connect(lambda item, _: self.link_button.setEnabled(item is not None))
        row.addWidget(self.search_button)
        row.addWidget(self.link_button)
        layout.addLayout(row)
        layout.addStretch()

        practice, layout = scroll_panel()
        self.tabs.addTab(practice, "Практика")
        self.practice = block(layout, "Задание")
        self.criteria = block(layout, "Критерии успеха")
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("Решение или описание результата")
        self.answer.setMinimumHeight(95)
        self.answer.setMaximumHeight(160)
        self.answer.textChanged.connect(self.save_draft)
        layout.addWidget(self.answer)
        self.ai_check = button("Проверить с ИИ", self.check_answer)
        self.self_check = button("Отметить по критериям", self.mark_answer)
        layout.addWidget(self.ai_check)
        layout.addWidget(self.self_check)
        self.feedback = block(layout, "Последняя проверка")
        layout.addStretch()

        notes, layout = scroll_panel()
        self.tabs.addTab(notes, "Шпаргалка")
        self.cheatsheet = block(layout, "Что запомнить")
        self.review_date = block(layout, "Повторение")
        self.topic_review = button("Вспомнить тему", self.review_selected)
        layout.addWidget(self.topic_review)
        layout.addStretch()
        return panel

    @property
    def roadmap(self):
        return self.roadmaps[self.roadmap_id]

    @property
    def topic(self):
        return next(topic for topic in self.roadmap["topics"] if topic["id"] == self.selected_id)

    def update_connection(self):
        self.connection_status.setText("Ключ OpenAI задан" if self.key else "Ключ OpenAI не задан")

    def configure(self):
        dialog = ConnectionDialog(self, self.key, self.model, self.language)
        if dialog.exec():
            self.key = dialog.key.text().strip()
            self.model = dialog.model.text().strip() or DEFAULT_MODEL
            self.language = dialog.language.text().strip() or "Русский"
            self.settings.setValue("model", self.model)
            self.settings.setValue("language", self.language)
            self.update_connection()

    def provider(self):
        if not self.key:
            self.configure()
        if not self.key:
            return None
        if not self.key.isascii() or any(character.isspace() for character in self.key):
            QMessageBox.warning(self, "API-ключ", "В API-ключе обнаружены пробелы или лишние символы.")
            return None
        return OpenAI(self.key, self.model)

    def reload_library(self, select=0):
        self.roadmaps = dict(self.store.all())
        self.library.blockSignals(True)
        self.library.clear()
        selected_item = None
        for identifier, roadmap in self.roadmaps.items():
            item = QListWidgetItem(roadmap["title"], self.library)
            item.setData(DATA, identifier)
            item.setToolTip(roadmap["title"])
            if identifier == select:
                selected_item = item
        self.library.blockSignals(False)
        if self.library.count():
            self.library.setCurrentItem(selected_item or self.library.item(0))
        else:
            self.pages.setCurrentIndex(0)

    def add_sample(self, sample):
        try:
            identifier = self.store.add(SAMPLES[sample], sample)
            self.reload_library(identifier)
        except sqlite3.Error:
            self.show_error("Не удалось сохранить пример в локальную базу.")

    def change_map(self, current, previous):
        if current is None:
            return
        self.roadmap_id = current.data(DATA)
        self.selected_id = None
        self.settings.setValue("active_roadmap", self.roadmap_id)
        self.pages.setCurrentIndex(1)
        self.refresh()
        remembered = self.settings.value(f"topic/{self.roadmap_id}", 0, int)
        identifiers = {topic["id"] for topic in self.roadmap["topics"]}
        self.select_topic(remembered if remembered in identifiers else self.next_topic()["id"], scroll=True)

    def refresh(self):
        self.progress = self.store.state(self.roadmap_id)
        self.map_title.setText(self.roadmap["title"])
        self.summary.setText(self.roadmap["summary"])
        total = len(self.roadmap["topics"])
        passed = sum(bool(state["completed_at"]) for state in self.progress.values())
        due = len(pending_reviews(self.roadmap, self.progress))
        self.count.setText(f"Пройдено {passed} из {total}")
        self.review_button.setText(f"Повторить · {due}" if due else "Повторить")
        self.review_button.setEnabled(passed > 0)
        self.continue_button.setEnabled(passed < total)
        self.map_view.show_map(self.roadmap, self.progress, self.selected_id)
        self.topic_list.blockSignals(True)
        self.topic_list.clear()
        for index, group in enumerate(stages(self.roadmap), 1):
            for topic in group:
                item = QListWidgetItem(f"Этап {index}  ·  {self.state_text(topic)}\n{topic['title']}", self.topic_list)
                item.setData(DATA, topic["id"])
        self.topic_list.blockSignals(False)
        if self.selected_id is not None:
            self.select_topic(self.selected_id)

    def next_topic(self):
        ordered = [topic for group in stages(self.roadmap) for topic in group]
        return next((
            topic for topic in ordered if available(topic, self.progress)
            and not self.progress.get(topic["id"], {}).get("completed_at")
        ), ordered[0])

    def continue_map(self):
        self.select_topic(self.next_topic()["id"], scroll=True)
        self.tabs.setCurrentIndex(0)

    def toggle_map_view(self):
        showing_list = self.map_views.currentIndex() == 1
        self.map_views.setCurrentIndex(0 if showing_list else 1)
        self.view_button.setText("Список" if showing_list else "Граф")
        if showing_list and self.selected_id is not None:
            self.map_view.highlight(self.selected_id, scroll=True)

    def select_from_list(self, current, previous):
        if current is not None:
            self.select_topic(current.data(DATA), scroll=True)

    def state_text(self, topic):
        if self.progress.get(topic["id"], {}).get("completed_at"):
            return "Пройдено"
        return "Доступна" if available(topic, self.progress) else "Закрыта"

    def select_topic(self, identifier, scroll=False):
        changed = identifier != self.selected_id
        self.selected_id = identifier
        self.settings.setValue(f"topic/{self.roadmap_id}", identifier)
        self.map_view.highlight(identifier, scroll)
        self.topic_list.blockSignals(True)
        for index in range(self.topic_list.count()):
            item = self.topic_list.item(index)
            if item.data(DATA) == identifier:
                self.topic_list.setCurrentItem(item)
                break
        self.topic_list.blockSignals(False)
        self.load_topic()
        if changed:
            self.tabs.setCurrentIndex(0)

    def load_topic(self):
        topic = self.topic
        state = self.progress.get(self.selected_id, {})
        open_topic = available(topic, self.progress)
        status = self.state_text(topic)
        if not open_topic:
            names = [t["title"] for t in self.roadmap["topics"]
                     if t["id"] in topic["prerequisites"] and not self.progress.get(t["id"], {}).get("completed_at")]
            status = "Сначала пройди: " + ", ".join(names)
        self.topic_status.setText(status)
        self.topic_title.setText(topic["title"])
        self.lesson.setText(topic["lesson"])
        self.practice.setText(topic["practice"])
        self.criteria.setText(topic["criteria"])
        self.cheatsheet.setText(topic["cheatsheet"])
        self.answer.blockSignals(True)
        self.answer.setPlainText(self.drafts.get((self.roadmap_id, self.selected_id), ""))
        self.answer.blockSignals(False)
        self.answer.setEnabled(open_topic and not state.get("completed_at"))
        self.ai_check.setEnabled(self.answer.isEnabled())
        self.self_check.setEnabled(self.answer.isEnabled())
        self.feedback.setText(state.get("feedback") or "Попыток пока нет.")
        review_at = state.get("review_at")
        review_date = datetime.fromisoformat(review_at).astimezone().strftime("%d.%m.%Y") if review_at else None
        self.review_date.setText(f"Следующее повторение {review_date}" if review_date else "Появится после практики.")
        self.topic_review.setEnabled(bool(review_at))
        self.resource_list.clear()
        for resource in state.get("resources", []):
            item = QListWidgetItem(resource["title"], self.resource_list)
            item.setData(DATA, resource["url"])
            item.setToolTip(resource["url"])
        has_resources = self.resource_list.count() > 0
        self.resource_list.setVisible(has_resources)
        self.no_resources.setVisible(not has_resources)
        self.link_button.setEnabled(has_resources)
        self.search_button.setText("Обновить поиск" if has_resources else "Найти материалы")
        self.search_button.setEnabled(open_topic)
        if has_resources:
            self.resource_list.setCurrentRow(0)

    def save_draft(self):
        if self.selected_id is not None:
            self.drafts[self.roadmap_id, self.selected_id] = self.answer.toPlainText()

    def ready(self):
        if self.job is not None:
            self.statusBar().showMessage("Дождись завершения текущего запроса.")
            return False
        return True

    def run(self, status, operation, callback):
        if not self.ready():
            return
        self.job = Task(operation)
        self.on_result = callback
        self.job.signals.success.connect(self.succeeded)
        self.job.signals.failure.connect(self.failed)
        self.statusBar().showMessage(status)
        self.pool.start(self.job)

    def clear_job(self):
        self.job = self.on_result = None

    @Slot(object)
    def succeeded(self, result):
        callback = self.on_result
        self.clear_job()
        self.statusBar().showMessage("Готово")
        try:
            callback(result)
        except (sqlite3.Error, ValueError):
            self.show_error("Не удалось сохранить результат в локальную базу.")

    @Slot(str)
    def failed(self, message):
        self.clear_job()
        self.show_error(message)

    def show_error(self, message):
        self.statusBar().showMessage(message, 10000)
        QMessageBox.warning(self, "Umbratlas", message)

    def new_map(self):
        if not self.ready():
            return
        api = self.provider()
        if api is None:
            return
        dialog = GoalDialog(self)
        if dialog.exec():
            goal = dialog.goal.toPlainText().strip()
            self.run("ИИ готовит вопросы…", partial(api.interview, goal), partial(self.set_interview, goal))

    def set_interview(self, goal, questions):
        self.interview_draft = (goal, questions, None)
        self.resume_interview.show()
        self.open_interview()

    def open_interview(self):
        if not self.ready() or not self.interview_draft:
            return
        api = self.provider()
        if api is None:
            return
        goal, questions, answers = self.interview_draft
        dialog = InterviewDialog(self, questions, answers)
        accepted = dialog.exec()
        answers = dialog.answers()
        self.interview_draft = (goal, questions, answers)
        if accepted:
            interview = [{"question": q, "answer": a} for q, a in zip(questions, answers, strict=True)]
            self.run("ИИ строит карту…", partial(api.roadmap, goal, interview), self.map_created)

    def map_created(self, roadmap):
        identifier = self.store.add(roadmap)
        self.interview_draft = None
        self.resume_interview.hide()
        self.reload_library(identifier)

    def search(self):
        if not self.ready():
            return
        api = self.provider()
        if api is not None:
            self.run(
                "Ищем материалы в интернете…",
                partial(api.resources, self.roadmap["title"], self.topic, self.language),
                partial(self.resources_found, self.roadmap_id, self.selected_id),
            )

    def resources_found(self, roadmap_id, topic_id, resources):
        self.store.save_resources(roadmap_id, topic_id, resources)
        if self.roadmap_id == roadmap_id:
            self.refresh()

    def open_selected_resource(self):
        self.open_resource(self.resource_list.currentItem())

    def open_resource(self, item):
        if item and web_url(item.data(DATA)):
            if not QDesktopServices.openUrl(QUrl(item.data(DATA))):
                self.show_error("Не удалось открыть браузер. Скопируй адрес из подсказки к ссылке.")

    def check_answer(self):
        if not self.ready():
            return
        answer = self.answer.toPlainText().strip()
        if not 1 <= len(answer) <= 12000:
            QMessageBox.information(self, "Практика", "Опиши результат или вставь решение до 12 000 символов.")
            return
        api = self.provider()
        if api is not None:
            self.run(
                "ИИ проверяет ответ…",
                partial(api.assess, self.roadmap["title"], self.topic, answer),
                partial(self.record_attempt, self.roadmap_id, self.topic, answer),
            )

    def mark_answer(self):
        if not self.ready():
            return
        answer = self.answer.toPlainText().strip()
        if not 1 <= len(answer) <= 12000:
            QMessageBox.information(self, "Практика", "Кратко запиши результат, прежде чем отметить практику.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Самопроверка")
        dialog.setMinimumWidth(430)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(self.topic["criteria"]))
        confirmed = QCheckBox("Я выполнил задание и проверил результат по критериям")
        layout.addWidget(confirmed)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = actions.button(QDialogButtonBox.StandardButton.Save)
        save.setText("Завершить тему")
        save.setEnabled(False)
        confirmed.toggled.connect(save.setEnabled)
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec():
            try:
                self.record_attempt(self.roadmap_id, self.topic, answer, (True, "Критерии подтверждены самостоятельно."))
            except (sqlite3.Error, ValueError):
                self.show_error("Не удалось сохранить результат практики.")

    def record_attempt(self, roadmap_id, topic, answer, assessment):
        passed, feedback = assessment
        self.store.attempt(roadmap_id, topic, answer, passed, feedback)
        if passed:
            self.drafts.pop((roadmap_id, topic["id"]), None)
        if roadmap_id == self.roadmap_id:
            self.refresh()
        self.statusBar().showMessage("Тема завершена" if passed else "Обратная связь сохранена", 6000)

    def open_review(self):
        due = pending_reviews(self.roadmap, self.progress)
        completed = [topic for topic in self.roadmap["topics"] if self.progress.get(topic["id"], {}).get("completed_at")]
        if due or completed:
            topic = (due or completed)[0]
            self.select_topic(topic["id"], scroll=True)
            self.review_selected()

    def review_selected(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Повторение темы")
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(self.topic["title"], "heading"))
        layout.addWidget(label(self.topic["recall"]))
        hint = label(self.topic["cheatsheet"])
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hint.hide()
        layout.addWidget(hint)
        row = QHBoxLayout()
        actions = QWidget()
        actions.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        forget_result = 2
        row.addWidget(button("Нужно повторить", lambda: dialog.done(forget_result)))
        row.addWidget(button("Вспомнил", dialog.accept, True))
        actions.hide()

        def reveal():
            hint.show()
            actions.show()
            reveal_button.hide()

        reveal_button = button("Показать шпаргалку", reveal)
        layout.addWidget(reveal_button)
        layout.addWidget(actions)
        result = dialog.exec()
        if result in (QDialog.DialogCode.Accepted, forget_result):
            try:
                self.store.review(
                    self.roadmap_id, self.selected_id, result == QDialog.DialogCode.Accepted
                )
                self.refresh()
                self.statusBar().showMessage("Повторение сохранено", 6000)
            except (sqlite3.Error, ValueError):
                self.show_error("Не удалось сохранить повторение.")

    def closeEvent(self, event):
        if self.job is not None:
            self.statusBar().showMessage("Запрос ещё выполняется. Окно можно свернуть до его завершения.")
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    app = QApplication()
    app.setOrganizationName("Umbratlas")
    app.setApplicationName("Umbratlas")
    app.setDesktopFileName("io.github.darolcup.umbratlas")
    app.setWindowIcon(QIcon(str(ASSETS / "umbratlas.png")))
    app.setStyleSheet(STYLE)
    data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    try:
        window = MainWindow(Store(data / "learning.sqlite3"))
    except (sqlite3.Error, OSError):
        QMessageBox.critical(None, "Umbratlas", f"Не удалось открыть локальную базу. Проверь доступ к папке:\n{data}")
        return 1
    window.show()
    return app.exec()
