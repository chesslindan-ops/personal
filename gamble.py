import os
import json
import asyncio
import threading
import random
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = "chesslindan-ops/personal"
FILE_PATH = "gamblingrec.json"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------------- FLASK ----------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot alive!", 200

def run_flask():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ---------------- GITHUB JSON ----------------
async def load_json():
    url = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                import base64
                decoded = base64.b64decode(data["content"]).decode()
                sha = data["sha"]
                data_json = json.loads(decoded)
                if isinstance(data_json, list):  # fix accidental list
                    data_json = {str(i): v for i, v in enumerate(data_json)}
                return data_json, sha
            else:
                return {}, None

async def save_json(new_data, sha):
    url = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    import base64
    encoded = base64.b64encode(json.dumps(new_data, indent=4).encode()).decode()
    payload = {"message": "update balances", "content": encoded, "sha": sha}
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=payload) as resp:
            return resp.status

# ---------------- BALANCE & NEW PLAYER ----------------
async def ensure_new_player(user: discord.User):
    data, sha = await load_json()
    if str(user.id) not in data:
        data[str(user.id)] = 0
        await save_json(data, sha)
        try:
            await user.send(
                "Welcome! 🎉\nYou will receive **2,000** currency as starting balance in a few minutes. (This is an automated message. Please do not respond)"
            )
        except:
            pass
        await asyncio.sleep(60)
        await update_balance(user.id, 2000)

async def get_balance(user_id):
    data, sha = await load_json()
    if str(user_id) not in data:
        await ensure_new_player(discord.Object(id=user_id))  # dummy object to trigger welcome
        return 0
    return data[str(user_id)]

async def update_balance(user_id, amount):
    data, sha = await load_json()
    if str(user_id) not in data:
        data[str(user_id)] = 0
    data[str(user_id)] += amount
    if data[str(user_id)] < 0:
        data[str(user_id)] = 0
    await save_json(data, sha)
    return data[str(user_id)]

# ---------------- COINFLIP ----------------
@tree.command(name="coinflip", description="Wager on a coin flip")
async def coinflip(interaction: discord.Interaction, amount: int):
    await ensure_new_player(interaction.user)
    bal = await get_balance(interaction.user.id)
    if amount <= 0 or amount > bal:
        return await interaction.response.send_message("Invalid or too high wager.", ephemeral=True)

    await update_balance(interaction.user.id, -amount)
    outcome = random.choice(["🪙 Heads", "🪙 Tails"])
    win = random.choice([True, False])
    if win:
        winnings = amount * 2
        await update_balance(interaction.user.id, winnings)
        msg = f"You flipped **{outcome}** and won **{winnings}**!"
    else:
        msg = f"You flipped **{outcome}** and lost your wager."
    await interaction.response.send_message(msg)

# ---------------- ROULETTE ----------------
@tree.command(name="roulette", description="Red/Black roulette")
async def roulette(interaction: discord.Interaction, amount: int, choice: str):
    await ensure_new_player(interaction.user)
    bal = await get_balance(interaction.user.id)
    choice = choice.lower()
    if choice not in ["red", "black"] or amount <= 0 or amount > bal:
        return await interaction.response.send_message("Invalid choice or wager.", ephemeral=True)

    await update_balance(interaction.user.id, -amount)
    result = random.choice(["red", "black"])
    if result == choice:
        winnings = amount * 2
        await update_balance(interaction.user.id, winnings)
        msg = f"Ball landed on **{result}**. You won **{winnings}**!"
    else:
        msg = f"Ball landed on **{result}**. You lost."
    await interaction.response.send_message(msg)

