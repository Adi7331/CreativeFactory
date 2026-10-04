"""Portable, offline desktop interface for the Creative Remix Engine."""
import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal, QStandardPaths, QTimer
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

from creative_factory import remix

APP_RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
CONFIG_DEFAULT = APP_RESOURCES / 'config.default.json'
VIDEO_FILTER = 'Filmy (*.mp4 *.mov *.mkv *.avi *.webm *.m4v)'


def create_project(path):
    path.mkdir(parents=True, exist_ok=True)
    for folder in ('hooks', 'clips', 'cta', 'output', '_work'):
        (path / folder).mkdir(exist_ok=True)
    config = path / 'config.json'
    if not config.exists():
        shutil.copyfile(CONFIG_DEFAULT, config)
    return path


def read_config(project):
    try:
        raw = json.loads((project / 'config.json').read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict):
            raise ValueError('plik musi zawierać ustawienia w formacie JSON')
        config = {**remix.DEFAULTS, **raw}
        remix.validate_config(config)
        return config
    except (OSError, ValueError, remix.RemixError) as exc:
        raise remix.RemixError(f'Nie można wczytać ustawień projektu: {exc}') from exc


def save_config(project, updates):
    config = read_config(project)
    config.update(updates)
    remix.validate_config(config)
    target = project / 'config.json'
    temp = project / 'config.json.tmp'
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(target)


def ffmpeg_path():
    return remix.tool('ffmpeg')


def thumbnail(source, project):
    cache = project / '_work' / 'thumbnails'
    cache.mkdir(parents=True, exist_ok=True)
    stat = source.stat()
    import hashlib
    key = hashlib.sha256(f'{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}'.encode('utf-8')).hexdigest()[:24]
    destination = cache / f'{key}.jpg'
    if not destination.exists():
        try:
            subprocess.run([ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                            '-ss', '0.3', '-i', str(source), '-frames:v', '1',
                            '-vf', 'scale=280:300:force_original_aspect_ratio=decrease',
                            '-y', str(destination)], capture_output=True, timeout=25, check=True)
        except (OSError, subprocess.SubprocessError, remix.RemixError):
            return None
    return QPixmap(str(destination))


class DropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)

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


