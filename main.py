from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import json
import os
import asyncio
import re

# Создаём приложение FastAPI
app = FastAPI()

# Разрешаем CORS для фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Пути к файлам
BASE_DIR = os.path.dirname(__file__)
models_path = os.path.join(BASE_DIR, "telegram_gift_models.json")
excluded_path = os.path.join(BASE_DIR, "excluded_owners.json")

# Загружаем модели подарков
with open(models_path, "r", encoding="utf-8") as f:
    gift_models = json.load(f)

# Загружаем список исключённых владельцев
def load_excluded_owners():
    if os.path.exists(excluded_path):
        with open(excluded_path, "r", encoding="utf-8") as f:
            return {owner.strip().lower() for owner in json.load(f)}
    return set()

# Проверяем валидность модели
def validate_model(model: str):
    if model not in gift_models:
        raise HTTPException(status_code=400, detail="Invalid gift model")

# Парсим владельца из HTML
def parse_owner(text: str) -> str:
    owner_pos = text.find("<th>Owner</th>")
    if owner_pos == -1:
        return "unknown"
    snippet = text[owner_pos:owner_pos + 300]
    match = re.search(r'<a href="https://t\.me/([a-zA-Z0-9_]+)"', snippet)
    if match:
        return f"@{match.group(1)}"
    return "unknown"

# Получаем данные подарка с Telegram
async def fetch_gift_data(model: str, gift_id: int):
    url = f"https://t.me/nft/{model}-{gift_id}"
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.get(url)
        except Exception as e:
            print(f"[Ошибка] {e}")
            return None

        if r.status_code != 200:
            return None

        text = r.text

        if "User hidden" in text or "This channel is private" in text:
            return None

        owner = parse_owner(text)

        return {
            "model": model,
            "id": gift_id,
            "owner": owner,
            "link": url
        }

# Основной API-эндпоинт
@app.get("/api/gifts")
async def get_gifts(
    model: str = Query(...),
    id_range: str = Query(...),
    limit: int = Query(20),
    order: str = Query("asc", regex="^(asc|desc)$", description="asc или desc"),
):
    validate_model(model)

    try:
        start, end = map(int, id_range.split("-"))
    except:
        raise HTTPException(status_code=400, detail="Invalid id_range format")

    if start > end:
        start, end = end, start

    max_id = gift_models[model]
    start = max(start, 1)
    end = min(end, max_id)

    excluded_owners = load_excluded_owners()

    semaphore = asyncio.Semaphore(100)

    async def sem_fetch(gift_id):
        async with semaphore:
            return await fetch_gift_data(model, gift_id)

    id_list = range(start, end + 1)
    tasks = [sem_fetch(gift_id) for gift_id in id_list]

    # Собираем все результаты
    all_results = []
    for _future in asyncio.as_completed(tasks):
        gift = await _future
        if gift and gift["owner"] != "unknown":
            if gift["owner"].lower() in excluded_owners:
                continue
            all_results.append(gift)

    # Сортируем по ID
    reverse = (order == "desc")
    all_results.sort(key=lambda x: x["id"], reverse=reverse)

    # Ограничиваем по лимиту
    results = all_results[:limit]

    return {"results": results}

@app.get("/ping")
async def ping():
    return {"message": "pong"}

# Отдаём статический фронтенд
app.mount("/", StaticFiles(directory="static", html=True), name="static")
