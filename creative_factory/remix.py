"""Lokalny Creative Remix Engine. Tylko standardowa biblioteka Pythona."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, '_MEIPASS', ROOT.parent))
EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}
DEFAULTS = dict(ads_count=5, clips_per_ad=4, hook_seconds=2.5,
                clip_seconds=1.8, cta_seconds=2.0, fps=30, width=1080,
                height=1920, seed=42, fit='crop', use_cta=False, crf=20, preset='fast',
                threads=4, inputs_dir='.', output_dir='output')


class RemixError(Exception):
    pass


def validate_config(cfg):
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise RemixError('Nieznane ustawienia: ' + ', '.join(sorted(unknown)))
    if type(cfg['use_cta']) is not bool:
        raise RemixError('use_cta: wpisz false (bez zakończenia) albo true (dodaj CTA).')
    for key in ('ads_count', 'clips_per_ad', 'fps', 'width', 'height', 'seed', 'crf', 'threads'):
        if type(cfg[key]) is not int:
            raise RemixError(f'{key}: wpisz liczbę całkowitą.')
    for key in ('ads_count', 'clips_per_ad', 'fps', 'width', 'height', 'threads'):
        if cfg[key] <= 0:
            raise RemixError(f'{key}: wartość musi być większa od zera.')
    if cfg['ads_count'] > 10000 or cfg['clips_per_ad'] > 100:
        raise RemixError('Limit bezpieczeństwa: 10000 reklam i 100 klipów w reklamie.')
    if cfg['fps'] > 120 or cfg['width'] != 1080 or cfg['height'] != 1920:
        raise RemixError('Etap 1 wymaga 1080x1920; FPS musi być w zakresie 1–120.')
    if not 0 <= cfg['crf'] <= 51 or not 1 <= cfg['threads'] <= 64:
        raise RemixError('crf: 0–51; threads: 1–64.')
    for key in ('hook_seconds', 'clip_seconds', 'cta_seconds'):
        value = cfg[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 600:
            raise RemixError(f'{key}: wpisz długość większą od 0 i do 600 sekund.')
        if round(value * cfg['fps']) < 1:
            raise RemixError(f'{key}: segment musi zawierać co najmniej jedną klatkę.')
    if cfg['fit'] not in ('pad', 'crop'):
        raise RemixError('fit: wybierz pad (cały obraz) lub crop (przycięcie).')
    if cfg['preset'] not in ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'):
        raise RemixError('Niepoprawne ustawienie preset.')
    for key in ('inputs_dir', 'output_dir'):
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            raise RemixError(f'{key}: wpisz ścieżkę folderu.')


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
    local = RESOURCES / 'tools' / 'ffmpeg' / (name + '.exe')
    found = str(local) if local.is_file() else shutil.which(name)
    if not found:
        raise RemixError(f'Brak {name}.exe. Umieść go w tools/ffmpeg/.')
    return found


def command(args, log=None, timeout=3600):
    try:
        result = subprocess.run([str(x) for x in args], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemixError(f'Nie można wykonać polecenia: {exc}') from exc
    if log:
        with log.open('a', encoding='utf-8') as f:
            f.write('\n' + json.dumps([str(x) for x in args], ensure_ascii=False) + '\n' + result.stderr)
    if result.returncode:
        raise RemixError('FFmpeg/ffprobe zgłosił błąd:\n' + result.stderr[-2400:])
    return result.stdout


def probe(path, ffprobe):
    try:
        data = json.loads(command([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], timeout=60))
        video = next(x for x in data['streams'] if x['codec_type'] == 'video' and not x.get('disposition', {}).get('attached_pic'))
        duration = float(video.get('duration', data['format'].get('duration', 0)))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('brak czasu trwania')
        return {'duration': duration, 'video_index': video['index'],
                'audio': any(x['codec_type'] == 'audio' for x in data['streams']), 'data': data}
    except (ValueError, KeyError, StopIteration, RemixError) as exc:
        raise RemixError(f'Niepoprawny materiał: {path.name}. {exc}') from exc


def inventory(base, ffprobe, use_cta=True):
    pools, metadata = {'cta': []}, {}
    for role in (('hooks', 'clips', 'cta') if use_cta else ('hooks', 'clips')):
        folder = base / role
        if not folder.is_dir():
            hint = ' Możesz też ustawić use_cta na false.' if role == 'cta' else ''
            raise RemixError(f'Brak folderu: {folder}. Utwórz go i dodaj materiały.{hint}')
        files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS), key=lambda p: p.name.casefold())
        if not files:
            hint = ' Możesz też ustawić use_cta na false.' if role == 'cta' else ''
            raise RemixError(f'Folder {folder} jest pusty. Dodaj pliki MP4/MOV/MKV/AVI/WEBM/M4V.{hint}')
        seen, pools[role] = set(), []
        for path in files:
            with path.open('rb') as source_file:
                digest = hashlib.file_digest(source_file, 'sha256').hexdigest()
            if digest in seen:
                print(f'Pomijam identyczną kopię: {path.name}')
                continue
            seen.add(digest)
            metadata[path] = {**probe(path, ffprobe), 'sha256': digest}
            pools[role].append(path)
    return pools, metadata


def normalize(source, target, meta, seconds, cfg, ffmpeg, log):
    frames = round(seconds * cfg['fps'])
    duration = frames / cfg['fps']
    w, h = cfg['width'], cfg['height']
    mode = 'decrease' if cfg['fit'] == 'pad' else 'increase'
    fit = f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black' if mode == 'decrease' else f'crop={w}:{h}'
    vf = (f'scale={w}:{h}:force_original_aspect_ratio={mode}:force_divisible_by=2:reset_sar=1,{fit},'
          f'setsar=1,fps={cfg["fps"]},tpad=stop_mode=clone:stop_duration={duration},'
          f'trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p')
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', source]
    if not meta['audio']:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    args += ['-map', f'0:{meta["video_index"]}', '-map', '0:a:0' if meta['audio'] else '1:a:0',
             '-vf', vf, '-af', f'aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration},asetpts=PTS-STARTPTS',
             '-t', str(duration), '-c:v', 'libx264', '-preset', cfg['preset'], '-crf', str(cfg['crf']),
             '-threads', str(cfg['threads']), '-filter_threads', str(cfg['threads']),
             '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', '-map_metadata', '-1', target]
    command(args, log)
    return duration


def verify(path, expected, cfg, ffprobe, ffmpeg, log):
    info = probe(path, ffprobe)['data']
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})
    num, den = map(int, video['avg_frame_rate'].split('/'))
    if (video['width'], video['height']) != (cfg['width'], cfg['height']) or abs(num/den - cfg['fps']) > 0.01:
        raise RemixError(f'Nieprawidłowy obraz lub FPS: {path.name}.')
    if video['codec_name'] != 'h264' or audio.get('codec_name') != 'aac' or audio.get('sample_rate') != '48000' or audio.get('channels') != 2:
        raise RemixError(f'Nieprawidłowe kodowanie obrazu/dźwięku: {path.name}.')
    actual = float(info['format']['duration'])
    if abs(actual - expected) > max(0.15, 2/cfg['fps']):
        raise RemixError(f'Nieprawidłowa długość {path.name}: {actual:.3f} s zamiast {expected:.3f} s.')
    command([ffmpeg, '-v', 'error', '-xerror', '-nostdin', '-i', path, '-f', 'null', '-'], log)
    return actual


def run(config_path, progress=None):
    try:
        raw = json.loads(config_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise RemixError(f'Nie można odczytać config.json: {exc}') from exc
    if not isinstance(raw, dict):
        raise RemixError('config.json musi zawierać obiekt ustawień.')
    cfg = {**DEFAULTS, **raw}
    validate_config(cfg)
    ffmpeg, ffprobe = tool('ffmpeg'), tool('ffprobe')
    project_root = config_path.resolve().parent
    base = (project_root / cfg['inputs_dir']).resolve()
    pools, metadata = inventory(base, ffprobe, use_cta=cfg['use_cta'])
    variants = plan_variants(pools, cfg['clips_per_ad'], cfg['ads_count'], cfg['seed'], use_cta=cfg['use_cta'])
    durations = [cfg['hook_seconds'], *([cfg['clip_seconds']] * cfg['clips_per_ad'])]
    roles = ['hook', *(['clip'] * cfg['clips_per_ad'])]
    if cfg['use_cta']:
        durations.append(cfg['cta_seconds'])
        roles.append('cta')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    output = (project_root / cfg['output_dir']).resolve() / stamp
    work = project_root / '_work' / stamp
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    log = output / 'render.log'
    (output / 'config_used.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    cache, rows = {}, []
    print(f'Materiały: {len(pools["hooks"])} hooków, {len(pools["clips"])} klipów, {len(pools["cta"])} CTA.')
    print(f'Generuję {len(variants)} reklam. Wyniki: {output}')
    if progress:
        progress(0, len(variants), 'Przygotowuję materiały')
    fields = ['ad', 'segment', 'role', 'source', 'sha256', 'source_start_seconds', 'source_duration_seconds', 'segment_seconds', 'ad_start_seconds', 'seed']
    with (output / 'manifest.csv').open('w', newline='', encoding='utf-8-sig') as mf:
        writer = csv.DictWriter(mf, fieldnames=fields)
        writer.writeheader()
        mf.flush()
        for ad_number, sources in enumerate(variants, 1):
            normalized, starts, cursor = [], [], 0.0
            for source, seconds in zip(sources, durations):
                frames = round(seconds * cfg['fps'])
                key = (source, frames)
                if key not in cache:
                    target = work / f'segment_{len(cache):05d}.mkv'
                    print(f'Przygotowuję: {source.name} ({frames/cfg["fps"]:.2f} s)')
                    length = normalize(source, target, metadata[source], seconds, cfg, ffmpeg, log)
                    cache[key] = (target, length)
                target, length = cache[key]
                starts.append((cursor, length))
                cursor += length
                normalized.append(target)
            concat = work / 'concat.txt'
            # Nazwy techniczne są ASCII i względne: poprawne także przy polskich znakach w ścieżce projektu.
            concat.write_text(''.join(f"file '{p.name}'\n" for p in normalized), encoding='ascii')
            name = f'AD_{ad_number:03d}.mp4'
            partial = output / f'AD_{ad_number:03d}.partial.mp4'
            command([ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-y', '-f', 'concat', '-safe', '1', '-i', concat,
                     '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                     '-ar', '48000', '-ac', '2', '-t', str(cursor), '-video_track_timescale', '90000',
                     '-movflags', '+faststart', '-map_metadata', '-1', partial], log)
            actual = verify(partial, cursor, cfg, ffprobe, ffmpeg, log)
            partial.rename(output / name)
            for position, (source, role, (start, length)) in enumerate(zip(sources, roles, starts), 1):
                writer.writerow(dict(ad=name, segment=position, role=role,
                                     source=str(source.relative_to(base)), sha256=metadata[source]['sha256'],
                                     source_start_seconds=0, source_duration_seconds=round(metadata[source]['duration'], 6),
                                     segment_seconds=round(length, 6), ad_start_seconds=round(start, 6), seed=cfg['seed']))
            mf.flush()
            rows.append({'ad': name, 'duration_seconds': actual, 'segments': len(sources), 'verified': True})
            print(f'Gotowe i sprawdzone: {name} — {actual:.3f} s')
            if progress:
                progress(ad_number, len(variants), f'Gotowy film {ad_number} z {len(variants)}')
    (output / 'verification.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
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

