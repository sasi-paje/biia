"""
supabase-rag-chat — Backend Server (Python/Flask)

Full RAG pipeline with optimized queries:
  1. Receives the user's question
  2. Detects user intent and tries optimized SQL queries first
  3. Falls back to vector similarity search via RAG
  4. Streams the LLM response back via Server-Sent Events (SSE)
"""

import os
import json
import re
from dotenv import load_dotenv
from flask import Flask, request, Response
from flask_cors import CORS
from supabase import create_client
from openai import OpenAI
from sentence_transformers import SentenceTransformer

from db import get_client
import queries

load_dotenv()


CONFIG = {
    "port": int(os.getenv("PORT", "3000")),
    "supabase_url": os.getenv("SUPABASE_URL"),
    "supabase_key": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    "openai_api_key": os.getenv("OPENAI_API_KEY"),
    "embedding_model": os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
    "chat_model": os.getenv("CHAT_MODEL", "gpt-4o-mini"),
    "rpc_function_name": os.getenv("RPC_FUNCTION", "match_biia"),
    "match_count": int(os.getenv("MATCH_COUNT", "10")),
    "similarity_threshold": float(os.getenv("SIMILARITY_THRESHOLD", "0.0")),
    "max_context_tokens": 10000,
    "hybrid_match_count": 20,
}

for key in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENAI_API_KEY"]:
    if not os.getenv(key):
        print(f"❌  Missing required environment variable: {key}")
        print("    Copy .env.example to .env and fill in all values.")
        raise ValueError(f"Missing required environment variable: {key}")

supabase = create_client(CONFIG["supabase_url"], CONFIG["supabase_key"])
openai_client = OpenAI(api_key=CONFIG["openai_api_key"])

_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        print(f"🔄  Loading embedding model: {CONFIG['embedding_model']} …")
        _embedder = SentenceTransformer(CONFIG["embedding_model"])
        print("✅  Embedding model ready.")
    return _embedder


def generate_embedding(text):
    embedder = get_embedder()
    embedding = embedder.encode(text.strip(), normalize_embeddings=True)
    return embedding.tolist()


def search_similar_documents(embedding, filter=None):
    if filter is None:
        filter = {}

    try:
        result = supabase.rpc(
            CONFIG["rpc_function_name"],
            {
                "query_embedding": embedding,
                "match_count": CONFIG["match_count"],
                "similarity_threshold": CONFIG["similarity_threshold"],
                "filter": filter,
            }
        ).execute()
        data = result.data
    except Exception as e:
        print("Supabase RPC error:", e)
        raise RuntimeError(f"Vector search failed: {str(e)}")

    print(f"🔍  RPC returned {len(data or [])} result(s) (threshold: {CONFIG['similarity_threshold']})")
    if data and len(data) > 0:
        for i, r in enumerate(data):
            print(f"   [{i+1}] similarity={r.get('similarity', 0):.4f}  item=\"{str(r.get('item', ''))[:60]}\"")
    else:
        raw_result = supabase.rpc(
            CONFIG["rpc_function_name"],
            {
                "query_embedding": embedding,
                "match_count": 5,
                "similarity_threshold": 0.0,
                "filter": {},
            }
        ).execute()
        raw = raw_result.data
        if raw and len(raw) > 0:
            print("   ⚠️  Results with threshold=0 (for diagnosis):")
            for i, r in enumerate(raw):
                print(f"   [{i+1}] similarity={r.get('similarity', 0):.4f}  item=\"{str(r.get('item', ''))[:60]}\"")
            print(f"   ➡️  Consider lowering SIMILARITY_THRESHOLD in .env (current: {CONFIG['similarity_threshold']})")
        else:
            print("   ❌  No results even with threshold=0 — check data in public.biia table")

    return data or []


