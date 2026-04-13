/**
 * supabase-rag-chat — Backend Server
 *
 * Full RAG pipeline:
 *   1. Receives the user's question
 *   2. Generates a 384-dim embedding using @xenova/transformers
 *      (same model that was used to populate the `biia` table:
 *       Xenova/all-MiniLM-L6-v2  →  vector(384))
 *   3. Runs a pgvector similarity search on Supabase via the
 *      `match_biia` RPC function (see supabase/match_biia.sql)
 *   4. Builds a context-rich system prompt with the retrieved rows
 *   5. Streams the LLM response back via Server-Sent Events (SSE)
 *
 * Table schema (biia):
 *   id         bigserial  — primary key
 *   item       text       — the text chunk that was embedded (content)
 *   metadata   jsonb      — arbitrary key/value pairs (ex: valor, categoria, fonte)
 *   vetorizada vector(384) — the embedding
 *   created_at timestamptz — insertion timestamp
 */

import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import { createClient } from '@supabase/supabase-js';
import OpenAI from 'openai';
import { pipeline } from '@xenova/transformers';

// ── ES Module __dirname shim ─────────────────────────────────────────────────
const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

// ── Validate required env vars ───────────────────────────────────────────────
const REQUIRED_ENV = ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'OPENAI_API_KEY'];
for (const key of REQUIRED_ENV) {
  if (!process.env[key]) {
    console.error(`❌  Missing required environment variable: ${key}`);
    console.error('    Copy .env.example to .env and fill in all values.');
    process.exit(1);
  }
}

// ── Configuration ─────────────────────────────────────────────────────────────
const CONFIG = {
  port:                parseInt(process.env.PORT || '3000', 10),
  supabaseUrl:         process.env.SUPABASE_URL,
  supabaseKey:         process.env.SUPABASE_SERVICE_ROLE_KEY,
  openaiApiKey:        process.env.OPENAI_API_KEY,
  // Local embedding model — must match what was used to populate `biia`
  embeddingModel:      process.env.EMBEDDING_MODEL || 'Xenova/all-MiniLM-L6-v2',
  // LLM for generating the final answer
  chatModel:           process.env.CHAT_MODEL || 'gpt-5.4',
  // Supabase RPC function name
  rpcFunctionName:     process.env.RPC_FUNCTION  || 'match_biia',
  // Top-K results to retrieve
  matchCount:          parseInt(process.env.MATCH_COUNT || '10', 10),
  // Minimum similarity threshold (0–1); results below this are discarded
  similarityThreshold: parseFloat(process.env.SIMILARITY_THRESHOLD || '0.0'),
};

// ── Clients ───────────────────────────────────────────────────────────────────
const supabase = createClient(CONFIG.supabaseUrl, CONFIG.supabaseKey);
const openai   = new OpenAI({ apiKey: CONFIG.openaiApiKey });

// ── Lazy-loaded embedding pipeline ───────────────────────────────────────────
// Loaded once on first request; subsequent calls reuse the cached pipeline.
let _embedder = null;
async function getEmbedder() {
  if (!_embedder) {
    console.log(`🔄  Loading embedding model: ${CONFIG.embeddingModel} …`);
    _embedder = await pipeline('feature-extraction', CONFIG.embeddingModel, {
      // Suppress the download progress bar in production
      progress_callback: null,
    });
    console.log('✅  Embedding model ready.');
  }
  return _embedder;
}

// ── Helper: generate a 384-dim embedding for a text string ───────────────────
async function generateEmbedding(text) {
  const embedder = await getEmbedder();
  // mean_pooling + normalise to get a unit-length sentence embedding
  const output = await embedder(text.trim(), { pooling: 'mean', normalize: true });
  // output.data is a Float32Array; Supabase expects a plain JS Array
  return Array.from(output.data);
}

// ── Helper: vector similarity search via Supabase RPC ─────────────────────────
/**
 * Calls the `match_biia` Postgres function (created in supabase/match_biia.sql).
 * Returns rows: { item, valor, similarity }
 */
