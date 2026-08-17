# Model Council — Raw Experiment v0.2

A minimal terminal-first multi-model deliberation experiment for local Ollama models,
with optional Gemini API support.

## What this prototype tests

1. Discover installed Ollama models.
2. Select models dynamically.
3. Recommend experimental roles from the selected model profiles.
4. Check model health before deliberation.
5. Run independent first-round reasoning.
6. Extract compact structured positions.
7. Detect explicit disagreements.
8. Run targeted deliberation on disagreements.
9. Synthesize a final answer.
10. Persist the complete session transcript/state to JSON.
11. Resume sessions later.
12. Run a small benchmark comparing individual models vs Council.

## Requirements

- Python 3.10+
- Ollama running locally
- At least 2 healthy Ollama models

Optional:
- Gemini API key for hybrid tests:
  - Windows PowerShell:
    `$env:GEMINI_API_KEY="your_key"`

No database, Docker, VPS, Redis, or vector database is required for v0.2.

## Run

```powershell
python main.py
```

Then:

```text
Council > models
Council > select
Council > roles
Council > new
Council > ask Should an AI startup use local, cloud, or hybrid models?
Council > status
Council > export
Council > debug
Council > sessions
Council > resume <session_id>
Council > exit
```

Direct commands:

```powershell
python main.py models
python main.py ask "Your question here"
python main.py sessions
python main.py resume <session_id>
python main.py export <session_id>
python main.py debug <session_id>
```

## Optional Gemini

Set:

```powershell
$env:GEMINI_API_KEY="..."
```

Then add a Gemini model to `config.json` and set `GEMINI_API_KEY`.

Example config:

```json
{
  "gemini_models": ["gemini-2.5-flash"]
}
```

The Gemini adapter is intentionally optional. Local Ollama testing works without it.

## Deliberation protocol

```text
User question
    |
    +--> Model A --+\
    +--> Model B --+ ---> structured positions
    +--> Model C --+/
                         |
                         v
                  explicit disagreements
                         |
                         v
                   targeted revisions
                         |
                         v
                     synthesis
```

The full transcript is stored, but later model calls receive compact Council State rather
than the entire historical transcript.

## Terminal Flow

```text
Council > select
Council > roles
Council > new
Council > ask Should the council remain terminal-first?
Council > status
Council > export
Council > debug
```

`status` is human-readable. `export` and `debug` print raw session JSON.

## Important limitation

This is an experiment harness, not the production Model Council architecture.
The scores produced by `benchmark` are only useful if the benchmark questions have
reliable reference answers or a human evaluator.
