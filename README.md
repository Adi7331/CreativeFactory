# Creative Factory — Windows

Lokalny program do składania krótkich filmów pionowych z hooków, środkowych klipów i opcjonalnych zakończeń CTA. Wynik to MP4 1080 × 1920, 30 klatek/s. Program działa bez konta i bez internetu.

## Dla użytkownika

1. Pobierz plik `CreativeFactory-windows-x64.zip` z sekcji **Releases** na GitHubie. Przycisk **Code → Download ZIP** pobiera tylko kod źródłowy.
2. Rozpakuj całe archiwum do wybranego folderu. Uruchom `CreativeFactory.exe`. Nie przenoś samego EXE bez folderu `_internal`.
3. Aplikacja utworzy projekt `Dokumenty\Creative Factory`. W programie możesz kliknąć **Zmień projekt** i wskazać istniejący projekt.
4. Dodaj hooki i klipy. CTA jest wyłączone domyślnie. Wybierz liczbę filmów, długości i sposób kadrowania. Kliknij **Generuj filmy**.
5. Wyniki znajdziesz w `output\data_i_godzina` wewnątrz projektu. `manifest.csv` zapisuje dokładny skład filmów. Dwuklik na gotowym filmie otwiera domyślny odtwarzacz Windows.

Domyślnie program składa pięć filmów z jednego hooka i czterech klipów na film, bez CTA. Hook jest pierwszy, CTA (gdy włączone) ostatnie, a klipy w środku nie powtarzają się w jednym filmie. Liczba możliwych unikalnych wariantów zależy od liczby plików. Jeśli jest ich za mało, program pokaże komunikat.

Filmy, ustawienia, miniatury i pliki robocze mieszkają w projekcie poza folderem aplikacji. Możesz rozpakować nową wersję w innym miejscu i nadal używać tego samego projektu. Program nie nadpisuje materiałów wejściowych.

## Dla budujących wydanie

Wymagane są Windows x64, Python, dostęp do PyPI oraz oficjalne pliki `ffmpeg.exe` i `ffprobe.exe`. Polecenie:

```powershell
.\build.ps1 -FfmpegDir 'C:\sciezka\do\ffmpeg' -OutputDir 'dist'
```

Skrypt używa PyInstaller w trybie `onedir`. Zawartość `dist\CreativeFactory` należy spakować jako `CreativeFactory-windows-x64.zip` i dodać do GitHub **Releases**. Sprawdź licencję konkretnej kompilacji FFmpeg przed publikacją; trzymaj jej pliki licencyjne w paczce. Kod projektu nie zawiera cudzych materiałów wideo ani FFmpeg.

Kod aplikacji jest w `main.py` i `creative_factory/remix.py`. Domyślne ustawienia są w `config.default.json`. Wersja wydania nie zawiera Pythona jako oddzielnej instalacji; runtime jest wewnątrz paczki EXE.

