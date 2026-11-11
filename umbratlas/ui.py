"""Qt desktop window and the learning workflow."""

from datetime import datetime
from functools import partial
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

from PySide6.QtCore import QEventLoop, QTimer, QObject, QRunnable, QSettings, QStandardPaths, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .core import now, Store, available, pending_reviews, stages, web_url
from .graph import MapView
from .provider import ApiError, DEFAULT_MODEL, GHOSTMODEL_URL, OPENAI_URL, Provider, base_url
from .samples import SAMPLES

DATA = Qt.ItemDataRole.UserRole
PROVIDERS = {"openai": "OpenAI", "ghostmodel": "GhostModel", "custom": "Другой провайдер"}
PRESETS = {"openai": OPENAI_URL, "ghostmodel": GHOSTMODEL_URL}
ASSETS = Path(__file__).resolve().parent.parent / "assets"
STYLE = """
QWidget { background: #10111d; color: #eeedf7; font-family: Inter, sans-serif; font-size: 14px; }
QFrame#sidebar { background: #151624; border-right: 1px solid #282a40; }
QFrame#detail { background: #191b2c; border: 1px solid #30324c; border-radius: 16px; }
QFrame#top { background: #151624; border-bottom: 1px solid #282a40; }
QLabel, QCheckBox { background: transparent; }
QLabel#brand { color: #e2d9ff; font-size: 22px; font-weight: 750; }
QLabel#hero { font-size: 36px; font-weight: 750; color: #f5f1ff; }
QLabel#heading { font-size: 26px; font-weight: 700; }
QLabel#subheading { font-size: 18px; font-weight: 650; }
QLabel#muted, QLabel#section { color: #a8abc5; }
QLabel#section { font-size: 12px; font-weight: 650; }
QLabel#accent { color: #c1adff; font-weight: 650; }
QPushButton, QLineEdit, QPlainTextEdit, QComboBox {
    background: #202238; border: 1px solid #393b58; border-radius: 10px; padding: 10px 14px;
}
QPushButton { font-weight: 550; }
QFrame#detail QPushButton { font-size: 13px; padding: 9px 8px; }
QPushButton:hover { background: #2c2b48; border-color: #8c78bf; }
QPushButton:pressed { background: #37304f; }
QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus { border: 1px solid #bda5ff; }
QPushButton#accent {
    color: #171225; background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #c4b0ff,stop:1 #a88aee);
    border: 1px solid #c8b4ff; font-weight: 700;
}
QPushButton#accent:hover { background: #d1c0ff; }
QPushButton#accent:pressed { background: #ad91ef; }
QPushButton:disabled, QPushButton#accent:disabled { color: #8e91ab; background: #25273b; border-color: #34364e; }
QListWidget { background: transparent; border: 1px solid transparent; outline: 0; }
QListWidget::item { padding: 13px 10px; border-radius: 10px; margin: 3px 0; }
QListWidget::item:selected { color: #e3d9ff; background: #302943; border: 1px solid #51416e; }
QListWidget::item:hover:!selected { background: #24263b; }
QTabWidget::pane { border: 0; border-top: 1px solid #343650; }
QTabBar::tab { background: #191b2c; color: #a8abc5; padding: 12px 11px; }
QTabBar::tab:selected { color: #d8c8ff; border-bottom: 2px solid #b99af9; }
QTabBar::tab:hover { color: #eee6ff; }
QScrollArea { border: 0; background: transparent; }
QWidget#tabBody { background: #191b2c; }
QStatusBar { background: #151624; color: #a8abc5; border-top: 1px solid #282a40; }
QSplitter::handle { background: #10111d; width: 14px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 3px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 3px; }
QScrollBar::handle { background: #4c496a; border-radius: 3px; min-height: 26px; min-width: 26px; }
QScrollBar::handle:hover { background: #8b78b7; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar { background: #292a42; border: 0; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #b99af9; border-radius: 3px; }
QToolTip { background: #29253e; color: #eee6ff; border: 1px solid #766091; padding: 7px; }
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


class EmbeddedPage(QDialog):
    """A form hosted by the main stack; never creates a native dialog window."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.host = parent
        self.loop = None
        self.result_code = 0
        self.setWindowFlags(Qt.WindowType.Widget)

    def exec(self):
        if self.host is None:
            raise RuntimeError("An embedded page requires a main window")
        host = self.host
        previous = host.screen_stack.currentWidget()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 24, 40, 24)
        layout.addWidget(button("← Вернуться", self.reject), alignment=Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self)
        layout.addWidget(scroll, 1)
        self.page_error = label(style="accent")
        layout.addWidget(self.page_error)
        host.screen_stack.addWidget(container)
        host.active_page = self
        host.screen_stack.setCurrentWidget(container)
        self.show()
        self.loop = QEventLoop()
        self.loop.exec()
        host.screen_stack.setCurrentWidget(previous)
        host.screen_stack.removeWidget(container)
        host.active_page = None
        self.setParent(host)
        self.hide()
        container.deleteLater()
        return self.result_code

    def done(self, result):
        self.result_code = int(result)
        if self.loop is not None:
            self.loop.quit()

    def accept(self):
        self.done(1)

    def reject(self):
        if getattr(self, "fetch_task", None) is not None:
            self.message.setText("Дождись завершения проверки подключения.")
            return
        self.done(0)

    def notice(self, message):
        if hasattr(self, "validation_message"):
            self.validation_message.setText(message)
        elif self.host:
            self.host.statusBar().showMessage(message)


