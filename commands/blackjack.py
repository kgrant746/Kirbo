# commands/blackjack.py
from __future__ import annotations
import json, os, random, asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

import discord, config
from discord import app_commands
from discord.ext import commands

DATA_DIR = "data"
ECON_FILE = os.path.join(DATA_DIR, "economy.json")
STATE_FILE = os.path.join(DATA_DIR, "blackjack_states.json")

_ec_lock = asyncio.Lock()
STATE_TTL_SECONDS = 300  # expire a hand if idle > 5 min (still persisted for resume)
STARTING_BALANCE = 500   # per your requirement

# ----------------------------- storage helpers -----------------------------
def _ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(ECON_FILE):
        with open(ECON_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)

async def _load_json(path: str) -> dict:
    _ensure_files()
    async with _ec_lock:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

async def _save_json(path: str, data: dict) -> None:
    _ensure_files()
    async with _ec_lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

def _ensure_user_row(eco: dict, user_id: int) -> dict:
    s = str(user_id)
    if s not in eco:
        eco[s] = {
            "balance": STARTING_BALANCE,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "hands": 0,
            "biggest_win": 0,
            "last_charity_ymd": None  # "YYYY-MM-DD" once used
        }
    return eco[s]

async def _get_balance(user_id: int) -> int:
    eco = await _load_json(ECON_FILE)
    row = _ensure_user_row(eco, user_id)
    await _save_json(ECON_FILE, eco)
    return int(row["balance"])

async def _set_balance(user_id: int, new_balance: int):
    eco = await _load_json(ECON_FILE)
    row = _ensure_user_row(eco, user_id)
    row["balance"] = max(0, int(new_balance))
    await _save_json(ECON_FILE, eco)

async def _bump_stats(user_id: int, *, win=0, loss=0, push=0, payout=0):
    eco = await _load_json(ECON_FILE)
    row = _ensure_user_row(eco, user_id)
    row["wins"] += win
    row["losses"] += loss
    row["pushes"] += push
    if win or loss or push:
        row["hands"] += 1
    if payout > row.get("biggest_win", 0):
        row["biggest_win"] = payout
    await _save_json(ECON_FILE, eco)

async def _get_last_charity_ymd(user_id: int) -> Optional[str]:
    eco = await _load_json(ECON_FILE)
    row = _ensure_user_row(eco, user_id)
    await _save_json(ECON_FILE, eco)
    return row.get("last_charity_ymd")

async def _set_last_charity_ymd(user_id: int, ymd: str):
    eco = await _load_json(ECON_FILE)
    row = _ensure_user_row(eco, user_id)
    row["last_charity_ymd"] = ymd
    await _save_json(ECON_FILE, eco)

# ----------------------------- blackjack engine -----------------------------
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def _new_deck(shoe_decks: int = 6) -> List[str]:
    deck = [f"{r}{s}" for r in RANKS for s in SUITS] * shoe_decks
    random.shuffle(deck)
    return deck

def _hand_value(cards: List[str]) -> Tuple[int, bool]:
    total = 0
    aces = 0
    for c in cards:
        r = c[:-1] if c[:-1] != "" else c[0]
        if r == "A":
            aces += 1
            total += 11
        elif r in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(r)
    soft = False
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    if aces > 0 and total <= 21:
        # at least one ace still counts as 11
        # note: if all aces were reduced to 1, this remains False
        soft = True
    return total, soft

def _is_blackjack(cards: List[str]) -> bool:
    if len(cards) != 2:
        return False
    v, _ = _hand_value(cards)
    return v == 21

@dataclass
class BJState:
    user_id: int
    bet: int
    deck: List[str] = field(default_factory=_new_deck)
    player: List[str] = field(default_factory=list)
    dealer: List[str] = field(default_factory=list)
    active: bool = True
    last_ts: float = 0.0
    doubled: bool = False
    surrendered: bool = False

    def serialize(self) -> dict:
        return {
            "user_id": self.user_id,
            "bet": self.bet,
            "deck": self.deck,
            "player": self.player,
            "dealer": self.dealer,
            "active": self.active,
            "last_ts": self.last_ts,
            "doubled": self.doubled,
            "surrendered": self.surrendered,
        }

    @staticmethod
    def deserialize(d: dict) -> "BJState":
        obj = BJState(d["user_id"], d["bet"])
        obj.deck = d["deck"]
        obj.player = d["player"]
        obj.dealer = d["dealer"]
        obj.active = d["active"]
        obj.last_ts = d.get("last_ts", 0.0)
        obj.doubled = d.get("doubled", False)
        obj.surrendered = d.get("surrendered", False)
        return obj

async def _load_state(user_id: int) -> BJState | None:
    states = await _load_json(STATE_FILE)
    s = states.get(str(user_id))
    if not s:
        return None
    return BJState.deserialize(s)

async def _save_state(state: BJState | None):
    states = await _load_json(STATE_FILE)
    if state is None:
        await _save_json(STATE_FILE, states)
        return
    states[str(state.user_id)] = state.serialize()
    await _save_json(STATE_FILE, states)

