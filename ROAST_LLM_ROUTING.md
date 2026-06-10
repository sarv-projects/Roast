# ROAST LLM Routing Architecture

> Extracted from the ROAST V3.0 spec, `router.py`, `groq_client.py`, `gemini_client.py`, `nvidia_nim_client.py`, `openrouter_client.py`, `circuit_breaker.py`, `config.py`, and `orchestrator.py`.
> Generated from live source at `/home/sarvesh/projects/roast/`

---

## 1. Providers (5 total)

| # | Provider | Env Var | Free Tier Limits |
|---|----------|---------|-----------------|
| 1 | **Groq** | `GROQ_API_KEYS` (comma-separated) | 8B: 14,400 RPD, Scout/70B/32B/20B: 1,000 RPD |
| 2 | **Gemini** (Google) | `GEMINI_API_KEYS` (comma-separated) | Flash Lite: 500 RPD, Gemma 4 27B: 1,500 RPD |
| 3 | **NVIDIA NIM** | `NVIDIA_NIM_API_KEY` | 40 RPM, no daily token limit, permanently free |
| 4 | **Cerebras** | `CEREBRAS_API_KEY` | 1M tokens/day free, 30 RPM |
| 5 | **OpenRouter** | `OPENROUTER_API_KEY` | 50 RPD (last resort only) |

---

## 2. Agents & Their Model Assignments

### 2.1 MarketContextAgent
- **Model**: `llama-3.1-8b-instant`
- **Provider**: Groq
- **Execution**: Alone first (Stage 2)
- **Why 8B**: Interprets already-distilled context from DIVE; no heavy reasoning needed
- **Router function**: `call_groq_8b()`

### 2.2 RedFlagAgent
- **Primary**: `llama-3.3-70b-versatile` (Groq)
- **Fallback**: `llama-3.1-8b-instant` (Groq)
- **Execution**: Parallel (Stage 3)
- **Router function**: `call_red_flag_agent()`
- **Quality gate**: Inference chains must pass semantic filter (blocklist of generic phrases)
- **Note**: Spec originally said Gemini 3.1 Flash Lite, but actual implementation uses Groq 70B

### 2.3 SixSecondAndTrajectoryAgent
- **Primary**: `qwen/qwen3-32b` (Groq)
- **Fallback**: `llama-3.1-8b-instant` (Groq)
- **Execution**: Parallel (Stage 3)
- **Qwen thinking mode**: Strips `<think>...</think>` wrapping from output; raises error if truncated thinking is detected
- **Router function**: `call_six_second_agent()`

### 2.4 CompetitivePositioningAgent
- **Primary**: `openai/gpt-oss-20b` (Groq)
- **Fallback**: NVIDIA NIM (`meta/llama-3.3-70b-instruct`)
- **Execution**: Parallel (Stage 3)
- **Router function**: `call_competitive_agent()`

### 2.5 TechnicalDepthAgent
- **Primary**: `openai/gpt-oss-120b` (Groq) — uses agentic loop (multiple calls with DuckDuckGo search)
- **Fallback (non-agentic)**: `openai/gpt-oss-120b` → `llama-3.1-8b-instant` (Groq)
- **Execution**: Parallel (Stage 3)
- **Semaphore**: Dedicated `_tech_depth_sem = Semaphore(3)` to prevent TPM overflow (24K TPM with 3 keys, ~3.5K tokens per call)
- **Router function**: `call_technical_depth_agent()`

### 2.6 ReviewAgent (Highest Stakes)
- **Execution**: Alone last (Stage 5)
- **Full 6-tier fallback chain** (see §3 below)
- **Quality gate**: 250–2000 word range, all follow-up lists present, inference chains (→ arrows) in hurting section, action_plan ≥60 words
- **Retry**: Up to 2 attempts per provider on quality gate failure
- **Partial assembly**: If all providers fail, assembles basic review from upstream outputs

### 2.7 ResumeExtractor
- **Model**: `llama-3.1-8b-instant` (Groq)
- **Cached by hash**: Same resume hash returns cached extraction
- **Runs pre-pipeline**

### 2.8 Supporting LLM Calls (Not Full Agents)

| Task | Model | Provider |
|------|-------|----------|
| DIVE Context Distiller | `llama-3.1-8b-instant` | Groq |
| Source classifier (ingestion) | `llama-3.1-8b-instant` | Groq |
| JD parsing | `llama-3.1-8b-instant` | Groq |
| Breaking signal synthesis | `gemini-2.5-flash-lite` | Gemini |
| Ingestion signal extraction | `gemma-4-27b` | Gemini |
| FollowUpAgent (on-demand) | `llama-3.1-8b-instant` | Groq |

---

## 3. ReviewAgent Fallback Chain

