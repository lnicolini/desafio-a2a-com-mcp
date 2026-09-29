# A Ponte: um agente A2A com MCP por dentro

*Onde termina a profundidade e começa o alcance*

Entrega do desafio **A Ponte**, que pede as duas pontas da fronteira que o curso
desenhou: um servidor MCP que expõe um domínio e um agente que o consome por
dentro (host MCP) e se oferece ao mundo por fora (servidor A2A).

O repositório tem dois processos independentes:

- **`servidor-mcp/`** — pacote `central_salas`, um servidor MCP em Streamable
  HTTP (porta **7301**, caminho `/mcp`) com três tools e um resource. A tool
  `reservar_sala` implementa o ciclo completo de **MRTR**.
- **`agente/`** — pacote `agente_salas`, um processo que é **host MCP por
  dentro** e **servidor A2A por fora** (porta **7300**, Agent Card em
  `/.well-known/agent-card.json` e JSON-RPC em `/a2a`).

Não há LLM no caminho de execução: o pedido chega em formato fixo
(`reservar sala=… inicio=… fim=… responsavel=…`) e é interpretado por regra, então
o mesmo pedido produz sempre o mesmo resultado.

---

## Estrutura do projeto

```text
.
├── README.md
├── .gitignore
├── dados/                  (do starter, sem alteração)
├── validador/              (do starter, sem alteração)
├── exemplos/               (do starter, sem alteração)
├── servidor-mcp/
│   ├── pyproject.toml      dependência mcp==2.2.0 + config do uv
│   ├── uv.lock             árvore transitiva de dependências travada
│   └── central_salas/
│       ├── __init__.py
│       ├── __main__.py     entrada (python -m central_salas)
│       ├── dominio.py      salas, reservas em memória, política e alternativas
│       ├── contratos.py    modelos de saída (structuredContent/outputSchema)
│       └── servidor.py     tools, resource, MRTR e log de requests no stderr
└── agente/
    ├── pyproject.toml      sem dependências de terceiros
    ├── uv.lock             vazio (só o projeto)
    └── agente_salas/
        ├── __init__.py
        ├── __main__.py     entrada (python -m agente_salas)
        ├── mcp_cliente.py  cliente MCP por HTTP (o agente como host)
        ├── nucleo.py       interpretação, máquina de estados e a ponte
        └── a2a_servidor.py Agent Card + executor A2A (SendMessage/GetTask)
```

Os `.gitkeep` do starter continuam em `servidor-mcp/` e `agente/` (vazios, sem
efeito).

---

## Como rodar

Pré-requisitos: **Python 3.10 ou superior** e o gerenciador **`uv`** (para instalar
o SDK MCP a partir do lock). O agente usa só a biblioteca padrão; o servidor usa
`mcp==2.2.0`, com a árvore transitiva travada em `servidor-mcp/uv.lock`.

Se ainda não tiver o `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`
(ou `pip install uv`).

### 1. Servidor MCP (terminal 1)

A integridade do `requestState` depende de um segredo que **não fica no código**
(o repositório é público). Gere e exporte na subida:

```bash
cd servidor-mcp
REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))") uv run python -m central_salas
```

O `uv run` cria o ambiente e instala as dependências (do `uv.lock`) na primeira
execução. O servidor atende em `http://127.0.0.1:7301/mcp` e registra no
**stderr** o método, o id e o traceparent de cada request.

> Para o teste de restart (o `requestState` continuar válido depois de reiniciar
> o processo), suba de novo com o **mesmo** `REQUEST_STATE_SECRET`. O estado só
> sobrevive ao restart se o segredo for o mesmo nas duas subidas.

### 2. Agente (terminal 2)

```bash
cd agente
uv run python -m agente_salas
```

O agente atende em `http://127.0.0.1:7300`. Na subida ele faz `tools/list` e lê
o resource `politica://uso`.

### 3. Validador (terminal 3)

Sempre com os dois processos **recém-iniciados** (as reservas criadas por uma
execução mudam o resultado da seguinte):

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

---

## Onde a ponte acontece

A ponte é a costura entre dois mecanismos de estado que, sozinhos, não têm
sessão. Ela vive no `agente/agente_salas/nucleo.py`:

1. **`NucleoAgente._processar`** — quando o servidor MCP responde
   `resultType: "input_required"`, o agente **não** responde a pergunta nem
   bloqueia. Ele guarda o `requestState` opaco e a chave da elicitação no
   objeto `Tarefa` daquela requisição, muda o estado para
   `TASK_STATE_INPUT_REQUIRED` e devolve a linha `alternativas: <ids>` como
   mensagem.
2. **`NucleoAgente._continuar`** — na continuação (`escolha=<valor>`), o agente
   repete o `tools/call` com um **id novo** (`mcp_cliente.requisitar` gera um
   `uuid` a cada request), ecoando `inputResponses` com a mesma chave recebida e
   o `requestState` **sem abrir, interpretar nem reconstruir**. `escolha=recusar`
   vira `action: "decline"` e a Task termina em `TASK_STATE_CANCELED`.

Do outro lado, no `servidor-mcp/central_salas/servidor.py`, `reservar_sala`
devolve um `InputRequiredResult` quando o intervalo está ocupado, e o middleware
`RequestStateBoundary` do SDK sela o `requestState` (AES-256-GCM) antes de
devolvê-lo e o verifica no retry.

---

## Decisões técnicas

- **Proteção do requestState.** O servidor configura
  `RequestStateSecurity(keys=[REQUEST_STATE_SECRET], ttl=600.0)` no `MCPServer`,
  o que instala o middleware `RequestStateBoundary`. Ele sela cada `requestState`
  com AES-256-GCM (HKDF a partir do segredo) e o **liga ao request**: método
  (`tools/call`), tool (`reservar_sala`) e digest dos argumentos viajam no
  envelope assinado. Uma adulteração é detectada e rejeitada com `-32602`
  ("Invalid or expired requestState"). O segredo vem de `REQUEST_STATE_SECRET`,
  nunca do código.

- **Validade.** `ttl=600.0` → o `requestState` vale **10 minutos**. O pedido
  original inteiro (sala, início, fim, responsável) viaja *dentro* do
  `requestState`, então ele sobrevive a um restart do servidor — nada é guardado
  em memória entre o `input_required` e o retry (desde que o segredo seja o
  mesmo).

- **Estado das Tasks.** Guardado em memória no processo do agente
  (`NucleoAgente._tarefas`, um dict chaveado pelo id da `Tarefa`, protegido por
  lock). O `requestState` fica no atributo privado da `Tarefa` e **não** aparece
  no JSON serializado — nunca vaza no card, no artifact nem em mensagem. O
  estado pausado é por Task: dois pedidos em conflito pausados ao mesmo tempo
  terminam cada um com a sua reserva.

- **Cliente MCP do agente.** Falado na mão com `urllib` (`mcp_cliente.py`), para
  controlar o `_meta` (declarando `{"elicitation": {"form": {}}}`) e os headers
  `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name`. O `traceparent` do cliente
  A2A é propagado com o mesmo trace-id e um span-id novo a cada request MCP
  daquela Task.

- **Erros.** Erros de negócio saem como `isError: true` (resultado `complete`
  com a mensagem exata); o ciclo MRTR exige a capability de elicitação em form
  mode e, sem ela, responde `-32021` com HTTP 400.

---

## Saída do validador

Última execução, com os dois processos recém-iniciados:

```text
trace-id desta execucao: d947c7a87123ddf786eb198383ce23db
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