async def _clear_state(user_id: int):
    states = await _load_json(STATE_FILE)
    states.pop(str(user_id), None)
    await _save_json(STATE_FILE, states)

def _format_hand(cards: List[str], hide_first: bool = False) -> str:
    if not hide_first:
        return " ".join(cards)
    if not cards:
        return ""
    visible = " ".join(cards[1:])  # show the upcard(s)
    return f"{visible} ??" if visible else "??"

def _options_text(state: Optional[BJState], resolved: bool) -> str:
    if resolved or not state or not state.active:
        return "Next: `/blackjack bet:<amount>`, `/balance`, `/leaderboard`, `/charity`"
    parts = ["Next: `/hit`, `/stand`"]
    if not state.doubled and len(state.player) == 2:
        parts.append("`/double`")
        parts.append("`/fold`") 
    parts.append("`/balance`")
    return " ".join(parts)

def _out_embed(user: discord.User, state: BJState, reveal: bool = False, footer: str | None = None, resolved: bool = False) -> discord.Embed:
    pv, psoft = _hand_value(state.player)

    e = discord.Embed(title=f"Blackjack — {user.display_name}", color=discord.Color.dark_green())
    # Player line always shows value
    e.add_field(
        name="Your Hand",
        value=f"{_format_hand(state.player)}  (**{pv}**{' soft' if psoft else ''})",
        inline=False
    )

    if reveal:
        dv, dsoft = _hand_value(state.dealer)
        dealer_line = f"{_format_hand(state.dealer, hide_first=False)}  (**{dv}**{' soft' if dsoft else ''})"
    else:
        # Hide dealer total entirely until reveal — no (??)
        dealer_line = _format_hand(state.dealer, hide_first=True)

    e.add_field(name="Dealer", value=dealer_line, inline=False)
    e.add_field(name="Bet", value=f"${state.bet}", inline=True)
    e.set_footer(text=footer or _options_text(state, resolved))
    return e


