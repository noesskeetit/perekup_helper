from fastapi import FastAPI

app = FastAPI(
    title="Perekup Helper",
    description="AI-агрегатор для перекупов: парсинг авто-объявлений, анализ цен ниже рынка",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