def search_hybrid_documents(user_query, limit=None):
    if limit is None:
        limit = CONFIG["hybrid_match_count"]

    keyword_results = queries.search_hybrid(user_query, limit=limit)

    if keyword_results:
        print(f"🔍  Keyword search returned {len(keyword_results)} result(s)")
        for i, r in enumerate(keyword_results):
            print(f"   [{i+1}] keyword match  item=\"{str(r.get('item', ''))[:60]}\"")
        return keyword_results

    print("🔍  Keyword search empty, falling back to vector search...")
    embedding = generate_embedding(user_query)
    vector_results = search_similar_documents(embedding, {})

    if not vector_results:
        print("🔍  Vector search empty, trying category fallback...")
        category_fallback = search_by_category_fallback(user_query)
        if category_fallback:
            print(f"🔍  Category fallback returned {len(category_fallback)} result(s)")
            return category_fallback

    return vector_results


def search_by_category_fallback(query, limit=20):
    words = query.lower().split()
    keywords = [w for w in words if len(w) > 3 and w not in ["para", "qual", "qual", "mais", "sobre", "como", "porque", "quanto", "quais", "esse", "essa", "este", "esta"]]
    if not keywords:
        keywords = words[-2:] if len(words) >= 2 else words
    try:
        client = get_client()
        response = client.table("biia").select("id, item, metadata").execute()
        if not response or not response.data:
            return []
        results = []
        for row in response.data:
            item_lower = (row.get("item") or "").lower()
            for kw in keywords:
                if kw in item_lower:
                    results.append({
                        "id": row["id"],
                        "item": row["item"],
                        "metadata": row.get("metadata"),
                        "similarity": 1.0
                    })
                    break
        return results[:limit]
    except Exception as e:
        print(f"Error in category fallback: {e}")
        return []


SCHEMA_SUMMARY = """Base de dados: tabela 'biia' com estrutura:
- id (bigint): identificador único
- item (text): texto descritivo/categoria do registro (ex: "Em Situação de Rua", "Deficiente", etc)
- metadata (jsonb): dados extras em formato JSON, pode conter: valor (float/número de inscrições), e outros campos
- vetorizada (vector 384): vetor de embeddings para busca semântica
- created_at (timestamptz): data de criação

Instruções sobre a base:
- Responda sempre com base nos dados fornecidos no contexto.
- Formate valores numéricos com separador de milhares: X.XXX
- O campo 'item' contém a categoria (ex: condição de moradia, situação de rua, etc)
- O campo 'metadata.valor' contém a quantidade de inscrições
- Seja direto e preciso em todas as respostas.
- Se não houver dados suficientes para responder, diga o que você tem disponível."""


def get_db_context_summary():
    try:
        total = queries.get_items_count()
        stats = queries.get_aggregated_stats()
        top = queries.get_top_values(limit=3)
        return {
            "total": total,
            "stats": stats,
            "top": top,
        }
    except Exception as e:
        print(f"⚠️  Could not fetch DB context: {e}")
        return None


def build_partial_context_prompt(db_ctx):
    total = db_ctx.get("total", 0)
    stats = db_ctx.get("stats", {})
    top = db_ctx.get("top", [])

    top_str = "\n".join([
        f"- {r.get('item')}: {r.get('metadata', {}).get('valor', 0):,.2f}"
        for r in top
    ]) if top else "Sem dados de valores disponíveis."

    return f"""{SCHEMA_SUMMARY}

Contexto atual da base:
- Total de registros: {total:,}
- Média: {stats.get('avg_valor', 0):,.2f}
- Menor valor: {stats.get('min_valor', 0):,.2f}
- Maior valor: {stats.get('max_valor', 0):,.2f}
- Soma total: {stats.get('sum_valor', 0):,.2f}

Top 3 valores cadastrados:
{top_str}

Você tem acesso parcial à base de dados. Se o usuário perguntar sobre algo
que não esteja nos dados acima, informe que você só consegue responder
sobre informações disponíveis na base BIIA."""


def build_system_prompt(rows, intent_data=None):
    if intent_data:
        return build_intent_prompt(intent_data)
    return build_rag_prompt(rows)


