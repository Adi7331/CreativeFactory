# Generator filmów Adi — Windows

Lokalna aplikacja do składania filmów z własnych materiałów. Hook trafia na początek, klipy do środka, a opcjonalne zakończenie na koniec. Program nie korzysta z AI ani z kont internetowych.

## Pobranie i uruchomienie

1. Pobierz `Generator-filmow-Adi-windows-x64.zip` z sekcji **Releases** na GitHubie. Przycisk **Code → Download ZIP** pobiera kod źródłowy, a nie gotową aplikację.
2. Rozpakuj cały ZIP i uruchom `GeneratorFilmowAdi.exe`. Nie przenoś samego EXE bez folderu `_internal`.
3. Dodaj filmy do bibliotek **Hooki**, **Klipy** i opcjonalnie **Zakończenia**. Możesz przeciągnąć pliki z Eksploratora Windows. Aplikacja kopiuje je do aktywnego projektu; oryginały pozostają na miejscu.
4. Wybierz liczbę filmów, liczbę klipów w każdym filmie i format. Zakończenie jest domyślnie wyłączone. Cały plik źródłowy jest używany domyślnie; ręczne „Od/Do” jest dostępne po wybraniu konkretnego ujęcia.
5. Kliknij **Generuj filmy**. Wyniki znajdziesz w `Gotowe filmy\data_i_godzina`. Plik `manifest.csv` pokazuje użyte ujęcia, kadrowanie, długości, rozdzielczość i FPS.

Przy pierwszym uruchomieniu nowy projekt powstaje w `Dokumenty\Generator filmów Adi\Projekty`. Lista u góry pozwala tworzyć, nazywać i przełączać projekty produktów. Projekty przechowują materiały i wyniki osobno.

**Pionowy 9:16** dopasowuje rozdzielczość do materiałów do maksymalnie 1080×1920. **Format materiałów** zachowuje proporcje najczęściej występujących wymiarów w projekcie. FPS każdego filmu jest dobierany z plików, które trafiły do tego filmu, maksymalnie do 60. Źródło 30 FPS w eksporcie 60 FPS nie zyskuje nowych momentów ruchu.

W pionowym formacie domyślne **Wypełnij kadr** zajmuje cały ekran; możesz przesunąć kadr dla każdego pliku osobno. **Pokaż cały obraz** zachowuje całe ujęcie, ale przy innym formacie może zostawić wolne pasy.

Pliki projektu są poza folderem aplikacji. Przy aktualizacji rozpakuj nowy ZIP osobno. Wyniki, ustawienia i projekty zostaną zachowane. Przy pierwszym uruchomieniu nowej wersji stary projekt może zostać skopiowany do nowego katalogu; oryginalny folder pozostaje kopią bezpieczeństwa.

## Dla osób budujących aplikację

Wymagane są Windows x64, Python oraz oficjalne pliki `ffmpeg.exe` i `ffprobe.exe` z ich plikami licencyjnymi. Polecenie:

```powershell
.\build.ps1 -FfmpegDir 'C:\sciezka\do\ffmpeg' -OutputDir 'dist'
```

Zawartość `dist\GeneratorFilmowAdi` można spakować do `Generator-filmow-Adi-windows-x64.zip` i dodać jako asset do GitHub Release. Użytkownik ZIP-a nie potrzebuje Pythona ani osobnej instalacji FFmpeg. Sprawdź licencję konkretnej kompilacji FFmpeg przed publikacją.

Kod aplikacji jest w `main.py` i `creative_factory/remix.py`. Domyślne ustawienia są w `config.default.json`. Wersja wydania nie zawiera Pythona jako oddzielnej instalacji; runtime jest wewnątrz paczki EXE.
Testy: ustaw `CF_TEST_FFMPEG_DIR` na folder zawierający `ffmpeg.exe` i `ffprobe.exe`, a następnie uruchom `python -m unittest discover -s tests -v`.