# ----------------------------- slash commands -----------------------------
def setup(bot: commands.Bot | discord.Bot) -> None:
    guilds = [discord.Object(id=config.GUILD_ID)]

    @bot.tree.command(name="balance", description="Show your casino balance", guilds=guilds)
    async def balance(interaction: discord.Interaction):
        bal = await _get_balance(interaction.user.id)
        await interaction.response.send_message(f"Your balance: **${bal}**", ephemeral=True)

    @bot.tree.command(name="leaderboard", description="Top balances", guilds=guilds)
    async def leaderboard(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        eco = await _load_json(ECON_FILE)
        rows = sorted(((int(uid), d.get("balance", 0)) for uid, d in eco.items()), key=lambda x: x[1], reverse=True)[:10]
        lines = []
        for i, (uid, bal) in enumerate(rows, 1):
            lines.append(f"{i}. <@{uid}> — **${bal}**")
        await interaction.followup.send("\n".join(lines) if lines else "No data yet.")

    @bot.tree.command(name="charity", description="Claim a daily random grant (0–1000). Once per day.", guilds=guilds)
    async def charity(interaction: discord.Interaction):
        from datetime import date as _date
        today = _date.today().isoformat()
        last = await _get_last_charity_ymd(interaction.user.id)
        if last == today:
            return await interaction.response.send_message("You already claimed today. Try again tomorrow.", ephemeral=True)
        amount = random.randint(0, 1000)
        bal = await _get_balance(interaction.user.id)
        await _set_balance(interaction.user.id, bal + amount)
        await _set_last_charity_ymd(interaction.user.id, today)
        await interaction.response.send_message(f"🎁 Charity granted **${amount}**. New balance: **${bal + amount}**", ephemeral=True)

    @bot.tree.command(name="blackjack", description="Start or resume a blackjack hand", guilds=guilds)
    @app_commands.describe(bet="Your wager in dollars (ignored if resuming an active hand)")
    async def blackjack(interaction: discord.Interaction, bet: Optional[int] = None):
        await interaction.response.defer(ephemeral=True)

        # resume if active
        existing = await _load_state(interaction.user.id)
        if existing and existing.active:
            footer = _options_text(existing, resolved=False)
            return await interaction.followup.send(embed=_out_embed(interaction.user, existing, reveal=False, footer=footer, resolved=False))

        if bet is None or bet <= 0:
            return await interaction.followup.send("Provide a positive bet to start a new hand, or resume your active one.")

        bal = await _get_balance(interaction.user.id)
        if bet > bal:
            return await interaction.followup.send(f"Insufficient balance. You have **${bal}**.")

        # take bet
        await _set_balance(interaction.user.id, bal - bet)

        state = BJState(user_id=interaction.user.id, bet=bet, deck=_new_deck())
        # deal
        state.player.append(state.deck.pop())
        state.dealer.append(state.deck.pop())
        state.player.append(state.deck.pop())
        state.dealer.append(state.deck.pop())
        state.last_ts = interaction.created_at.timestamp() if interaction.created_at else 0

        player_bj = _is_blackjack(state.player)
        dealer_bj = _is_blackjack(state.dealer)
        if player_bj or dealer_bj:
            await _resolve_and_payout(interaction, state, natural=True)
            return

        await _save_state(state)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, resolved=False))

    @bot.tree.command(name="hit", description="Hit in your blackjack hand", guilds=guilds)
    async def hit(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return
        state.player.append(state.deck.pop())
        v, _ = _hand_value(state.player)
        if v >= 21:
            await _dealer_maybe_and_resolve(interaction, state)
            return
        await _save_state(state)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, resolved=False))

    @bot.tree.command(name="stand", description="Stand in your blackjack hand", guilds=guilds)
    async def stand(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return
        await _dealer_play(state)
        await _resolve_and_payout(interaction, state)

    @bot.tree.command(name="double", description="Double your bet and take one card", guilds=guilds)
    async def double(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return
        if state.doubled:
            return await interaction.followup.send("You already doubled.")
        if len(state.player) != 2:
            return await interaction.followup.send("You can only double on your first action.")
        bal = await _get_balance(interaction.user.id)
        if bal < state.bet:
            return await interaction.followup.send("Insufficient balance to double.")
        await _set_balance(interaction.user.id, bal + 0 - state.bet)
        state.bet *= 2
        state.doubled = True
        state.player.append(state.deck.pop())
        await _dealer_maybe_and_resolve(interaction, state)

    @bot.tree.command(name="fold", description="Fold your hand (get half your bet back)", guilds=guilds)
    async def fold(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return
        if len(state.player) != 2:
            return await interaction.followup.send("You can only fold on your first action.")
        state.surrendered = True  # reuse the same flag
        await _resolve_and_payout(interaction, state)


# ----------------------------- helpers -----------------------------
def _validate_state(inter: discord.Interaction, state: Optional[BJState]) -> bool:
    if state is None or not state.active:
        asyncio.create_task(inter.followup.send("You do not have an active hand. Use `/blackjack bet:<amount>` to start."))
        return False
    now_ts = inter.created_at.timestamp() if inter.created_at else 0
    if state.last_ts and now_ts - state.last_ts > STATE_TTL_SECONDS:
        asyncio.create_task(inter.followup.send("Your hand expired due to inactivity."))
        asyncio.create_task(_clear_state(inter.user.id))
        return False
    state.last_ts = now_ts
    return True

async def _dealer_play(state: BJState):
    while True:
        v, soft = _hand_value(state.dealer)
        if v < 17 or (v == 17 and soft):  # hit soft 17
            state.dealer.append(state.deck.pop())
        else:
            break

async def _dealer_maybe_and_resolve(interaction: discord.Interaction, state: BJState):
    pv, _ = _hand_value(state.player)
    if pv < 21 and state.doubled:
        await _dealer_play(state)
    await _resolve_and_payout(interaction, state)

async def _resolve_and_payout(interaction: discord.Interaction, state: BJState, natural: bool = False):
    user_id = interaction.user.id
    pv, _ = _hand_value(state.player)
    dv, _ = _hand_value(state.dealer)
    reveal = True

    result = ""
    payout = 0

    if state.surrendered:
        result = f"You folded. Returned **${state.bet // 2}**."
        payout = state.bet // 2
        await _bump_stats(user_id, loss=1)

    else:
        if natural:
            player_bj = _is_blackjack(state.player)
            dealer_bj = _is_blackjack(state.dealer)
            if player_bj and dealer_bj:
                result = "Push on blackjacks. Bet returned."
                payout = state.bet
                await _bump_stats(user_id, push=1)
            elif player_bj:
                win_amt = int(state.bet * 2.5)  # bet returned + 3:2
                payout = win_amt
                result = f"Blackjack — you win **${win_amt - state.bet}**."
                await _bump_stats(user_id, win=1, payout=win_amt - state.bet)
            else:
                result = "Dealer blackjack. You lose."
                await _bump_stats(user_id, loss=1)
        else:
            if pv > 21:
                result = "You busted. You lose."
                await _bump_stats(user_id, loss=1)
            else:
                await _dealer_play(state)
                dv, _ = _hand_value(state.dealer)
                if dv > 21:
                    win_amt = state.bet * 2
                    payout = win_amt
                    result = f"Dealer busts — you win **${win_amt - state.bet}**."
                    await _bump_stats(user_id, win=1, payout=win_amt - state.bet)
                elif pv > dv:
                    win_amt = state.bet * 2
                    payout = win_amt
                    result = f"You win **${win_amt - state.bet}**."
                    await _bump_stats(user_id, win=1, payout=win_amt - state.bet)
                elif pv < dv:
                    result = "You lose."
                    await _bump_stats(user_id, loss=1)
                else:
                    payout = state.bet
                    result = "Push. Bet returned."
                    await _bump_stats(user_id, push=1)

    if payout:
        bal = await _get_balance(user_id)
        await _set_balance(user_id, bal + payout)

    state.active = False
    await _save_state(state)
    e = _out_embed(
        interaction.user,
        state,
        reveal=reveal,
        footer=f"{result}  Balance: **${await _get_balance(user_id)}**",
        resolved=True
    )
    await interaction.followup.send(embed=e)
    await _clear_state(user_id)
