# Benchmarks

`docker compose exec api python -m app.bench` writes a report here for each
run: latency, queue time, tokens per second, schema retries and warnings per
agent, plus the memory the model occupies. Keep the reports with the project —
they are the evidence behind each model choice and each capacity change.