class RenderWorker(QThread):
    advanced = Signal(int, int, str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, config_path):
        super().__init__()
        self.config_path = config_path
        self.exit_code = 1

    def run(self):
        try:
            output = remix.run(self.config_path, progress=lambda done, total, status:
                               self.advanced.emit(done, total, status))
            self.exit_code = 0
            self.completed.emit(str(output))
        except Exception as exc:
            self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(self, project_override=None):
        super().__init__()
        self.settings = QSettings('Creative Factory', 'Creative Factory')
        saved = self.settings.value('project_path', '', str)
        documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        self.project = create_project(Path(project_override) if project_override else (Path(saved) if saved else Path(documents) / 'Creative Factory'))
        self.worker = None
        self.current_output = None
        self.setWindowTitle('Creative Factory')
        self.resize(1440, 920)
        self.setMinimumSize(1050, 700)
        self.build_ui()
        self.load_project()

    def build_ui(self):
        self.setStyleSheet('''
            QMainWindow, QWidget#root {background:#121212}
            QWidget {color:#f5f5f5;font-family:"Segoe UI";font-size:13px}
            QLabel {background:transparent}
            QFrame#panel {background:#202022;border:1px solid #38383b;border-radius:16px}
            QLabel#title {font-size:25px;font-weight:750}
            QLabel#heading {font-size:17px;font-weight:700}
            QLabel#muted {color:#b7b7bd}
            QPushButton {background:#2b2b2e;color:white;border:1px solid #444449;border-radius:9px;padding:9px 13px}
            QPushButton:hover {background:#3b3b3f}
            QPushButton#primary {background:#f7a66e;color:#19130f;border:0;font-weight:750;font-size:16px;padding:14px}
            QPushButton#primary:disabled {background:#645447;color:#c9b9ae}
            QListWidget, QSpinBox, QDoubleSpinBox, QComboBox {background:#29292c;color:#f5f5f5;border:1px solid #414145;border-radius:9px;padding:6px}
            QListWidget::item {padding:7px;border-bottom:1px solid #3a3a3c}
            QListWidget::item:selected {background:#594235}
            QProgressBar {background:#343436;border:0;border-radius:6px;text-align:center;min-height:14px}
            QProgressBar::chunk {background:#f7a66e;border-radius:6px}
            QScrollArea {border:0;background:transparent}
        ''')
        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 22)
        outer.setSpacing(18)
        header = QHBoxLayout()
        brand = QLabel('◩  Creative Factory')
        brand.setObjectName('heading')
        header.addWidget(brand)
        header.addStretch()
        self.project_label = QLabel()
        self.project_label.setObjectName('muted')
        header.addWidget(self.project_label)
        choose = QPushButton('Zmień projekt')
        choose.clicked.connect(self.choose_project)
        header.addWidget(choose)
        outer.addLayout(header)
        title = QLabel('Zobacz swoje ujęcia. Złóż serię.')
        title.setObjectName('title')
        outer.addWidget(title)
        subtitle = QLabel('Dodaj hooki i klipy, ustaw długość, potem wygeneruj filmy pionowe.')
        subtitle.setObjectName('muted')
        outer.addWidget(subtitle)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        outer.addLayout(columns, 1)

        library = self.panel('Biblioteka ujęć')
        library.setMinimumWidth(245)
        library.setMaximumWidth(310)
        columns.addWidget(library)
        lib_layout = library.layout()
        self.lists = {}
        for role, label in (('hooks', 'Hooki'), ('clips', 'Klipy'), ('cta', 'CTA — opcjonalne')):
            row = QHBoxLayout()
            heading = QLabel(label)
            heading.setObjectName('heading')
            row.addWidget(heading)
            row.addStretch()
            add = QPushButton('+ Dodaj')
            add.clicked.connect(lambda _=False, r=role: self.add_files(r))
            row.addWidget(add)
            lib_layout.addLayout(row)
            listing = DropList()
            listing.setMinimumHeight(100 if role == 'cta' else 155)
            listing.files_dropped.connect(lambda paths, r=role: self.import_files(r, paths))
            listing.itemClicked.connect(lambda item: self.show_preview(Path(item.data(Qt.UserRole))))
            lib_layout.addWidget(listing, 1)
            self.lists[role] = listing
        lib_layout.addWidget(self.muted('Przeciągnij pliki do odpowiedniej listy.\nOryginały zostaną skopiowane do projektu.'))

        center = self.panel('Podgląd')
        columns.addWidget(center, 1)
        center_layout = center.layout()
        self.preview_image = QLabel('Wybierz ujęcie lub gotowy film')
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setMinimumHeight(390)
        self.preview_image.setStyleSheet('background:#2b2b2d;border:1px solid #3d3d40;border-radius:12px')
        center_layout.addWidget(self.preview_image, 1)
        self.preview_path = None
        self.preview_name = self.muted('Podgląd pierwszej klatki')
        self.preview_name.setAlignment(Qt.AlignCenter)
        center_layout.addWidget(self.preview_name)
        view = QPushButton('Otwórz wybrany film')
        view.clicked.connect(self.open_preview)
        center_layout.addWidget(view)
        row = QHBoxLayout()
        heading = QLabel('Gotowe filmy')
        heading.setObjectName('heading')
        row.addWidget(heading)
        row.addStretch()
        out = QPushButton('Otwórz folder wyników')
        out.clicked.connect(self.open_output)
        row.addWidget(out)
        center_layout.addLayout(row)
        self.results = QListWidget()
        self.results.setMaximumHeight(155)
        self.results.itemClicked.connect(lambda item: self.show_preview(Path(item.data(Qt.UserRole))))
        self.results.itemDoubleClicked.connect(lambda item: QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.UserRole))))
        center_layout.addWidget(self.results)

        settings = self.panel('Ustawienia filmu')
        settings.setMinimumWidth(280)
        settings.setMaximumWidth(330)
        columns.addWidget(settings)
        set_layout = settings.layout()
        set_layout.addWidget(self.muted('Najważniejsze opcje pod ręką.'))
        form = QFormLayout()
        form.setSpacing(15)
        self.ads = QSpinBox(); self.ads.setRange(1, 10000)
        self.clips = QSpinBox(); self.clips.setRange(1, 100)
        self.hook_seconds = QDoubleSpinBox(); self.hook_seconds.setRange(.1, 600); self.hook_seconds.setSingleStep(.1); self.hook_seconds.setSuffix(' s')
        self.clip_seconds = QDoubleSpinBox(); self.clip_seconds.setRange(.1, 600); self.clip_seconds.setSingleStep(.1); self.clip_seconds.setSuffix(' s')
        self.cta_seconds = QDoubleSpinBox(); self.cta_seconds.setRange(.1, 600); self.cta_seconds.setSingleStep(.1); self.cta_seconds.setSuffix(' s')
        self.fit = QComboBox(); self.fit.addItem('Wypełnij kadr 9:16', 'crop'); self.fit.addItem('Zachowaj cały obraz', 'pad')
        self.cta = QCheckBox('Dodaj CTA na końcu')
        form.addRow('Liczba filmów', self.ads)
        form.addRow('Klipy w filmie', self.clips)
        form.addRow('Długość hooka', self.hook_seconds)
        form.addRow('Długość klipu', self.clip_seconds)
        form.addRow('Kadrowanie', self.fit)
        form.addRow('', self.cta)
        form.addRow('Długość CTA', self.cta_seconds)
        set_layout.addLayout(form)
        self.cta.toggled.connect(self.cta_seconds.setEnabled)
        set_layout.addStretch()
        set_layout.addWidget(self.muted('Pion 1080 × 1920 • 30 kl./s • MP4'))
        self.generate = QPushButton('Generuj filmy')
        self.generate.setObjectName('primary')
        self.generate.clicked.connect(self.start_render)
        set_layout.addWidget(self.generate)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        set_layout.addWidget(self.progress)
        self.status = self.muted('Gotowe do pracy')
        self.status.setWordWrap(True)
        set_layout.addWidget(self.status)

    def panel(self, title):
        frame = QFrame()
        frame.setObjectName('panel')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(13)
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        return frame

    def muted(self, text):
        label = QLabel(text)
        label.setObjectName('muted')
        return label

    def load_project(self):
        try:
            config = read_config(self.project)
        except remix.RemixError as exc:
            QMessageBox.warning(self, 'Ustawienia projektu', str(exc))
            return
        self.project_label.setText(f'Projekt: {self.project.name}')
        self.project_label.setToolTip(str(self.project))
        self.ads.setValue(config['ads_count'])
        self.clips.setValue(config['clips_per_ad'])
        self.hook_seconds.setValue(config['hook_seconds'])
        self.clip_seconds.setValue(config['clip_seconds'])
        self.cta_seconds.setValue(config['cta_seconds'])
        self.cta.setChecked(config['use_cta'])
        self.fit.setCurrentIndex(self.fit.findData(config['fit']))
        self.refresh()

    def choose_project(self):
        selected = QFileDialog.getExistingDirectory(self, 'Wybierz istniejący projekt lub pusty folder', str(self.project))
        if selected:
            self.project = create_project(Path(selected))
            self.settings.setValue('project_path', str(self.project))
            self.load_project()

    def add_files(self, role):
        files, _ = QFileDialog.getOpenFileNames(self, 'Dodaj filmy', str(Path.home()), VIDEO_FILTER)
        self.import_files(role, [Path(path) for path in files])

    def import_files(self, role, paths):
        target_dir = self.project / role
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
            for source in sorted((self.project / role).iterdir(), key=lambda p: p.name.casefold()):
                if source.is_file() and source.suffix.lower() in remix.EXTENSIONS:
                    item = QListWidgetItem(source.name)
                    item.setData(Qt.UserRole, str(source))
                    listing.addItem(item)
        self.refresh_results()

    def refresh_results(self):
        self.results.clear()
        batches = sorted((p for p in (self.project / 'output').iterdir() if p.is_dir()), reverse=True)
        files = sorted(batches[0].glob('AD_*.mp4')) if batches else []
        self.current_output = batches[0] if batches else None
        for path in files:
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            self.results.addItem(item)

    def show_preview(self, path):
        self.preview_path = path
        self.preview_name.setText(path.name)
        picture = thumbnail(path, self.project)
        if picture and not picture.isNull():
            self.preview_image.setPixmap(picture.scaled(330, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.preview_image.setText('Nie można wyświetlić miniatury')

    def open_preview(self):
        if self.preview_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.preview_path)))

    def open_output(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_output or self.project / 'output')))

    def start_render(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            save_config(self.project, dict(ads_count=self.ads.value(), clips_per_ad=self.clips.value(),
                                           hook_seconds=self.hook_seconds.value(), clip_seconds=self.clip_seconds.value(),
                                           cta_seconds=self.cta_seconds.value(), fit=self.fit.currentData(),
                                           use_cta=self.cta.isChecked()))
            remix.tool('ffmpeg')
            remix.tool('ffprobe')
        except (OSError, remix.RemixError) as exc:
            QMessageBox.warning(self, 'Nie można rozpocząć', str(exc))
            return
        self.generate.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText('Przygotowuję materiały…')
        self.worker = RenderWorker(self.project / 'config.json')
        self.worker.advanced.connect(self.on_progress)
        self.worker.completed.connect(self.on_complete)
        self.worker.failed.connect(self.on_error)
        self.worker.finished.connect(lambda: self.generate.setEnabled(True))
        self.worker.start()

    def on_progress(self, done, total, status):
        self.progress.setValue(round(done * 100 / total))
        self.status.setText(status)

    def on_complete(self, output):
        self.current_output = Path(output)
        self.refresh_results()
        self.status.setText(f'Gotowe: {self.results.count()} filmów. Otwórz folder wyników.')
        if self.results.count():
            self.show_preview(Path(self.results.item(0).data(Qt.UserRole)))

    def on_error(self, message):
        self.status.setText('Generowanie przerwane. Sprawdź komunikat.')
        self.refresh_results()
        if os.environ.get('CREATIVE_FACTORY_SMOKE_RENDER') == '1':
            return
        else:
            QMessageBox.critical(self, 'Błąd generowania', message)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, 'Trwa generowanie', 'Poczekaj na zakończenie. Gotowe filmy pozostaną w projekcie.')
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description='Creative Factory')
    parser.add_argument('--project', type=Path, help='Folder projektu')
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName('Creative Factory')
    app.setOrganizationName('Creative Factory')
    try:
        window = Window(args.project)
        window.show()
    except Exception as exc:
        QMessageBox.critical(None, 'Creative Factory', f'Nie można uruchomić aplikacji:\n{exc}')
        return 1
    if os.environ.get('CREATIVE_FACTORY_SMOKE_EXIT') == '1':
        QTimer.singleShot(1000, app.quit)
    if os.environ.get('CREATIVE_FACTORY_SMOKE_RENDER') == '1':
        def render_smoke():
            window.start_render()
            if window.worker:
                window.worker.finished.connect(lambda: app.exit(window.worker.exit_code))
            else:
                app.exit(2)
        QTimer.singleShot(100, render_smoke)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

