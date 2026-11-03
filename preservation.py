"""Immutable local source snapshots and verified portable backups."""
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

COMMANDS = ('snapshot', 'check-sources', 'backup', 'verify-backup', 'restore')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.text = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'tr', 'br'):
            self.text.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'tr'):
            self.text.append('\n')
    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def load_manifest(root):
    path = root / 'sources' / 'manifest.json'
    return json.loads(path.read_text()) if path.exists() else {'schema_version': 1, 'snapshots': []}


def snapshot(root, identity, url, version):
    import science
    docs, _ = science.collect(root)
    identities = {a: r['id'] for r, _, _ in docs for a in [r['id']] + r['aliases']}
    identity = identities.get(science.canonical_id(identity))
    if not identity:
        raise ValueError('Сначала создайте карточку источника')
    if urlparse(url).scheme not in ('http', 'https') or not version.strip():
        raise ValueError('Нужны HTTP(S) URL и явная версия источника')
    with urlopen(Request(url, headers={'User-Agent': 'science-local/1.0 (personal research archive)'}), timeout=60) as response:
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or 'utf-8'
        data = response.read(50 * 1024 * 1024 + 1)
        final_url = response.url
    if len(data) > 50 * 1024 * 1024:
        raise ValueError('Источник превышает лимит 50 MiB')
    if content_type not in ('text/html', 'application/xhtml+xml', 'text/plain'):
        raise ValueError('Поддерживаются HTML и plain text, получено: ' + content_type)
    checksum = sha(data)
    folder = root / 'attachments' / checksum
    folder.mkdir(parents=True, exist_ok=True)
    suffix = '.txt' if content_type == 'text/plain' else '.html'
    raw = folder / ('source' + suffix)
    if raw.exists() and sha(raw.read_bytes()) != checksum:
        raise ValueError('Повреждён существующий снимок')
    raw.write_bytes(data)
    decoded = data.decode(charset, errors='replace')
    if suffix == '.html':
        extractor = TextExtractor(); extractor.feed(decoded)
        decoded = '\n'.join(' '.join(line.split()) for line in ''.join(extractor.text).splitlines() if line.strip())
    text_file = folder / 'text.txt'
    text_file.write_text(decoded, encoding='utf-8')
    entry = {'id': checksum, 'document': identity, 'requested_url': url, 'resolved_url': final_url,
             'version': version, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
             'sha256': checksum, 'bytes': len(data), 'content_type': content_type,
             'local_path': raw.relative_to(root).as_posix(), 'text_path': text_file.relative_to(root).as_posix(),
             'text_sha256': sha(text_file.read_bytes())}
    manifest = load_manifest(root)
    existing = next((s for s in manifest['snapshots'] if s['id'] == checksum and s['document'] == identity and s['version'] == version), None)
    if not existing:
        manifest['snapshots'].append(entry)
        atomic_json(root / 'sources/manifest.json', manifest)
    return existing or entry


