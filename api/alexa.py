# -*- coding: utf-8 -*-
"""
Alexa Skill -> Grok (xAI) | Serverless function Python para a Vercel.

Fluxo: voce fala com o Echo -> a Amazon transcreve e faz POST de um JSON
para este endpoint -> chamamos o Grok -> devolvemos o texto no formato da
Alexa -> o Echo fala a resposta.

Depois do deploy, o endpoint e: https://SEU-PROJETO.vercel.app/api/alexa
"""

import base64
import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ------------------------------------------------------------------
# Configuracao (variaveis de ambiente na Vercel)
# ------------------------------------------------------------------
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-fast")  # troque sem mexer no codigo
XAI_URL = "https://api.x.ai/v1/chat/completions"

# ID da sua skill (amzn1.ask.skill....). Se definido, recusamos chamadas de outras skills.
ALEXA_SKILL_ID = os.environ.get("ALEXA_SKILL_ID", "")

# So para testar localmente sem assinatura da Amazon. NUNCA ligue em producao.
SKIP_VERIFY = os.environ.get("ALEXA_SKIP_VERIFY", "") == "1"

# Quantas trocas (pergunta + resposta) lembrar durante a conversa
MAX_HISTORY = 6

SYSTEM_PROMPT = os.environ.get(
    "GROK_SYSTEM_PROMPT",
    "Você é um assistente de voz em português do Brasil, falando por uma Alexa. "
    "Responda de forma curta, clara e natural para ser ouvida, em no máximo 3 frases. "
    "Nunca use listas, markdown, emojis ou links.",
)

# A Alexa espera resposta em ~8s; deixamos margem.
GROK_TIMEOUT_S = 6


# ------------------------------------------------------------------
# Verificacao de que a requisicao veio mesmo da Amazon
# (obrigatoria para endpoints HTTPS que nao sao AWS Lambda)
# ------------------------------------------------------------------
_cert_cache: dict = {}


def _valid_cert_url(url: str) -> bool:
    p = urlparse(url)
    path = os.path.normpath(p.path) if p.path else ""
    return (
        p.scheme.lower() == "https"
        and (p.hostname or "").lower() == "s3.amazonaws.com"
        and path.startswith("/echo.api/")
        and (p.port in (None, 443))
    )


