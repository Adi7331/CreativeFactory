"""Prosty, lokalny interfejs aplikacji Generator filmów Adi."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal, QStandardPaths, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
    QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from creative_factory import projects, remix

APP_NAME = 'Generator filmów Adi'
APP_RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
CONFIG_DEFAULT = APP_RESOURCES / 'config.default.json'
VIDEO_FILTER = 'Filmy (*.mp4 *.mov *.mkv *.avi *.webm *.m4v)'
ROLE_LABELS = {'hooks': 'Hooki', 'clips': 'Klipy', 'cta': 'Zakończenia'}


def _smoke_log(message):
    target = os.environ.get('CREATIVE_FACTORY_SMOKE_LOG')
    if target:
        try:
            Path(target).write_text(str(message), encoding='utf-8')
        except OSError:
            pass


def read_config(project):
    try:
        raw = json.loads((Path(project) / 'config.json').read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict):
            raise ValueError('plik musi zawierać ustawienia w formacie JSON')
        config = dict(remix.DEFAULTS)
        for key in remix.DEFAULTS:
            if key in raw:
                config[key] = raw[key]
        if 'output_mode' not in raw:
            config['seed'] = None
        remix.validate_config(config)
        return config
    except (OSError, ValueError, remix.RemixError) as exc:
        raise remix.RemixError(f'Nie można wczytać ustawień projektu: {exc}') from exc


def save_config(project, updates):
    config = read_config(project)
    config.update(updates)
    remix.validate_config(config)
    target = Path(project) / 'config.json'
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(target)


def _read_assets(project):
    path = Path(project) / 'assets.json'
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise remix.RemixError(f'Nie można odczytać ustawień ujęć: {exc}') from exc
    return value if isinstance(value, dict) else {}


def _write_assets(project, values):
    path = Path(project) / 'assets.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def _settings_key(project, source):
    return Path(source).resolve().relative_to(Path(project).resolve()).as_posix()


def _safe_project_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', value).strip(' .')
    if not value or value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('Podaj nazwę projektu, której można użyć jako nazwy folderu Windows.')
    return value[:80]


def _project_root(settings, legacy_path=None):
    if legacy_path and Path(legacy_path).drive.casefold() == 'g:':
        return Path('G:/Generator filmów Adi/Projekty')
    saved = settings.value('projects_root', '', str)
    if saved:
        return Path(saved)
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    return Path(docs) / APP_NAME / 'Projekty'


def _register_project(settings, path):
    values = settings.value('projects', [], type=list)
    path = str(Path(path).resolve())
    values = [str(value) for value in values if Path(str(value)).exists()]
    if path not in values:
        values.append(path)
    settings.setValue('projects', values)
    settings.setValue('project_path', path)
    return values


def thumbnail(source, project, seconds=0.2, geometry=None, settings=None):
    cache = projects.work_dir(Path(project)) / 'miniatury'
    cache.mkdir(parents=True, exist_ok=True)
    stat = Path(source).stat()
    import hashlib
    spec = json.dumps({'geometry': geometry, 'settings': settings or {}}, sort_keys=True)
    key = hashlib.sha256(f'{Path(source).resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{seconds}|{spec}'.encode('utf-8')).hexdigest()[:24]
    destination = cache / f'{key}.jpg'
    if not destination.exists():
        filters = []
        if geometry:
            width, height = geometry
            fit = (settings or {}).get('fit', 'fill')
            if fit == 'fill':
                scale = 'increase'
                x = min(1.0, max(0.0, float((settings or {}).get('x', .5))))
                y = min(1.0, max(0.0, float((settings or {}).get('y', .5))))
                filters.extend([f'scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2:reset_sar=1',
                                f'crop={width}:{height}:(in_w-out_w)*{x:.6f}:(in_h-out_h)*{y:.6f}'])
            else:
                filters.extend([f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1',
                                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black'])
        filters.append('scale=600:850:force_original_aspect_ratio=decrease')
        subprocess.run([remix.tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-ss', str(max(0.0, seconds)), '-i', str(source), '-frames:v', '1',
                        '-vf', ','.join(filters), '-y', str(destination)],
                       capture_output=True, timeout=25, check=True)
    return destination


class DropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, placeholder='Przeciągnij pliki tutaj lub kliknij „Dodaj pliki”.'):
        super().__init__()
        self._placeholder = placeholder
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)

    def placeholder_text(self):
        return self._placeholder

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count():
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor('#b7b7bd'))
        painter.drawText(self.viewport().rect().adjusted(16, 16, -16, -16),
                         Qt.AlignCenter | Qt.TextWordWrap, self._placeholder)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self.files_dropped.emit(paths)
        event.acceptProposedAction()


class CollapsibleSection(QFrame):
    toggled = Signal(bool)

    def __init__(self, title, expanded=False):
        super().__init__()
        self.setObjectName('section')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.clicked.connect(self.setExpanded)
        layout.addWidget(self.toggle_button)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 8, 12, 12)
        self.body_layout.setSpacing(9)
        layout.addWidget(self.body)
        self.setExpanded(expanded)

    def setExpanded(self, expanded):
        expanded = bool(expanded)
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle_button.blockSignals(False)
        self.body.setVisible(expanded)
        self.toggled.emit(expanded)

    def isExpanded(self):
        return self.toggle_button.isChecked()


class ThumbnailWorker(QThread):
    ready = Signal(str, str)

    def __init__(self, source, project, seconds, request_id, geometry=None, settings=None):
        super().__init__()
        self.source, self.project, self.seconds, self.request_id = source, project, seconds, request_id
        self.geometry, self.settings = geometry, settings

    def run(self):
        try:
            path = thumbnail(self.source, self.project, self.seconds, self.geometry, self.settings)
            self.ready.emit(self.request_id, str(path))
        except Exception:
            self.ready.emit(self.request_id, '')


class RenderWorker(QThread):
    advanced = Signal(int, int, str)
    completed = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, config_path):
        super().__init__()
        self.config_path = Path(config_path)
        self.project = self.config_path.parent
        self.cancel_event = threading.Event()
        self.exit_code = 1

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            output = remix.run(self.config_path, progress=lambda done, total, status:
                               self.advanced.emit(done, total, status), cancel_event=self.cancel_event)
            self.exit_code = 0
            _smoke_log(f'OK\n{output}')
            self.completed.emit(str(output), str(self.project))
        except Exception as exc:
            _smoke_log(f'ERROR\n{exc}')
            self.failed.emit(str(exc), str(self.project))


class Window(QMainWindow):
    smoke_exit_requested = Signal(int)

    def __init__(self, project_override=None, settings=None, project_root=None):
        super().__init__()
        self.settings = settings or QSettings('Adi', APP_NAME)
        if project_root:
            self.project_root = Path(project_root)
        elif project_override and Path(project_override).drive.casefold() != 'g:':
            self.project_root = Path(project_override).parent
        else:
            self.project_root = _project_root(self.settings, project_override)
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.worker = None
        self.preview_worker = None
        self.preview_workers = []
        self.preview_request = ''
        self.preview_path = None
        self.current_output = None
        self.close_after_cancel = False
        self.close_after_preview = False
        self.media_metadata = {}
        self.output_geometry = None
        self._loading_project = False

        saved = str(self.settings.value('project_path', '', str) or '')
        if project_override:
            project = Path(project_override)
        elif saved and Path(saved).is_dir():
            project = Path(saved)
        else:
            legacy = QSettings('Creative Factory', 'Creative Factory').value('project_path', '', str)
            if legacy and Path(legacy).is_dir():
                old = Path(legacy)
                self.project_root = _project_root(self.settings, old)
                name = 'Dotychczasowy projekt' if old.name == 'Dropshipping Creative Factory' else old.name
                project = self.project_root / name
                try:
                    projects.migrate_project(old, project, remix.DEFAULTS)
                except (OSError, ValueError, shutil.Error):
                    project = old
                    QMessageBox.warning(self, 'Nie przeniesiono projektu', 'Stary projekt został zachowany w dotychczasowym folderze. Możesz dodać go do listy projektów.')
            else:
                project = self.project_root / 'Projekt 1'
        self.project = projects.create_project(project, remix.DEFAULTS)
        _register_project(self.settings, self.project)
        self.settings.setValue('projects_root', str(self.project_root))
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 850)
        self.setMinimumSize(980, 680)
        self.build_ui()
        self.load_project()

    def build_ui(self):
        self.setStyleSheet('''
            QMainWindow, QWidget#root {background:#121212}
            QWidget {color:#f5f5f5;font-family:"Segoe UI";font-size:13px}
            QLabel {background:transparent}
            QFrame#panel {background:#202022;border:1px solid #38383b;border-radius:14px}
            QLabel#title {font-size:24px;font-weight:750}
            QLabel#heading {font-size:16px;font-weight:700}
            QLabel#muted {color:#b7b7bd}
            QPushButton, QToolButton {background:#2b2b2e;color:white;border:1px solid #444449;border-radius:9px;padding:8px 12px;min-height:36px}
            QPushButton:hover, QToolButton:hover {background:#3b3b3f}
            QPushButton:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QListWidget:focus, QSlider:focus, QToolButton:focus {border:2px solid #f7a66e}
            QPushButton#primary {background:#f7a66e;color:#19130f;border:0;font-weight:750;font-size:15px;padding:13px}
            QPushButton#primary:disabled {background:#645447;color:#c9b9ae}
            QPushButton#danger {background:#402c2c;color:#fff;border:1px solid #664141}
            QToolButton {text-align:left;font-weight:700;font-size:14px;background:#29292c;border:0;border-radius:8px;padding:9px 10px}
            QToolButton:hover {background:#343438}
            QFrame#section {background:#202022;border:1px solid #38383b;border-radius:10px}
            QListWidget, QSpinBox, QDoubleSpinBox, QComboBox {background:#29292c;color:#f5f5f5;border:1px solid #414145;border-radius:8px;padding:7px 10px;min-height:36px}
            QComboBox::drop-down {width:32px;border:0}
            QCheckBox {min-height:36px;padding:4px 0}
            QCheckBox::indicator {width:18px;height:18px}
            QListWidget::item {padding:6px;border-bottom:1px solid #3a3a3c}
            QListWidget::item:selected {background:#594235}
            QProgressBar {background:#343436;border:0;border-radius:6px;text-align:center;min-height:14px}
            QProgressBar::chunk {background:#f7a66e;border-radius:6px}
            QSlider::groove:horizontal {height:5px;background:#3c3c40;border-radius:2px}
            QSlider::handle:horizontal {background:#f7a66e;width:14px;margin:-5px 0;border-radius:7px}
            QScrollArea {border:0;background:transparent}
        ''')
        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        brand = QLabel('Generator filmów Adi')
        brand.setObjectName('heading')
        header.addWidget(brand)
        header.addStretch()
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(190)
        self.project_combo.currentIndexChanged.connect(self.switch_project)
        header.addWidget(self.project_combo)
        self.new_project_button = QPushButton('Nowy projekt')
        self.new_project_button.clicked.connect(self.new_project)
        header.addWidget(self.new_project_button)
        self.rename_project_button = QPushButton('Zmień nazwę')
        self.rename_project_button.clicked.connect(self.rename_project)
        header.addWidget(self.rename_project_button)
        open_project = QPushButton('Otwórz folder')
        open_project.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project))))
        self.open_project_button = open_project
        header.addWidget(open_project)
        outer.addLayout(header)

        title = QLabel('Złóż filmy z własnych ujęć')
        title.setObjectName('title')
        outer.addWidget(title)
        subtitle = QLabel('Każdy produkt ma osobny projekt i własną bibliotekę materiałów.')
        subtitle.setObjectName('muted')
        outer.addWidget(subtitle)

        columns = QSplitter(Qt.Horizontal)
        columns.setChildrenCollapsible(False)
        outer.addWidget(columns, 1)

        library = self.panel('Materiały')
        library.setMinimumWidth(215)
        columns.addWidget(library)
        lib_layout = library.layout()
        self.lists = {}
        self.library_counts = {}
        self.add_buttons = []
        for role, label in ROLE_LABELS.items():
            row = QHBoxLayout()
            heading = QLabel(label)
            heading.setObjectName('heading')
            row.addWidget(heading)
            count = QLabel('0')
            count.setObjectName('muted')
            count.setAlignment(Qt.AlignCenter)
            count.setMinimumWidth(28)
            self.library_counts[role] = count
            row.addWidget(count)
            row.addStretch()
            add = QPushButton('Dodaj pliki')
            add.clicked.connect(lambda _=False, r=role: self.add_files(r))
            self.add_buttons.append(add)
            row.addWidget(add)
            lib_layout.addLayout(row)
            listing = DropList(f'Przeciągnij tutaj {label.lower()}\nlub kliknij „Dodaj pliki”.')
            listing.setMinimumHeight(125 if role == 'cta' else 155)
            listing.files_dropped.connect(lambda paths, r=role: self.import_files(r, paths))
            listing.itemSelectionChanged.connect(lambda r=role: self.selection_changed(r))
            lib_layout.addWidget(listing, 1)
            self.lists[role] = listing

        center = self.panel('Podgląd')
        center.setMinimumWidth(360)
        columns.addWidget(center)
        center_layout = center.layout()
        self.preview_image = QLabel('Wybierz ujęcie, aby zobaczyć podgląd')
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setMinimumHeight(310)
        self.preview_image.setStyleSheet('background:#2b2b2d;border:1px solid #3d3d40;border-radius:12px')
        center_layout.addWidget(self.preview_image, 1)
        self.preview_name = self.muted('Podgląd jest ładowany w tle')
        self.preview_name.setAlignment(Qt.AlignCenter)
        center_layout.addWidget(self.preview_name)
        self.open_preview_button = QPushButton('Odtwórz wybrany film')
        self.open_preview_button.clicked.connect(self.open_preview)
        center_layout.addWidget(self.open_preview_button)

        row = QHBoxLayout()
        heading = QLabel('Gotowe filmy')
        heading.setObjectName('heading')
        row.addWidget(heading)
        row.addStretch()
        self.batch_combo = QComboBox()
        self.batch_combo.setMinimumWidth(155)
        self.batch_combo.currentIndexChanged.connect(self.load_batch)
        row.addWidget(self.batch_combo)
        self.open_output_button = QPushButton('Otwórz folder serii')
        self.open_output_button.clicked.connect(self.open_output)
        row.addWidget(self.open_output_button)
        center_layout.addLayout(row)
        self.results = QListWidget()
        self.results.setMaximumHeight(130)
        self.results.itemClicked.connect(lambda item: self.show_preview(Path(item.data(Qt.UserRole))))
        self.results.itemDoubleClicked.connect(lambda item: QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.UserRole))))
        center_layout.addWidget(self.results)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(320)
        right_scroll.setMaximumWidth(380)
        self.right_scroll = right_scroll
        settings = QFrame()
        settings.setObjectName('panel')
        settings.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        set_layout = QVBoxLayout(settings)
        set_layout.setContentsMargins(10, 10, 10, 10)
        set_layout.setSpacing(10)
        right_scroll.setWidget(settings)
        columns.addWidget(right_scroll)
        self.settings_section = CollapsibleSection('Ustawienia filmu', expanded=True)
        set_layout.addWidget(self.settings_section)
        film_layout = self.settings_section.body_layout
        film_layout.addWidget(self.muted('Całe ujęcia są używane domyślnie.'))
        form = QFormLayout()
        form.setSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.ads = self._count_combo('Ile gotowych filmów złożyć w tej serii.')
        self.clips = self._count_combo('Ile różnych klipów wstawić między hookiem a zakończeniem.')
        self.format = QComboBox()
        self.format.addItem('Pionowy 9:16', 'portrait')
        self.format.addItem('Format materiałów', 'source')
        self.cta = QCheckBox('Dodaj zakończenie na końcu')
        form.addRow('Liczba filmów', self.ads)
        form.addRow('Klipy w filmie', self.clips)
        form.addRow('Format filmu', self.format)
        form.addRow('', self.cta)
        film_layout.addLayout(form)
        self.output_info = self.muted('Rozmiar i FPS dopasują się do materiałów.')
        self.output_info.setWordWrap(True)
        film_layout.addWidget(self.output_info)
        self.format.currentIndexChanged.connect(self.refresh_output_info)
        self.cta.toggled.connect(self.refresh_output_info)

        self.asset_section = CollapsibleSection('Wybrane ujęcie', expanded=False)
        set_layout.addWidget(self.asset_section)
        asset_layout = self.asset_section.body_layout
        self.asset_hint = self.muted('Wybierz plik z biblioteki, aby ustawić jego kadr lub skrócić go.')
        self.asset_hint.setWordWrap(True)
        asset_layout.addWidget(self.asset_hint)
        self.asset_status = self.muted('Wybierz ujęcie')
        asset_layout.addWidget(self.asset_status)
        self.asset_form = QFormLayout()
        self.trim_start = self._time_field()
        self.trim_end = self._time_field()
        self.fit = QComboBox(); self.fit.addItem('Wypełnij kadr', 'fill'); self.fit.addItem('Pokaż cały obraz', 'contain')
        self.asset_form.addRow('Od', self.trim_start)
        self.asset_form.addRow('Do', self.trim_end)
        self.asset_form.addRow('Kadr', self.fit)
        asset_layout.addLayout(self.asset_form)
        self.crop_x, self.crop_x_value = self._slider_row(asset_layout, 'Poziom')
        self.crop_y, self.crop_y_value = self._slider_row(asset_layout, 'Pion')
        for widget in (self.trim_start, self.trim_end, self.fit, self.crop_x, self.crop_y):
            widget.setEnabled(False)
        self.asset_update_timer = QTimer(self)
        self.asset_update_timer.setSingleShot(True)
        self.asset_update_timer.setInterval(220)
        self.asset_update_timer.timeout.connect(self._apply_asset_changes)
        self.trim_start.valueChanged.connect(self.schedule_asset_update)
        self.trim_end.valueChanged.connect(self.schedule_asset_update)
        self.fit.currentIndexChanged.connect(self.schedule_asset_update)
        self.crop_x.valueChanged.connect(self.schedule_asset_update)
        self.crop_y.valueChanged.connect(self.schedule_asset_update)
        set_layout.addStretch()
        self.generate = QPushButton('Generuj filmy')
        self.generate.setObjectName('primary')
        self.generate.clicked.connect(self.start_render)
        set_layout.addWidget(self.generate)
        self.cancel_button = QPushButton('Anuluj generowanie')
        self.cancel_button.setObjectName('danger')
        self.cancel_button.clicked.connect(self.cancel_render)
        self.cancel_button.hide()
        set_layout.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        set_layout.addWidget(self.progress)
        self.status = self.muted('Gotowe do pracy')
        self.status.setWordWrap(True)
        set_layout.addWidget(self.status)
        self.ads.currentIndexChanged.connect(self.schedule_config_save)
        self.clips.currentIndexChanged.connect(self.schedule_config_save)
        self.config_save_timer = QTimer(self)
        self.config_save_timer.setSingleShot(True)
        self.config_save_timer.setInterval(250)
        self.config_save_timer.timeout.connect(self.save_current_settings)
        columns.setSizes([260, 720, 315])

    def _count_combo(self, tooltip):
        combo = QComboBox()
        for value in [*range(1, 11), 15, 20, 30, 50, 100]:
            combo.addItem(str(value), value)
        combo.setToolTip(tooltip)
        return combo

    def _set_count_combo(self, combo, value):
        value = int(value)
        index = combo.findData(value)
        if index < 0:
            combo.insertItem(0, f'{value} (niestandardowa)', value)
            index = 0
        combo.setCurrentIndex(index)

    def _time_field(self):
        field = QDoubleSpinBox()
        field.setRange(0, 86400)
        field.setDecimals(2)
        field.setSingleStep(.1)
        field.setSuffix(' s')
        field.setButtonSymbols(QAbstractSpinBox.NoButtons)
        field.setMinimumWidth(120)
        return field

    def _slider_row(self, layout, title):
        row = QHBoxLayout()
        row.addWidget(self.muted(title))
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 1000)
        slider.setValue(500)
        value = QLabel('50%')
        value.setObjectName('muted')
        value.setMinimumWidth(42)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(lambda amount, label=value: label.setText(f'{amount / 10:g}%'))
        row.addWidget(slider, 1)
        row.addWidget(value)
        wrapper = QWidget()
        wrapper.setLayout(row)
        layout.addWidget(wrapper)
        return slider, value

    def panel(self, title):
        frame = QFrame()
        frame.setObjectName('panel')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        return frame

    def muted(self, text):
        label = QLabel(text)
        label.setObjectName('muted')
        return label

    def load_project(self):
        self._loading_project = True
        self.media_metadata.clear()
        try:
            config = read_config(self.project)
        except remix.RemixError as exc:
            QMessageBox.warning(self, 'Ustawienia projektu', str(exc))
            config = dict(remix.DEFAULTS)
        self.project_label = self.project.name
        self.settings.setValue('project_path', str(self.project))
        self._set_count_combo(self.ads, config['ads_count'])
        self._set_count_combo(self.clips, config['clips_per_ad'])
        self.format.setCurrentIndex(max(0, self.format.findData(config['output_mode'])))
        self.cta.setChecked(config['use_cta'])
        self._loading_project = False
        self.refresh()
        self.refresh_project_combo()
        self.refresh_output_info()

    def refresh_project_combo(self):
        paths = _register_project(self.settings, self.project)
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for path in paths:
            self.project_combo.addItem(Path(path).name, path)
        index = self.project_combo.findData(str(self.project.resolve()))
        self.project_combo.setCurrentIndex(max(index, 0))
        self.project_combo.blockSignals(False)

    def switch_project(self, index):
        if index < 0:
            return
        selected = Path(self.project_combo.itemData(index))
        if selected.resolve() == self.project.resolve():
            return
        if self.worker and self.worker.isRunning():
            return
        self.save_current_settings()
        self.project = projects.create_project(selected, remix.DEFAULTS)
        self.media_metadata.clear()
        self.load_project()

    def new_project(self):
        name, accepted = QInputDialog.getText(self, 'Nowy projekt', 'Nazwa produktu:')
        if not accepted:
            return
        try:
            safe = _safe_project_name(name)
            path = self.project_root / safe
            if path.exists():
                raise FileExistsError('Projekt o takiej nazwie już istnieje.')
            self.save_current_settings()
            projects.create_project(path, remix.DEFAULTS)
        except (ValueError, OSError, FileExistsError) as exc:
            QMessageBox.warning(self, 'Nie utworzono projektu', str(exc))
            return
        self.project = path
        self.load_project()

    def rename_project(self):
        name, accepted = QInputDialog.getText(self, 'Zmień nazwę projektu', 'Nowa nazwa:', text=self.project.name)
        if not accepted:
            return
        try:
            self.save_current_settings()
            target = self.project.parent / _safe_project_name(name)
            if target.exists() and target.resolve() != self.project.resolve():
                raise FileExistsError('Projekt o takiej nazwie już istnieje.')
            if target.resolve() != self.project.resolve():
                old = self.project
                old.rename(target)
                values = self.settings.value('projects', [], type=list)
                self.settings.setValue('projects', [str(target) if Path(value).resolve() == old.resolve() else value for value in values])
                self.project = target
            self.load_project()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Nie zmieniono nazwy', str(exc))

    def add_files(self, role):
        files, _ = QFileDialog.getOpenFileNames(self, f'Dodaj filmy do: {ROLE_LABELS[role]}', str(Path.home()), VIDEO_FILTER)
        self.import_files(role, [Path(path) for path in files])

    def import_files(self, role, paths):
        target_dir = projects.role_dir(self.project, role)
        problems = []
        for source in paths:
            if not source.is_file() or source.suffix.lower() not in remix.EXTENSIONS:
                problems.append(source.name)
                continue
            destination = target_dir / source.name
            try:
                if source.resolve() == destination.resolve():
                    continue
                suffix = 2
                while destination.exists():
                    destination = target_dir / f'{source.stem} ({suffix}){source.suffix}'
                    suffix += 1
                shutil.copy2(source, destination)
            except OSError:
                problems.append(source.name)
        self.refresh()
        if problems:
            QMessageBox.warning(self, 'Nie dodano części plików', 'Sprawdź format lub dostęp do plików:\n' + '\n'.join(problems[:10]))

    def refresh(self):
        for role, listing in self.lists.items():
            listing.clear()
            folder = projects.role_dir(self.project, role)
            for source in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
                if source.is_file() and source.suffix.lower() in remix.EXTENSIONS:
                    item = QListWidgetItem(source.name)
                    item.setData(Qt.UserRole, str(source))
                    listing.addItem(item)
            self.library_counts[role].setText(str(listing.count()))
        self.refresh_results()
        self.refresh_output_info()

    def selected_asset(self):
        for role, listing in self.lists.items():
            selected = listing.selectedItems()
            if selected:
                return role, Path(selected[0].data(Qt.UserRole))
        return None, None

    def _metadata_for(self, source):
        source = Path(source)
        stat = source.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns)
        cached = self.media_metadata.get(source)
        if cached and cached.get('_fingerprint') == fingerprint:
            return cached
        meta = remix.probe(source, remix.tool('ffprobe'))
        meta['_fingerprint'] = fingerprint
        self.media_metadata[source] = meta
        return meta

    def selection_changed(self, changed_role=None):
        if changed_role in self.lists:
            for role, listing in self.lists.items():
                if role != changed_role and listing.selectedItems():
                    listing.blockSignals(True)
                    listing.clearSelection()
                    listing.blockSignals(False)
        role, source = self.selected_asset()
        enabled = source is not None
        for widget in (self.trim_start, self.trim_end, self.fit, self.crop_x, self.crop_y):
            widget.setEnabled(enabled)
        if not source:
            self.asset_section.setExpanded(False)
            self.asset_status.setText('Wybierz ujęcie')
            self.asset_hint.setText('Wybierz plik z biblioteki, aby ustawić jego kadr lub skrócić go.')
            return
        self.asset_section.setExpanded(True)
        self.preview_path = source
        key = _settings_key(self.project, source)
        settings = _read_assets(self.project).get(key, {})
        try:
            meta = self._metadata_for(source)
            self.trim_start.blockSignals(True)
            self.trim_end.blockSignals(True)
            self.trim_start.setMaximum(meta['duration'])
            self.trim_end.setMaximum(meta['duration'])
            trim = settings.get('trim') or {'start': 0, 'end': meta['duration']}
            self.trim_start.setValue(float(trim['start']))
            self.trim_end.setValue(float(trim['end']))
            self.trim_start.blockSignals(False)
            self.trim_end.blockSignals(False)
            for widget in (self.fit, self.crop_x, self.crop_y):
                widget.blockSignals(True)
            self.fit.setCurrentIndex(max(0, self.fit.findData(settings.get('fit', 'fill'))))
            self.crop_x.setValue(round(float(settings.get('x', .5)) * 1000))
            self.crop_y.setValue(round(float(settings.get('y', .5)) * 1000))
            for widget in (self.fit, self.crop_x, self.crop_y):
                widget.blockSignals(False)
            self.asset_status.setText('Gotowe do edycji')
            self.asset_hint.setText(f'{ROLE_LABELS[role]} · {meta["width"]}×{meta["height"]} · {meta["duration"]:.2f} s')
        except (OSError, ValueError, remix.RemixError, KeyError) as exc:
            self.asset_status.setText('Nie można odczytać ujęcia')
            self.asset_hint.setText(f'Nie można odczytać filmu: {exc}')
        self.show_preview(source)

    def refresh_selected_frame(self):
        role, source = self.selected_asset()
        if source:
            self._apply_asset_changes()

    def schedule_asset_update(self, *_):
        if not self._loading_project:
            self.asset_update_timer.start()

    def _apply_asset_changes(self):
        self.save_selected_asset()
        role, source = self.selected_asset()
        if source:
            self.show_preview(source, self.trim_start.value(), self.output_geometry,
                              {'fit': self.fit.currentData(), 'x': self.crop_x.value()/1000, 'y': self.crop_y.value()/1000})

    def save_selected_asset(self, *_):
        role, source = self.selected_asset()
        if not source:
            return
        meta = self.media_metadata.get(source)
        if meta is None:
            self.asset_status.setText('Nie zapisano')
            return
        start, end = self.trim_start.value(), self.trim_end.value()
        if end <= start:
            self.asset_status.setText('Nie zapisano')
            self.asset_hint.setText('Koniec ujęcia musi być późniejszy niż jego początek.')
            return
        self.asset_status.setText('Zapisuję…')
        settings = _read_assets(self.project)
        key = _settings_key(self.project, source)
        full_length = start < .01 and abs(end - meta['duration']) < .02
        settings[key] = {
            'trim': None if full_length else {'start': start, 'end': end},
            'fit': self.fit.currentData(),
            'x': round(self.crop_x.value() / 1000, 3),
            'y': round(self.crop_y.value() / 1000, 3),
        }
        try:
            _write_assets(self.project, settings)
            self.asset_status.setText('Zapisano')
            self.asset_hint.setText(f'{ROLE_LABELS[role]} · ustawienia zapisane')
        except OSError as exc:
            self.asset_status.setText('Nie zapisano')
            QMessageBox.warning(self, 'Nie zapisano ustawień ujęcia', str(exc))

    def refresh_output_info(self, *_):
        if not hasattr(self, 'output_info'):
            return
        try:
            use_cta = self.cta.isChecked()
            pools, metadata = remix.preview_inventory(self.project, remix.tool('ffprobe'), use_cta=use_cta)
            sources = [path for values in pools.values() for path in values]
            sizes = {path: (metadata[path]['width'], metadata[path]['height']) for path in sources}
            geometry = remix.choose_output_geometry(sizes, self.format.currentData())
            self.output_geometry = geometry
            fps = remix.choose_output_fps(metadata[path]['fps'] for path in sources)
            for path, meta in metadata.items():
                stat = path.stat()
                meta['_fingerprint'] = (stat.st_size, stat.st_mtime_ns)
                self.media_metadata[path] = meta
            self.output_info.setText(f'Rozmiar serii: {geometry[0]}×{geometry[1]}\nMaksymalny FPS materiałów: {float(fps):g}; każdy film dobiera FPS do użytych ujęć (maks. 60).')
        except (OSError, remix.RemixError, ValueError) as exc:
            self.output_geometry = None
            self.output_info.setText(str(exc))
        if not self._loading_project:
            self.save_current_settings()

    def schedule_config_save(self, *_):
        if not self._loading_project:
            self.config_save_timer.start()

    def save_current_settings(self):
        if self._loading_project or not hasattr(self, 'ads') or not self.project.exists():
            return
        try:
            save_config(self.project, dict(ads_count=int(self.ads.currentData()), clips_per_ad=int(self.clips.currentData()),
                                           output_mode=self.format.currentData(), use_cta=self.cta.isChecked()))
        except (OSError, remix.RemixError):
            pass

    def refresh_results(self):
        self.results.clear()
        root = projects.output_dir(self.project)
        batches = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name, reverse=True)
        loose_videos = sorted([*root.glob('Film_*.mp4'), *root.glob('AD_*.mp4')], key=lambda item: item.name.casefold())
        selected = self.batch_combo.currentData() if hasattr(self, 'batch_combo') else None
        if hasattr(self, 'batch_combo'):
            self.batch_combo.blockSignals(True)
            self.batch_combo.clear()
            for path in batches:
                self.batch_combo.addItem(path.name, str(path))
            if loose_videos:
                self.batch_combo.addItem('Wcześniejsze wyniki (folder główny)', str(root))
            index = self.batch_combo.findData(selected) if selected else 0
            self.batch_combo.setCurrentIndex(max(index, 0) if self.batch_combo.count() else -1)
            self.batch_combo.blockSignals(False)
        self.current_output = Path(self.batch_combo.currentData()) if self.batch_combo.currentData() else root
        self.load_batch()

    def load_batch(self, *_):
        if not hasattr(self, 'results'):
            return
        self.results.clear()
        path = Path(self.batch_combo.currentData()) if self.batch_combo.currentData() else None
        self.current_output = path or projects.output_dir(self.project)
        if path and path.is_dir():
            videos = sorted([*path.glob('Film_*.mp4'), *path.glob('AD_*.mp4')], key=lambda item: item.name.casefold())
            for video in videos:
                item = QListWidgetItem(video.name)
                item.setData(Qt.UserRole, str(video))
                self.results.addItem(item)

    def show_preview(self, path, seconds=.2, geometry=None, settings=None):
        path = Path(path)
        self.preview_path = path
        self.preview_name.setText(f'Ładowanie: {path.name}')
        self.preview_image.setText('Przygotowuję podgląd…')
        request = f'{path.resolve()}|{seconds}'
        self.preview_request = request
        self.preview_worker = ThumbnailWorker(path, self.project, seconds, request, geometry, settings)
        self.preview_workers.append(self.preview_worker)
        self.preview_worker.ready.connect(self.show_thumbnail)
        self.preview_worker.finished.connect(lambda w=self.preview_worker: self._forget_preview_worker(w))
        self.preview_worker.start()

    def _forget_preview_worker(self, worker):
        try:
            self.preview_workers.remove(worker)
        except ValueError:
            pass
        if self.close_after_preview and not self.preview_workers:
            QTimer.singleShot(0, self.close)

    def show_thumbnail(self, request, path):
        if request != self.preview_request:
            return
        if path and Path(path).is_file():
            pixmap = QPixmap(path)
            self.preview_image.setPixmap(pixmap.scaled(self.preview_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview_name.setText(self.preview_path.name)
        else:
            self.preview_image.setText('Nie można przygotować podglądu. Otwórz film, aby sprawdzić go ręcznie.')

    def open_preview(self):
        if self.preview_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.preview_path)))

    def open_output(self):
        self.current_output.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_output)))

    def refresh_project_combo_after_creation(self):
        paths = _register_project(self.settings, self.project)
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for path in paths:
            self.project_combo.addItem(Path(path).name, path)
        self.project_combo.setCurrentIndex(self.project_combo.findData(str(self.project.resolve())))
        self.project_combo.blockSignals(False)

    def start_render(self):
        _smoke_log('START RENDER')
        if self.worker and self.worker.isRunning():
            return
        self.save_selected_asset()
        try:
            self.save_current_settings()
            remix.tool('ffmpeg')
            remix.tool('ffprobe')
        except (OSError, remix.RemixError) as exc:
            _smoke_log(f'START ERROR\n{exc}')
            QMessageBox.warning(self, 'Nie można rozpocząć', str(exc))
            return
        self.worker = RenderWorker(self.project / 'config.json')
        self.worker.advanced.connect(self.on_progress)
        self.worker.completed.connect(self.on_complete)
        self.worker.failed.connect(self.on_error)
        self.worker.finished.connect(self.render_finished)
        self.set_rendering(True)
        self.worker.start()

    def set_rendering(self, active):
        self.project_combo.setEnabled(not active)
        for widget in (self.new_project_button, self.rename_project_button, self.open_project_button,
                       self.generate, self.format, self.ads, self.clips, self.cta,
                       self.batch_combo, self.open_output_button, self.open_preview_button,
                       self.trim_start, self.trim_end, self.fit,
                       self.crop_x, self.crop_y, self.settings_section, self.asset_section,
                       *self.add_buttons, *self.lists.values()):
            widget.setEnabled(not active)
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(active)
        if active:
            self.progress.setValue(0)
            self.status.setText('Przygotowuję filmy…')

    def cancel_render(self):
        if self.worker and self.worker.isRunning():
            self.cancel_button.setEnabled(False)
            self.status.setText('Anuluję… Gotowe filmy pozostaną w projekcie.')
            self.worker.cancel()

    def render_finished(self):
        self.set_rendering(False)
        if self.close_after_cancel:
            QTimer.singleShot(0, self.close)

    def on_progress(self, done, total, status):
        self.progress.setValue(round(done * 100 / total))
        self.status.setText(status)

    def on_complete(self, output, project):
        self.project = Path(project)
        self.current_output = Path(output)
        self.refresh_results()
        self.status.setText(f'Gotowe. Zapisano {self.results.count()} filmów w wybranej serii.')
        if self.results.count() and os.environ.get('CREATIVE_FACTORY_SMOKE_RENDER') != '1':
            self.show_preview(Path(self.results.item(0).data(Qt.UserRole)))

    def on_error(self, message, project):
        self.project = Path(project)
        self.refresh_results()
        self.status.setText('Generowanie anulowane.' if 'anulowano' in message.casefold() else 'Generowanie przerwane. Sprawdź komunikat.')
        if os.environ.get('CREATIVE_FACTORY_SMOKE_RENDER') != '1' and 'anulowano' not in message.casefold():
            QMessageBox.critical(self, 'Nie udało się wygenerować filmów', message)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(self, 'Trwa generowanie', 'Anulować generowanie i zamknąć aplikację? Gotowe filmy zostaną zachowane.')
            if answer == QMessageBox.StandardButton.Yes:
                self.close_after_cancel = True
                self.worker.cancel()
            event.ignore()
        elif self.preview_workers:
            self.close_after_preview = True
            event.ignore()
        else:
            self.save_current_settings()
            super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument('--project', type=Path, help='Folder wybranego projektu')
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName('Adi')
    try:
        window = Window(args.project)
        window.show()
    except Exception as exc:
        _smoke_log(f'APP INIT ERROR\n{exc}')
        QMessageBox.critical(None, APP_NAME, f'Nie można uruchomić aplikacji:\n{exc}')
        return 1
    _smoke_log('APP READY')
    if os.environ.get('CREATIVE_FACTORY_SMOKE_EXIT') == '1':
        QTimer.singleShot(1000, app.quit)
    if os.environ.get('CREATIVE_FACTORY_SMOKE_RENDER') == '1':
        window.smoke_exit_requested.connect(app.exit, Qt.ConnectionType.QueuedConnection)
        def render_smoke():
            _smoke_log('RENDER TRIGGERED')
            window.start_render()
            if window.worker:
                window.worker.finished.connect(lambda: window.smoke_exit_requested.emit(window.worker.exit_code))
            else:
                _smoke_log('ERROR\nRender worker was not started')
                app.exit(2)
        QTimer.singleShot(100, render_smoke)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

