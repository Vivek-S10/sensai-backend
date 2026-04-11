import asyncio
from api.utils.db import get_new_db_connection
from api.config import users_table_name

async def seed():
    async with get_new_db_connection() as conn:
        cursor = await conn.cursor()
        await cursor.execute(f"SELECT id FROM {users_table_name} WHERE id=1")
        existing = await cursor.fetchone()
        if not existing:
            await cursor.execute(
                f"""INSERT INTO {users_table_name} 
                    (id, email, first_name, last_name, default_dp_color, created_at) 
                    VALUES (1, 'ayub@example.com', 'Ayub', 'H', '#000000', CURRENT_TIMESTAMP)"""
            )
            await conn.commit()
            print("Successfully recovered User 1!")
        else:
            print("User 1 already exists.")

if __name__ == "__main__":
    asyncio.run(seed())
