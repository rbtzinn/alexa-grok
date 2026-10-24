# Alexa + IA (Skill personalizada)

Faz o seu Echo responder usando uma IA. A Alexa vira só o microfone e o alto-falante: você fala *"Alexa, abrir meu grok"* e tudo o que disser depois vai para o seu servidor na Vercel, que pergunta à IA e devolve a resposta para a Alexa falar. A skill guarda as últimas 6 trocas, então dá para fazer perguntas de continuação.

O padrão é o **Groq**, que é gratuito, não pede cartão e é muito rápido (a Alexa só espera cerca de 8 segundos). Qualquer API no formato da OpenAI funciona, e a troca de IA é feita só nas variáveis de ambiente.

```
Echo -> Amazon (transcreve) -> POST /api/alexa (Vercel) -> API da IA
     <- Amazon (fala)       <- JSON da resposta        <-
```

## Arquivos

```
api/alexa.py                                        endpoint (Python, serverless Vercel)
vercel.json                                         faz a Vercel tratar /api como funções (evita PYTHON_ENTRYPOINT_NOT_FOUND)
requirements.txt                                    cryptography (verificação da assinatura da Amazon)
skill-package/interactionModels/custom/pt-BR.json   modelo de voz (colar no console da Alexa)
test_local.py                                       teste sem Echo
```

## Variáveis de ambiente (Vercel → Settings → Environment Variables)

| Variável | Obrigatória | Valor |
|---|---|---|
| `AI_API_KEY` | sim | chave do provedor (Groq: console.groq.com → API Keys) |
| `AI_MODEL` | não | padrão `llama-3.3-70b-versatile`. Se o Groq recusar, use um nome da lista de modelos do console |
| `AI_BASE_URL` | não | padrão `https://api.groq.com/openai/v1` |
| `ALEXA_SKILL_ID` | recomendada | ID da skill (`amzn1.ask.skill...`) |
| `AI_SYSTEM_PROMPT` | não | personalidade do assistente |

Para usar outros provedores, troque `AI_BASE_URL`, `AI_MODEL` e `AI_API_KEY`:
- **OpenRouter** (tem modelos grátis): `https://openrouter.ai/api/v1`, com o modelo `openrouter/free`
- **xAI/Grok** (pago): `https://api.x.ai/v1`, com o modelo `grok-4-fast`

Depois de mudar qualquer variável, faça **Redeploy**.

## Passo a passo

1. **Chave:** em console.groq.com, faça login e vá em API Keys → Create API Key.
2. **Vercel:** importe o repositório, configure `AI_API_KEY` e faça o deploy. Para conferir, abra `https://SEU-PROJETO.vercel.app/api/alexa`: deve aparecer `"status": "ok"` e `"chave_configurada": true`.
3. **Skill:** em developer.amazon.com/alexa/console/ask, use a mesma conta do Echo, idioma Portuguese (BR), modelo Custom e "Provision your own".
4. **Modelo de voz:** em Build → Interaction Model → JSON Editor, cole o `pt-BR.json` e depois clique em Save e em Build Skill.
5. **Endpoint:** em Build → Endpoint, escolha HTTPS, cole a URL `/api/alexa` e, no certificado, escolha a opção de "wildcard certificate". Depois copie o Skill ID para `ALEXA_SKILL_ID` e faça Redeploy.
6. **Teste:** na aba Test, escolha Development e digite `abrir meu grok` e depois `pergunta qual a capital de Pernambuco`. Em seguida, teste no Echo.

## Como falar

Comece a pergunta com **pergunta**, **me diz**, **me explica**, **o que é**, **quem é**, **como**, **por que**... Essa "palavra de entrada" é exigida pela Amazon para perguntas livres.

Atalho: *"Alexa, pergunte ao meu grok quem foi Chico Science"*.

## Limites

- A Alexa nativa continua igual. A IA só responde dentro da skill.
- O plano gratuito do Groq tem limite de requisições por minuto e por dia. Para uso pessoal, sobra.
- Em planos gratuitos, os provedores podem usar o que você manda. Não fale dados sensíveis.

## Segurança

- Toda requisição tem a assinatura da Amazon verificada: certificado, cadeia, SHA-256 e timestamp de até 150 segundos.
- `ALEXA_SKIP_VERIFY=1` é só para o `test_local.py`. **Nunca** configure essa variável na Vercel.
- Os logs ficam em Vercel → projeto → Logs e mostram o motivo de cada recusa ou erro da IA.
