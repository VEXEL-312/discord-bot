import asyncio
import os
import random
import sqlite3
import threading
import discord
from discord.ext import commands, tasks
from flask import Flask

# --- 24시간 유지를 위한 가짜 웹 서버 설정 (Render용) ---
app = Flask('')

@app.route('/')
def home():
    return "Vexel Bot is running!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

keep_alive()
# ----------------------------------------------------

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "shop.db")

# --- 데이터베이스 초기화 (데이터 유실 방지) ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 유저 정보 (잔액, 누적금액, 포인트, 뽑기권 보관)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0
        )
    """)
    
    # 상품 정보
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            item_code TEXT PRIMARY KEY,
            name TEXT,
            price INTEGER,
            stock INTEGER,
            custom_message TEXT
        )
    """)
    
    # 주식 종목
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            stock_code TEXT PRIMARY KEY,
            name TEXT,
            price INTEGER
        )
    """)
    
    # 유저별 주식 보유량
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_stocks (
            user_id TEXT,
            stock_code TEXT,
            amount INTEGER,
            PRIMARY KEY (user_id, stock_code)
        )
    """)
    
    # 뽑기 종류 (가문, 아이템, 코스 등)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gacha_types (
            gacha_code TEXT PRIMARY KEY,
            name TEXT,
            ticket_name TEXT,
            ticket_price INTEGER
        )
    """)
    
    # 뽑기 아이템 및 (표기 확률, 실제 확률)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gacha_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gacha_code TEXT,
            item_name TEXT,
            display_rate REAL,
            actual_rate REAL
        )
    """)
    
    # 유저별 뽑기권 보유량
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_tickets (
            user_id TEXT,
            gacha_code TEXT,
            amount INTEGER,
            PRIMARY KEY (user_id, gacha_code)
        )
    """)
    
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, total_spent, points FROM users WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"balance": row[0], "total_spent": row[1], "points": row[2]}
    return None

def upsert_user(user_id, balance=None, total_spent=None, points=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, total_spent, points FROM users WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    
    curr_balance = row[0] if row else 0
    curr_spent = row[1] if row else 0
    curr_points = row[2] if row else 0
    
    new_balance = balance if balance is not None else curr_balance
    new_spent = total_spent if total_spent is not None else curr_spent
    new_points = points if points is not None else curr_points
    
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, balance, total_spent, points) VALUES (?, ?, ?, ?)",
        (str(user_id), new_balance, new_spent, new_points)
    )
    conn.commit()
    conn.close()

init_db()

# --- 주식 자동 변동 시스템 (오를 때는 조금씩, 내릴 때도 자연스럽게) ---
@tasks.loop(minutes=10) # 10분마다 주가 변동
async def fluctuate_stocks():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_code, price FROM stocks")
    stocks = cursor.fetchall()
    
    for code, price in stocks:
        change_rate = random.choices(
            [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04],
            weights=[15, 15, 20, 25, 15, 5, 5]
        )[0]
        new_price = int(price * (1 + change_rate))
        if new_price < 100: new_price = 100
        cursor.execute("UPDATE stocks SET price = ? WHERE stock_code = ?", (new_price, code))
        
    conn.commit()
    conn.close()

@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user.name}")
    if not fluctuate_stocks.is_running():
        fluctuate_stocks.start()
    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(e)

# --- 관리자: 포인트 및 뽑기 관리 명령어 ---
@bot.tree.command(name="포인트조절", description="[관리자] 특정 유저의 포인트를 추가하거나 차감합니다.")
async def adjust_points(interaction: discord.Interaction, 유저: discord.Member, 모드: str, 포인트: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    if 모드 not in ["추가", "차감"] or 포인트 < 0:
        await interaction.response.send_message("❌ 모드는 '추가' 또는 '차감', 금액은 0 이상으로 입력하세요.", ephemeral=True)
        return

    user_info = get_user(유저.id)
    curr_points = user_info["points"] if user_info else 0
    new_points = curr_points + 포인트 if 모드 == "추가" else max(0, curr_points - 포인트)
    upsert_user(유저.id, points=new_points)
    
    await interaction.response.send_message(f"✅ {유저.mention}님의 포인트가 **{포인트:,}점** {모드}되었습니다. (현재 포인트: **{new_points:,}점**)", ephemeral=True)

@bot.tree.command(name="뽑기종류추가", description="[관리자] 새로운 뽑기(가문, 아이템 등)를 생성합니다.")
async def create_gacha_type(interaction: discord.Interaction, 뽑기코드: str, 뽑기이름: str, 뽑기권이름: str, 뽑기권가격포인트: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO gacha_types (gacha_code, name, ticket_name, ticket_price) VALUES (?, ?, ?, ?)", 
                       (뽑기코드, 뽑기이름, 뽑기권이름, 뽑기권가격포인트))
        conn.commit()
        await interaction.response.send_message(f"✨ 새 뽑기 종류 생성 완료!\n• 이름: **{뽑기이름** (`{뽑기코드}`)\n• 전용 뽑기권: **{뽑기권이름}**\n• 뽑기권 가격: **{뽑기권가격포인트:,}포인트**", ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"❌ 이미 존재하는 뽑기 코드입니다.", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="뽑기아이템추가", description="[관리자] 특정 뽑기에 아이템과 '보이는 확률', '실제 확률'을 설정해 추가합니다.")
async def add_gacha_item(interaction: discord.Interaction, 뽑기코드: str, 아이템이름: str, 표기확률퍼센트: float, 실제확률퍼센트: float):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO gacha_items (gacha_code, item_name, display_rate, actual_rate) VALUES (?, ?, ?, ?)",
                   (뽑기코드, 아이템이름, 표기확률퍼센트, 실제확률퍼센트))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"🎁 뽑기 아이템 추가 완료!\n• 뽑기 코드: `{뽑기코드}`\n• 아이템: **{아이템이름}**\n• 표기 확률: `{표기확률퍼센트}%` | 실제 확률: `{actual_rate_msg :=실제확률퍼센트}%`", ephemeral=True)

# --- 유저: 포인트 상점(뽑기권 구매) 및 뽑기/도박/주식 명령어 ---
@bot.tree.command(name="포인트상점", description="포인트로 원하는 뽑기권을 구매합니다.")
async def point_shop(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT gacha_code, name, ticket_name, ticket_price FROM gacha_types")
    gachas = cursor.fetchall()
    conn.close()

    if not gachas:
        await interaction.response.send_message("🛒 현재 열린 포인트 상점이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title="🛍️ 포인트 뽑기권 상점", description="포인트로 원하는 뽑기권을 구매해보세요!", color=discord.Color.purple())
    for code, name, t_name, t_price in gachas:
        embed.add_field(name=f"🎟️ {name} ({t_name})", value=f"• 코드: `{code}`\n• 가격: **{t_price:,}포인트**", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="뽑기권구매", description="포인트로 특정 뽑기권을 구매합니다.")
async def buy_ticket(interaction: discord.Interaction, 뽑기코드: str, 수량: int = 1):
    if 수량 <= 0:
        await interaction.response.send_message("❌ 1개 이상만 구매할 수 있습니다.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, ticket_name, ticket_price FROM gacha_types WHERE gacha_code = ?", (뽑기코드,))
    gacha = cursor.fetchone()
    
    if not gacha:
        conn.close()
        await interaction.response.send_message("❌ 존재하지 않는 뽑기 코드입니다.", ephemeral=True)
        return

    g_name, t_name, t_price = gacha
    total_cost = t_price * 수량

    user_info = get_user(interaction.user.id)
    my_points = user_info["points"] if user_info else 0

    if my_points < total_cost:
        conn.close()
        await interaction.response.send_message(f"❌ 포인트가 부족합니다. (필요: {total_cost:,}점, 보유: {my_points:,}점)", ephemeral=True)
        return

    # 포인트 차감 및 뽑기권 지급
    upsert_user(interaction.user.id, points=my_points - total_cost)
    
    cursor.execute("SELECT amount FROM user_tickets WHERE user_id = ? AND gacha_code = ?", (str(interaction.user.id), 뽑기코드))
    row = cursor.fetchone()
    if row:
        new_amt = row[0] + 수량
        cursor.execute("UPDATE user_tickets SET amount = ? WHERE user_id = ? AND gacha_code = ?", (new_amt, str(interaction.user.id), 뽑기코드))
    else:
        cursor.execute("INSERT INTO user_tickets (user_id, gacha_code, amount) VALUES (?, ?, ?)", (str(interaction.user.id), 뽑기코드, 수량))
        
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"🎉 **{g_name}**의 **{t_name}** `{수량}장`을 총 **{total_cost:,}포인트**로 구매했습니다!", ephemeral=True)

@bot.tree.command(name="뽑기실행", description="소유한 뽑기권을 사용하여 실제 확률에 따라 아이템을 뽑습니다.")
async def run_gacha(interaction: discord.Interaction, 뽑기코드: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 뽑기권 확인
    cursor.execute("SELECT amount FROM user_tickets WHERE user_id = ? AND gacha_code = ?", (str(interaction.user.id), 뽑기코드))
    t_row = cursor.fetchone()
    if not t_row or t_row[0] <= 0:
        conn.close()
        await interaction.response.send_message("❌ 해당 뽑기권이 부족합니다!", ephemeral=True)
        return
        
    # 아이템 목록 및 실제 확률 가져오기
    cursor.execute("SELECT item_name, actual_rate FROM gacha_items WHERE gacha_code = ?", (뽑기코드,))
    items = cursor.fetchall()
    if not items:
        conn.close()
        await interaction.response.send_message("❌ 해당 뽑기에 등록된 아이템이 없습니다.", ephemeral=True)
        return

    # 뽑기권 1장 차감
    cursor.execute("UPDATE user_tickets SET amount = amount - 1 WHERE user_id = ? AND gacha_code = ?", (str(interaction.user.id), 뽑기코드))
    conn.commit()
    conn.close()

    # 실제 확률(actual_rate) 기반 추첨 로직
    item_names = [i[0] for i in items]
    actual_weights = [i[1] for i in items]
    
    chosen_item = random.choices(item_names, weights=actual_weights, k=1)[0]

    embed = discord.Embed(title="🎰 뽑기 결과!", description=f"두근두근... 결과는?!", color=discord.Color.gold())
    embed.add_field(name="당첨 아이템", value=f"✨ **{chosen_item}**", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="도박", description="포인트를 걸고 50% 확률로 두 배를 얻거나 잃습니다.")
async def gamble(interaction: discord.Interaction, 걸고싶은포인트: int):
    if 걸고싶은포인트 <= 0:
        await interaction.response.send_message("❌ 1포인트 이상만 걸 수 있습니다.", ephemeral=True)
        return

    user_info = get_user(interaction.user.id)
    my_points = user_info["points"] if user_info else 0

    if my_points < 걸고싶은포인트:
        await interaction.response.send_message(f"❌ 포인트를 너무 많이 걸었습니다. (보유 포인트: {my_points:,}점)", ephemeral=True)
        return

    # 50% 확률
    win = random.choice([True, False])
    if win:
        new_points = my_points + 걸고싶은포인트
        upsert_user(interaction.user.id, points=new_points)
        await interaction.response.send_message(f"🎲 **[도박 승리!]** 축하합니다! 획득 포인트: `+{걸고싶은포인트:,}점` (현재 포인트: {new_points:,}점)", ephemeral=True)
    else:
        new_points = my_points - 걸고싶은포인트
        upsert_user(interaction.user.id, points=new_points)
        await interaction.response.send_message(f"🎲 **[도박 패배...]** 아쉽네요! 차감된 포인트: `-{걸고싶은포인트:,}점` (현재 포인트: {new_points:,}점)", ephemeral=True)

@bot.tree.command(name="주식상장", description="[관리자] 새로운 주식 종목을 상장합니다.")
async def create_stock(interaction: discord.Interaction, 종목코드: str, 종목이름: str, 초기가격: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만.", ephemeral=True)
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO stocks (stock_code, name, price) VALUES (?, ?, ?)", (종목코드, 종목이름, 초기가격))
        conn.commit()
        await interaction.response.send_message(f"📈 주식 상장: **{종목이름}** (`{종목코드}`) / 초기가: {초기가격:,}점", ephemeral=True)
    except:
        await interaction.response.send_message("❌ 이미 존재하는 종목 코드입니다.", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="주식시장", description="주식 시세를 확인합니다.")
async def stock_market(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_code, name, price FROM stocks")
    stocks = cursor.fetchall()
    conn.close()

    if not stocks:
        await interaction.response.send_message("📉 상장된 주식이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title="📈 주식 시장 시세판", color=discord.Color.gold())
    for code, name, price in stocks:
        embed.add_field(name=f"{name} (`{code}`)", value=f"가격: **{price:,}포인트**", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="주식매수", description="포인트로 주식을 구매합니다.")
async def buy_stock(interaction: discord.Interaction, 종목코드: str, 수량: int):
    if 수량 <= 0: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, price FROM stocks WHERE stock_code = ?", (종목코드,))
    stock = cursor.fetchone()
    if not stock:
        conn.close()
        await interaction.response.send_message("❌ 없는 종목입니다.", ephemeral=True)
        return

    name, price = stock
    total_cost = price * 수량
    user_info = get_user(interaction.user.id)
    my_points = user_info["points"] if user_info else 0

    if my_points < total_cost:
        conn.close()
        await interaction.response.send_message("❌ 포인트가 부족합니다.", ephemeral=True)
        return

    upsert_user(interaction.user.id, points=my_points - total_cost)
    cursor.execute("SELECT amount FROM user_stocks WHERE user_id = ? AND stock_code = ?", (str(interaction.user.id), 종목코드))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE user_stocks SET amount = ? WHERE user_id = ? AND stock_code = ?", (row[0] + 수량, str(interaction.user.id), 종목코드))
    else:
        cursor.execute("INSERT INTO user_stocks (user_id, stock_code, amount) VALUES (?, ?, ?)", (str(interaction.user.id), 종목코드, 수량))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **{name}** 주식 `{수량}주` 매수 완료!", ephemeral=True)

@bot.tree.command(name="내정보", description="내 잔액, 포인트, 뽑기권 보유량을 확인합니다.")
async def my_info(interaction: discord.Interaction):
    user_info = get_user(interaction.user.id)
    bal = user_info["balance"] if user_info else 0
    pts = user_info["points"] if user_info else 0

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT gacha_code, amount FROM user_tickets WHERE user_id = ? AND amount > 0", (str(interaction.user.id),))
    tickets = cursor.fetchall()
    
    cursor.execute("SELECT stock_code, amount FROM user_stocks WHERE user_id = ? AND amount > 0", (str(interaction.user.id),))
    stocks = cursor.fetchall()
    conn.close()

    embed = discord.Embed(title=f"👤 {interaction.user.name}님의 자산 정보", color=discord.Color.green())
    embed.add_field(name="보유 잔액", value=f"{bal:,}원", inline=True)
    embed.add_field(name="보유 포인트", value=f"{pts:,}점", inline=True)
    
    t_str = "\n".join([f"• `{code}`: {amt}장" for code, amt in tickets]) if tickets else "없음"
    embed.add_field(name="🎟️ 보유 뽑기권", value=t_str, inline=False)
    
    s_str = "\n".join([f"• `{code}`: {amt}주" for code, amt in stocks]) if stocks else "없음"
    embed.add_field(name="📈 보유 주식", value=s_str, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# 봇 실행 토큰
bot.run("MTUyOTY5NDc1NTA2MjE1MzMwNw.GdHyqn.GFLO0r3ASNCv2396e_JKstVSe4FPXtDtYqnVP4")import asyncio
import os
import random
import sqlite3
import threading
import discord
from discord.ext import commands, tasks
from flask import Flask

# --- 24시간 유지를 위한 가짜 웹 서버 설정 (Render용) ---
app = Flask('')

@app.route('/')
def home():
    return "Vexel Bot is running!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

keep_alive()
# ----------------------------------------------------

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "shop.db")

# --- 데이터베이스 초기화 (데이터 유실 방지) ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 유저 정보 (잔액, 누적금액, 포인트, 뽑기권 보관)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0
        )
    """)
    
    # 상품 정보
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            item_code TEXT PRIMARY KEY,
            name TEXT,
            price INTEGER,
            stock INTEGER,
            custom_message TEXT
        )
    """)
    
    # 주식 종목
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            stock_code TEXT PRIMARY KEY,
            name TEXT,
            price INTEGER
        )
    """)
    
    # 유저별 주식 보유량
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_stocks (
            user_id TEXT,
            stock_code TEXT,
            amount INTEGER,
            PRIMARY KEY (user_id, stock_code)
        )
    """)
    
    # 뽑기 종류 (가문, 아이템, 코스 등)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gacha_types (
            gacha_code TEXT PRIMARY KEY,
            name TEXT,
            ticket_name TEXT,
            ticket_price INTEGER
        )
    """)
    
    # 뽑기 아이템 및 (표기 확률, 실제 확률)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gacha_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gacha_code TEXT,
            item_name TEXT,
            display_rate REAL,
            actual_rate REAL
        )
    """)
    
    # 유저별 뽑기권 보유량
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_tickets (
            user_id TEXT,
            gacha_code TEXT,
            amount INTEGER,
            PRIMARY KEY (user_id, gacha_code)
        )
    """)
    
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, total_spent, points FROM users WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"balance": row[0], "total_spent": row[1], "points": row[2]}
    return None