def build_intent_prompt(intent_data):
    intent_type = intent_data.get("type")
    result = intent_data.get("result")

    if intent_type == "count":
        return f"""{SCHEMA_SUMMARY}

DADOS:
O banco de dados contém {result:,} registros no total."""

    if intent_type == "sum":
        return f"""{SCHEMA_SUMMARY}

DADOS:
Total de inscrições: {result:,.0f}"""

    if intent_type == "average":
        return f"""{SCHEMA_SUMMARY}

DADOS:
A média dos valores na base é: {result:,.2f}"""

    if intent_type == "aggregate":
        query_result = intent_data.get("query_result", [])
        if query_result and len(query_result) > 0:
            result_value = query_result[0].get("result", 0)
            table = intent_data.get("table", "desconhecida")
            operation = intent_data.get("operation", "resultado")
            col = intent_data.get("column", "")
            return f"""{SCHEMA_SUMMARY}

DADOS:
Tabela consultada: {table}
Operação: {operation.upper()} da coluna {col}
Resultado: {result_value:,.2f}"""
        return f"""{SCHEMA_SUMMARY}

DADOS:
Não foi possível obter o resultado da consulta."""

    if intent_type in ["top_values", "min_values"]:
        items_str = "\n".join([
            f"- {r.get('item', '')}: {r.get('metadata', {}).get('valor', 0):,.2f}"
            for r in result
        ])
        label = "maiores" if intent_type == "top_values" else "menores"
        return f"""{SCHEMA_SUMMARY}

DADOS:
Os {label} valores na base são:
{items_str}"""

    if intent_type in ["range", "category"]:
        items_str = "\n".join([
            f"- {r.get('item', '')}: {r.get('metadata', {}).get('valor', 0):,.2f}"
            for r in result
        ])
        return f"""{SCHEMA_SUMMARY}

DADOS:
Resultados encontrados:
{items_str}"""

    if intent_type == "search_by_value":
        search_result = intent_data.get("result", [])
        table = intent_data.get("table", "desconhecida")
        search_col = intent_data.get("column", "")
        search_val = intent_data.get("value", "")
        if search_result and len(search_result) > 0:
            rows_info = []
            for r in search_result:
                row_str = ", ".join([f"{k}: {v}" for k, v in r.items() if k not in ["id", "created_at"]])
                rows_info.append(row_str)
            results_str = "\n".join([f"- {info}" for info in rows_info])
            return f"""{SCHEMA_SUMMARY}

RESULTADO DA BUSCA:
Valor procurado: {search_val}
Tabela: {table}
Coluna: {search_col}

Registros encontrados:
{results_str}"""
        return f"""{SCHEMA_SUMMARY}

Não foram encontrados registros para o valor '{search_val}'."""

    return build_rag_prompt([])


def build_rag_prompt(rows):
    if len(rows) == 0:
        db_ctx = get_db_context_summary()
        if db_ctx:
            return build_partial_context_prompt(db_ctx)
        return f"""{SCHEMA_SUMMARY}

A busca não retornou resultados específicos para esta pergunta.
Tente reformular ou pergunte sobre outro tema da base."""

    context_block = []
    for i, row in enumerate(rows):
        sim = f" (similaridade: {row.get('similarity', 0) * 100:.1f}%)" if row.get('similarity') is not None else ""

        meta = row.get("metadata", {}) or {}
        valor = meta.get("valor")

        valor_line = f"Valor: {valor:,.2f}" if valor is not None else ""

        extra_meta = " | ".join(f"{k}: {v}" for k, v in meta.items() if k != "valor")

        parts = [
            f"Categoria: {row.get('item', '')}",
            valor_line,
            extra_meta,
        ]
        parts = [p for p in parts if p]

        context_block.append(f"[{i + 1}]{sim}\n" + "\n".join(parts))

    context_block_str = "\n\n".join(context_block)

    return f"""{SCHEMA_SUMMARY}

DADOS RECUPERADOS DA BASE:
{context_block_str}"""


