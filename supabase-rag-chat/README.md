# supabase-rag-chat

Interface de chat estilo ChatGPT que responde perguntas usando **RAG** (Retrieval-Augmented Generation) sobre a tabela `biia` no Supabase (Postgres + pgvector).

---

## Como funciona

```
Pergunta do usuário
       │
       ▼
Gera embedding local (sentence-transformers/all-MiniLM-L6-v2 · 384-dim)
       │
       ▼
Busca por similaridade no Supabase (pgvector · tabela biia)
       │
       ▼
Monta system prompt com os top-5 resultados mais relevantes
       │
       ▼
Chama OpenAI GPT (streaming via SSE)
       │
       ▼
Exibe a resposta no chat em tempo real
```

---

## Estrutura do projeto

```
supabase-rag-chat/
├── public/
│   └── index.html          # Frontend (ChatGPT-like UI)
├── supabase/
│   └── match_biia.sql      # Função RPC pgvector (execute no Supabase)
├── app.py                  # Backend Flask (RAG pipeline)
├── requirements.txt        # Dependências Python
├── .env                    # Suas credenciais (não commitar!)
├── .env.example            # Template de variáveis de ambiente
└── README.md
```

---

## Pré-requisitos

- **Python ≥ 3.10**
- Conta no [Supabase](https://supabase.com) com a tabela `biia` populada
- Chave de API da [OpenAI](https://platform.openai.com/api-keys)
- Conexão com a internet (para baixar o modelo de embedding na 1ª execução)

---

## Configuração passo a passo

### 1. Clone e instale as dependências

```bash
cd supabase-rag-chat
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate no Windows
pip install -r requirements.txt
```

> Na primeira execução, o `sentence-transformers` baixará o modelo
> `sentence-transformers/all-MiniLM-L6-v2` (~90 MB) e fará cache local em `~/.cache`.

---

### 2. Configure o `.env`

Copie o template e preencha os valores:

```bash
cp .env.example .env
```

Edite o `.env`:

| Variável | Como obter |
|----------|------------|
| `SUPABASE_URL` | Supabase Dashboard → Project Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase Dashboard → Project Settings → API → **service_role** (⚠️ nunca exponha no frontend) |
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

> **Atenção**: a chave `anon` **não funciona** para chamar funções RPC com `supabase.rpc()`.
> É necessária a **service_role key**.

---

### 3. Crie a função RPC no Supabase

1. Acesse o **Supabase Dashboard** do seu projeto
2. Vá em **SQL Editor → New Query**
3. Cole e execute o conteúdo de `supabase/match_biia.sql`

Isso cria a função `match_biia(query_embedding, match_count)` que realiza a busca por similaridade via pgvector.

---

### 4. Rode o servidor

```bash
python app.py
# ou em modo desenvolvimento:
flask run --host=0.0.0.0 --port=3000 --debug
```

Abra no navegador: **http://localhost:3000**

---

## Tabela `biia` — schema

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `item` | `varchar` | Texto/conteúdo que foi embedado |
| `valor` | `numeric` | Valor numérico associado ao item |
| `vetorizada` | `vector(384)` | Embedding gerado por `all-MiniLM-L6-v2` |

---

## Modelo de embedding

A tabela usa vetores de **384 dimensões**, gerados pelo modelo
`sentence-transformers/all-MiniLM-L6-v2` (via `sentence-transformers` no Python).

---

## Trocar o LLM

O projeto usa OpenAI por padrão. Para trocar:

### Groq (muito mais rápido, modelos open-source)

```bash
pip install groq
```

Em `app.py`, substitua o cliente OpenAI:

```python
from groq import Groq
groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
# Use groq.chat.completions.create({ ... }) — mesma API
```

### Anthropic (Claude)

```bash
pip install anthropic
```

### Grok (xAI)

```python
from openai import OpenAI
client = OpenAI(
    api_key=os.getenv("GROK_API_KEY"),
    base_url="https://api.x.ai/v1",
)
```

---

## Variáveis de ambiente — referência completa

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `SUPABASE_URL` | — | URL do projeto Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | — | Chave service_role (backend only) |
| `OPENAI_API_KEY` | — | Chave da API OpenAI |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Modelo de embedding local |
| `CHAT_MODEL` | `gpt-4o-mini` | Modelo LLM para a resposta |
| `RPC_FUNCTION` | `match_biia` | Nome da função RPC no Supabase |
| `MATCH_COUNT` | `10` | Top-K resultados da busca vetorial |
| `SIMILARITY_THRESHOLD` | `0.0` | Score mínimo de similaridade |
| `PORT` | `3000` | Porta do servidor Flask |

---

## Endpoint da API

### `POST /api/chat`

**Body:**
```json
{
  "messages": [
    { "role": "user", "content": "Qual o valor do item X?" }
  ]
}
```

**Resposta:** Server-Sent Events (SSE)

```
data: {"type":"status","message":"Gerando embedding..."}
data: {"type":"status","message":"Pesquisando na base de conhecimento..."}
data: {"type":"token","content":"O valor"}
data: {"type":"token","content":" do item X é"}
data: {"type":"sources","sources":["item X","item Y"]}
data: {"type":"done"}
```

### `GET /api/health`

Retorna status e configuração atual do servidor.

---

## Segurança

- A **service_role key** nunca é exposta ao frontend — fica apenas no `.env` do servidor
- Adicione `.env` ao `.gitignore` antes de commitar

```bash
echo ".env" >> .gitignore
```