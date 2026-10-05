import os
import time
from pathlib import Path
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DSN = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/lunch")

LINES = {1: "Красная", 2: "Синяя", 3: "Зелёная", 4: "Оранжевая", 5: "Фиолетовая"}
STATIONS = {
    "Площадь Восстания": 1, "Владимирская": 1, "Пушкинская": 1,
    "Чернышевская": 1, "Площадь Ленина": 1, "Нарвская": 1,
    "Невский проспект": 2, "Сенная площадь": 2, "Горьковская": 2,
    "Петроградская": 2, "Московские ворота": 2, "Парк Победы": 2,
    "Гостиный двор": 3, "Маяковская": 3, "Василеостровская": 3,
    "Приморская": 3, "Беговая": 3,
    "Спасская": 4, "Достоевская": 4, "Лиговский проспект": 4, "Новочеркасская": 4,
    "Адмиралтейская": 5, "Садовая": 5, "Звенигородская": 5,
    "Спортивная": 5, "Чкаловская": 5,
}

SEED = [
    ("Борщ и Ко", "Сенная площадь", "Свекольный борщ, котлета по-киевски с пюре, компот. Быстрое обслуживание, в 13:00 людно.", 420, "12:00–16:00"),
    ("Пельменная №7", "Владимирская", "Пельмени на выбор (3 начинки), маринованный салат, морс.", 350, "11:30–15:30"),
    ("Бистро «Нева»", "Гостиный двор", "Суп дня, запечённый лосось с рисом, кофе. Тихо, подходит для деловых встреч.", 690, "12:00–16:00"),
    ("Пушкин Дели", "Пушкинская", "Вегетарианский сет: суп из чечевицы, боул с фалафелем, лимонад.", 480, "12:00–17:00"),
    ("Кухня у парка", "Парк Победы", "Солянка, бефстроганов с гречкой, чай.", 390, "11:00–16:00"),
]


def db():
    return psycopg.connect(DSN, row_factory=dict_row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(20):  # wait for PostgreSQL to come up
        try:
            with db() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS lunches (
                        id SERIAL PRIMARY KEY,
                        place TEXT NOT NULL,
                        station TEXT NOT NULL,
                        line INT NOT NULL,
                        description TEXT NOT NULL,
                        price_rub INT NOT NULL CHECK (price_rub > 0),
                        hours TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )""")
                if conn.execute("SELECT count(*) AS n FROM lunches").fetchone()["n"] == 0:
                    for place, station, desc, price, hours in SEED:
                        conn.execute(
                            "INSERT INTO lunches (place, station, line, description, price_rub, hours) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (place, station, STATIONS[station], desc, price, hours),
                        )
            break
        except psycopg.OperationalError:
            if attempt == 19:
                raise
            time.sleep(1)
    yield


app = FastAPI(title="Бизнес-ланчи Петербурга", lifespan=lifespan)


class LunchIn(BaseModel):
    place: str = Field(min_length=2, max_length=80)
    station: str
    description: str = Field(min_length=5, max_length=300)
    price_rub: int = Field(ge=100, le=5000)
    hours: str = Field(default="12:00–16:00", max_length=30)

    @field_validator("station")
    @classmethod
    def known_station(cls, v):
        if v not in STATIONS:
            raise ValueError("Неизвестная станция метро")
        return v


@app.get("/api/stations")
def stations():
    return [{"name": n, "line": l, "line_name": LINES[l]} for n, l in STATIONS.items()]


@app.get("/api/lunches")
def list_lunches(station: str | None = None, max_price: int | None = Query(None, ge=0)):
    sql, params = "SELECT * FROM lunches WHERE true", []
    if station:
        sql += " AND station = %s"
        params.append(station)
    if max_price:
        sql += " AND price_rub <= %s"
        params.append(max_price)
    sql += " ORDER BY id DESC"
    with db() as conn:
        return conn.execute(sql, params).fetchall()


@app.post("/api/lunches", status_code=201)
def add_lunch(item: LunchIn):
    with db() as conn:
        return conn.execute(
            "INSERT INTO lunches (place, station, line, description, price_rub, hours) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (item.place, item.station, STATIONS[item.station], item.description, item.price_rub, item.hours),
        ).fetchone()


@app.delete("/api/lunches/{lunch_id}", status_code=204)
def delete_lunch(lunch_id: int):
    with db() as conn:
        if conn.execute("DELETE FROM lunches WHERE id = %s", (lunch_id,)).rowcount == 0:
            raise HTTPException(404, "Обед не найден")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")