def safe_local(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Недопустимый путь: ' + relative)
    return path


def check_sources(root, require_files=False):
    rows = []
    for item in load_manifest(root)['snapshots']:
        status = 'ok'
        for key, hashkey in [('local_path', 'sha256'), ('text_path', 'text_sha256')]:
            path = safe_local(root, item[key])
            if not path.exists():
                if require_files:
                    raise ValueError('Нет снимка: ' + item[key])
                status = 'not_downloaded'
            elif sha(path.read_bytes()) != item[hashkey]:
                raise ValueError('Нарушена целостность снимка: ' + item[key])
        rows.append({'document': item['document'], 'version': item['version'], 'status': status, 'sha256': item['sha256']})
    return rows


def source_files(root):
    directories = {'papers', 'notes', 'learning', 'templates', 'docs', 'tests', 'sources', 'evals', 'attachments', '.github'}
    for folder, dirs, files in os.walk(root, followlinks=False):
        relative = Path(folder).relative_to(root)
        dirs[:] = sorted(d for d in dirs if d not in ('.git', '.venv', 'data', 'exports', '__pycache__')
                         and not (Path(folder) / d).is_symlink()
                         and (relative != Path('.') or d in directories))
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_symlink() or (name.startswith('.env') and name != '.env.example') or name == '.DS_Store':
                continue
            if relative != Path('.') or path.suffix in ('.py', '.md', '.json', '.txt', '.yaml', '.yml') or name in ('.gitignore', '.env.example'):
                yield path


def backup(root, destination):
    import science
    science.collect(root)
    destination = destination.resolve()
    if destination == root or root in destination.parents:
        raise ValueError('Резервные копии нужно хранить вне репозитория')
    destination.mkdir(parents=True, exist_ok=True)
    name = 'science-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz'
    archive = destination / name
    staging = archive.with_suffix('.tmp')
    hashes = {}
    try:
        with tarfile.open(staging, 'w:gz') as tar:
            for file in source_files(root):
                data = file.read_bytes()
                rel = file.relative_to(root).as_posix()
                hashes[rel] = sha(data)
                info = tarfile.TarInfo(rel); info.size = len(data); info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
            with tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / 'history.bundle'
                proc = subprocess.run(['git', '-C', str(root), 'bundle', 'create', str(bundle), '--all'], capture_output=True)
                if proc.returncode == 0:
                    data = bundle.read_bytes(); hashes['history.bundle'] = sha(data)
                    info = tarfile.TarInfo('history.bundle'); info.size = len(data); info.mode = 0o600
                    tar.addfile(info, io.BytesIO(data))
            data = json.dumps({'schema_version': 1, 'files': hashes}, sort_keys=True).encode()
            info = tarfile.TarInfo('_backup_manifest.json'); info.size = len(data); info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
        for relative, checksum in hashes.items():
            if relative != 'history.bundle' and sha((root / relative).read_bytes()) != checksum:
                raise ValueError('Файлы изменились во время резервирования; повторите backup')
        staging.replace(archive)
        os.chmod(archive, 0o600)
        verify_backup(archive)
        archive.with_suffix(archive.suffix + '.sha256').write_text(sha(archive.read_bytes()) + '\n')
    finally:
        if staging.exists():
            staging.unlink()
    return {'archive': str(archive), 'files': len(hashes), 'verified': True}


def verify_backup(archive):
    sidecar = archive.with_suffix(archive.suffix + '.sha256')
    if sidecar.exists() and sha(archive.read_bytes()) != sidecar.read_text().strip():
        raise ValueError('Контрольная сумма архива не совпадает')
    with tarfile.open(archive, 'r:gz') as tar:
        names = set()
        for member in tar.getmembers():
            path = Path(member.name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts or member.name in names:
                raise ValueError('Небезопасный или повторный элемент архива')
            names.add(member.name)
        manifest = json.load(tar.extractfile('_backup_manifest.json'))
        if names != set(manifest['files']) | {'_backup_manifest.json'}:
            raise ValueError('Содержимое не соответствует манифесту')
        for name, checksum in manifest['files'].items():
            if sha(tar.extractfile(name).read()) != checksum:
                raise ValueError('Повреждён файл: ' + name)
    return {'verified': True, 'files': len(manifest['files'])}


def restore(archive, target):
    verify_backup(archive)
    if target.exists():
        raise ValueError('Восстановление допускается только в новый каталог')
    target.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            if member.name == '_backup_manifest.json':
                continue
            path = target / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(tar.extractfile(member).read())
    return {'restored': str(target), 'next': 'python3 science.py index; history.bundle contains Git history'}


def setup_parser(sub):
    q = sub.add_parser('snapshot'); q.add_argument('id'); q.add_argument('--url', required=True); q.add_argument('--version', required=True)
    q = sub.add_parser('check-sources'); q.add_argument('--require-files', action='store_true')
    q = sub.add_parser('backup'); q.add_argument('--destination', type=Path, required=True)
    q = sub.add_parser('verify-backup'); q.add_argument('archive', type=Path)
    q = sub.add_parser('restore'); q.add_argument('archive', type=Path); q.add_argument('--to', type=Path, required=True)


def run(root, args):
    if args.command == 'snapshot':
        return snapshot(root, args.id, args.url, args.version)
    if args.command == 'check-sources':
        return check_sources(root, args.require_files)
    if args.command == 'backup':
        return backup(root, args.destination)
    if args.command == 'verify-backup':
        return verify_backup(args.archive)
    return restore(args.archive, args.to)
