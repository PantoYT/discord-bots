import discord
from discord.ext import commands, tasks
import aiohttp
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import json
import asyncio
import pytz

# -------------------------------
# Load environment
# -------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
OWNER_ID = int(os.getenv("OWNER_ID"))
EPIC_API_KEY = os.getenv("EPIC_API_KEY")
EPIC_API_URL = "https://epic-games-store-free-games.p.rapidapi.com/free?country=PL"
HEADERS = {
    "x-rapidapi-key": EPIC_API_KEY,
    "x-rapidapi-host": "epic-games-store-free-games.p.rapidapi.com"
}

POSTED_FILE = "posted_games.json"
CET = pytz.timezone('Europe/Warsaw')

# -------------------------------
# Discord bot setup
# -------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

pending_confirmations = {}

# -------------------------------
# Sync slash commands
# -------------------------------
@bot.event
async def on_ready():
    global last_daily_run
    now = datetime.now(CET)
    print(f"Bot logged in as {bot.user} at {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="Epic Games | /commands"
    ))
    
    today_str = str(now.date())
    target_time = datetime.now(CET).replace(hour=17, minute=1, second=0, microsecond=0).time()

    if last_daily_run != today_str and now.time() >= target_time:
        print("Running late start check...")
        await run_check()
    
    daily_check.start()

# -------------------------------
# Load or initialize posted games
# -------------------------------
try:
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        posted_games = data.get("current", [])
        posted_upcoming = data.get("upcoming", [])
        last_daily_run = data.get("last_daily_run", None)
except (FileNotFoundError, json.JSONDecodeError):
    posted_games = []
    posted_upcoming = []
    last_daily_run = None

def save_posted():
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "current": posted_games,
            "upcoming": posted_upcoming,
            "last_daily_run": last_daily_run
        }, f, ensure_ascii=False, indent=2)

# -------------------------------
# Helper: fetch games
# -------------------------------
async def fetch_games():
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
            async with session.get(EPIC_API_URL) as resp:
                if resp.status != 200:
                    print(f"API error: {resp.status}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"Error fetching games: {e}")
        return None

# -------------------------------
# Helper: create embeds for games
# -------------------------------
def make_embeds(games, ctx_mention=None, upcoming=False):
    embeds = []
    for g in games:
        title = g.get("title", "Unknown Game")
        desc = g.get("description", "No description")
        seller = g.get("seller", {}).get("name", "Unknown")
        slug = g.get("urlSlug")
        url = f"https://www.epicgames.com/store/p/{slug}" if slug else "No link"

        date_field = None
        if upcoming:
            date_field = g.get("effectiveDate", "Unknown start")
            if date_field:
                try:
                    dt = datetime.fromisoformat(date_field.replace("Z", "+00:00")) + timedelta(hours=1)
                    date_field = dt.strftime("%Y-%m-%d")
                except:
                    pass
        else:
            promos = g.get("promotions", {}).get("promotionalOffers", [])
            if promos and promos[0].get("promotionalOffers"):
                end_raw = promos[0]["promotionalOffers"][0].get("endDate")
                if end_raw:
                    try:
                        dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")) + timedelta(hours=1)
                        date_field = dt.strftime("%Y-%m-%d")
                    except:
                        pass

        thumbnail_url = None
        for img in g.get("keyImages", []):
            if img.get("type") == "Thumbnail":
                thumbnail_url = img.get("url")
                break

        embed = discord.Embed(title=title, description=desc, color=0x1E3A8A)
        embed.add_field(name="Seller", value=seller)
        embed.add_field(name="Store Link", value=url, inline=False)
        if date_field:
            if upcoming:
                embed.add_field(name="Start Date", value=date_field)
            else:
                embed.add_field(name="Available Until", value=date_field)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if ctx_mention:
            embed.set_footer(text=f"Checked by {ctx_mention}")
        embeds.append(embed)
    return embeds

# -------------------------------
# Helper: check if games are same
# -------------------------------
def are_games_same(new_games, old_games):
    new_titles = {g.get("title") for g in new_games}
    old_titles = {g.get("title") for g in old_games}
    return new_titles == old_titles

# -------------------------------
# Core: run the check
# -------------------------------
async def run_check(ctx_mention=None, force=False, interaction_channel=None):
    global last_daily_run, posted_games, posted_upcoming
    
    data = await fetch_games()
    if not data:
        print("Failed to fetch games")
        return False

    channel = interaction_channel or bot.get_channel(CHANNEL_ID)
    if not channel:
        print("Channel not found")
        return False

    current_games = data.get("currentGames", [])
    next_games = data.get("nextGames", [])

    if are_games_same(current_games, posted_games) and not force:
        if ctx_mention:
            pending_confirmations[ctx_mention] = datetime.now(CET) + timedelta(minutes=1)
            await channel.send(f"{ctx_mention}, games are the same as last check. Use /confirm within 1 min to see them again.")
        return True

    posted_games = current_games.copy()

    new_upcoming = [g for g in next_games if g.get("title") not in [u.get("title") for u in posted_upcoming]]
    posted_upcoming.extend(new_upcoming)

    now = datetime.now(CET)
    last_daily_run = str(now.date())
    save_posted()

    embeds_current = make_embeds(current_games, ctx_mention=ctx_mention, upcoming=False)
    embeds_upcoming = make_embeds(new_upcoming, ctx_mention=ctx_mention, upcoming=True)

    if embeds_current:
        await channel.send("**Current Free Games:**")
        for e in embeds_current:
            await channel.send(embed=e)

    if embeds_upcoming:
        await channel.send("**Upcoming Free Games:**")
        for e in embeds_upcoming:
            await channel.send(embed=e)

    return True

# -------------------------------
# Commands
# -------------------------------
@bot.tree.command(name="commands", description="Show all available commands")
async def commands_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Fred - Epic Games Tracker",
        description="Track free Epic Games Store games automatically",
        color=0x1E3A8A
    )
    embed.add_field(
        name="/getgame",
        value="Manually check for new free games",
        inline=False
    )
    embed.add_field(
        name="/confirm",
        value="Show games again if they haven't changed",
        inline=False
    )
    embed.add_field(
        name="/showcurrent",
        value="Display current free games",
        inline=False
    )
    embed.add_field(
        name="/commands",
        value="Show this help menu",
        inline=False
    )
    embed.add_field(
        name="/shutdown",
        value="Shut down the bot (owner only)",
        inline=False
    )
    embed.set_footer(text="Daily automatic check at 17:01 CET")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="showcurrent", description="Display current free games")