app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)


@app.route("/api/chat", methods=["POST"])
def chat():
    messages = request.json.get("messages")

    if not messages or not isinstance(messages, list) or len(messages) == 0:
        return {"error": "messages array is required"}, 400

    last_user_message = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user_message:
        return {"error": "No user message found"}, 400

    def send_event(data):
        return f"data: {json.dumps(data)}\n\n"

    def generate():
        try:
            yield send_event({"type": "status", "message": "Analisando sua pergunta…"})
            
            intent_data = queries.detect_intent_and_query(last_user_message["content"])
            
            if intent_data:
                yield send_event({"type": "status", "message": f"Consulta otimizada detectada ({intent_data['type']})…"})
                intent_type = intent_data.get("type")
                rows = []
                
                if intent_type in ["aggregate", "select"]:
                    query = intent_data.get("query")
                    if query:
                        raw_result = queries.execute_custom_query(query)
                        if raw_result:
                            if intent_type == "aggregate":
                                result_value = raw_result[0].get("result") if raw_result else 0
                                intent_data["query_result"] = raw_result
                                rows = [{"item": f"Resultado da consulta", "metadata": {"valor": result_value}}]
                            else:
                                rows = raw_result
                
                system_prompt = build_system_prompt(rows, intent_data)
            else:
                yield send_event({"type": "status", "message": "Buscando na base de conhecimento…"})
                rows = search_hybrid_documents(last_user_message["content"])
                system_prompt = build_system_prompt(rows)
                intent_type = None

            found_msg = f"Encontrei {len(rows) if rows else 0} resultado(s). Gerando resposta…"
            yield send_event({"type": "status", "message": found_msg})

            conversation_history = [m for m in messages if m.get("role") != "system"]
            llm_messages = [
                {"role": "system", "content": system_prompt},
                *conversation_history,
            ]

            stream = openai_client.chat.completions.create(
                model=CONFIG["chat_model"],
                messages=llm_messages,
                stream=True,
                temperature=0.3,
                max_tokens=2048,
            )

            accumulated_text = ""
            for chunk in stream:
                token = chunk.choices[0].delta.content
                finish_reason = chunk.choices[0].finish_reason

                if token:
                    accumulated_text += token
                    yield send_event({"type": "token", "content": token})

                if finish_reason == "stop":
                    if rows and intent_type:
                        sources = []
                        for r in rows:
                            label = r.get("item", "resultado sem texto")
                            if len(label) > 80:
                                label = label[:77] + "…"
                            sim = f" · {r.get('similarity', 0) * 100:.0f}%" if r.get("similarity") is not None else ""
                            valor = r.get("metadata", {}).get("valor")
                            if valor is not None:
                                label += f" ({valor:,.2f})"
                            sources.append(label + sim)
                        yield send_event({"type": "sources", "sources": sources})
                    yield send_event({"type": "done"})

        except Exception as err:
            print("RAG pipeline error:", err)
            yield send_event({"type": "error", "message": str(err) or "Erro inesperado no servidor."})

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/health")
def health():
    return {
        "status": "ok",
        "config": {
            "rpc_function": CONFIG["rpc_function_name"],
            "embedding_model": CONFIG["embedding_model"],
            "chat_model": CONFIG["chat_model"],
            "match_count": CONFIG["match_count"],
            "similarity_threshold": CONFIG["similarity_threshold"],
        },
    }


@app.route("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    print(f"\n🚀  supabase-rag-chat running → http://localhost:{CONFIG['port']}")
    print(f"   Tabela  : biia  (id, item, metadata, vetorizada, created_at)")
    print(f"   Embed   : {CONFIG['embedding_model']}")
    print(f"   Chat    : {CONFIG['chat_model']}")
    print(f"   Top-K   : {CONFIG['match_count']}")
    print(f"   Threshold: {CONFIG['similarity_threshold']}\n")
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=True)