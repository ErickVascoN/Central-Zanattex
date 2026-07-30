"""
Cliente de Google Sheets — ponto único de download de planilhas.

Portado da Central (Streamlit) `utils/cache_manager.py`, adaptado ao Django:
o diretório de cache passa a ficar em BASE_DIR/cache/sheets (configurável via
settings.SHEETS_CACHE_DIR).

Fluxo por chamada:
  1. Verifica cache em disco  cache/sheets/<id>_<gid>.csv
  2. Se fresco (idade < TTL)  → retorna do disco imediatamente (zero rede)
  3. Se obsoleto              → tenta download → salva no disco → retorna
  4. Se download falha        → usa cache obsoleto (degradação graciosa)
  5. Se sem cache nenhum      → retorna None

Assim, preencher a planilha no Google Sheets continua alimentando os dashboards
automaticamente (mesma lógica de leitura ao vivo de hoje), com o cache evitando
rede desnecessária dentro do TTL.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from pathlib import Path

import pandas as pd
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _cache_dir() -> Path:
    """Diretório de cache (configurável). Default: BASE_DIR/cache/sheets."""
    default = Path(settings.BASE_DIR) / "cache" / "sheets"
    return Path(getattr(settings, "SHEETS_CACHE_DIR", default))


# Trava por arquivo de cache — evita que 2 requisições concorrentes disparem o
# download da MESMA planilha ao mesmo tempo quando o cache expira: a segunda
# espera a primeira e reaproveita o cache recém-atualizado.
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _write_atomic(path: Path, content: str) -> None:
    """Grava em arquivo temporário e substitui via os.replace() (atômico) —
    evita que uma leitura concorrente pegue um CSV parcialmente escrito."""
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    # Evita que o CDN do Google sirva uma resposta em cache logo após uma edição.
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
_DEFAULT_TTL = 120  # segundos


# ─── internos ────────────────────────────────────────────────────────────────

def _cache_path(sheet_id: str, gid: str) -> Path:
    safe = f"{sheet_id}_{gid}".replace("/", "_").replace("\\", "_")
    return _cache_dir() / f"{safe}.csv"


def _download(sheet_id: str, gid: str, timeout: int = 30) -> str | None:
    """Baixa CSV via gviz/tq (rápido) com fallback para /export. Duas tentativas
    por URL com backoff de 2s antes de passar para a próxima."""
    urls = [
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}",
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}",
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv",
    ]
    for url_idx, url in enumerate(urls):
        for retry in range(2):
            try:
                r = requests.get(url, timeout=timeout, headers=_HEADERS)
                r.raise_for_status()
                # O gviz/tq e o /export sempre respondem em UTF-8, mas o requests
                # às vezes erra a detecção automática em respostas pequenas (poucos
                # bytes multibyte pro chardet confirmar) e troca acentos por
                # caracteres inválidos de forma irreversível — força explicitamente
                # (mesmo fix já usado em get_raw_sheet, que faltava aqui).
                r.encoding = "utf-8"
                content = r.text
                if content.strip():
                    logger.info("Download OK: %s_%s (url=%d, retry=%d)", sheet_id[:20], gid, url_idx + 1, retry)
                    return content
            except requests.exceptions.Timeout:
                logger.warning("Timeout url=%d retry=%d: %s_%s", url_idx + 1, retry, sheet_id[:20], gid)
                if retry == 0:
                    time.sleep(2)
            except requests.exceptions.HTTPError as e:
                logger.debug("HTTP %s url=%d: %s", e.response.status_code, url_idx + 1, url[:60])
                break  # erro HTTP → próxima URL sem retry
            except Exception as e:
                logger.debug("url=%d: %s", url_idx + 1, str(e)[:80])
                break
    return None


# ─── API pública ─────────────────────────────────────────────────────────────

def get_raw(sheet_id: str, gid: str = "0", ttl: int = _DEFAULT_TTL) -> str | None:
    """Retorna texto CSV bruto. Cache em disco com TTL e fallback obsoleto."""
    _cache_dir().mkdir(parents=True, exist_ok=True)
    path = _cache_path(sheet_id, gid)

    with _lock_for(str(path)):
        is_fresh = False
        cache_age = None
        if path.exists():
            cache_age = time.time() - path.stat().st_mtime
            is_fresh = cache_age < ttl

        if is_fresh:
            logger.debug("Cache fresco (%.0fs < %ds): %s", cache_age, ttl, path.name)
            return path.read_text(encoding="utf-8")

        content = _download(sheet_id, gid)
        if content:
            _write_atomic(path, content)
            logger.info("Cache salvo: %s", path.name)
            return content

        # Fallback para cache obsoleto
        if path.exists():
            age_min = (time.time() - path.stat().st_mtime) / 60
            logger.warning("Download falhou — usando cache com %.0f min: %s", age_min, path.name)
            return path.read_text(encoding="utf-8")

        logger.error("Sem dados disponíveis para %s_%s", sheet_id[:20], gid)
        return None


def get_dataframe(
    sheet_id: str,
    gid: str = "0",
    ttl: int = _DEFAULT_TTL,
    dtype=str,
    skiprows: int = 0,
) -> pd.DataFrame | None:
    """Retorna DataFrame a partir do cache CSV (default dtype=str preserva valores)."""
    content = get_raw(sheet_id, gid, ttl)
    if content is None:
        return None
    try:
        df = pd.read_csv(io.StringIO(content), dtype=dtype, skiprows=skiprows)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all")
        return df
    except Exception as e:
        logger.error("Erro ao parsear CSV %s_%s: %s", sheet_id[:20], gid, e)
        return None


def get_raw_sheet(sheet_id: str, sheet_name: str, ttl: int = _DEFAULT_TTL) -> str | None:
    """Retorna CSV de uma aba por NOME (via ?sheet= da API gviz). Útil quando
    os gids não são fixos. Prefira gid quando possível (mais confiável)."""
    import urllib.parse

    _cache_dir().mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in sheet_name)
    path = _cache_dir() / f"{sheet_id}_{safe}.csv"

    with _lock_for(str(path)):
        is_fresh = False
        if path.exists():
            is_fresh = (time.time() - path.stat().st_mtime) < ttl
        if is_fresh:
            return path.read_text(encoding="utf-8")

        encoded = urllib.parse.quote(sheet_name)
        url = (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            f"/gviz/tq?tqx=out:csv&sheet={encoded}"
        )
        try:
            r = requests.get(url, timeout=30, headers=_HEADERS, allow_redirects=True)
            r.raise_for_status()
            # O gviz/tq sempre responde em UTF-8, mas o requests às vezes erra a
            # detecção automática em respostas pequenas (poucos bytes multibyte
            # para o chardet confirmar) e troca acentos por caracteres inválidos
            # de forma irreversível — força explicitamente, como o original fazia.
            r.encoding = "utf-8"
            content = r.text
            if content.strip():
                _write_atomic(path, content)
                return content
        except Exception as e:
            logger.warning("get_raw_sheet falhou (sheet=%r): %s", sheet_name, str(e)[:100])

        if path.exists():
            return path.read_text(encoding="utf-8")
        return None


def invalidate_all() -> int:
    """Remove todo o cache. Retorna quantidade de arquivos removidos."""
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return 0
    count = 0
    for f in cache_dir.glob("*.csv"):
        f.unlink()
        count += 1
    logger.info("Cache limpo: %d arquivos removidos", count)
    return count


def cache_status() -> list[dict]:
    """Info de cada arquivo em cache (para exibir status/frescura nos dashboards)."""
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return []
    now = time.time()
    result = []
    for f in sorted(cache_dir.glob("*.csv")):
        age_s = now - f.stat().st_mtime
        result.append({
            "arquivo": f.name,
            "tamanho_kb": round(f.stat().st_size / 1024, 1),
            "idade_min": round(age_s / 60, 1),
            "idade_s": round(age_s),
        })
    return result
