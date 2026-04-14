"""
supabase-rag-chat — Backend Server (Python/Flask)

RAG pipeline simplificado:
  1. Recebe a pergunta do usuário
  2. Gera embedding da pergunta
  3. Busca documentos similares via vector search
  4. Retorna o resultado ao LLM para gerar resposta
"""

import os
import json
from dotenv import load_dotenv
from flask import Flask, request, Response
from flask_cors import CORS
from supabase import create_client
from openai import OpenAI
from sentence_transformers import SentenceTransformer

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

    return data or []


def search_by_keyword(user_query, limit=20):
    query_lower = user_query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]

    try:
        response = supabase.table("biia").select("id, item, metadata").execute()
        if not response or not response.data:
            return []

        results = []
        for row in response.data:
            item_text = (row.get("item") or "").lower()
            score = 0
            for word in query_words:
                if word in item_text:
                    score += 1

            if score > 0:
                results.append({
                    "id": row["id"],
                    "item": row["item"],
                    "metadata": row.get("metadata"),
                    "similarity": score / len(query_words),
                    "match_type": "keyword"
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]
    except Exception as e:
        print(f"Keyword search error: {e}")
        return []


def search_hybrid(user_query):
    print(f"\n📝 Busca híbrida para: '{user_query}'")

    print("🔍 Executando busca semântica...")
    embedding = generate_embedding(user_query)
    semantic_results = search_similar_documents(embedding, {})

    if semantic_results and len(semantic_results) > 0:
        best_sim = semantic_results[0].get("similarity", 0)
        print(f"   Semântica: {len(semantic_results)} resultados, melhor similaridade: {best_sim:.4f}")
        for i, r in enumerate(semantic_results[:3]):
            print(f"   [{i+1}] sim={r.get('similarity', 0):.4f} → \"{str(r.get('item', ''))[:50]}\"")

    print("🔍 Executando busca por palavras-chave...")
    keyword_results = search_by_keyword(user_query)

    if keyword_results and len(keyword_results) > 0:
        best_kw = keyword_results[0].get("similarity", 0)
        print(f"   Keywords: {len(keyword_results)} resultados, melhor score: {best_kw:.4f}")
        for i, r in enumerate(keyword_results[:3]):
            print(f"   [{i+1}] score={r.get('similarity', 0):.4f} → \"{str(r.get('item', ''))[:50]}\"")

    all_results = {}

    if semantic_results:
        for r in semantic_results:
            key = r.get("item", "")
            if key not in all_results:
                all_results[key] = {
                    "id": r["id"],
                    "item": r["item"],
                    "metadata": r.get("metadata"),
                    "similarity": r.get("similarity", 0),
                    "match_types": ["semantic"]
                }
            else:
                all_results[key]["match_types"].append("semantic")

    if keyword_results:
        for r in keyword_results:
            key = r.get("item", "")
            if key in all_results:
                all_results[key]["similarity"] = max(all_results[key]["similarity"], r.get("similarity", 0))
                all_results[key]["match_types"].append("keyword")
            else:
                all_results[key] = {
                    "id": r["id"],
                    "item": r["item"],
                    "metadata": r.get("metadata"),
                    "similarity": r.get("similarity", 0),
                    "match_types": ["keyword"]
                }

    results = list(all_results.values())
    results.sort(key=lambda x: x["similarity"], reverse=True)

    print(f"\n✅ Total de resultados únicos: {len(results)}")
    print("📊 Ranking final:")
    for i, r in enumerate(results[:5]):
        match_types = ", ".join(r.get("match_types", []))
        print(f"   [{i+1}] sim={r.get('similarity', 0):.4f} [{match_types}] → \"{str(r.get('item', ''))[:50]}\"")

    return results[:CONFIG["match_count"]]


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


def build_rag_prompt(rows):
    if len(rows) == 0:
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
            print(f"\n💬 Pergunta do usuário: {last_user_message['content']}")
            yield send_event({"type": "status", "message": "Buscando na base de conhecimento…"})

            rows = search_hybrid(last_user_message["content"])
            system_prompt = build_rag_prompt(rows)

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

            for chunk in stream:
                token = chunk.choices[0].delta.content
                finish_reason = chunk.choices[0].finish_reason

                if token:
                    yield send_event({"type": "token", "content": token})

                if finish_reason == "stop":
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