class ConnectionDialog(EmbeddedPage):
    def __init__(self, parent, settings, keys):
        super().__init__(parent)
        self.setWindowTitle("Настройки ИИ")
        self.setMinimumWidth(560)
        self.settings = settings
        self.keys = keys.copy()
        self.fetch_task = None
        self.active_provider = None
        self.active_address = None
        self.saved = {}
        form = QFormLayout(self)
        self.provider = QComboBox()
        for identifier, name in PROVIDERS.items():
            self.provider.addItem(name, identifier)
        self.address = QLineEdit()
        self.address.setPlaceholderText("https://provider.example/v1")
        self.address.setMaxLength(600)
        self.protocol = QComboBox()
        self.protocol.addItem("Chat Completions", "chat")
        self.protocol.addItem("Responses API", "responses")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Ключ выбранного провайдера")
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.lineEdit().setPlaceholderText("ID модели или выбор из каталога")
        self.model.lineEdit().setMaxLength(240)
        self.fetch_button = button("Загрузить модели", self.load_models)
        models = QHBoxLayout()
        models.addWidget(self.model, 1)
        models.addWidget(self.fetch_button)
        self.search_model = QLineEdit()
        self.search_model.setPlaceholderText("ID модели с Responses API и веб-поиском")
        self.search_model.setMaxLength(240)
        self.test_button = button("Проверить модель", self.test_model)
        self.test_button.setToolTip("Выполняет короткий запрос с твоим API-ключом")
        self.language = QLineEdit(settings.value("language", "Русский", str))
        self.language.setMaxLength(100)
        self.message = label(style="muted")
        form.addRow("Провайдер", self.provider)
        form.addRow("Адрес API", self.address)
        form.addRow("Протокол", self.protocol)
        form.addRow("API-ключ", self.key)
        form.addRow("Модель", models)
        form.addRow("Модель для поиска", self.search_model)
        form.addRow("Язык материалов", self.language)
        form.addRow(self.test_button)
        form.addRow(label("Ключ действует до закрытия приложения и отправляется на указанный адрес API.", "muted"))
        form.addRow(self.message)
        self.actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.actions.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        self.actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.actions.accepted.connect(self.save)
        self.actions.rejected.connect(self.reject)
        form.addRow(self.actions)
        selected = self.provider.findData(settings.value("provider", "openai", str))
        self.provider.setCurrentIndex(max(0, selected))
        self.provider.currentIndexChanged.connect(self.change_provider)
        self.address.textChanged.connect(self.change_address)
        self.key.textChanged.connect(self.remember_key)
        self.change_provider()

    def change_provider(self):
        if self.active_provider is not None:
            self.saved[self.active_provider] = (
                self.address.text(), self.protocol.currentData(), self.model.currentText().strip(),
                self.search_model.text().strip(),
            )
        identifier = self.provider.currentData()
        default_model = self.settings.value("model", DEFAULT_MODEL, str) if identifier == "openai" else ""
        default_protocol = "responses" if identifier == "openai" else "chat"
        address, protocol, model, search_model = self.saved.get(identifier, (
            self.settings.value(f"providers/{identifier}/address", "", str),
            self.settings.value(f"providers/{identifier}/protocol", default_protocol, str),
            self.settings.value(f"providers/{identifier}/model", default_model, str),
            self.settings.value(f"providers/{identifier}/search_model", "", str),
        ))
        self.active_provider = identifier
        self.address.setReadOnly(identifier in PRESETS)
        self.protocol.setEnabled(identifier != "openai")
        index = self.protocol.findData("responses" if identifier == "openai" else protocol)
        self.protocol.setCurrentIndex(max(0, index))
        self.address.blockSignals(True)
        self.address.setText(PRESETS.get(identifier, address))
        self.address.blockSignals(False)
        self.change_address()
        self.model.clear()
        self.model.setEditText(model)
        self.search_model.setText(search_model)

    def change_address(self):
        try:
            address = base_url(self.address.text())
        except ValueError:
            address = None
        scope = (self.active_provider, address) if address else None
        if scope != self.active_address:
            self.model.clear()
            self.search_model.clear()
        self.active_address = scope
        self.key.blockSignals(True)
        self.key.setText(self.keys.get(self.active_address, ""))
        self.key.blockSignals(False)
        self.message.clear()

    def remember_key(self, key):
        if self.active_address is not None:
            self.keys[self.active_address] = key.strip()

    def validate_key(self):
        key = self.key.text().strip()
        if key and (not key.isascii() or any(not 33 <= ord(char) <= 126 for char in key)):
            raise ValueError("В API-ключе обнаружены пробелы или недопустимые символы.")
        return key

    def save(self):
        try:
            address = base_url(self.address.text())
            key = self.validate_key()
            identifier = self.provider.currentData()
            model = self.model.currentText().strip()
            if not model and identifier != "openai":
                raise ValueError("Укажи ID модели или выбери модель из каталога.")
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.settings.setValue("provider", identifier)
        self.settings.setValue(f"providers/{identifier}/address", address)
        self.settings.setValue(f"providers/{identifier}/protocol", self.protocol.currentData())
        self.settings.setValue(f"providers/{identifier}/model", model or DEFAULT_MODEL)
        self.settings.setValue(f"providers/{identifier}/search_model", self.search_model.text().strip())
        self.settings.setValue("language", self.language.text().strip() or "Русский")
        self.keys[(identifier, address)] = key
        self.accept()

    def connect_api(self, *, require_model=False):
        address = base_url(self.address.text())
        key = self.validate_key()
        if not key:
            raise ValueError("Сначала введи API-ключ провайдера.")
        model = self.model.currentText().strip()
        if require_model and not model and self.provider.currentData() != "openai":
            raise ValueError("Укажи ID модели или выбери модель из каталога.")
        return Provider(key, model or DEFAULT_MODEL, url=address, protocol=self.protocol.currentData())

    def load_models(self):
        try:
            api = self.connect_api()
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.run_check("Загружаем модели…", api.models, self.models_loaded, self.models_failed)

    def test_model(self):
        try:
            api = self.connect_api(require_model=True)
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.run_check(
            "Проверяем модель коротким запросом…",
            partial(api.interview, "Научиться планировать учёбу на неделю"),
            self.model_tested, self.model_test_failed,
        )

    def run_check(self, message, operation, success, failure):
        self.message.setText(message)
        self.set_loading(True)
        self.fetch_task = Task(operation)
        self.fetch_task.signals.success.connect(success)
        self.fetch_task.signals.failure.connect(failure)
        QThreadPool.globalInstance().start(self.fetch_task)

    def set_loading(self, loading):
        self.provider.setEnabled(not loading)
        self.address.setEnabled(not loading)
        self.key.setEnabled(not loading)
        self.model.setEnabled(not loading)
        self.protocol.setEnabled(not loading and self.provider.currentData() != "openai")
        self.fetch_button.setEnabled(not loading)
        self.test_button.setEnabled(not loading)
        self.actions.button(QDialogButtonBox.StandardButton.Save).setEnabled(not loading)

    @Slot(object)
    def model_tested(self, questions):
        self.fetch_task = None
        self.set_loading(False)
        self.message.setText("Модель ответила на проверку. Можно сохранить настройки.")

    @Slot(str)
    def model_test_failed(self, error):
        self.fetch_task = None
        self.set_loading(False)
        self.message.setText(error)

    @Slot(object)
    def models_loaded(self, models):
        self.fetch_task = None
        self.set_loading(False)
        if not models:
            self.message.setText("Каталог пуст. Введи ID модели вручную.")
            return
        previous = self.model.currentText().strip()
        self.model.clear()
        self.model.addItems(models)
        self.model.setEditText(previous)
        self.message.setText(f"Загружено моделей: {len(models)}. Выбери модель для текстовых запросов.")
        self.model.showPopup()

    @Slot(str)
    def models_failed(self, error):
        self.fetch_task = None
        self.set_loading(False)
        self.message.setText(f"{error} Можно ввести ID модели вручную.")