# ---------------- MINES 5x5 ----------------# ---------------- MINES 5x5 ----------------
class MinesButton(discord.ui.Button):
    def __init__(self, idx):
        super().__init__(label="❔", style=discord.ButtonStyle.secondary, row=idx // 5)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        view: MinesView = self.view
        if interaction.user.id != view.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        if view.game_over:
            return await interaction.response.send_message("Game over.", ephemeral=True)

        cell = view.board[self.idx]

        if cell == "bomb":
            self.label = "💣"
            self.style = discord.ButtonStyle.danger
            view.game_over = True
            for b in view.children:
                b.disabled = True
            await interaction.response.edit_message(
                content=f"💥 You hit a bomb! Lost **{view.wager}**.",
                view=view
            )
            return

        # Safe cell clicked
        self.label = "💎"
        self.style = discord.ButtonStyle.success
        self.disabled = True
        view.revealed += 1

        # Check if all safe cells revealed
        if view.revealed == view.safe_count:
            winnings = view.wager * 5
            await update_balance(view.user.id, winnings)
            view.game_over = True
            for b in view.children:
                b.disabled = True
            await interaction.response.edit_message(
                content=f"🎉 Cleared the board! You won **{winnings}**!",
                view=view
            )
            return

        await interaction.response.edit_message(view=view)


class MinesView(discord.ui.View):
    def __init__(self, user, wager):
        super().__init__(timeout=120)
        self.user = user
        self.wager = wager
        self.game_over = False
        self.revealed = 0

        # 5x5 grid -> 25 cells, 10 bombs
        self.board = ["bomb"] * 5 + ["safe"] * 20
        random.shuffle(self.board)
        self.safe_count = 20

        for i in range(25):
            self.add_item(MinesButton(i))


@tree.command(name="mines", description="Play Mines 5x5")
async def mines(interaction: discord.Interaction, amount: int):
    user = interaction.user
    bal = await get_balance(user.id)
    if amount <= 0 or amount > bal:
        return await interaction.response.send_message("Invalid wager or not enough balance.", ephemeral=True)

    await update_balance(user.id, -amount)

    view = MinesView(user, amount)
    await interaction.response.send_message("💎 Mines Game 5x5! Avoid the bombs and click safely.", view=view)

# ---------------- BLACKJACK ----------------
# ---------------- BLACKJACK ----------------
def draw_card():
    ranks = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
    suits = ["♠","♥","♦","♣"]
    return random.choice(ranks) + random.choice(suits)

def hand_value(cards):
    value = 0
    aces = 0
    for c in cards:
        rank = c[:-1]
        if rank in ["J","Q","K"]:
            value += 10
        elif rank == "A":
            value += 11
            aces += 1
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value

class BJView(discord.ui.View):
    def __init__(self, user, wager):
        super().__init__(timeout=60)
        self.user = user
        self.wager = wager
        self.player = [draw_card(), draw_card()]
        self.dealer = [draw_card(), draw_card()]
        self.game_over = False

        self.hit_button = BJHit()
        self.stand_button = BJStand()
        self.add_item(self.hit_button)
        self.add_item(self.stand_button)

    async def render(self, interaction, final=False):
        player_val = hand_value(self.player)
        dealer_val = hand_value(self.dealer)

        if final:
            content = f"Your cards: {self.player} = **{player_val}**\nDealer: {self.dealer} = **{dealer_val}**"
        else:
            content = f"Your cards: {self.player} = **{player_val}**\nDealer shows: {self.dealer[0]}"

        await interaction.response.edit_message(content=content, view=self)

class BJHit(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Hit", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: BJView = self.view
        if interaction.user.id != view.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        if view.game_over: return

        view.player.append(draw_card())
        player_val = hand_value(view.player)

        if player_val > 21:
            view.game_over = True
            for child in view.children: child.disabled = True
            await interaction.response.edit_message(
                content=f"Your cards: {view.player} = **{player_val}**\nBusted! Lost **{view.wager}**.",
                view=view
            )
            return

        await view.render(interaction)

class BJStand(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Stand", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        view: BJView = self.view
        if interaction.user.id != view.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        if view.game_over: return

        view.game_over = True
        # Dealer draws until 17+
        while hand_value(view.dealer) < 17:
            view.dealer.append(draw_card())

        p_val = hand_value(view.player)
        d_val = hand_value(view.dealer)

        for child in view.children: child.disabled = True

        if d_val > 21 or p_val > d_val:
            winnings = view.wager * 2
            await update_balance(view.user.id, winnings)
            result_msg = f"You win **{winnings}**!"
        elif p_val == d_val:
            await update_balance(view.user.id, view.wager)
            result_msg = "Push. Wager refunded."
        else:
            result_msg = f"You lost **{view.wager}**."

        await interaction.response.edit_message(
            content=f"Your cards: {view.player} ({p_val})\nDealer: {view.dealer} ({d_val})\n{result_msg}",
            view=view
        )

@tree.command(name="blackjack", description="Play Blackjack against the dealer")
async def blackjack(interaction: discord.Interaction, amount: int):
    user = interaction.user
    bal = await get_balance(user.id)
    if amount <= 0 or amount > bal:
        return await interaction.response.send_message("Invalid wager or insufficient balance.", ephemeral=True)

    await update_balance(user.id, -amount)
    view = BJView(user, amount)
    await interaction.response.send_message("🃏 Blackjack:", view=view)
# ---------------- BALANCE COMMAND ----------------
@tree.command(name="bal", description="Check your balance")
async def bal(interaction: discord.Interaction):
    user = interaction.user
    balance = await get_balance(user.id)
    await interaction.response.send_message(f"💰 **{user.display_name}**, your balance is **{balance}** coins.")

# ---------------- GIFT COMMAND ----------------
@tree.command(name="gift", description="Gift coins to another user")
@app_commands.describe(user="The user to gift coins to", amount="Amount to gift")
async def gift(interaction: discord.Interaction, user: discord.User, amount: int):
    giver = interaction.user
    if giver.id == user.id:
        return await interaction.response.send_message("You can't gift coins to yourself.", ephemeral=True)
    
    giver_bal = await get_balance(giver.id)
    if amount <= 0:
        return await interaction.response.send_message("Enter a valid amount.", ephemeral=True)
    if amount > giver_bal:
        return await interaction.response.send_message("You don't have enough balance to gift that much.", ephemeral=True)
    
    # Subtract from giver and add to recipient
    await update_balance(giver.id, -amount)
    await update_balance(user.id, amount)
    
    await interaction.response.send_message(f"🎁 **{giver.display_name}** gifted **{amount}** coins to **{user.display_name}**!")
import random
import random
import asyncio
import discord
from discord import app_commands

@tree.command(name="slots", description="Play interactive slots!")
@app_commands.describe(amount="Amount to wager")
async def slots(interaction: discord.Interaction, amount: int):
    user = interaction.user
    bal = await get_balance(user.id)

    if amount <= 0:
        return await interaction.response.send_message("Wager must be greater than 0.", ephemeral=True)
    if amount > bal:
        return await interaction.response.send_message("You don't have enough balance.", ephemeral=True)

    await update_balance(user.id, -amount)  # deduct upfront

    emojis = ["🍒", "🍋", "🍊", "🍉", "⭐", "7️⃣"]
    roll = [random.choice(emojis) for _ in range(3)]
    display = ["❔", "❔", "❔"]  # start with empty slots

    msg = await interaction.response.send_message(f"🎰 | {' | '.join(display)}")

    # reveal each reel progressively
    for i in range(3):
        await asyncio.sleep(1)  # 1 second delay for animation
        display[i] = roll[i]
        await interaction.edit_original_response(content=f"🎰 | {' | '.join(display)}")

    # determine result
    if roll.count(roll[0]) == 3:
        multiplier = 5
        result_text = "🎉 JACKPOT! All 3 match!"
    elif any(roll.count(e) == 2 for e in roll):
        multiplier = 2
        result_text = "✅ 2 match! You win double!"
    else:
        multiplier = 0
        result_text = "❌ No match. You lost."

    winnings = amount * multiplier
    if winnings > 0:
        await update_balance(user.id, winnings)

    await asyncio.sleep(0.5)
    await interaction.edit_original_response(
        content=f"🎰 | {' | '.join(display)}\n{result_text}\nWinnings: **{winnings}**"
    )
# ---------------- OWNER-ONLY BALANCE COMMANDS ----------------
def is_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == 1329161792936476683  # replace with your Discord ID
    return app_commands.check(predicate)

@tree.command(name="add", description="Add coins to a user's balance (owner only)")
@is_owner()
@app_commands.describe(user="The user to add coins to", amount="Amount to add")
async def add(interaction: discord.Interaction, user: discord.User, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("Enter a valid positive amount.", ephemeral=True)
    new_bal = await update_balance(user.id, amount)
    await interaction.response.send_message(f"✅ Added **{amount}** coins to **{user.display_name}**. New balance: **{new_bal}**.")

@tree.command(name="rem", description="Remove coins from a user's balance (owner only)")
@is_owner()
@app_commands.describe(user="The user to remove coins from", amount="Amount to remove")
async def rem(interaction: discord.Interaction, user: discord.User, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("Enter a valid positive amount.", ephemeral=True)
    new_bal = await update_balance(user.id, -amount)
    await interaction.response.send_message(f"✅ Removed **{amount}** coins from **{user.display_name}**. New balance: **{new_bal}**.")
# ---------------- BOT READY ----------------
@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")

# ---------------- RUN ----------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