Defined in `router.py` as `REVIEW_MODEL_CHAIN`:

| Priority | Provider | Model | Notes |
|----------|----------|-------|-------|
| 1 | Groq | `llama-3.3-70b-versatile` | Primary. 280 tok/s, 32K output, 1,000 RPD |
| 2 | Groq | `openai/gpt-oss-20b` | Different TPM bucket, 1,000 tok/s |
| 3 | Groq | `qwen/qwen3-32b` | Separate TPM bucket, 400 tok/s |
| 4 | Gemini | `gemini-2.5-flash-lite` | Thinking disabled, 500 RPD |
| 5 | NVIDIA NIM | `meta/llama-3.3-70b-instruct` | No daily cap |
| 6 | OpenRouter | `meta-llama/llama-3.3-70b:free` | 50 RPD, emergency-only |

**Retry policy**: 3 attempts per provider with 2s/4s/8s exponential backoff. Timeout per attempt: 30 seconds.

**Note**: The spec (v2.txt, Section 8.5) lists a *different* fallback chain (llama-4-scout → 70b-versatile → qwen3-32b → Cerebras → OpenRouter), but `router.py` is the **actual implementation** with 6 tiers including NVIDIA NIM instead of Cerebras.

---

## 4. Key Rotation

### 4.1 Groq Key Rotation (`groq_client.py`)

- **Source**: `GROQ_API_KEYS` from `.env` — comma-separated list
- **Strategy**: Distributed round-robin via Redis `INCR` (`groq:round_robin_counter`) — works across multiple workers
- **On 429**: `_rotate()` increments index by 1, wraps around
- **RPD tracking per key per model**: Redis key `groq:rpd:{model}:{key_index}`
- **Midnight TTL**: Set only on first call of the day, with 0–300s random jitter to stagger reset
- **Budget check**: `_check_rpd()` scans all keys; returns True if ANY key has remaining budget

### 4.2 Gemini Key Rotation (`gemini_client.py`)

- **Source**: `GEMINI_API_KEYS` from `.env` — comma-separated list
- **Strategy**: Simple index rotation with `asyncio.Lock`
- **On 429/rate-limit/quota**: `_rotate()` increments index by 1, wraps around
- **Models**: `gemini-2.5-flash-lite`, `gemma-4-26b-a4b-it`

### 4.3 NVIDIA NIM / OpenRouter / Cerebras

- Single key, no rotation
- Circuit breaker only

---

## 5. Circuit Breakers

### 5.1 Custom `CircuitBreaker` Class (`circuit_breaker.py`)

Three-state pattern — clean 30-line implementation (replaces abandoned `aiobreaker` library):

| State | Behaviour |
|-------|-----------|
| **closed** | Normal operation, requests go through |
| **open** | Provider failed ≥3 consecutive times. `should_skip()` returns True (skip entirely) |
| **half_open** | After 300s cooldown, allows one probe request. Success → closed. Failure → open again |

### 5.2 Module-Level Singletons

```python
groq_circuit = CircuitBreaker(name="groq")
gemini_circuit = CircuitBreaker(name="gemini")
cerebras_circuit = CircuitBreaker(name="cerebras")
openrouter_circuit = CircuitBreaker(name="openrouter")
nim_circuit = CircuitBreaker(name="nvidia_nim")
```

### 5.3 Usage in Clients

- Every client checks `{provider}_circuit.should_skip()` at the start
- Records failure on `APIStatusError`, `Exception`
- Records success on valid response
- Discord alert fires when any circuit opens
- Startup warmup event pings all circuits on FastAPI startup

---

## 6. Proactive Fallback (Groq RateLimitMonitor)

### 6.1 RPM Header Tracking

- Groq returns `x-ratelimit-remaining-requests` in response headers
- Tracked in Redis: `groq:rpm_remaining:{model}` with 60s expiry
- **Threshold**: When remaining drops below **50**, logs a warning (`groq_rpm_low`)
- Logging happens but actual fallback switching occurs in the caller (router chain)

### 6.2 RPD Server-Side Tracking

- Groq does **not** expose RPD in headers, so it's tracked server-side
- Redis key: `groq:rpd:{model}:{key_index}`
- Incremented on success, TTL set to midnight UTC with jitter
- Throws `RuntimeError("groq_rpd_exhausted:{model}")` when budget is exhausted

---

## 7. Semaphores (Concurrency Control)

From `orchestrator.py`:

| Semaphore | Limit | Purpose |
|-----------|-------|---------|
| `_groq_sem` | 2 | Max 2 concurrent Groq LLM calls |
| `_gemini_sem` | 1 | Max 1 concurrent Gemini call |
| `_global_sem` | 3 | Max 3 simultaneous full analysis pipelines |
| `_tech_depth_sem` | 3 | gpt-oss-120b TPM management (24K TPM across 3 keys) |

