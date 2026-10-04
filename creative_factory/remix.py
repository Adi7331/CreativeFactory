"""Lokalny Creative Remix Engine. Tylko standardowa biblioteka Pythona."""
import argparse
import csv
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
import time

from . import projects

ROOT = Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, '_MEIPASS', ROOT.parent))
EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}
DEFAULTS = dict(ads_count=5, clips_per_ad=4, use_cta=False,
                output_mode='portrait', seed=None, crf=20, preset='fast',
                threads=4, inputs_dir='.', output_dir='Gotowe filmy')
LEGACY_CONFIG_KEYS = {'hook_seconds', 'clip_seconds', 'cta_seconds', 'fps', 'width', 'height', 'fit'}


class RemixError(Exception):
    pass


def validate_config(cfg):
    unknown = set(cfg) - set(DEFAULTS) - LEGACY_CONFIG_KEYS
    if unknown:
        raise RemixError('Nieznane ustawienia: ' + ', '.join(sorted(unknown)))
    if type(cfg['use_cta']) is not bool:
        raise RemixError('use_cta: wpisz false (bez zakończenia) albo true (dodaj CTA).')
    for key in ('ads_count', 'clips_per_ad', 'crf', 'threads'):
        if type(cfg[key]) is not int:
            raise RemixError(f'{key}: wpisz liczbę całkowitą.')
    if cfg['seed'] is not None and type(cfg['seed']) is not int:
        raise RemixError('seed: wpisz liczbę całkowitą albo null.')
    for key in ('ads_count', 'clips_per_ad', 'threads'):
        if cfg[key] <= 0:
            raise RemixError(f'{key}: wartość musi być większa od zera.')
    if cfg['ads_count'] > 10000 or cfg['clips_per_ad'] > 100:
        raise RemixError('Limit bezpieczeństwa: 10000 reklam i 100 klipów w reklamie.')
    if cfg['output_mode'] not in ('portrait', 'source'):
        raise RemixError('Wybierz format pionowy 9:16 albo format materiałów.')
    if not 0 <= cfg['crf'] <= 51 or not 1 <= cfg['threads'] <= 64:
        raise RemixError('crf: 0–51; threads: 1–64.')
    if cfg['preset'] not in ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'):
        raise RemixError('Niepoprawne ustawienie preset.')
    for key in ('inputs_dir', 'output_dir'):
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            raise RemixError(f'{key}: wpisz ścieżkę folderu.')


