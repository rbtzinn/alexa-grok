# -*- coding: utf-8 -*-
"""
Alexa Skill -> IA (Groq gratuito por padrao; qualquer API compativel com OpenAI)
Serverless function Python para a Vercel.

Fluxo: voce fala com o Echo -> a Amazon transcreve e faz POST de um JSON
para este endpoint -> chamamos a IA -> devolvemos o texto no formato da
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
# Qualquer provedor com API no formato da OpenAI funciona. Padrao: Groq (gratuito, sem cartao).
#   Groq:       AI_BASE_URL=https://api.groq.com/openai/v1   AI_MODEL=llama-3.3-70b-versatile
#   OpenRouter: AI_BASE_URL=https://openrouter.ai/api/v1      AI_MODEL=openrouter/free
#   xAI/Grok:   AI_BASE_URL=https://api.x.ai/v1               AI_MODEL=grok-4-fast (pago)
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "llama-3.3-70b-versatile")
# Modelos tentados, em ordem, se o principal for recusado (ex.: descontinuado).
AI_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "AI_FALLBACK_MODELS",
        "openai/gpt-oss-20b,llama-3.1-8b-instant,moonshotai/kimi-k2-instruct,openai/gpt-oss-120b",
    ).split(",")
    if m.strip()
]
AI_URL = f"{AI_BASE_URL}/chat/completions"

# ID da sua skill (amzn1.ask.skill....). Se definido, recusamos chamadas de outras skills.
ALEXA_SKILL_ID = os.environ.get("ALEXA_SKILL_ID", "")

# So para testar localmente sem assinatura da Amazon. NUNCA ligue em producao.
SKIP_VERIFY = os.environ.get("ALEXA_SKIP_VERIFY", "") == "1"

# Quantas trocas (pergunta + resposta) lembrar durante a conversa
MAX_HISTORY = 6

SYSTEM_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    "Você é um assistente de voz em português do Brasil, falando por uma Alexa. "
    "Responda de forma curta, clara e natural para ser ouvida, em no máximo 3 frases. "
    "Nunca use listas, markdown, emojis ou links.",
)

# A Alexa espera resposta em ~8s; deixamos margem.
AI_TIMEOUT_S = 6.5


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
# IA
# ------------------------------------------------------------------
_working_model: str | None = None  # lembrado entre chamadas enquanto a funcao estiver "quente"


def _call_model(model: str, messages: list, timeout: float) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 300}
    req = Request(
        AI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "alexa-ia-skill/1.0",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data["choices"][0]["message"].get("content") or "").strip()


def ask_ai(user_text: str, history: list) -> str:
    global _working_model
    if not AI_API_KEY:
        return "A chave da API da IA não está configurada no servidor."

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-MAX_HISTORY * 2 :]
    messages.append({"role": "user", "content": user_text})

    candidates = [_working_model] if _working_model else []
    candidates += [m for m in [AI_MODEL, *AI_FALLBACK_MODELS] if m not in candidates]

    deadline = time.monotonic() + AI_TIMEOUT_S
    last_code = None
    for model in candidates:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        try:
            answer = _call_model(model, messages, remaining)
            if not answer:
                continue
            if model != _working_model:
                print(f"[ia] usando modelo {model}")
            _working_model = model
            return answer
        except HTTPError as e:
            last_code = e.code
            body = e.read()[:400]
            print(f"[ia] modelo {model} -> HTTP {e.code}: {body!r}")
            if e.code in (400, 404):  # modelo invalido/descontinuado: tenta o proximo
                continue
            break
        except (URLError, TimeoutError):
            return "A IA demorou demais pra responder. Pode repetir?"
        except (KeyError, IndexError, json.JSONDecodeError):
            return "Recebi uma resposta estranha da IA."

    if last_code == 401:
        return "A chave da IA foi recusada. Confira a variável AI API KEY na Vercel."
    if last_code == 403:
        return "O provedor da IA bloqueou o acesso, erro 403."
    if last_code == 429:
        return "Atingi o limite gratuito da IA. Tenta de novo daqui a pouco."
    if last_code in (400, 404):
        return "Nenhum modelo da IA aceitou o pedido. Confira a variável AI MODEL."
    if last_code:
        return f"A IA respondeu com erro {last_code}. Tenta de novo daqui a pouco."
    return "A IA não respondeu a tempo. Pode repetir?"


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
            "Oi! Pode perguntar o que quiser.",
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

        answer = ask_ai(user_text, history)
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
        self._send(200, {"status": "ok", "modelo": AI_MODEL, "chave_configurada": bool(AI_API_KEY), "dica": "a Alexa usa POST aqui"})
