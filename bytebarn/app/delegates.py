"""Custom-painted rows for the sidebar and workspace lists.

Item views stop being strings-in-a-tree and become composed cards: a sprite
avatar in a rounded well, a two-line title/meta layout, a soft accent rail
for selection instead of a full-row highlight, and letter-spaced monospace
eyebrow headers with a hairline rule. Colors follow the active theme.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from . import theme

# item data roles shared with the views that fill them
KIND_ROLE = Qt.UserRole + 1     # "session" | "project" | "header"
TITLE_ROLE = Qt.UserRole + 3
META_ROLE = Qt.UserRole + 4
RUNNING_ROLE = Qt.UserRole + 5
COUNT_ROLE = Qt.UserRole + 6    # project session count

_RUNNING = QColor("#98c379")


def _accent() -> QColor:
    return QColor(theme.MODERN_ACCENT if theme.is_modern() else theme.ACCENT)


def _muted(option) -> QColor:
    color = option.palette.text().color()
    color.setAlpha(140)
    return color


def _mono(size: float = 9.5, *, bold: bool = False) -> QFont:
    font = QFont("Menlo")
    font.setStyleHint(QFont.Monospace)
    font.setPointSizeF(size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.PercentageSpacing, 108)
    return font


class SessionRowDelegate(QStyledItemDelegate):
    """Paints header / project / session rows; anything else falls through."""

    PAD = 8

    def sizeHint(self, option, index) -> QSize:
        kind = index.data(KIND_ROLE)
        if kind == "header":
            return QSize(option.rect.width(), 30)
        if kind == "project":
            return QSize(option.rect.width(), 34)
        if kind == "session":
            return QSize(option.rect.width(), 48)
        return super().sizeHint(option, index)

    def paint(self, painter: QPainter, option, index) -> None:
        kind = index.data(KIND_ROLE)
        if kind == "header":
            self._paint_header(painter, option, index)
        elif kind == "project":
            self._paint_project(painter, option, index)
        elif kind == "session":
            self._paint_session(painter, option, index)
        else:
            super().paint(painter, option, index)

    # -- rows ----------------------------------------------------------------

    def _paint_header(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(self.PAD, 0, -self.PAD, 0)
        label = (index.data(TITLE_ROLE) or index.data(Qt.DisplayRole) or "").upper()
        # collapsible section headers get a chevron; leaf/empty headers don't
        if option.state & QStyle.State_Children:
            label = ("▾ " if option.state & QStyle.State_Open else "▸ ") + label
        painter.setFont(_mono(9.0, bold=True))
        painter.setPen(_muted(option))
        metrics = QFontMetrics(painter.font())
        baseline = rect.center().y() + metrics.ascent() // 2 + 2
        painter.drawText(rect.x(), baseline, label)
        # hairline rule filling the remaining width
        text_w = metrics.horizontalAdvance(label)
        rule = QColor(option.palette.text().color())
        rule.setAlpha(34)
        painter.setPen(QPen(rule, 1))
        y = rect.center().y() + 2
        painter.drawLine(rect.x() + text_w + 8, y, rect.right(), y)
        painter.restore()

    def _paint_project(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(self.PAD - 2, 2, -self.PAD, -2)
        self._row_background(painter, option, rect)
        title = index.data(TITLE_ROLE) or index.data(Qt.DisplayRole) or ""
        painter.setPen(option.palette.text().color())
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text_rect = rect.adjusted(10, 0, -60, 0)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         metrics.elidedText(str(title), Qt.ElideRight, text_rect.width()))
        count = index.data(COUNT_ROLE)
        if count is not None:
            pill_font = _mono(8.5, bold=True)
            pm = QFontMetrics(pill_font)
            label = str(count)
            w = max(22, pm.horizontalAdvance(label) + 12)
            pill = QRectF(rect.right() - w - 6, rect.center().y() - 9, w, 18)
            bg = QColor(option.palette.text().color())
            bg.setAlpha(26)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(pill, 9, 9)
            painter.setPen(_muted(option))
            painter.setFont(pill_font)
            painter.drawText(pill, Qt.AlignCenter, label)
        painter.restore()

    def _paint_session(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(self.PAD - 2, 2, -self.PAD, -2)
        selected = bool(option.state & QStyle.State_Selected)
        self._row_background(painter, option, rect)

        # avatar in a rounded well
        icon = index.data(Qt.DecorationRole)
        well = QRectF(rect.x() + 6, rect.center().y() - 14, 28, 28)
        well_bg = QColor(0, 0, 0, 46) if theme.current_mode() != "light" \
            else QColor(0, 0, 0, 14)
        painter.setPen(Qt.NoPen)
        painter.setBrush(well_bg)
        painter.drawRoundedRect(well, 7, 7)
        if icon is not None and not icon.isNull():
            icon.paint(painter, well.toRect().adjusted(2, 2, -2, -2))

        running = bool(index.data(RUNNING_ROLE))
        title = str(index.data(TITLE_ROLE) or index.data(Qt.DisplayRole) or "")
        meta = str(index.data(META_ROLE) or "")

        text_x = int(well.right()) + 9
        text_w = rect.right() - text_x - (14 if running else 6)
        title_font = option.font
        title_font.setPointSizeF(max(option.font.pointSizeF(), 12.0))
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(_accent() if selected and theme.is_modern()
                       else option.palette.text().color())
        tm = QFontMetrics(title_font)
        painter.drawText(text_x, rect.y() + 6 + tm.ascent(),
                         tm.elidedText(title, Qt.ElideRight, text_w))

        if meta:
            painter.setFont(_mono(8.5))
            painter.setPen(_muted(option))
            mm = QFontMetrics(painter.font())
            painter.drawText(text_x, rect.bottom() - 7,
                             mm.elidedText(meta.upper(), Qt.ElideRight, text_w))

        if running:
            dot = QRectF(rect.right() - 14, rect.y() + 10, 7, 7)
            glow = QColor(_RUNNING)
            glow.setAlpha(56)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(dot.adjusted(-3, -3, 3, 3))
            painter.setBrush(_RUNNING)
            painter.drawEllipse(dot)
        painter.restore()

    # -- shared --------------------------------------------------------------

    def _row_background(self, painter, option, rect) -> None:
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        radius = 8
        if selected:
            wash = _accent()
            wash.setAlpha(30)
            painter.setPen(Qt.NoPen)
            painter.setBrush(wash)
            painter.drawRoundedRect(QRectF(rect), radius, radius)
            rail = QRectF(rect.x(), rect.y() + 5, 3, rect.height() - 10)
            painter.setBrush(_accent())
            painter.drawRoundedRect(rail, 1.5, 1.5)
        elif hovered:
            wash = QColor(option.palette.text().color())
            wash.setAlpha(14)
            painter.setPen(Qt.NoPen)
            painter.setBrush(wash)
            painter.drawRoundedRect(QRectF(rect), radius, radius)
