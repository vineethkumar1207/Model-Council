import config_loader
from pathlib import Path

engine, _, _ = config_loader.build_engine(Path('.'))
models = [
    {'id': 'llama3.2:latest', 'provider': 'ollama', 'locality': 'local'},
    {'id': 'qwen2.5:7b', 'provider': 'ollama', 'locality': 'local'},
    {'id': 'minimax-m3:cloud', 'provider': 'ollama', 'locality': 'cloud'}
]
health_report, _ = engine.health_check_selected_models(models)
for m in models:
    info = health_report.get(m['id'], {})
    print(f"{m['id']} -> {info.get('status', 'FAILED')} (latency: {info.get('latency_ms')} ms, error: {info.get('reason', 'none')})")
