# Pull local Qwen model into Ollama container
param(
    [string]$Model = "qwen2.5:7b"
)

Write-Host "Pulling model '$Model' into tender-ollama..." -ForegroundColor Cyan
docker exec -it tender-ollama ollama pull $Model
if ($LASTEXITCODE -eq 0) {
    Write-Host "Done. Select the model in Open WebUI (http://localhost:3000)." -ForegroundColor Green
} else {
    Write-Host "Failed. Is the stack running? Try: docker compose up -d" -ForegroundColor Red
    exit $LASTEXITCODE
}
