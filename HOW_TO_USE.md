# NaijaPulse Engine — Quick Start Guide

---

## 🚀 Quick Start (Restarting Servers & Pipeline)

### 1. Start Ollama (if not running)
```bash
ollama serve                    # Starts Ollama server
ollama pull nomic-embed-text    # Downloads embedding model (~4GB, one-time)
```

### 2. Start the API Server (Port 8000)
```bash
cd /Users/Adegoke/Documents/news
source venv/bin/activate
cd naijapulse-engine
uvicorn phase6_api:app --port 8000 --host 0.0.0.0
```

### 3. Start the UI Server (Port 8001) - Optional
```bash
cd /Users/Adegoke/Documents/news/naijapulse-engine/ui
python -m http.server 8001
```

### 4. Run the Full Pipeline
In a **separate terminal** (API/UI must be stopped or won't conflict):
```bash
cd /Users/Adegoke/Documents/news
source venv/bin/activate
cd naijapulse-engine

# Seed source bias (run once, or whenever you want fresh lean data)
./venv/bin/python seed_source_bias.py

# Run full pipeline with live progress
./venv/bin/python run_pipeline.py --bias
```

### 5. Verify Results
```bash
# Check pipeline health
curl -s http://localhost:8000/pipeline-health | python3 -m json.tool

# Or open in browser
open http://localhost:8000/docs
```

---

## 🔁 Restart Commands (for when you've used it before)

```bash
# Restart everything (API + UI + Pipeline)
# Terminal 1: API Server
ollama serve
cd /Users/Adegoke/Documents/news && source venv/bin/activate
cd naijapulse-engine && uvicorn phase6_api:app --port 8000

# Terminal 2: UI Server (optional)
cd /Users/Adegoke/Documents/news/naijapulse-engine/ui && python -m http.server 8001

# Terminal 3: Pipeline
cd /Users/Adegoke/Documents/news && source venv/bin/activate
cd naijapulse-engine
./venv/bin/python seed_source_bias.py
./venv/bin/python run_pipeline.py --bias

# Then open http://localhost:8000/docs or http://localhost:8001/
```

---

## 📊 Pipeline Progress (What you'll see)

When running `./venv/bin/python run_pipeline.py --bias`:

```
[Phase 1] Fetching RSS feeds...
  → Punch: 52 articles
  → Vanguard: 41 articles
  → ...
[Phase 1] ✅ Ingested 498 articles

[Phase 2] Embedding via Ollama...
  → Processing 498 articles in batches of 32...
  → [████████████████████] 100% | 498/498
[Phase 2] ✅ Embedded 498 articles

[Phase 3] Deduplicating wire copy...
[Phase 3] ✅ Dedup complete

[Phase 4] Clustering into stories...
  → 498 canonical articles → 163 story clusters
[Phase 4] ✅ Clustered

[Phase 5] Tagging bias + blindspots...
  → Blindspots flagged: 2
[Phase 5] ✅ Bias tagged, blindspots detected
```

---

## 🛠️ Common Commands

| Command | Description |
|---------|-------------|
| `ollama serve` | Start Ollama server |
| `ollama pull nomic-embed-text` | Download embedding model |
| `./venv/bin/python run_pipeline.py --bias` | Run full 5-phase pipeline |
| `./venv/bin/python run_pipeline.py --bias-only` | Re-run only Phase 5 (bias tagging) |
| `uvicorn phase6_api:app --port 8000` | Start API server |
| `python -m http.server 8001` | Start UI server (in ui/ folder) |
| `curl http://localhost:8000/pipeline-health` | Check pipeline status |
| `Ctrl+C` | Stop any running server/pipeline |

---

## ❗ Important Notes

- **Ollama must be running** before running the pipeline
- **seed_source_bias.py** should run before `--bias` (or once initially)
- Pipeline writes to Supabase, API reads from it
- API is read-only (GET only) - no data is modified through endpoints
- UI at `http://localhost:8001/` makes requests to API at `http://localhost:8000`

---

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ollama: command not found` | Install Ollama from https://ollama.com |
| `curl: Failed to connect` | Start API server first |
| `Missing SUPABASE_URL` | Check `.env` file in `naijapulse-engine/` |
| Pipeline hangs at Phase 2 | Ollama not running - run `ollama serve` |
| No stories after pipeline | Check `.env` and re-run `seed_source_bias.py` |