class GoalDialog(EmbeddedPage):
    def __init__(self, parent, goal=""):
        super().__init__(parent)
        self.setWindowTitle("Новая карта")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Чему хочешь научиться?", "heading"))
        self.goal = QPlainTextEdit(goal)
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
        self.validation_message = label(style="muted")
        layout.addWidget(self.validation_message)

    def validate(self):
        if 3 <= len(self.goal.toPlainText().strip()) <= 1000:
            self.accept()
        else:
            self.notice("Опиши цель текстом длиной от 3 до 1000 символов.")


class InterviewDialog(EmbeddedPage):
    def __init__(self, parent, questions, answers=None):
        super().__init__(parent)
        self.setWindowTitle("Стартовая диагностика")
        if not 1 <= len(questions) <= 17:
            raise ValueError("Диагностика должна содержать не более 17 вопросов.")
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.addWidget(label("Настроим маршрут под тебя", "hero"))
        self.counter = label(style="accent")
        layout.addWidget(self.counter)
        self.steps = QStackedWidget()
        self.editors = []
        for index, question in enumerate(questions):
            page = QWidget()
            form = QVBoxLayout(page)
            form.addWidget(label(question, "heading"))
            editor = QPlainTextEdit(answers[index] if answers and index < len(answers) else "")
            editor.setPlaceholderText("Расскажи своими словами. Если не знаешь — так и напиши.")
            editor.setMinimumHeight(150)
            form.addWidget(editor)
            self.steps.addWidget(page)
            self.editors.append(editor)
        layout.addWidget(self.steps, 1)
        self.validation_message = label(style="muted")
        layout.addWidget(self.validation_message)
        row = QHBoxLayout()
        self.back = button("Назад", lambda: self.move(-1))
        self.forward = button("Дальше →", self.validate, True)
        row.addWidget(self.back)
        row.addStretch()
        row.addWidget(self.forward)
        layout.addLayout(row)
        self.move(0)

    def answers(self):
        return [editor.toPlainText().strip() for editor in self.editors]

    def move(self, delta):
        index = self.steps.currentIndex() + delta
        self.steps.setCurrentIndex(index)
        self.counter.setText(f"ВОПРОС {index + 1} ИЗ {len(self.editors)}")
        self.back.setEnabled(index > 0)
        self.forward.setText("Построить карту" if index == len(self.editors) - 1 else "Дальше →")
        self.validation_message.clear()

    def validate(self):
        answer = self.answers()[self.steps.currentIndex()]
        if not 1 <= len(answer) <= 2400:
            self.notice("Ответь на вопрос: от 1 до 2400 символов.")
            return
        if self.steps.currentIndex() == len(self.editors) - 1:
            self.accept()
        else:
            self.move(1)


