"""Durable quotas shared by every worker on the PythonAnywhere host.

A private SQLite ledger is separate from academic data. BEGIN IMMEDIATE serializes
admission, not the network call. Attempts consume quota even on ambiguous failures.
"""

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from django.conf import settings


class AIControlError(Exception):
    def __init__(self, message, code="ia_indisponivel", status=503, retry_after=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


def subject_key(value):
    return hmac.new(settings.SECRET_KEY.encode(), str(value).encode(), hashlib.sha256).hexdigest()


def connect():
    path = Path(settings.AI_CONTROL_DATABASE)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive create avoids changing the process-global umask in threaded workers.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        pass
    connection = sqlite3.connect(path, timeout=2)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, subject TEXT NOT NULL, "
        "fingerprint TEXT NOT NULL, started REAL NOT NULL, state TEXT NOT NULL, result TEXT)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS attempts_started ON attempts(started)")
    return connection


def admit(request_id, subject, payload):
    now = time.time()
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    try:
        with closing(connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM attempts WHERE started < ?", (now - 172800,))
            existing = connection.execute(
                "SELECT subject,fingerprint,state,result FROM attempts WHERE id=?", (request_id,)
            ).fetchone()
            if existing:
                if existing[:2] != (subject, fingerprint):
                    raise AIControlError("Esta solicitação foi usada com outros dados.", "ia_conflito", 409)
                if existing[2] == "completed":
                    return existing[3]
                raise AIControlError(
                    "Esta geração já foi recebida. Aguarde; ela não será repetida automaticamente.",
                    "ia_ja_recebida",
                    409,
                )
            limits = [
                ("started >= ?", (now - 60,), settings.AI_GLOBAL_PER_MINUTE, 60),
                (
                    "started >= ?",
                    (int(now // 86400) * 86400,),
                    settings.AI_GLOBAL_PER_DAY,
                    int(86400 - now % 86400) + 1,
                ),
                ("started >= ? AND subject=?", (now - 60, subject), settings.AI_SUBJECT_PER_MINUTE, 60),
                (
                    "state='pending' AND started >= ?",
                    (now - settings.GEMINI_TIMEOUT_SECONDS - 30,),
                    settings.AI_MAX_CONCURRENT,
                    60,
                ),
            ]
            for where, args, maximum, delay in limits:
                count = connection.execute("SELECT COUNT(*) FROM attempts WHERE " + where, args).fetchone()[0]
                if count >= maximum:
                    raise AIControlError(
                        f"O limite de geração por IA foi atingido. Aguarde {delay} segundos. "
                        "Você pode continuar elaborando a questão manualmente; seus dados foram preservados.",
                        "cota_ia",
                        429,
                        delay,
                    )
            connection.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,NULL)",
                (request_id, subject, fingerprint, now, "pending"),
            )
        return None
    except sqlite3.Error:
        raise AIControlError("O controle de cotas está indisponível. Seus dados foram preservados.") from None


def finish(request_id, result=None):
    try:
        with closing(connect()) as connection, connection:
            connection.execute(
                "UPDATE attempts SET state=?,result=? WHERE id=?",
                ("completed" if result is not None else "failed", result, request_id),
            )
    except sqlite3.Error:
        # Do not return a result whose receipt could not be saved, or retry a paid call.
        raise AIControlError(
            "A geração foi recebida, mas não foi possível confirmar sua recuperação."
        ) from None


def generate_text(prompt, *, request_id=None, subject="administration", json_output=False):
    import httpx
    from google import genai
    from google.genai import errors, types

    if not settings.GEMINI_API_KEY:
        raise AIControlError("A IA não está configurada. Continue elaborando manualmente.")
    request_id = str(request_id or uuid.uuid4())
    cached = admit(
        request_id, subject, {"prompt": prompt, "model": settings.GEMINI_MODEL, "json": json_output}
    )
    if cached is not None:
        return cached
    try:
        with genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=settings.GEMINI_TIMEOUT_SECONDS * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ) as client:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
                    response_mime_type="application/json" if json_output else "text/plain",
                ),
            )
        text = response.text
        if not text or len(text) > 100000:
            raise AIControlError(
                "A IA retornou uma resposta vazia ou muito extensa.", "rascunho_invalido", 502
            )
    except Exception as error:
        finish(request_id)
        if isinstance(error, AIControlError):
            raise
        if isinstance(error, errors.APIError) and error.code == 429:
            raise AIControlError(
                "A cota do provedor de IA foi atingida. Aguarde antes de tentar novamente.",
                "cota_provedor",
                429,
                60,
            ) from None
        if isinstance(error, httpx.TimeoutException):
            raise AIControlError(
                "A IA excedeu o tempo limite. A chamada não será repetida automaticamente.", "ia_timeout", 504
            ) from None
        raise AIControlError(
            "A IA está indisponível. Continue elaborando manualmente; seus dados foram preservados.",
            "provedor_indisponivel",
            502,
        ) from None
    finish(request_id, text)
    return text