def upsert_user(user_id, balance=None, total_spent=None, points=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, total_spent, points FROM users WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    
    curr_balance = row[0] if row else 0
    curr_spent = row[1] if row else 0
    curr_points = row[2] if row else 0
    
    new_balance = balance if balance is not None else curr_balance
    new_spent = total_spent if total_spent is not None else curr_spent
    new_points = points if points is not None else curr_points
    
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, balance, total_spent, points) VALUES (?, ?, ?, ?)",
        (str(user_id), new_balance, new_spent, new_points)
    )
    conn.commit()
    conn.close()

init_db()

# --- 주식 자동 변동 시스템 (오를 때는 조금씩, 내릴 때도 자연스럽게) ---
@tasks.loop(minutes=10) # 10분마다 주가 변동
async def fluctuate_stocks():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_code, price FROM stocks")
    stocks = cursor.fetchall()
    
    for code, price in stocks:
        change_rate = random.choices(
            [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04],
            weights=[15, 15, 20, 25, 15, 5, 5]
        )[0]
        new_price = int(price * (1 + change_rate))
        if new_price < 100: new_price = 100
        cursor.execute("UPDATE stocks SET price = ? WHERE stock_code = ?", (new_price, code))
        
    conn.commit()
    conn.close()

@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user.name}")
    if not fluctuate_stocks.is_running():
        fluctuate_stocks.start()
    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(e)

