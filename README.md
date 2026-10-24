# Alexa + Grok (Skill personalizada)

Faz o seu Echo responder usando o Grok. A Alexa vira só o microfone e o alto-falante: você fala *"Alexa, abrir meu grok"*, e tudo o que disser depois vai para o seu servidor na Vercel, que pergunta ao Grok e devolve a resposta para a Alexa falar. A conversa guarda as últimas 6 trocas, então dá para fazer perguntas de continuação ("e quantos habitantes ela tem?").

```
Echo -> Amazon (transcreve a fala) -> POST /api/alexa (Vercel) -> API do Grok
     <- Amazon (fala a resposta)  <- JSON da resposta        <-
```

## Estrutura

```
api/alexa.py                                   endpoint (Python, serverless Vercel)
requirements.txt                               dependência (cryptography, para checar a assinatura da Amazon)
skill-package/interactionModels/custom/pt-BR.json   modelo de voz da skill (cole no console da Alexa)
test_local.py                                  teste sem Echo
```

## Passo a passo

### 1. Chave do Grok
Crie uma chave em https://console.x.ai e guarde-a.

### 2. Deploy na Vercel
1. Suba esta pasta para um repositório no GitHub e importe-o na Vercel (ou rode `vercel` na pasta).
2. Em **Settings → Environment Variables**, crie:
   | Variável | Valor |
   |---|---|
   | `XAI_API_KEY` | sua chave da xAI |
   | `XAI_MODEL` | opcional; o padrão é `grok-4-fast`. Troque pelo nome que aparecer no console da xAI |
   | `ALEXA_SKILL_ID` | opcional, mas recomendado: o ID da skill (passo 3) |
   | `GROK_SYSTEM_PROMPT` | opcional: a personalidade do assistente |
3. Abra `https://SEU-PROJETO.vercel.app/api/alexa` no navegador. Deve aparecer `{"status": "ok", ...}`.

### 3. Criar a skill
1. Acesse https://developer.amazon.com/alexa/console/ask e entre com **a mesma conta Amazon do seu Echo**.
2. Clique em **Create Skill**, dê um nome, escolha o idioma **Portuguese (BR)**, o modelo **Custom** e o backend **Provision your own**.
3. Em **Build → Interaction Model → JSON Editor**, cole o conteúdo de `pt-BR.json`, clique em **Save** e depois em **Build Skill**.
4. Em **Build → Endpoint**, escolha **HTTPS**, cole a URL `https://SEU-PROJETO.vercel.app/api/alexa` e, no certificado, escolha *"My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority"*.
5. Copie o **Skill ID** e coloque-o em `ALEXA_SKILL_ID` na Vercel. Depois faça redeploy.

### 4. Testar
- No console, aba **Test**, ative **Development** e digite: `abrir meu grok` e depois `pergunta qual a capital de Pernambuco`.
- No Echo, a skill de desenvolvimento já aparece na sua conta: *"Alexa, abrir meu grok"*.
- Localmente, sem Amazon: `XAI_API_KEY=... python test_local.py "sua pergunta"`.

## Como falar com ela
A Amazon exige uma "palavra de entrada" antes da pergunta livre. Por isso, comece com **pergunta**, **me diz**, **me explica**, **o que é**, **quem é**, **como**, **por que**... Exemplo: *"me explica como funciona energia solar"*. Para incluir outras palavras de entrada, adicione-as nos `samples` do `pt-BR.json`.

Atalho de uma frase só: *"Alexa, pergunte ao meu grok quem foi Chico Science"*.

## Limites
- **A Alexa nativa continua igual.** O Grok responde só dentro da skill. Não existe como trocar o assistente padrão do Echo, nem com engenharia reversa: o processamento acontece nos servidores da Amazon.
- **Prazo de cerca de 8 segundos.** Se o Grok demorar mais que isso, a Alexa desiste. Por isso o código usa um modelo rápido e respostas curtas, e corta a chamada em 6 segundos.
- **Plano gratuito da Vercel:** as funções têm limite de tempo de execução, o que não atrapalha aqui, porque o próprio limite da Alexa é menor.
- **Custo:** cada pergunta é uma chamada paga à API da xAI. Acompanhe o uso no console.

## Segurança
- O endpoint verifica a assinatura da Amazon em toda requisição (certificado, cadeia, assinatura SHA-256 e timestamp de no máximo 150 segundos). Sem essa verificação, qualquer pessoa poderia chamar o endpoint e gastar seus créditos do Grok. A Amazon também exige essa verificação para publicar a skill.
- `ALEXA_SKIP_VERIFY=1` desliga a verificação. Use só no `test_local.py` e **nunca** configure essa variável na Vercel.
- Defina `ALEXA_SKILL_ID` para aceitar apenas a sua skill.

## Próximos passos
- Trocar o Grok por outra IA: só a função `ask_grok` muda. A API da xAI segue o formato da OpenAI, então a troca é fácil.
- Ferramentas: a API da xAI aceita servidores MCP remotos. Com eles, o Grok pode consultar suas próprias APIs (casa, agenda etc.) antes de responder.
