"""Teste local sem Echo e sem Amazon: python test_local.py "sua pergunta"
Usa ALEXA_SKIP_VERIFY=1 automaticamente. Precisa de XAI_API_KEY para respostas reais."""
import json
import os
import sys

os.environ["ALEXA_SKIP_VERIFY"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from alexa import handle_alexa_request  # noqa: E402


def intent(text, attrs=None):
    return {
        "session": {"attributes": attrs or {}},
        "request": {
            "type": "IntentRequest",
            "intent": {"name": "ChatIntent", "slots": {"query": {"name": "query", "value": text}}},
        },
    }


print(json.dumps(handle_alexa_request({"request": {"type": "LaunchRequest"}}), ensure_ascii=False, indent=2))
pergunta = " ".join(sys.argv[1:]) or "qual a capital de Pernambuco"
r = handle_alexa_request(intent(pergunta))
print(json.dumps(r, ensure_ascii=False, indent=2))
r2 = handle_alexa_request(intent("e quantos habitantes ela tem", r["sessionAttributes"]))
print(json.dumps(r2["response"], ensure_ascii=False, indent=2))