# --- 관리자: 포인트 및 뽑기 관리 명령어 ---
@bot.tree.command(name="포인트조절", description="[관리자] 특정 유저의 포인트를 추가하거나 차감합니다.")
async def adjust_points(interaction: discord.Interaction, 유저: discord.Member, 모드: str, 포인트: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    if 모드 not in ["추가", "차감"] or 포인트 < 0:
        await interaction.response.send_message("❌ 모드는 '추가' 또는 '차감', 금액은 0 이상으로 입력하세요.", ephemeral=True)
        return

    user_info = get_user(유저.id)
    curr_points = user_info["points"] if user_info else 0
    new_points = curr_points + 포인트 if 모드 == "추가" else max(0, curr_points - 포인트)
    upsert_user(유저.id, points=new_points)
    
    await interaction.response.send_message(f"✅ {유저.mention}님의 포인트가 **{포인트:,}점** {모드}되었습니다. (현재 포인트: **{new_points:,}점**)", ephemeral=True)

@bot.tree.command(name="뽑기종류추가", description="[관리자] 새로운 뽑기(가문, 아이템 등)를 생성합니다.")
async def create_gacha_type(interaction: discord.Interaction, 뽑기코드: str, 뽑기이름: str, 뽑기권이름: str, 뽑기권가격포인트: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO gacha_types (gacha_code, name, ticket_name, ticket_price) VALUES (?, ?, ?, ?)", 
                       (뽑기코드, 뽑기이름, 뽑기권이름, 뽑기권가격포인트))
        conn.commit()
        await interaction.response.send_message(f"✨ 새 뽑기 종류 생성 완료!\n• 이름: **{뽑기이름** (`{뽑기코드}`)\n• 전용 뽑기권: **{뽑기권이름}**\n• 뽑기권 가격: **{뽑기권가격포인트:,}포인트**", ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"❌ 이미 존재하는 뽑기 코드입니다.", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="뽑기아이템추가", description="[관리자] 특정 뽑기에 아이템과 '보이는 확률', '실제 확률'을 설정해 추가합니다.")
async def add_gacha_item(interaction: discord.Interaction, 뽑기코드: str, 아이템이름: str, 표기확률퍼센트: float, 실제확률퍼센트: float):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO gacha_items (gacha_code, item_name, display_rate, actual_rate) VALUES (?, ?, ?, ?)",
                   (뽑기코드, 아이템이름, 표기확률퍼센트, 실제확률퍼센트))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"🎁 뽑기 아이템 추가 완료!\n• 뽑기 코드: `{뽑기코드}`\n• 아이템: **{아이템이름}**\n• 표기 확률: `{표기확률퍼센트}%` | 실제 확률: `{actual_rate_msg :=실제확률퍼센트}%`", ephemeral=True)