async function searchSimilarDocuments(embedding, filter = {}) {
  const { data, error } = await supabase.rpc(CONFIG.rpcFunctionName, {
    query_embedding:      embedding,
    match_count:          CONFIG.matchCount,
    similarity_threshold: CONFIG.similarityThreshold,
    filter:               filter,
  });

  if (error) {
    console.error('Supabase RPC error:', error);
    throw new Error(`Vector search failed: ${error.message}`);
  }

  console.log(`🔍  RPC retornou ${(data || []).length} resultado(s) (threshold: ${CONFIG.similarityThreshold})`);
  if (data && data.length > 0) {
    data.forEach((r, i) => {
      console.log(`   [${i+1}] similarity=${r.similarity?.toFixed(4)}  item="${r.item?.slice(0,60)}"`);
    });
  } else {
    // Busca sem threshold para diagnóstico — mostra os scores reais
    const { data: raw } = await supabase.rpc(CONFIG.rpcFunctionName, {
      query_embedding:      embedding,
      match_count:          5,
      similarity_threshold: 0.0,
      filter:               {},
    });
    if (raw && raw.length > 0) {
      console.log('   ⚠️  Resultados com threshold=0 (para diagnóstico):');
      raw.forEach((r, i) => {
        console.log(`   [${i+1}] similarity=${r.similarity?.toFixed(4)}  item="${r.item?.slice(0,60)}"`);
      });
      console.log(`   ➡️  Considere baixar SIMILARITY_THRESHOLD no .env (atual: ${CONFIG.similarityThreshold})`);
    } else {
      console.log('   ❌  Nenhum resultado mesmo com threshold=0 — verifique os dados na tabela public.biia');
    }
  }

  return data || [];
}

// ── Helper: build the system prompt from retrieved rows ───────────────────────
function buildSystemPrompt(rows) {
  if (rows.length === 0) {
    return `You are a helpful assistant.
The knowledge base did not return any relevant results for this question.
Tell the user honestly that you could not find relevant information in your knowledge base.
Always respond in the same language the user used.`;
  }

  const contextBlock = rows
    .map((row, i) => {
      const sim = row.similarity !== undefined
        ? ` (similaridade: ${(row.similarity * 100).toFixed(1)}%)`
        : '';

      const meta   = row.metadata || {};
      const valor  = meta.valor !== undefined ? meta.valor : null;

      // Monta linha de dados: "Categoria: <item> → Quantidade/Valor: <valor>"
      const valorLine = valor !== null
        ? `Quantidade/Valor: ${valor}`
        : '';

      // Outros campos de metadata além de `valor`
      const extraMeta = Object.entries(meta)
        .filter(([k]) => k !== 'valor')
        .map(([k, v]) => `${k}: ${v}`)
        .join(' | ');

      const parts = [
        `Categoria: ${row.item}`,
        valorLine,
        extraMeta,
      ].filter(Boolean).join('\n');

      return `[${i + 1}]${sim}\n${parts}`;
    })
    .join('\n\n');

  return `Você é um assistente de dados especializado na base de conhecimento BIIA.
Os dados abaixo são registros estruturados com categorias e valores numéricos.
Responda à pergunta do usuário com base EXCLUSIVAMENTE nos dados fornecidos abaixo.
Seja direto e preciso. Formate números claramente.
Responda sempre no mesmo idioma que o usuário usou.

DADOS RECUPERADOS DA BASE:
${contextBlock}

---
Instruções:
- Os campos "Categoria" descrevem o tipo do registro e "Quantidade/Valor" é o número associado.
- Se a pergunta pedir uma soma ou contagem, some os valores relevantes do contexto.
- Se o contexto não contiver a informação pedida, diga: "Não encontrei essa informação na base de conhecimento."
- Nunca invente dados que não estejam no contexto acima.`;
}

