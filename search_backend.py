"""OpenRouter embeddings and Qdrant REST API; no third-party Python dependencies."""
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ServiceError(ValueError):
    def __init__(self, service, status=None):
        self.status = status
        message = service + (': HTTP ' + str(status) if status else ': сервис недоступен')
        if service == 'OpenRouter' and status in (401, 402, 403):
            message += '; проверьте ключ, баланс и доступ к модели'
        super().__init__(message)


def request_json(service, url, method='GET', payload=None, headers=None):
    """Never include response bodies, credentials or request texts in exceptions."""
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    request = Request(url, data=data, method=method,
                      headers={'Content-Type': 'application/json', **(headers or {})})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ServiceError(service, status) from None
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise ServiceError(service) from None
        except (ValueError, UnicodeError):
            raise ValueError(service + ': некорректный JSON') from None
        time.sleep(2 ** attempt)


def env_file(root):
    """Small literal KEY=value format; do not execute shell or expand variables."""
    values = {}
    path = root / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if not re.match(r'^[A-Z][A-Z0-9_]*=', line):
                raise ValueError('В .env ожидается KEY=value (без shell-команд)')
            key, value = line.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key] = value
    return values


@dataclass
class Config:
    model: str = 'baai/bge-m3'
    dimension: int = 1024
    version: str = '1'
    url: str = 'http://127.0.0.1:6333'
    collection: str = 'science'
    openrouter_key: str = field(default='', repr=False)
    qdrant_key: str = field(default='', repr=False)

    @classmethod
    def load(cls, root):
        values = {**env_file(root), **os.environ}
        config = cls(model=values.get('EMBEDDING_MODEL', 'baai/bge-m3'),
                     dimension=int(values.get('EMBEDDING_DIMENSIONS', '1024')),
                     version=values.get('EMBEDDING_VERSION', '1'),
                     url=values.get('QDRANT_URL', 'http://127.0.0.1:6333').rstrip('/'),
                     collection=values.get('QDRANT_COLLECTION', 'science'),
                     openrouter_key=values.get('OPENROUTER_API_KEY', ''),
                     qdrant_key=values.get('QDRANT_API_KEY', ''))
        parsed = urlsplit(config.url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('QDRANT_URL должен быть HTTP(S) URL без встроенного ключа')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', config.collection):
            raise ValueError('QDRANT_COLLECTION: допустимы буквы, цифры, _ и -')
        if not config.model or not config.version or not 1 <= config.dimension <= 65536:
            raise ValueError('Некорректные настройки embedding model/dimensions/version')
        return config

    def identity(self):
        return {'provider': 'openrouter', 'model': self.model, 'dimension': self.dimension,
                'version': self.version, 'input_format': 'plain-text-v1'}


def validate_vector(vector, dimension):
    if (not isinstance(vector, list) or len(vector) != dimension
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
            or not any(x != 0 for x in vector)):
        raise ValueError('Embedding: неверная размерность, пустой или нечисловой вектор')
    return vector


class Embedder:
    def __init__(self, root, config):
        self.config = config
        self.path = root / 'data/embedding-cache.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = hashlib.sha256(json.dumps(config.identity(), sort_keys=True).encode()).hexdigest()
        self.new_embeddings = 0
        self.requests = 0
        self.prompt_tokens = 0
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(namespace TEXT, hash TEXT, vector BLOB, PRIMARY KEY(namespace,hash))')

    def remote(self, texts):
        if not self.config.openrouter_key:
            raise ValueError('Добавьте OPENROUTER_API_KEY в локальный .env')
        result = request_json('OpenRouter', 'https://openrouter.ai/api/v1/embeddings', 'POST',
                              {'model': self.config.model, 'input': texts, 'encoding_format': 'float'},
                              {'Authorization': 'Bearer ' + self.config.openrouter_key})
        rows = result.get('data', [])
        if (len(rows) != len(texts) or any(type(r.get('index')) is not int for r in rows)
                or sorted(r['index'] for r in rows) != list(range(len(texts)))):
            raise ValueError('OpenRouter: неполный или некорректный ответ embeddings')
        vectors = [validate_vector(row.get('embedding'), self.config.dimension)
                   for row in sorted(rows, key=lambda row: row['index'])]
        self.requests += 1
        self.new_embeddings += len(vectors)
        self.prompt_tokens += result.get('usage', {}).get('prompt_tokens', 0)
        return vectors

    def encode(self, texts):
        if any(not isinstance(t, str) or not t.strip() or len(t) > 2000 for t in texts):
            raise ValueError('Embedding input: нужен текст длиной от 1 до 2000 символов')
        hashes = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        found = {}
        with sqlite3.connect(self.path) as db:
            for digest in set(hashes):
                row = db.execute('SELECT vector FROM vectors WHERE namespace=? AND hash=?', (self.namespace, digest)).fetchone()
                if row:
                    found[digest] = list(struct.unpack('<' + 'f' * self.config.dimension, row[0]))
            missing = {h: t for h, t in zip(hashes, texts) if h not in found}
            keys = list(missing)
            for offset in range(0, len(keys), 16):
                batch = keys[offset:offset + 16]
                vectors = self.remote([missing[h] for h in batch])
                with db:
                    for digest, vector in zip(batch, vectors):
                        packed = struct.pack('<' + 'f' * self.config.dimension, *vector)
                        db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?)', (self.namespace, digest, packed))
                        found[digest] = list(struct.unpack('<' + 'f' * self.config.dimension, packed))
        return [found[h] for h in hashes]