---

## 8. Pipeline Execution Order

```
Stage 1:  DIVE retrieval + breaking signal overlay → FullMarketContext
Stage 2:  MarketContextAgent (alone, under _groq_sem)
Stage 3:  RedFlagAgent, SixSecondAgent, CompetitiveAgent, TechnicalDepthAgent (parallel)
Stage 4:  Python synthesis function (no LLM — deterministic concatenation with smart truncation)
Stage 5:  ReviewAgent with fallback chain
Post:     Anonymised corpus storage, bullet curation, Discord notification
```

---

## 9. Agent-Specific Router Functions

Defined in `backend/llm/router.py`:

| Function | Primary Model | Fallback Model(s) | Used By |
|----------|--------------|-------------------|---------|
| `call_review_agent()` | See 6-tier chain §3 | Full chain | ReviewAgent |
| `call_groq_8b()` | `llama-3.1-8b-instant` | None | MarketContextAgent, DIVE distiller, JD parser, FollowUpAgent |
| `call_red_flag_agent()` | `llama-3.3-70b-versatile` | `llama-3.1-8b-instant` | RedFlagAgent |
| `call_six_second_agent()` | `qwen/qwen3-32b` | `llama-3.1-8b-instant` | SixSecondAgent |
| `call_competitive_agent()` | `openai/gpt-oss-20b` | NVIDIA NIM | CompetitiveAgent |
| `call_technical_depth_agent()` | `openai/gpt-oss-120b` (agentic) → `llama-3.1-8b-instant` | 2-level fallback | TechnicalDepthAgent |

---

## 10. Model Capabilities Summary

| Model | Provider | Tok/s | RPD | Use Case |
|-------|----------|-------|-----|----------|
| `llama-3.1-8b-instant` | Groq | High | 14,400 | Workhorse — context distillation, JD parsing, classification, fallback |
| `llama-3.3-70b-versatile` | Groq | 280 | 1,000 | ReviewAgent primary, RedFlagAgent |
| `qwen/qwen3-32b` | Groq | 400 | 1,000 | SixSecondAgent (thinking mode), ReviewAgent fallback |
| `openai/gpt-oss-20b` | Groq | 1,000 | 1,000 | CompetitiveAgent, ReviewAgent fallback |
| `openai/gpt-oss-120b` | Groq | — | 1,000 | TechnicalDepthAgent agentic loop |
| `gemini-2.5-flash-lite` | Gemini | 159 | 500 | ReviewAgent fallback, breaking signal synthesis |
| `gemma-4-27b` | Gemini | — | 1,500 | Ingestion signal extraction |
| `meta/llama-3.3-70b-instruct` | NVIDIA NIM | — | Unlimited | CompetitiveAgent fallback, ReviewAgent fallback |
| `meta-llama/llama-3.3-70b:free` | OpenRouter | Slow | 50 | Last-resort emergency only |

---

## 11. Environment Variables Reference

| Variable | Purpose |
|----------|---------|
| `GROQ_API_KEYS` | Comma-separated Groq API keys (rotation pool) |
| `GEMINI_API_KEYS` | Comma-separated Gemini API keys (rotation pool) |
| `CEREBRAS_API_KEY` | Cerebras API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `NVIDIA_NIM_API_KEY` | NVIDIA NIM API key |
| `TAVILY_API_KEY_DEEP` | Tavily key 1 — targeted site: queries |
| `TAVILY_API_KEY_GENERAL` | Tavily key 2 — broad market queries |
| `UPSTASH_REDIS_URL` / `TOKEN` | Upstash Redis connection (all state) |
| `LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` | Langfuse observability tracing |
| `DISCORD_WEBHOOK_URL` | Pipeline completion + circuit breaker alerts |

---

## 12. Key Architectural Decisions

1. **No paid models** — all 5 providers are permanently free-tier
2. **Multiple Groq keys** via comma-separated env var — legit strategy for student project
3. **Redis-based distributed round-robin** for Groq key rotation (works across multiple containers)
4. **Custom circuit breaker** (30 lines) instead of abandoned `aiobreaker` library
5. **Proactive fallback** — switches provider before hitting rate limits (RPM threshold: 50)
6. **Server-side RPD tracking** since Groq doesn't expose RPD in response headers
7. **Per-agent routing** via dedicated functions in `router.py` rather than a generic dispatch
8. **Quality gate on ReviewAgent** — word count, follow-up presence, inference chain arrows, action plan length
9. **Semaphores prevent TPM/RPM overflow** — especially critical for gpt-oss-120b (3 concurrent max)