def choose_output_geometry(sizes, mode):
    """Choose a stable canvas for one generated batch from its source dimensions."""
    if mode not in ('portrait', 'source'):
        raise RemixError('Nieznany format filmu.')
    valid = {Path(path): (int(size[0]), int(size[1])) for path, size in sizes.items()
             if len(size) == 2 and int(size[0]) > 0 and int(size[1]) > 0}
    if not valid:
        raise RemixError('Nie udało się odczytać rozdzielczości materiałów.')
    counts = {}
    for dims in valid.values():
        counts[dims] = counts.get(dims, 0) + 1
    most_common = max(counts.values())
    tied = {dims for dims, count in counts.items() if count == most_common}
    hooks = [path for path, dims in valid.items()
             if dims in tied and (path.parent.name.casefold() in ('hooki', 'hooks') or path.stem.casefold().startswith('hook'))]
    references = hooks or [path for path, dims in valid.items() if dims in tied]
    width, height = valid[sorted(references, key=lambda path: path.name.casefold())[0]]
    if mode == 'portrait':
        short_side = min(1080, min(width, height))
        out_width = max(18, (short_side // 18) * 18)
        return out_width, out_width * 16 // 9
    scale = min(1.0, 1920 / max(width, height))
    out_width, out_height = int(width * scale), int(height * scale)
    return out_width - out_width % 2, out_height - out_height % 2


def choose_output_fps(rates):
    parsed = []
    for rate in rates:
        try:
            value = rate if isinstance(rate, Fraction) else Fraction(str(rate))
        except (ValueError, ZeroDivisionError):
            continue
        if value > 0:
            parsed.append(value)
    if not parsed:
        return Fraction(30, 1)
    return min(max(parsed), Fraction(60, 1))


def resolve_trim(duration, trim):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Plik nie ma poprawnego czasu trwania.')
    if trim is None:
        return 0.0, float(duration)
    try:
        start, end = float(trim['start']), float(trim['end'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Przycięcie musi zawierać wartości „od” i „do”.') from exc
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration + 1e-3:
        raise ValueError(f'Niepoprawny zakres przycięcia: {start:g}–{end:g} s; materiał ma {duration:g} s.')
    return start, min(end, float(duration))


def plan_variants(pools, k, count, seed, use_cta=True):
    h, c = pools['hooks'], pools['clips']
    t = pools['cta'] if use_cta else [None]
    if not h or len(c) < k:
        raise RemixError(f'Potrzebny minimum 1 hook oraz {k} różnych klipów; dostępnych klipów: {len(c)}. Dodaj klipy albo zmniejsz clips_per_ad.')
    if not t:
        raise RemixError('CTA jest włączone, ale brak zakończeń. Dodaj materiał do cta lub ustaw use_cta na false.')
    permutations = math.perm(len(c), k)
    total = len(h) * permutations * len(t)
    if count > total:
        raise RemixError(f'Żądano {count} reklam, ale istnieje tylko {total} unikalnych układów. Dodaj materiały lub zmniejsz ads_count.')
    rng = random.Random(seed)
    if total <= sys.maxsize:
        ranks = rng.sample(range(total), count)
    else:
        ranks, seen = [], set()
        while len(ranks) < count:
            rank = rng.randrange(total)
            if rank not in seen:
                seen.add(rank)
                ranks.append(rank)
    variants = []
    for rank in ranks:
        hi, rest = divmod(rank, permutations * len(t))
        pi, ti = divmod(rest, len(t))
        available, middle = list(c), []
        for pos in range(k):
            block = math.perm(len(available) - 1, k - pos - 1)
            idx, pi = divmod(pi, block)
            middle.append(available.pop(idx))
        variants.append([h[hi], *middle, *([t[ti]] if use_cta else [])])
    return variants


def tool(name):
    configured = os.environ.get('CREATIVE_FACTORY_FFMPEG_DIR')
    if configured:
        candidate = Path(configured) / (name + '.exe')
        if candidate.is_file():
            return str(candidate)
    local = RESOURCES / 'tools' / 'ffmpeg' / (name + '.exe')
    found = str(local) if local.is_file() else shutil.which(name)
    if not found:
        raise RemixError(f'Brak {name}.exe. Umieść go w tools/ffmpeg/.')
    return found


def command(args, log=None, timeout=3600, cancel_event=None):
    command_args = [str(x) for x in args]
    try:
        if cancel_event is None:
            result = subprocess.run(command_args, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=timeout)
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
        else:
            process = subprocess.Popen(command_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace')
            started = time.monotonic()
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event.is_set():
                        process.terminate()
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                        raise RemixError('Generowanie anulowano. Gotowe filmy zostały zachowane.')
                    if time.monotonic() - started > timeout:
                        process.kill()
                        process.communicate()
                        raise subprocess.TimeoutExpired(command_args, timeout)
            returncode = process.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemixError(f'Nie można wykonać polecenia: {exc}') from exc
    if log:
        with log.open('a', encoding='utf-8') as f:
            f.write('\n' + json.dumps(command_args, ensure_ascii=False) + '\n' + stderr)
    if returncode:
        raise RemixError('FFmpeg/ffprobe zgłosił błąd:\n' + stderr[-2400:])
    return stdout


def probe(path, ffprobe, cancel_event=None):
    try:
        data = json.loads(command([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], timeout=60, cancel_event=cancel_event))
        video = next(x for x in data['streams'] if x['codec_type'] == 'video' and not x.get('disposition', {}).get('attached_pic'))
        duration = float(video.get('duration', data['format'].get('duration', 0)))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('brak czasu trwania')
        rate_text = video.get('avg_frame_rate') or video.get('r_frame_rate') or '30/1'
        try:
            rate = Fraction(rate_text)
        except (ValueError, ZeroDivisionError):
            rate = Fraction(30, 1)
        rotation = int(video.get('tags', {}).get('rotate', 0) or 0) % 360
        for side_data in video.get('side_data_list', []):
            if 'rotation' in side_data:
                rotation = int(float(side_data['rotation'])) % 360
                break
        width, height = int(video['width']), int(video['height'])
        if rotation in (90, 270):
            width, height = height, width
        return {'duration': duration, 'video_index': video['index'], 'width': width,
                'height': height, 'fps': rate, 'audio': any(x['codec_type'] == 'audio' for x in data['streams']), 'data': data}
    except RemixError:
        if cancel_event and cancel_event.is_set():
            raise RemixError('Generowanie anulowano. Gotowe filmy zostały zachowane.')
        raise
    except (ValueError, KeyError, StopIteration) as exc:
        raise RemixError(f'Niepoprawny materiał: {path.name}. {exc}') from exc


def inventory(base, ffprobe, use_cta=True, cancel_event=None):
    pools, metadata = {'cta': []}, {}
    for role in (('hooks', 'clips', 'cta') if use_cta else ('hooks', 'clips')):
        folder = projects.role_dir(base, role)
        if not folder.is_dir():
            hint = ' Możesz też ustawić use_cta na false.' if role == 'cta' else ''
            raise RemixError(f'Brak folderu: {folder}. Utwórz go i dodaj materiały.{hint}')
        files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS), key=lambda p: p.name.casefold())
        if not files:
            hint = ' Możesz też ustawić use_cta na false.' if role == 'cta' else ''
            raise RemixError(f'Folder {folder} jest pusty. Dodaj pliki MP4/MOV/MKV/AVI/WEBM/M4V.{hint}')
        seen, pools[role] = set(), []
        for path in files:
            if cancel_event and cancel_event.is_set():
                raise RemixError('Generowanie anulowano. Gotowe filmy zostały zachowane.')
            with path.open('rb') as source_file:
                digest = hashlib.file_digest(source_file, 'sha256').hexdigest()
            if digest in seen:
                print(f'Pomijam identyczną kopię: {path.name}')
                continue
            seen.add(digest)
            metadata[path] = {**probe(path, ffprobe, cancel_event), 'sha256': digest}
            pools[role].append(path)
    return pools, metadata


def preview_inventory(base, ffprobe, use_cta=True):
    """Read only the metadata needed for the live format preview; do not hash media."""
    pools, metadata = {'hooks': [], 'clips': [], 'cta': []}, {}
    required = ('hooks', 'clips', 'cta') if use_cta else ('hooks', 'clips')
    for role in required:
        folder = projects.role_dir(base, role)
        if not folder.is_dir():
            if role == 'cta' and not use_cta:
                continue
            raise RemixError(f'Brak folderu „{projects.FOLDERS[role]}”. Utwórz go i dodaj materiały.')
        files = sorted((path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in EXTENSIONS), key=lambda path: path.name.casefold())
        if not files:
            if role == 'cta' and not use_cta:
                continue
            raise RemixError(f'Folder „{projects.FOLDERS[role]}” nie zawiera obsługiwanych filmów.')
        for path in files:
            metadata[path] = probe(path, ffprobe)
            pools[role].append(path)
    return pools, metadata


def normalize(source, target, meta, start, end, cfg, geometry, fps, settings, ffmpeg, log, cancel_event=None):
    rate_text = f'{fps.numerator}/{fps.denominator}'
    frames = max(1, round((end - start) * float(fps)))
    duration = frames / float(fps)
    w, h = geometry
    fit_mode = settings.get('fit', 'fill')
    if fit_mode == 'fill':
        x = min(1.0, max(0.0, float(settings.get('x', 0.5))))
        y = min(1.0, max(0.0, float(settings.get('y', 0.5))))
        fit = f"crop={w}:{h}:(in_w-out_w)*{x:.6f}:(in_h-out_h)*{y:.6f}"
        scale_mode = 'increase'
    elif fit_mode == 'contain':
        fit = f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black'
        scale_mode = 'decrease'
    else:
        raise RemixError(f'Nieznany sposób kadrowania materiału: {fit_mode}')
    vf = (f'scale={w}:{h}:force_original_aspect_ratio={scale_mode}:force_divisible_by=2:reset_sar=1,{fit},'
          f'setsar=1,fps={rate_text},trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p')
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', f'{start:.6f}', '-i', source]
    if not meta['audio']:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    args += ['-map', f'0:{meta["video_index"]}', '-map', '0:a:0' if meta['audio'] else '1:a:0',
             '-vf', vf, '-af', f'aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration},asetpts=PTS-STARTPTS',
             '-r', rate_text,
             '-t', str(duration), '-c:v', 'libx264', '-preset', cfg['preset'], '-crf', str(cfg['crf']),
             '-threads', str(cfg['threads']), '-filter_threads', str(cfg['threads']),
             '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-map_metadata', '-1', target]
    command(args, log, cancel_event=cancel_event)
    return duration


def verify(path, expected, geometry, fps, ffprobe, ffmpeg, log, cancel_event=None):
    info = probe(path, ffprobe)['data']
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})
    num, den = map(int, video['avg_frame_rate'].split('/'))
    if (video['width'], video['height']) != geometry or abs(num/den - float(fps)) > 0.01:
        raise RemixError(f'Nieprawidłowy obraz lub FPS: {path.name}; otrzymano {video["width"]}×{video["height"]}, średnio {num/den:.5f} kl./s (nominalnie {video.get("r_frame_rate")}, {video.get("nb_frames")} klatek, {video.get("duration")} s), oczekiwano {geometry[0]}×{geometry[1]} przy {float(fps):.5f} kl./s.')
    if video['codec_name'] != 'h264' or audio.get('codec_name') != 'aac' or audio.get('sample_rate') != '48000' or audio.get('channels') != 2:
        raise RemixError(f'Nieprawidłowe kodowanie obrazu/dźwięku: {path.name}.')
    actual = float(info['format']['duration'])
    if abs(actual - expected) > max(0.15, 2/float(fps)):
        raise RemixError(f'Nieprawidłowa długość {path.name}: {actual:.3f} s zamiast {expected:.3f} s.')
    command([ffmpeg, '-v', 'error', '-xerror', '-nostdin', '-i', path, '-f', 'null', '-'], log, cancel_event=cancel_event)
    return actual


def _load_asset_settings(project_root):
    path = project_root / 'assets.json'
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise RemixError(f'Nie można odczytać ustawień ujęć: {exc}') from exc
    return raw if isinstance(raw, dict) else {}


def run(config_path, progress=None, cancel_event=None):
    try:
        raw = json.loads(config_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise RemixError(f'Nie można odczytać config.json: {exc}') from exc
    if not isinstance(raw, dict):
        raise RemixError('config.json musi zawierać obiekt ustawień.')
    cfg = {**DEFAULTS, **raw}
    if 'output_mode' not in raw:
        cfg['seed'] = None
    validate_config(cfg)
    cfg['seed'] = cfg['seed'] if cfg['seed'] is not None else secrets.randbits(32)
    ffmpeg, ffprobe = tool('ffmpeg'), tool('ffprobe')
    project_root = config_path.resolve().parent
    base = (project_root / cfg['inputs_dir']).resolve()
    pools, metadata = inventory(base, ffprobe, use_cta=cfg['use_cta'], cancel_event=cancel_event)
    variants = plan_variants(pools, cfg['clips_per_ad'], cfg['ads_count'], cfg['seed'], use_cta=cfg['use_cta'])
    asset_settings = _load_asset_settings(project_root)
    geometry = choose_output_geometry({path: (meta['width'], meta['height']) for path, meta in metadata.items()}, cfg['output_mode'])
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    output = (project_root / cfg['output_dir']).resolve() / stamp
    work = projects.work_dir(project_root) / stamp
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    log = output / 'render.log'
    (output / 'config_used.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    cache, rows = {}, []
    print(f'Materiały: {len(pools["hooks"])} hooków, {len(pools["clips"])} klipów, {len(pools["cta"])} zakończeń.')
    print(f'Generuję {len(variants)} filmów w {geometry[0]}×{geometry[1]}. Wyniki: {output}')
    if progress:
        progress(0, len(variants), 'Przygotowuję materiały')
    fields = ['ad', 'segment', 'role', 'source', 'sha256', 'source_start_seconds', 'source_duration_seconds', 'segment_seconds', 'ad_start_seconds', 'seed', 'width', 'height', 'fps', 'fit', 'crop_x', 'crop_y']
    with (output / 'manifest.csv').open('w', newline='', encoding='utf-8-sig') as mf:
        writer = csv.DictWriter(mf, fieldnames=fields)
        writer.writeheader()
        mf.flush()
        for ad_number, sources in enumerate(variants, 1):
            if cancel_event and cancel_event.is_set():
                raise RemixError('Generowanie anulowano. Gotowe filmy zostały zachowane.')
            fps = choose_output_fps(metadata[source]['fps'] for source in sources)
            normalized, starts, cursor, segment_data = [], [], 0.0, []
            for source in sources:
                relative = source.relative_to(base).as_posix()
                settings = asset_settings.get(relative, {})
                trim = settings.get('trim')
                try:
                    start, end = resolve_trim(metadata[source]['duration'], trim)
                except ValueError as exc:
                    raise RemixError(f'{source.name}: {exc}') from exc
                frames = max(1, round((end - start) * float(fps)))
                key = (source, start, end, fps, geometry, settings.get('fit', 'fill'), settings.get('x', .5), settings.get('y', .5))
                if key not in cache:
                    target = work / f'segment_{len(cache):05d}.mkv'
                    print(f'Przygotowuję: {source.name} ({(end-start):.2f} s)')
                    length = normalize(source, target, metadata[source], start, end, cfg, geometry, fps, settings, ffmpeg, log, cancel_event)
                    cache[key] = (target, length)
                target, length = cache[key]
                starts.append((cursor, length))
                cursor += length
                normalized.append(target)
                segment_data.append((source, relative, settings, start, end, length))
            concat = work / 'concat.txt'
            # Nazwy techniczne są ASCII i względne: poprawne także przy polskich znakach w ścieżce projektu.
            concat.write_text(''.join(f"file '{p.name}'\n" for p in normalized), encoding='ascii')
            name = f'Film_{ad_number:03d}.mp4'
            partial = output / f'Film_{ad_number:03d}.partial.mp4'
            total_frames = round(cursor * float(fps))
            rate_text = f'{fps.numerator}/{fps.denominator}'
            try:
                command([ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-y', '-f', 'concat', '-safe', '1', '-i', concat,
                         '-map', '0:v:0', '-map', '0:a:0', '-vf', f'fps={rate_text}', '-r', rate_text, '-fps_mode', 'cfr',
                         '-frames:v', str(total_frames), '-c:v', 'libx264', '-preset', cfg['preset'], '-crf', str(cfg['crf']),
                         '-pix_fmt', 'yuv420p', '-threads', str(cfg['threads']), '-c:a', 'aac', '-b:a', '192k',
                         '-ar', '48000', '-ac', '2', '-t', str(cursor), '-video_track_timescale', '90000',
                         '-movflags', '+faststart', '-map_metadata', '-1', partial], log, cancel_event=cancel_event)
                actual = verify(partial, cursor, geometry, fps, ffprobe, ffmpeg, log, cancel_event)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(output / name)
            for position, ((source, relative, settings, start, end, length), (ad_start, _)) in enumerate(zip(segment_data, starts), 1):
                writer.writerow(dict(ad=name, segment=position, role=('hook' if position == 1 else 'cta' if cfg['use_cta'] and position == len(sources) else 'clip'),
                                     source=relative, sha256=metadata[source]['sha256'],
                                     source_start_seconds=round(start, 6), source_duration_seconds=round(end-start, 6),
                                     segment_seconds=round(length, 6), ad_start_seconds=round(ad_start, 6), seed=cfg['seed'],
                                     width=geometry[0], height=geometry[1], fps=f'{fps.numerator}/{fps.denominator}',
                                     fit=settings.get('fit', 'fill'), crop_x=settings.get('x', .5), crop_y=settings.get('y', .5)))
            mf.flush()
            rows.append({'ad': name, 'duration_seconds': actual, 'segments': len(sources), 'verified': True})
            print(f'Gotowe i sprawdzone: {name} — {actual:.3f} s')
            if progress:
                progress(ad_number, len(variants), f'Gotowy film {ad_number} z {len(variants)}')
    (output / 'verification.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    shutil.rmtree(work, ignore_errors=True)
    print(f'Zakończono. Manifest: {output / "manifest.csv"}')
    return output


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Creative Remix Engine — etap 1')
    parser.add_argument('--config', type=Path, default=Path.cwd() / 'config.json')
    args = parser.parse_args()
    try:
        run(args.config.resolve())
    except (RemixError, OSError) as exc:
        print(f'\nBŁĄD: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nPrzerwano. Ukończone reklamy i częściowy manifest pozostały w output.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