# --- 유저: 포인트 상점(뽑기권 구매) 및 뽑기/도박/주식 명령어 ---
@bot.tree.command(name="포인트상점", description="포인트로 원하는 뽑기권을 구매합니다.")
async def point_shop(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT gacha_code, name, ticket_name, ticket_price FROM gacha_types")
    gachas = cursor.fetchall()
    conn.close()

    if not gachas:
        await interaction.response.send_message("🛒 현재 열린 포인트 상점이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title="🛍️ 포인트 뽑기권 상점", description="포인트로 원하는 뽑기권을 구매해보세요!", color=discord.Color.purple())
    for code, name, t_name, t_price in gachas:
        embed.add_field(name=f"🎟️ {name} ({t_name})", value=f"• 코드: `{code}`\n• 가격: **{t_price:,}포인트**", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="뽑기권구매", description="포인트로 특정 뽑기권을 구매합니다.")
async def buy_ticket(interaction: discord.Interaction, 뽑기코드: str, 수량: int = 1):
    if 수량 <= 0:
        await interaction.response.send_message("❌ 1개 이상만 구매할 수 있습니다.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, ticket_name, ticket_price FROM gacha_types WHERE gacha_code = ?", (뽑기코드,))
    gacha = cursor.fetchone()
    
    if not gacha:
        conn.close()
        await interaction.response.send_message("❌ 존재하지 않는 뽑기 코드입니다.", ephemeral=True)
        return

    g_name, t_name, t_price = gacha
    total_cost = t_price * 수량

    user_info = get_user(interaction.user.id)
    my_points = user_info["points"] if user_info else 0

    if my_points < total_cost:
        conn.close()
        await interaction.response.send_message(f"❌ 포인트가 부족합니다. (필요: {total_cost:,}점, 보유: {my_points:,}점)", ephemeral=True)
        return

    # 포인트 차감 및 뽑기권 지급
    upsert_user(interaction.user.id, points=my_points - total_cost)
    
    cursor.execute("SELECT amount FROM user_tickets WHERE user_id = ? AND gacha_code = ?", (str(interaction.user.id), 뽑기코드))
    row = cursor.fetchone()
    if row:
        new_amt = row[0] + 수량
        cursor.execute("UPDATE user_tickets SET amount = ? WHERE user_id = ? AND gacha_code = ?", (new_amt, str(interaction.user.id), 뽑기코드))
    else:
        cursor.execute("INSERT INTO user_tickets (user_id, gacha_code, amount) VALUES (?, ?, ?)", (str(interaction.user.id), 뽑기코드, 수량))
        
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"🎉 **{g_name}**의 **{t_name}** `{수량}장`을 총 **{total_cost:,}포인트**로 구매했습니다!", ephemeral=True)

@bot.tree.command(name="뽑기실행", description="소유한 뽑기권을 사용하여 실제 확률에 따라 아이템을 뽑습니다.")
async def run_gacha(interaction: discord.Interaction, 뽑기코드: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 뽑기권 확인
    cursor.execute("SELECT amount FROM user_tickets WHERE user_id = ? AND gacha_code = ?", (str(interaction.user.id), 뽑기코드))
    t_row = cursor.fetchone()
    if not t_row or t_row[0] <= 0:
        conn.close()
        await interaction.response.send_message("❌ 해당 뽑기권이 부족합니다!", ephemeral=True)
        return
        
    # 아이템 목록 및 실제 확률 가져오기
    cursor.execute("SELECT item_name, actual_rate FROM gacha_items WHERE gacha_code = ?", (뽑기코드,))
    items = cursor.fetchall()
    if not items:
        conn.close()
        await interaction.response.send_message("❌ 해당 뽑기에 등록된 아이템이 없습니다.", ephemeral=True)
        return

    # 뽑기권 1장 차감
    cursor.execute("UPDATE user_tickets SET amount = amount - 1 WHERE user_id = ? AND gacha_code = ?", (str(interaction.user.id), 뽑기코드))
    conn.commit()
    conn.close()

    # 실제 확률(actual_rate) 기반 추첨 로직
    item_names = [i[0] for i in items]
    actual_weights = [i[1] for i in items]
    
    chosen_item = random.choices(item_names, weights=actual_weights, k=1)[0]

    embed = discord.Embed(title="🎰 뽑기 결과!", description=f"두근두근... 결과는?!", color=discord.Color.gold())
    embed.add_field(name="당첨 아이템", value=f"✨ **{chosen_item}**", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="도박", description="포인트를 걸고 50% 확률로 두 배를 얻거나 잃습니다.")
async def gamble(interaction: discord.Interaction, 걸고싶은포인트: int):
    if 걸고싶은포인트 <= 0:
        await interaction.response.send_message("❌ 1포인트 이상만 걸 수 있습니다.", ephemeral=True)
        return

    user_info = get_user(interaction.user.id)
    my_points = user_info["points"] if user_info else 0

    if my_points < 걸고싶은포인트:
        await interaction.response.send_message(f"❌ 포인트를 너무 많이 걸었습니다. (보유 포인트: {my_points:,}점)", ephemeral=True)
        return

    # 50% 확률
    win = random.choice([True, False])
    if win:
        new_points = my_points + 걸고싶은포인트
        upsert_user(interaction.user.id, points=new_points)
        await interaction.response.send_message(f"🎲 **[도박 승리!]** 축하합니다! 획득 포인트: `+{걸고싶은포인트:,}점` (현재 포인트: {new_points:,}점)", ephemeral=True)
    else:
        new_points = my_points - 걸고싶은포인트
        upsert_user(interaction.user.id, points=new_points)
        await interaction.response.send_message(f"🎲 **[도박 패배...]** 아쉽네요! 차감된 포인트: `-{걸고싶은포인트:,}점` (현재 포인트: {new_points:,}점)", ephemeral=True)

@bot.tree.command(name="주식상장", description="[관리자] 새로운 주식 종목을 상장합니다.")
async def create_stock(interaction: discord.Interaction, 종목코드: str, 종목이름: str, 초기가격: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만.", ephemeral=True)
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO stocks (stock_code, name, price) VALUES (?, ?, ?)", (종목코드, 종목이름, 초기가격))
        conn.commit()
        await interaction.response.send_message(f"📈 주식 상장: **{종목이름}** (`{종목코드}`) / 초기가: {초기가격:,}점", ephemeral=True)
    except:
        await interaction.response.send_message("❌ 이미 존재하는 종목 코드입니다.", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="주식시장", description="주식 시세를 확인합니다.")
async def stock_market(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_code, name, price FROM stocks")
    stocks = cursor.fetchall()
    conn.close()

    if not stocks:
        await interaction.response.send_message("📉 상장된 주식이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title="📈 주식 시장 시세판", color=discord.Color.gold())
    for code, name, price in stocks:
        embed.add_field(name=f"{name} (`{code}`)", value=f"가격: **{price:,}포인트**", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="주식매수", description="포인트로 주식을 구매합니다.")
async def buy_stock(interaction: discord.Interaction, 종목코드: str, 수량: int):
    if 수량 <= 0: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, price FROM stocks WHERE stock_code = ?", (종목코드,))
    stock = cursor.fetchone()
    if not stock:
        conn.close()
        await interaction.response.send_message("❌ 없는 종목입니다.", ephemeral=True)
        return

    name, price = stock
    total_cost = price * 수량
    user_info = get_user(interaction.user.id)
    my_points = user_info["points"] if user_info else 0

    if my_points < total_cost:
        conn.close()
        await interaction.response.send_message("❌ 포인트가 부족합니다.", ephemeral=True)
        return

    upsert_user(interaction.user.id, points=my_points - total_cost)
    cursor.execute("SELECT amount FROM user_stocks WHERE user_id = ? AND stock_code = ?", (str(interaction.user.id), 종목코드))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE user_stocks SET amount = ? WHERE user_id = ? AND stock_code = ?", (row[0] + 수량, str(interaction.user.id), 종목코드))
    else:
        cursor.execute("INSERT INTO user_stocks (user_id, stock_code, amount) VALUES (?, ?, ?)", (str(interaction.user.id), 종목코드, 수량))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **{name}** 주식 `{수량}주` 매수 완료!", ephemeral=True)

@bot.tree.command(name="내정보", description="내 잔액, 포인트, 뽑기권 보유량을 확인합니다.")
async def my_info(interaction: discord.Interaction):
    user_info = get_user(interaction.user.id)
    bal = user_info["balance"] if user_info else 0
    pts = user_info["points"] if user_info else 0

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT gacha_code, amount FROM user_tickets WHERE user_id = ? AND amount > 0", (str(interaction.user.id),))
    tickets = cursor.fetchall()
    
    cursor.execute("SELECT stock_code, amount FROM user_stocks WHERE user_id = ? AND amount > 0", (str(interaction.user.id),))
    stocks = cursor.fetchall()
    conn.close()

    embed = discord.Embed(title=f"👤 {interaction.user.name}님의 자산 정보", color=discord.Color.green())
    embed.add_field(name="보유 잔액", value=f"{bal:,}원", inline=True)
    embed.add_field(name="보유 포인트", value=f"{pts:,}점", inline=True)
    
    t_str = "\n".join([f"• `{code}`: {amt}장" for code, amt in tickets]) if tickets else "없음"
    embed.add_field(name="🎟️ 보유 뽑기권", value=t_str, inline=False)
    
    s_str = "\n".join([f"• `{code}`: {amt}주" for code, amt in stocks]) if stocks else "없음"
    embed.add_field(name="📈 보유 주식", value=s_str, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# 봇 실행 토큰
bot.run("MTUyOTY5NDc1NTA2MjE1MzMwNw.GdHyqn.GFLO0r3ASNCv2396e_JKstVSe4FPXtDtYqnVP4")