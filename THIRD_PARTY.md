# Składniki dołączone do wydania

- **FFmpeg/ffprobe 9.0.2**, kompilacja `essentials_build-www.gyan.dev`. Ma włączone `--enable-gpl` i deklaruje GPL v3. `FFmpeg-README.txt` zawiera opcje kompilacji, a `FFmpeg-SOURCE.txt` wskazuje dokładną rewizję i archiwum źródeł. Pełny tekst GPL v3 znajduje się w `FFmpeg-LICENSE.txt`.
- **PySide6/Qt 6.9.2** — deklaracja pakietu PyPI: `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. Biblioteki Qt są w paczce jako oddzielne pliki DLL. Teksty LGPL v3 i GPL v3 są dołączone. Projekt źródłowy: https://code.qt.io/cgit/pyside/pyside-setup.git/ i https://code.qt.io/cgit/qt/qtbase.git/.
- **Python 3.12** — interpreter dołączony przez PyInstaller. Licencja Python Software Foundation znajduje się w `Python-LICENSE.txt`.

Przed publicznym wydaniem trzeba sprawdzić ostateczny zestaw bibliotek i wymagania ich licencji dla konkretnej paczki. Projekt nie zawiera własnych filmów użytkownika.
Każde wydanie powinno zachować pliki licencyjne i odnośniki do źródeł składników dołączonych do paczki.