MANIFEST_ID = '00000000-0000-0000-0000-000000000001'


class Qdrant:
    def __init__(self, config):
        self.config = config

    def call(self, path, method='GET', payload=None):
        result = request_json('Qdrant', self.config.url + path, method, payload,
                              {'api-key': self.config.qdrant_key} if self.config.qdrant_key else {})
        return result.get('result', result)

    def aliases(self):
        return {row['alias_name']: row['collection_name'] for row in self.call('/aliases')['aliases']}

    def manifest(self, collection):
        rows = self.call('/collections/' + collection + '/points', 'POST',
                         {'ids': [MANIFEST_ID], 'with_payload': True, 'with_vector': False})
        if not rows or rows[0]['payload'].get('kind') != 'science-manifest':
            raise ValueError('Нет манифеста science: выполните semantic-index')
        return rows[0]['payload']

    def create(self, collection):
        self.call('/collections/' + collection, 'PUT', {
            'vectors': {'size': self.config.dimension, 'distance': 'Cosine', 'on_disk': True},
            'on_disk_payload': True})
        for name in ('document', 'kind'):
            self.call('/collections/' + collection + '/index?wait=true', 'PUT',
                      {'field_name': name, 'field_schema': 'keyword'})

    def upload(self, collection, points):
        self.call('/collections/' + collection + '/points?wait=true', 'PUT', {'points': points})

    def switch(self, collection):
        name = self.config.collection
        previous = name + '_previous'
        aliases = self.aliases()
        actions = [{'delete_alias': {'alias_name': alias}} for alias in (name, previous) if alias in aliases]
        if name in aliases:
            actions.append({'create_alias': {'alias_name': previous, 'collection_name': aliases[name]}})
        actions.append({'create_alias': {'alias_name': name, 'collection_name': collection}})
        self.call('/collections/aliases', 'POST', {'actions': actions})

    def groups(self, collection, vector, limit):
        return self.call('/collections/' + collection + '/points/query/groups', 'POST', {
            'query': vector, 'group_by': 'document', 'group_size': 1, 'limit': limit,
            'filter': {'must': [{'key': 'kind', 'match': {'value': 'chunk'}}]},
            'with_payload': True, 'with_vector': False})['groups']