// ── Express app ───────────────────────────────────────────────────────────────
const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── POST /api/chat ─────────────────────────────────────────────────────────────
app.post('/api/chat', async (req, res) => {
  const { messages } = req.body;

  if (!messages || !Array.isArray(messages) || messages.length === 0) {
    return res.status(400).json({ error: 'messages array is required' });
  }

  const lastUserMessage = [...messages].reverse().find(m => m.role === 'user');
  if (!lastUserMessage) {
    return res.status(400).json({ error: 'No user message found' });
  }

  // ── Set up SSE ──────────────────────────────────────────────────────────────
  res.setHeader('Content-Type',  'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection',    'keep-alive');
  res.flushHeaders();

  const sendEvent = (data) => res.write(`data: ${JSON.stringify(data)}\n\n`);

  try {
    // ── 1. Generate embedding ─────────────────────────────────────────────────
    sendEvent({ type: 'status', message: 'Gerando embedding da pergunta…' });
    const embedding = await generateEmbedding(lastUserMessage.content);

    // ── 2. Similarity search ──────────────────────────────────────────────────
    sendEvent({ type: 'status', message: 'Pesquisando na base de conhecimento…' });
    const rows = await searchSimilarDocuments(embedding, {});

    const foundMsg = rows.length > 0
      ? `Encontrei ${rows.length} resultado(s) relevante(s). Gerando resposta…`
      : 'Nenhum resultado relevante encontrado. Respondendo mesmo assim…';
    sendEvent({ type: 'status', message: foundMsg });

    // ── 3. Build LLM messages ─────────────────────────────────────────────────
    const systemPrompt       = buildSystemPrompt(rows);
    const conversationHistory = messages.filter(m => m.role !== 'system');
    const llmMessages = [
      { role: 'system', content: systemPrompt },
      ...conversationHistory,
    ];

    // ── 4. Stream LLM response ────────────────────────────────────────────────
    const stream = await openai.chat.completions.create({
      model:       CONFIG.chatModel,
      messages:    llmMessages,
      stream:      true,
      temperature: 0.3,   // lower = more faithful to context
      max_tokens:  2048,
    });

    for await (const chunk of stream) {
      const token      = chunk.choices[0]?.delta?.content;
      const finishReason = chunk.choices[0]?.finish_reason;

      if (token) sendEvent({ type: 'token', content: token });

      if (finishReason === 'stop') {
        // Send source labels (item text truncated) so the UI can display them
        if (rows.length > 0) {
          const sources = rows.map(r => {
            const label = r.item
              ? (r.item.length > 80 ? r.item.slice(0, 77) + '…' : r.item)
              : 'resultado sem texto';
            const sim = r.similarity !== undefined
              ? ` · ${(r.similarity * 100).toFixed(0)}%`
              : '';
            return label + sim;
          });
          sendEvent({ type: 'sources', sources });
        }
        sendEvent({ type: 'done' });
      }
    }

  } catch (err) {
    console.error('RAG pipeline error:', err);
    sendEvent({ type: 'error', message: err.message || 'Erro inesperado no servidor.' });
  } finally {
    res.end();
  }
});

// ── GET /api/health ───────────────────────────────────────────────────────────
app.get('/api/health', (_req, res) => {
  res.json({
    status: 'ok',
    config: {
      rpcFunction:         CONFIG.rpcFunctionName,
      embeddingModel:      CONFIG.embeddingModel,
      chatModel:           CONFIG.chatModel,
      matchCount:          CONFIG.matchCount,
      similarityThreshold: CONFIG.similarityThreshold,
    },
  });
});

// ── Fallback → index.html ─────────────────────────────────────────────────────
app.get('*', (_req, res) =>
  res.sendFile(path.join(__dirname, 'public', 'index.html'))
);

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(CONFIG.port, () => {
  console.log(`\n🚀  supabase-rag-chat running → http://localhost:${CONFIG.port}`);
  console.log(`   Tabela  : biia  (id, item, metadata, vetorizada, created_at)`);
  console.log(`   Embed   : ${CONFIG.embeddingModel}`);
  console.log(`   Chat    : ${CONFIG.chatModel}`);
  console.log(`   Top-K   : ${CONFIG.matchCount}`);
  console.log(`   Threshold: ${CONFIG.similarityThreshold}\n`);
  // Warm up the embedding model in the background
  generateEmbedding('warmup').catch(() => {});
});
