# CruiseNews Aggregator

A local-first news aggregation and enrichment project that ingests articles, stores them in Supabase, and prepares them for downstream embedding and clustering workflows.

## What this project includes

- Article ingestion pipeline
- Supabase-backed storage setup
- Local embedding preparation workflow
- Progress notes and implementation plan

## Getting started

1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure your environment variables in a local `.env` file.
3. Run the ingestion pipeline:
   ```bash
   python naijapulse-engine/run_pipeline.py
   ```

## Notes

- Local secrets and environment files are intentionally ignored.
- The project is designed to work with Supabase and optional local Ollama embedding support.
