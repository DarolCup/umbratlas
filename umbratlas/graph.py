"""Clickable Qt scene for roadmap prerequisites."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject, QGraphicsScene, QGraphicsView

from .core import available, stages

WIDTH, HEIGHT = 218, 104
INK = "#eeedf7"
MUTED = "#a8abc5"
ACCENT = "#bea5ff"


class TopicCard(QGraphicsObject):
    chosen = Signal(int)

    def __init__(self, topic, status, selected):
        super().__init__()
        self.topic = topic
        self.status = status
        self.selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(topic["title"])
        self.setAcceptHoverEvents(True)
        self.hovered = False

    def boundingRect(self):
        return QRectF(-2, -2, WIDTH + 4, HEIGHT + 8)

    def paint(self, painter, option, widget=None):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0b0c16"))
        painter.drawRoundedRect(QRectF(0, 4, WIDTH, HEIGHT), 15, 15)
        gradient = QLinearGradient(0, 0, WIDTH, HEIGHT)
        gradient.setColorAt(0, QColor("#34304f" if self.selected else "#24263d" if self.hovered else "#1d1f33"))
        gradient.setColorAt(1, QColor("#24213a" if self.selected else "#191b2c"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(ACCENT if self.selected else "#71608f" if self.hovered else "#383a54"), 1.5))
        painter.drawRoundedRect(QRectF(0, 0, WIDTH, HEIGHT), 15, 15)
        color = "#91d4c3" if self.status.startswith("Пройдено") else MUTED if self.status.startswith("Закрыта") else ACCENT
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(17, 23), 3, 3)
        painter.setPen(QColor(color))
        painter.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
        painter.drawText(QRectF(28, 12, WIDTH - 40, 21), Qt.AlignmentFlag.AlignLeft, self.status)
        painter.setPen(QColor(INK))
        painter.setFont(QFont("Inter", 11, QFont.Weight.DemiBold))
        rect = QRectF(16, 43, WIDTH - 32, 53)
        flags = int(Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap)
        title = self.topic["title"]
        painter.save()
        painter.setClipRect(rect)
        painter.drawText(rect, flags, title)
        painter.restore()

    def hoverEnterEvent(self, event):
        self.hovered = True
        self.update()

    def hoverLeaveEvent(self, event):
        self.hovered = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.chosen.emit(self.topic["id"])
        else:
            event.ignore()


class MapView(QGraphicsView):
    chosen = Signal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor("#10111d"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.cards = {}

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#10111d"))
        painter.setPen(QPen(QColor("#292a40"), 1))
        step = 24
        left, top = int(rect.left()) // step * step, int(rect.top()) // step * step
        for x in range(left, int(rect.right()) + step, step):
            for y in range(top, int(rect.bottom()) + step, step):
                painter.drawPoint(QPointF(x, y))

    def show_map(self, roadmap, progress, selected):
        scene = self.scene()
        scene.clear()
        self.cards.clear()
        for row, group in enumerate(stages(roadmap)):
            y = 48 + row * 150
            label = scene.addSimpleText(f"Этап {row + 1}", QFont("Inter", 9, QFont.Weight.DemiBold))
            label.setBrush(QColor(MUTED))
            label.setPos(26, y - 27)
            for col, topic in enumerate(group):
                identifier = topic["id"]
                if progress.get(identifier, {}).get("completed_at"):
                    status = "Пройдено"
                else:
                    status = "Доступна" if available(topic, progress) else "Закрыта"
                if topic.get("optional"):
                    status += " · дополнительно"
                card = TopicCard(topic, status, identifier == selected)
                card.setPos(26 + col * (WIDTH + 24), y)
                card.chosen.connect(self.chosen.emit)
                scene.addItem(card)
                self.cards[identifier] = card
        for topic in roadmap["topics"]:
            end = self.cards[topic["id"]].pos()
            for prerequisite in topic["prerequisites"]:
                start = self.cards[prerequisite].pos()
                sx, sy = start.x() + WIDTH / 2, start.y() + HEIGHT
                ex, ey = end.x() + WIDTH / 2, end.y()
                path = QPainterPath()
                path.moveTo(sx, sy)
                middle = sy + min(24, (ey - sy) / 2)
                path.cubicTo(sx, middle, ex, middle, ex, ey)
                line = scene.addPath(path, QPen(QColor("#6e5c93"), 1.5))
                line.setZValue(-1)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-18, -18, 28, 30))

    def highlight(self, identifier, scroll=False):
        for key, card in self.cards.items():
            card.selected = key == identifier
            card.update()
        if scroll and identifier in self.cards:
            self.ensureVisible(self.cards[identifier], 32, 32)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            if 0.4 <= self.transform().m11() * factor <= 2.5:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)