async def showcurrent_slash(interaction: discord.Interaction):
    embeds = make_embeds(posted_games, ctx_mention=interaction.user.mention, upcoming=False)
    if embeds:
        await interaction.response.send_message("**Current Free Games:**")
        for e in embeds:
            await interaction.followup.send(embed=e)
    else:
        await interaction.response.send_message("No current games to display.")

@bot.tree.command(name="getgame", description="Manually check for new free games")
async def getgame_slash(interaction: discord.Interaction):
    await interaction.response.send_message(f"Manual check triggered by {interaction.user.mention}")
    result = await run_check(ctx_mention=interaction.user.mention, force=False, interaction_channel=interaction.channel)
    if not result:
        await interaction.followup.send("Failed to fetch games.")

@bot.tree.command(name="confirm", description="Confirm to see games again if they're the same")
async def confirm_slash(interaction: discord.Interaction):
    now = datetime.now(CET)
    expiry = pending_confirmations.get(interaction.user.mention)
    if expiry and now <= expiry:
        pending_confirmations.pop(interaction.user.mention)
        embeds = make_embeds(posted_games, ctx_mention=interaction.user.mention, upcoming=False)
        if embeds:
            await interaction.response.send_message("**Current Free Games:**")
            for e in embeds:
                await interaction.followup.send(embed=e)
        else:
            await interaction.response.send_message("No current games to display.")
    else:
        await interaction.response.send_message("No pending confirmation or it expired.")

@bot.tree.command(name="shutdown", description="Shutdown the bot (owner only)")
async def shutdown_slash(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("You don't have permission.")
        return
    await interaction.response.send_message("Shutting down...")
    await bot.close()

# -------------------------------
# On bot ready: start daily check
# -------------------------------
# Moved to combined on_ready above

# -------------------------------
# Daily scheduled task at 17:01 CET
# -------------------------------
@tasks.loop(hours=24)
async def daily_check():
    global last_daily_run
    now = datetime.now(CET)
    today_str = str(now.date())
    if last_daily_run != today_str:
        print(f"Running daily check at {now.strftime('%Y-%m-%d %H:%M:%S')}")
        await run_check()
        next_run = now + timedelta(hours=24)
        print(f"Next daily check scheduled for {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

@daily_check.before_loop
async def before_daily_check():
    await bot.wait_until_ready()
    now = datetime.now(CET)
    target = now.replace(hour=17, minute=1, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    sleep_seconds = (target - now).total_seconds()
    print(f"Daily check will start in {sleep_seconds/3600:.2f} hours at {target.strftime('%H:%M:%S')}")
    await asyncio.sleep(sleep_seconds)

# -------------------------------
# Run the bot
# -------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)