class MainWindow(QMainWindow):
    def __init__(self, store, settings=None):
        super().__init__()
        self.store = store
        self.settings = settings if settings is not None else QSettings()
        self.keys = {
            ("openai", OPENAI_URL): os.environ.get("OPENAI_API_KEY", "").strip(),
            ("ghostmodel", GHOSTMODEL_URL): os.environ.get("GHOSTMODEL_API_KEY", "").strip(),
        }
        self.load_connection()
        self.pool = QThreadPool(self)
        self.job = None
        self.on_result = None
        self.roadmaps = {}
        self.roadmap_id = None
        self.selected_id = None
        self.progress = {}
        self.drafts = self.store.drafts()
        self.active_page = None
        self.absent_days = self.store.visit()
        self.interview_draft = self.store.interview_draft()
        self.setWindowTitle(f"Umbratlas {__version__}")
        self.setMinimumSize(880, 610)
        self.resize(1260, 800)
        self.build()
        self.reload_library(self.settings.value("active_roadmap", 0, int))
        if self.absent_days >= 7 and self.roadmap_id:
            QTimer.singleShot(0, self.welcome_back)
        self.activity_timer = QTimer(self)
        self.activity_timer.timeout.connect(self.store.visit)
        self.activity_timer.start(60000)

    def build(self):
        shell = QWidget()
        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.screen_stack = QStackedWidget()
        self.screen_stack.addWidget(shell)
        self.setCentralWidget(self.screen_stack)
        top = QFrame(objectName="top")
        bar = QHBoxLayout(top)
        bar.setContentsMargins(22, 15, 24, 15)
        logo = QLabel()
        logo.setPixmap(QIcon(str(ASSETS / "umbratlas.png")).pixmap(31, 31))
        bar.addWidget(logo)
        bar.addWidget(label("Umbratlas", "brand"))
        bar.addStretch()
        self.connection_status = label(style="muted")
        bar.addWidget(self.connection_status)
        bar.addWidget(button("Настройки ИИ", self.configure))
        root.addWidget(top)
        self.error_banner = label(style="accent")
        self.error_banner.setMargin(14)
        self.error_banner.hide()
        root.addWidget(self.error_banner)

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        root.addLayout(main, 1)

        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(220)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(16, 24, 16, 20)
        nav.setSpacing(11)
        nav.addWidget(button("＋  Новая карта", self.new_map, True))
        self.resume_interview = button("К диагностике", self.open_interview)
        self.resume_interview.setVisible(self.interview_draft is not None)
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
        intro.addWidget(label("ТВОЙ ЛИЧНЫЙ АТЛАС ЗНАНИЙ", "accent"))
        intro.addWidget(label("Большая цель.\nПонятный следующий шаг.", "hero"))
        intro.addWidget(label("Опиши цель, ответь на вопросы и получи маршрут с практикой и шпаргалками.", "muted"))
        intro.addSpacing(18)
        intro.addWidget(label("01  Определи цель     ·     02  Построй маршрут     ·     03  Закрепи практикой", "muted"))
        intro.addSpacing(18)
        intro.addWidget(button("Создать карту", self.new_map, True), alignment=Qt.AlignmentFlag.AlignLeft)
        intro.addWidget(button("Открыть пример C++", partial(self.add_sample, "cpp")), alignment=Qt.AlignmentFlag.AlignLeft)
        intro.addStretch()
        self.pages.addWidget(welcome)

        workspace = QWidget()
        self.pages.addWidget(workspace)
        columns = QVBoxLayout(workspace)
        columns.setContentsMargins(8, 0, 18, 16)
        columns.setSpacing(0)
        header = QVBoxLayout()
        header.setContentsMargins(18, 24, 8, 20)
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
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName("Прогресс маршрута")
        header.addSpacing(10)
        header.addWidget(self.progress_bar)
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
        details.setContentsMargins(18, 20, 18, 12)
        details.setSpacing(10)
        self.topic_status = label(style="accent")
        self.topic_title = label(style="subheading")
        details.addWidget(self.topic_status)
        details.addWidget(self.topic_title)

        self.tabs = QTabWidget()
        details.addWidget(self.tabs)
        lesson, layout = scroll_panel()
        self.tabs.addTab(lesson, "Изучение")
        self.lesson = block(layout, "Теория")
        layout.addWidget(button("Объяснить подробнее с ИИ", self.ask_teacher))
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
        self.search_hint = label("Для подбора ссылок укажи модель для поиска в настройках ИИ.", "muted")
        layout.addWidget(self.search_hint)
        browser_search = QHBoxLayout()
        self.articles_button = button("Поиск статей", partial(self.search_in_browser, False))
        self.videos_button = button("Поиск видео", partial(self.search_in_browser, True))
        browser_search.addWidget(self.articles_button)
        browser_search.addWidget(self.videos_button)
        layout.addLayout(browser_search)
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

    def load_connection(self):
        identifier = self.settings.value("provider", "openai", str)
        self.provider_id = identifier if identifier in PROVIDERS else "openai"
        self.endpoint = PRESETS.get(self.provider_id) or self.settings.value(
            f"providers/{self.provider_id}/address", "", str
        )
        self.protocol = "responses" if self.provider_id == "openai" else self.settings.value(
            f"providers/{self.provider_id}/protocol", "chat", str
        )
        previous_model = self.settings.value("model", DEFAULT_MODEL, str) if self.provider_id == "openai" else ""
        self.model = self.settings.value(f"providers/{self.provider_id}/model", previous_model, str)
        self.language = self.settings.value("language", "Русский", str)
        self.search_model = self.settings.value(f"providers/{self.provider_id}/search_model", "", str)

    def update_connection(self):
        status = "ключ задан" if self.keys.get((self.provider_id, self.endpoint)) else "без ключа"
        self.connection_status.setText(f"{PROVIDERS[self.provider_id]} · {status}")

    def configure(self):
        dialog = ConnectionDialog(self, self.settings, self.keys)
        if dialog.exec():
            self.keys = dialog.keys
            self.load_connection()
            self.update_connection()
            if self.selected_id is not None:
                self.load_topic()

    def provider(self):
        key = self.keys.get((self.provider_id, self.endpoint))
        if not key:
            self.configure()
            key = self.keys.get((self.provider_id, self.endpoint))
        if not key:
            return None
        if not key.isascii() or any(not 33 <= ord(char) <= 126 for char in key):
            self.show_error("В API-ключе обнаружены пробелы или недопустимые символы.")
            return None
        return Provider(key, self.model, url=self.endpoint, protocol=self.protocol)

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
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(passed)
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
        ordered = sorted([topic for group in stages(self.roadmap) for topic in group],
                         key=lambda topic: topic.get("optional", False))
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
        self.topic_status.setText(("Дополнительно · " if topic.get("optional") else "") + status)
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
        can_search = self.protocol == "responses" or bool(self.search_model)
        self.search_button.setVisible(can_search)
        self.search_button.setEnabled(open_topic)
        self.search_hint.setVisible(not can_search)
        self.articles_button.setEnabled(open_topic)
        self.videos_button.setEnabled(open_topic)
        if has_resources:
            self.resource_list.setCurrentRow(0)

    def save_draft(self):
        if self.selected_id is not None:
            self.drafts[self.roadmap_id, self.selected_id] = self.answer.toPlainText()
            self.store.save_answer(self.roadmap_id, self.selected_id, self.answer.toPlainText())

    def ready(self):
        if self.job is not None:
            return False
        return True

    def run(self, status, operation, callback):
        if not self.ready():
            return
        self.job = Task(operation)
        self.on_result = callback
        self.job.signals.success.connect(self.succeeded)
        self.job.signals.failure.connect(self.failed)
        self.error_banner.hide()
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
        if self.active_page is not None and hasattr(self.active_page, "page_error"):
            self.active_page.page_error.setText(message)
        self.error_banner.setText(message)
        self.error_banner.show()

    def save_interview(self, goal, questions=None, answers=None):
        try:
            self.store.save_interview(goal, questions, answers)
        except sqlite3.Error:
            self.show_error("Не удалось сохранить черновик диагностики.")
            return False
        self.interview_draft = (goal, questions, answers)
        self.resume_interview.show()
        return True

    def new_map(self):
        if self.active_page or not self.ready():
            return
        previous = self.interview_draft[0] if self.interview_draft and self.interview_draft[1] is None else ""
        dialog = GoalDialog(self, previous)
        if dialog.exec() and self.save_interview(dialog.goal.toPlainText().strip()):
            self.open_interview()

    def set_interview(self, goal, questions):
        if self.save_interview(goal, questions):
            self.open_interview()

    def open_interview(self):
        if not self.ready() or not self.interview_draft:
            return
        goal, questions, answers = self.interview_draft
        if questions is None:
            api = self.provider()
            if api is not None:
                self.run("ИИ готовит вопросы…", partial(api.interview, goal), partial(self.set_interview, goal))
            return
        dialog = InterviewDialog(self, questions, answers)
        for editor in dialog.editors:
            editor.textChanged.connect(lambda: self.save_interview(goal, questions, dialog.answers()))
        accepted = dialog.exec()
        answers = dialog.answers()
        if not self.save_interview(goal, questions, answers) or not accepted:
            return
        api = self.provider()
        if api is not None:
            interview = [{"question": q, "answer": a} for q, a in zip(questions, answers, strict=True)]
            self.run("ИИ строит карту. Это может занять несколько минут…",
                     partial(api.roadmap, goal, interview), self.map_created)

    def map_created(self, roadmap):
        identifier = self.store.add(roadmap, finish_interview=True)
        self.interview_draft = None
        self.resume_interview.hide()
        self.reload_library(identifier)

    def search(self):
        if not self.ready():
            return
        api = self.provider()
        if api is None or (api.protocol != "responses" and not self.search_model):
            return
        if self.search_model:
            api = Provider(api.key, self.search_model, url=api.url, protocol="responses")
        self.run(
            "Ищем материалы в интернете…",
            partial(api.resources, self.roadmap["title"], self.topic, self.language),
            partial(self.resources_found, self.roadmap_id, self.selected_id),
        )

    def search_in_browser(self, video):
        query = f"{self.roadmap['title']} {self.topic['title']} {self.language}"
        if video:
            url = "https://www.youtube.com/results?" + urlencode({"search_query": query})
        else:
            url = "https://www.google.com/search?" + urlencode({"q": query})
        self.open_web(url)

    def resources_found(self, roadmap_id, topic_id, resources):
        self.store.save_resources(roadmap_id, topic_id, resources)
        if self.roadmap_id == roadmap_id:
            self.refresh()

    def open_selected_resource(self):
        self.open_resource(self.resource_list.currentItem())

    def open_resource(self, item):
        if item and web_url(item.data(DATA)):
            self.open_web(item.data(DATA))

    def open_web(self, url):
        if not web_url(url):
            return
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEnginePage
        except ImportError:
            self.show_error("Для встроенных материалов нужен Qt WebEngine: установи python3-pyside6.")
            return
        page = EmbeddedPage(self)
        layout = QVBoxLayout(page)
        address = label(url, "muted")
        address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(address)
        status = label(style="muted")
        layout.addWidget(status)
        class ContentPage(QWebEnginePage):
            def javaScriptAlert(self, origin, message):
                status.setText(message[:1000])
            def javaScriptConfirm(self, origin, message):
                return False
            def javaScriptPrompt(self, origin, message, default):
                return False, ""
            def acceptNavigationRequest(self, target, navigation, main_frame):
                return web_url(target.toString()) or target.toString() == "about:blank"
        view = QWebEngineView()
        view.setPage(ContentPage(view))
        view.urlChanged.connect(lambda target: address.setText(target.toString()))
        view.loadFinished.connect(lambda ok: status.setText("" if ok else "Сайт не загрузился. Можно скопировать адрес выше и открыть вручную."))
        row = QHBoxLayout()
        row.addWidget(button("← Назад по сайту", view.back))
        row.addWidget(button("Обновить", view.reload))
        layout.addLayout(row)
        layout.addWidget(view, 1)
        view.setUrl(QUrl(url))
        page.exec()
        view.stop()
        view.deleteLater()

    def check_answer(self):
        if not self.ready():
            return
        answer = self.answer.toPlainText().strip()
        if not 1 <= len(answer) <= 12000:
            self.show_error("Опиши результат или вставь решение до 12 000 символов.")
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
            self.show_error("Кратко запиши результат, прежде чем отметить практику.")
            return
        dialog = EmbeddedPage(self)
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
            self.store.save_answer(roadmap_id, topic["id"], "")
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
        dialog = EmbeddedPage(self)
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

    def ask_teacher(self):
        if not self.ready() or not available(self.topic, self.progress):
            return
        api = self.provider()
        if api is None:
            return
        page = EmbeddedPage(self)
        layout = QVBoxLayout(page)
        layout.addWidget(label(self.topic["title"], "heading"))
        question = QPlainTextEdit()
        question.setPlaceholderText("Задай вопрос или попроси разобрать пример")
        question.setMaximumHeight(130)
        layout.addWidget(question)
        response = QPlainTextEdit()
        response.setReadOnly(True)
        response.setPlainText(self.topic["lesson"])
        layout.addWidget(response, 1)
        topic = self.topic.copy()
        goal = self.roadmap["title"]
        history = []
        def send():
            text = question.toPlainText().strip() or "Объясни эту тему подробно, с примером и маленьким упражнением."
            if not self.ready() or len(text) > 6000:
                return
            def received(answer):
                history.extend([{"role": "user", "text": text}, {"role": "teacher", "text": answer}])
                response.appendPlainText("\nТы: " + text + "\n\nУчитель: " + answer)
                question.clear()
            self.run("Учитель готовит объяснение…", partial(api.teach, goal, topic, text, history[-8:]), received)
        layout.addWidget(button("Спросить учителя", send, True))
        page.exec()

    def welcome_back(self):
        if self.active_page or not self.roadmap_id:
            return
        page = EmbeddedPage(self)
        layout = QVBoxLayout(page)
        layout.addWidget(label("С возвращением", "hero"))
        layout.addWidget(label(f"Перерыв: {self.absent_days} дн. Начнём с короткого повторения, а затем продолжим маршрут.", "muted"))
        topic = next(iter(pending_reviews(self.roadmap, self.progress)), self.next_topic())
        layout.addWidget(label(topic["recall"], "heading"))
        answer = QPlainTextEdit()
        answer.setPlaceholderText("Что ты помнишь? Можно ответить «не помню».")
        layout.addWidget(answer)
        feedback = label(style="accent")
        layout.addWidget(feedback)
        def assess():
            if not answer.toPlainText().strip():
                feedback.setText("Напиши, что удалось вспомнить.")
                return
            key = self.keys.get((self.provider_id, self.endpoint))
            api = Provider(key, self.model, url=self.endpoint, protocol=self.protocol) if key else None
            if api:
                self.run("ИИ готовит план возвращения…", partial(api.return_plan,
                    self.roadmap["title"], topic, answer.toPlainText()[:12000], self.absent_days),
                    feedback.setText)
            else:
                feedback.setText(topic["cheatsheet"])
        layout.addWidget(button("Получить план возвращения", assess, True))
        layout.addWidget(button("Продолжить маршрут", page.accept))
        page.exec()
        self.continue_map()

    def closeEvent(self, event):
        if self.active_page is not None:
            self.active_page.reject()
            event.ignore()
        elif self.job is not None:
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