def verify_amazon_request(headers, raw_body: bytes, body: dict) -> str | None:
    """Retorna None se valido, ou uma string explicando o erro."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509.oid import ExtensionOID

    cert_url = headers.get("SignatureCertChainUrl", "")
    signature_b64 = headers.get("Signature-256", "")
    if not cert_url or not signature_b64:
        return "cabecalhos de assinatura ausentes"
    if not _valid_cert_url(cert_url):
        return "URL do certificado invalida"

    # Baixa (ou reaproveita) a cadeia de certificados da Amazon
    pem = _cert_cache.get(cert_url)
    if pem is None:
        try:
            with urlopen(cert_url, timeout=3) as r:
                pem = r.read()
        except URLError:
            return "nao foi possivel baixar o certificado"
        _cert_cache[cert_url] = pem

    certs = x509.load_pem_x509_certificates(pem)
    leaf = certs[0]

    now = datetime.now(timezone.utc)
    if not (leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc):
        return "certificado expirado"

    try:
        san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        names = san.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    if "echo-api.amazon.com" not in names:
        return "certificado nao pertence a echo-api.amazon.com"

    # Cada certificado da cadeia deve ser assinado pelo proximo
    try:
        for child, parent in zip(certs, certs[1:]):
            child.verify_directly_issued_by(parent)
    except Exception:
        return "cadeia de certificados invalida"

    # Assinatura do corpo (SHA-256 com RSA)
    try:
        leaf.public_key().verify(
            base64.b64decode(signature_b64), raw_body, padding.PKCS1v15(), hashes.SHA256()
        )
    except Exception:
        return "assinatura invalida"

    # Timestamp: no maximo 150 segundos de diferenca (protege contra replay)
    ts = body.get("request", {}).get("timestamp", "")
    try:
        req_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "timestamp invalido"
    if abs((now - req_time).total_seconds()) > 150:
        return "timestamp fora da janela"

    return None


# ------------------------------------------------------------------
# Grok
# ------------------------------------------------------------------
def ask_grok(user_text: str, history: list) -> str:
    if not XAI_API_KEY:
        return "A chave da API do Grok não está configurada no servidor."

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-MAX_HISTORY * 2 :]
    messages.append({"role": "user", "content": user_text})

    payload = {"model": XAI_MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 250}
    req = Request(
        XAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=GROK_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except HTTPError as e:
        print(f"[grok] HTTP {e.code}: {e.read()[:300]!r}")
        return "O Grok respondeu com erro. Tenta de novo daqui a pouco."
    except (URLError, TimeoutError):
        return "O Grok demorou demais pra responder. Pode repetir?"
    except (KeyError, IndexError, json.JSONDecodeError):
        return "Recebi uma resposta estranha do Grok."


# ------------------------------------------------------------------
# Respostas no formato da Alexa
# ------------------------------------------------------------------
def speak(text: str, end: bool = False, session_attrs: dict | None = None, reprompt: str | None = None) -> dict:
    resp: dict = {
        "version": "1.0",
        "sessionAttributes": session_attrs or {},
        "response": {"shouldEndSession": end},
    }
    if text:
        resp["response"]["outputSpeech"] = {"type": "PlainText", "text": text}
    if reprompt and not end:
        resp["response"]["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return resp


def handle_alexa_request(body: dict) -> dict:
    request = body.get("request", {})
    req_type = request.get("type")
    session = body.get("session", {}) or {}
    attrs = session.get("attributes", {}) or {}
    history = attrs.get("history", [])

    if req_type == "LaunchRequest":
        return speak(
            "Oi! Agora quem responde é o Grok. Pode perguntar.",
            reprompt="Pode me perguntar qualquer coisa.",
        )

    if req_type == "IntentRequest":
        intent = request.get("intent", {})
        name = intent.get("name", "")

        if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
            return speak("Até mais!", end=True)
        if name == "AMAZON.HelpIntent":
            return speak(
                "Comece com pergunta, me diz ou me explica, e depois fale o que quiser.",
                session_attrs=attrs,
                reprompt="O que você quer saber?",
            )
        if name == "AMAZON.FallbackIntent":
            return speak(
                "Não peguei. Tenta começar com pergunta ou me diz.",
                session_attrs=attrs,
                reprompt="O que você quer saber?",
            )

        slots = intent.get("slots", {}) or {}
        user_text = (slots.get("query") or {}).get("value", "")
        if not user_text:
            return speak("Não entendi. Pode repetir?", session_attrs=attrs, reprompt="Pode repetir?")

        answer = ask_grok(user_text, history)
        history = (history + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer},
        ])[-MAX_HISTORY * 2 :]
        return speak(answer, session_attrs={"history": history}, reprompt="Quer perguntar mais alguma coisa?")

    if req_type == "SessionEndedRequest":
        return speak("", end=True)

    return speak("Não entendi esse pedido.")


# ------------------------------------------------------------------
# Handler HTTP da Vercel
# ------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict):
        out = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, {"error": "JSON invalido"})

        if not SKIP_VERIFY:
            err = verify_amazon_request(self.headers, raw, body)
            if err:
                print(f"[alexa] requisicao recusada: {err}")
                return self._send(400, {"error": err})

        app_id = (body.get("session", {}) or {}).get("application", {}).get("applicationId") or (
            body.get("context", {}).get("System", {}).get("application", {}).get("applicationId")
        )
        if ALEXA_SKILL_ID and app_id != ALEXA_SKILL_ID:
            return self._send(400, {"error": "skill nao autorizada"})

        self._send(200, handle_alexa_request(body))

    def do_GET(self):
        self._send(200, {"status": "ok", "modelo": XAI_MODEL, "dica": "a Alexa usa POST aqui"})
