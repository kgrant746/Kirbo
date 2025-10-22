from __future__ import annotations
import json, os, random, asyncio
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import discord, config
from discord import app_commands
from discord.ext import commands

DATA_DIR = "data"
ECON_FILE = os.path.join(DATA_DIR, "economy.json")
STATE_FILE = os.path.join(DATA_DIR, "blackjack_states.json")

_ec_lock = asyncio.Lock()
STATE_TTL_SECONDS = 300   # hand expires if idle > 5 minutes (persisted but not playable)
STARTING_BALANCE = 500

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
            "last_charity_ymd": None
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

def _parse_bet(bet_raw: Optional[str], balance: int) -> Optional[int]:
    if bet_raw is None:
        return None
    s = str(bet_raw).strip().lower()
    if s in ("all", "max"):
        return balance
    try:
        v = int(s)
        return v if v > 0 else None
    except ValueError:
        return None

# ----------------------------- blackjack engine -----------------------------
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def _new_deck(shoe_decks: int = 6) -> List[str]:
    deck = [f"{r}{s}" for r in RANKS for s in SUITS] * shoe_decks
    random.shuffle(deck)
    return deck

def _rank(card: str) -> str:
    # card like "10♠" or "A♦"
    r = card[:-1]
    return r if r else card[0]

def _hand_value(cards: List[str]) -> Tuple[int, bool]:
    total = 0
    aces = 0
    for c in cards:
        r = _rank(c)
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

    # Hand 1 (always exists)
    player: List[str] = field(default_factory=list)
    doubled1: bool = False
    surrendered1: bool = False
    finished1: bool = False

    # Split support (one additional hand max)
    split: bool = False
    hand2: List[str] = field(default_factory=list)
    bet2: int = 0
    doubled2: bool = False
    surrendered2: bool = False
    finished2: bool = False

    # Common
    dealer: List[str] = field(default_factory=list)
    active: bool = True
    active_idx: int = 1  # 1 or 2 (if split)
    last_ts: float = 0.0

    def serialize(self) -> dict:
        return {
            "user_id": self.user_id,
            "bet": self.bet,
            "deck": self.deck,
            "player": self.player,
            "doubled1": self.doubled1,
            "surrendered1": self.surrendered1,
            "finished1": self.finished1,
            "split": self.split,
            "hand2": self.hand2,
            "bet2": self.bet2,
            "doubled2": self.doubled2,
            "surrendered2": self.surrendered2,
            "finished2": self.finished2,
            "dealer": self.dealer,
            "active": self.active,
            "active_idx": self.active_idx,
            "last_ts": self.last_ts,
        }

    @staticmethod
    def deserialize(d: dict) -> "BJState":
        obj = BJState(d["user_id"], d["bet"])
        obj.deck = d["deck"]
        obj.player = d["player"]
        obj.doubled1 = d.get("doubled1", False)
        obj.surrendered1 = d.get("surrendered1", False)
        obj.finished1 = d.get("finished1", False)
        obj.split = d.get("split", False)
        obj.hand2 = d.get("hand2", [])
        obj.bet2 = d.get("bet2", 0)
        obj.doubled2 = d.get("doubled2", False)
        obj.surrendered2 = d.get("surrendered2", False)
        obj.finished2 = d.get("finished2", False)
        obj.dealer = d["dealer"]
        obj.active = d["active"]
        obj.active_idx = d.get("active_idx", 1)
        obj.last_ts = d.get("last_ts", 0.0)
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
    visible = " ".join(cards[1:])
    return f"{visible} ??" if visible else "??"

def _can_split(state: BJState, balance: int) -> bool:
    if state.split:
        return False
    if len(state.player) != 2:
        return False
    r1, r2 = _rank(state.player[0]), _rank(state.player[1])
    if r1 != r2:
        return False
    if balance < state.bet:
        return False
    return True

def _options_text(state: Optional[BJState], resolved: bool, balance: Optional[int] = None) -> str:
    if resolved or not state or not state.active:
        return "Next: `/blackjack bet:<amount>`, `/balance`, `/leaderboard`, `/charity`"
    parts = ["Next: `/hit`, `/stand`"]
    # double/fold only when starting a hand (len == 2) on active hand
    active_cards = state.player if state.active_idx == 1 else state.hand2
    if len(active_cards) == 2:
        if (state.active_idx == 1 and not state.doubled1) or (state.active_idx == 2 and not state.doubled2):
            parts.append("`/double`")
        parts.append("`/fold`")
        # split only before any action and only on first hand before split
        if state.active_idx == 1 and balance is not None and _can_split(state, balance):
            parts.append("`/split`")
    parts.append("`/balance`")
    return " ".join(parts)

def _hand_line(cards: List[str], title: str, mark_active: bool) -> Tuple[str, str]:
    v, soft = _hand_value(cards)
    name = f"{'→ ' if mark_active else ''}{title}"
    val = f"{_format_hand(cards)}  (**{v}**{' soft' if soft else ''})"
    return name, val

def _out_embed(user: discord.User, state: BJState, *, reveal: bool = False, footer: str | None = None, resolved: bool = False) -> discord.Embed:
    e = discord.Embed(title=f"Blackjack — {user.display_name}", color=discord.Color.dark_green())
    # Player (one or two hands)
    if state.split:
        n1, v1 = _hand_line(state.player, "Hand 1", state.active_idx == 1 and state.active)
        n2, v2 = _hand_line(state.hand2, "Hand 2", state.active_idx == 2 and state.active)
        e.add_field(name=n1, value=v1, inline=False)
        e.add_field(name=n2, value=v2, inline=False)
        e.add_field(name="Bets", value=f"Hand 1: ${state.bet} • Hand 2: ${state.bet2}", inline=True)
    else:
        pv, psoft = _hand_value(state.player)
        e.add_field(
            name="Your Hand",
            value=f"{_format_hand(state.player)}  (**{pv}**{' soft' if psoft else ''})",
            inline=False
        )
        e.add_field(name="Bet", value=f"${state.bet}", inline=True)

    # Dealer
    if reveal:
        dv, dsoft = _hand_value(state.dealer)
        dealer_line = f"{_format_hand(state.dealer, hide_first=False)}  (**{dv}**{' soft' if dsoft else ''})"
    else:
        dealer_line = _format_hand(state.dealer, hide_first=True)
    e.add_field(name="Dealer", value=dealer_line, inline=False)

    if footer:
        e.set_footer(text=footer)
    return e

# ----------------------------- helpers for split flow -----------------------------
def _active_cards(state: BJState) -> List[str]:
    return state.player if state.active_idx == 1 else state.hand2

def _set_active_cards(state: BJState, cards: List[str]):
    if state.active_idx == 1:
        state.player = cards
    else:
        state.hand2 = cards

def _mark_finished_current(state: BJState):
    if state.active_idx == 1:
        state.finished1 = True
    else:
        state.finished2 = True

def _current_len(state: BJState) -> int:
    return len(_active_cards(state))

def _current_doubled(state: BJState) -> bool:
    return state.doubled1 if state.active_idx == 1 else state.doubled2

def _set_current_doubled(state: BJState):
    if state.active_idx == 1:
        state.doubled1 = True
    else:
        state.doubled2 = True

def _both_finished(state: BJState) -> bool:
    return state.finished1 and (not state.split or state.finished2)

async def _dealer_play(state: BJState):
    while True:
        v, soft = _hand_value(state.dealer)
        if v < 17 or (v == 17 and soft):  # hit soft 17
            state.dealer.append(state.deck.pop())
        else:
            break

async def _resolve_split_or_single(inter: discord.Interaction, state: BJState, *, natural: bool = False):
    """Resolve a single-hand round or both hands if split."""
    user_id = inter.user.id
    results_lines: List[str] = []
    total_payout = 0

    async def settle_one(cards: List[str], bet: int, tag: str):
        nonlocal total_payout
        pv, _ = _hand_value(cards)
        dv, _ = _hand_value(state.dealer)

        if natural:
            player_bj = _is_blackjack(cards)
            dealer_bj = _is_blackjack(state.dealer)
            if player_bj and dealer_bj:
                results_lines.append(f"{tag}: Push on blackjacks. Bet returned.")
                total_payout += bet
                await _bump_stats(user_id, push=1)
            elif player_bj:
                win_amt = int(bet * 2.5)
                total_payout += win_amt
                results_lines.append(f"{tag}: Blackjack — you win **${win_amt - bet}**.")
                await _bump_stats(user_id, win=1, payout=win_amt - bet)
            else:
                results_lines.append(f"{tag}: Dealer blackjack. You lose.")
                await _bump_stats(user_id, loss=1)
            return

        if pv > 21:
            results_lines.append(f"{tag}: You busted. You lose.")
            await _bump_stats(user_id, loss=1)
            return

        if dv > 21:
            win_amt = bet * 2
            total_payout += win_amt
            results_lines.append(f"{tag}: Dealer busts — you win **${win_amt - bet}**.")
            await _bump_stats(user_id, win=1, payout=win_amt - bet)
        elif pv > dv:
            win_amt = bet * 2
            total_payout += win_amt
            results_lines.append(f"{tag}: You win **${win_amt - bet}**.")
            await _bump_stats(user_id, win=1, payout=win_amt - bet)
        elif pv < dv:
            results_lines.append(f"{tag}: You lose.")
            await _bump_stats(user_id, loss=1)
        else:
            total_payout += bet
            results_lines.append(f"{tag}: Push. Bet returned.")
            await _bump_stats(user_id, push=1)

    # if folded (surrendered) on single-hand flow
    if not state.split and state.surrendered1:
        refund = state.bet // 2
        total_payout += refund
        results_lines.append(f"Returned **${refund}** for folding.")
        await _bump_stats(user_id, loss=1)
    else:
        # If both hands finished (or single), dealer plays (except pure natural resolution handled upstream)
        if not natural:
            await _dealer_play(state)

        if state.split:
            # Hand 1
            if state.surrendered1:
                r = state.bet // 2
                total_payout += r
                results_lines.append(f"Hand 1: Folded — returned **${r}**.")
                await _bump_stats(user_id, loss=1)
            else:
                await settle_one(state.player, state.bet, "Hand 1")

            # Hand 2
            if state.surrendered2:
                r = state.bet2 // 2
                total_payout += r
                results_lines.append(f"Hand 2: Folded — returned **${r}**.")
                await _bump_stats(user_id, loss=1)
            else:
                await settle_one(state.hand2, state.bet2, "Hand 2")
        else:
            await settle_one(state.player, state.bet, "Result")

    if total_payout:
        bal = await _get_balance(user_id)
        await _set_balance(user_id, bal + total_payout)

    state.active = False
    await _save_state(state)

    footer = "  ".join(results_lines) + f"  Balance: **${await _get_balance(user_id)}**"
    e = _out_embed(inter.user, state, reveal=True, footer=footer, resolved=True)
    await inter.followup.send(embed=e)
    await _clear_state(user_id)

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
        rows = sorted(((int(uid), d.get("balance", 0), d.get("hands", 0)) for uid, d in eco.items()),
                      key=lambda x: x[1], reverse=True)[:10]
        lines = []
        for i, (uid, bal, hands) in enumerate(rows, 1):
            lines.append(f"{i}. <@{uid}> — **${bal}** — Total Hands: **{hands}**")
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

    @bot.tree.command(name="broke", description="Only usable entirely broke ($0). Gives a random amount of money ($1-$50)", guilds=guilds)
    async def broke(interaction: discord.Interaction):
        bal = await _get_balance(interaction.user.id)
        if bal != 0:
            return await interaction.response.send_message("You ain't broke!", ephemeral=True)
        amount = random.randint(1, 50)
        await _set_balance(interaction.user.id, bal + amount)
        await interaction.response.send_message(f"Pity money granted. New balance: **${bal + amount}**", ephemeral=True)

    @bot.tree.command(name="blackjack", description="Start or resume a blackjack hand", guilds=guilds)
    @app_commands.describe(bet="Your wager (e.g., 250 or 'all'). Ignored if resuming an active hand.")
    async def blackjack(interaction: discord.Interaction, bet: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)

        # Resume if there’s an active hand
        existing = await _load_state(interaction.user.id)
        if existing and existing.active:
            bal = await _get_balance(interaction.user.id)
            footer = _options_text(existing, resolved=False, balance=bal)
            return await interaction.followup.send(
                embed=_out_embed(interaction.user, existing, reveal=False, footer=footer, resolved=False)
            )

        bal = await _get_balance(interaction.user.id)

        # Parse bet (supports "all"/"max" or a number)
        bet_val = _parse_bet(bet, bal)
        if bet_val is None:
            return await interaction.followup.send("Provide a valid bet (e.g., `250` or `all`).")
        if bet_val > bal:
            return await interaction.followup.send(f"Insufficient balance. You have **${bal}**.")

        # Take bet
        await _set_balance(interaction.user.id, bal - bet_val)

        state = BJState(user_id=interaction.user.id, bet=bet_val, deck=_new_deck())
        # initial deal
        state.player.append(state.deck.pop())
        state.dealer.append(state.deck.pop())
        state.player.append(state.deck.pop())
        state.dealer.append(state.deck.pop())
        state.last_ts = interaction.created_at.timestamp() if interaction.created_at else 0

        player_bj = _is_blackjack(state.player)
        dealer_bj = _is_blackjack(state.dealer)
        if player_bj or dealer_bj:
            await _resolve_split_or_single(interaction, state, natural=True)
            return

        await _save_state(state)
        bal = await _get_balance(interaction.user.id)
        footer = _options_text(state, resolved=False, balance=bal)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

    @bot.tree.command(name="hit", description="Hit in your blackjack hand", guilds=guilds)
    async def hit(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return

        cards = _active_cards(state)
        cards.append(state.deck.pop())
        _set_active_cards(state, cards)

        v, _ = _hand_value(cards)
        # If bust or (double -> forced stand after one card)
        if v >= 21 or _current_doubled(state):
            _mark_finished_current(state)
            # move to second hand if exists
            if state.split and state.active_idx == 1 and not state.finished2:
                state.active_idx = 2
                await _save_state(state)
                bal = await _get_balance(interaction.user.id)
                footer = _options_text(state, resolved=False, balance=bal)
                return await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

            # resolve if both done
            if _both_finished(state):
                await _save_state(state)
                return await _resolve_split_or_single(interaction, state)

        await _save_state(state)
        bal = await _get_balance(interaction.user.id)
        footer = _options_text(state, resolved=False, balance=bal)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

    @bot.tree.command(name="stand", description="Stand in your blackjack hand", guilds=guilds)
    async def stand(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return

        _mark_finished_current(state)

        # move to next hand if split
        if state.split and state.active_idx == 1 and not state.finished2:
            state.active_idx = 2
            await _save_state(state)
            bal = await _get_balance(interaction.user.id)
            footer = _options_text(state, resolved=False, balance=bal)
            return await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

        if _both_finished(state):
            await _save_state(state)
            return await _resolve_split_or_single(interaction, state)

        await _save_state(state)
        bal = await _get_balance(interaction.user.id)
        footer = _options_text(state, resolved=False, balance=bal)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

    @bot.tree.command(name="double", description="Double your bet and take one card", guilds=guilds)
    async def double(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return

        cards = _active_cards(state)
        if len(cards) != 2:
            return await interaction.followup.send("You can only double on your first action of a hand.")

        if _current_doubled(state):
            return await interaction.followup.send("You already doubled this hand.")

        bal = await _get_balance(interaction.user.id)
        needed = state.bet if state.active_idx == 1 else state.bet2
        if bal < needed:
            return await interaction.followup.send("Insufficient balance to double.")

        await _set_balance(interaction.user.id, bal - needed)
        if state.active_idx == 1:
            state.bet *= 2
        else:
            state.bet2 *= 2

        _set_current_doubled(state)

        # one card only, then stand on this hand
        cards.append(state.deck.pop())
        _set_active_cards(state, cards)
        _mark_finished_current(state)

        # move to next or resolve
        if state.split and state.active_idx == 1 and not state.finished2:
            state.active_idx = 2
            await _save_state(state)
            bal = await _get_balance(interaction.user.id)
            footer = _options_text(state, resolved=False, balance=bal)
            return await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

        if _both_finished(state):
            await _save_state(state)
            return await _resolve_split_or_single(interaction, state)

        await _save_state(state)
        bal = await _get_balance(interaction.user.id)
        footer = _options_text(state, resolved=False, balance=bal)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

    @bot.tree.command(name="fold", description="Fold your hand (get half your bet back)", guilds=guilds)
    async def fold(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return

        # Only allowed as first action on a hand
        if _current_len(state) != 2:
            return await interaction.followup.send("You can only fold on your first action of a hand.")
        if state.split and state.active_idx == 2 and not state.finished1:
            # shouldn't happen, but keep order: play hand 1 fully first
            return await interaction.followup.send("Finish Hand 1 first.")

        if state.active_idx == 1:
            state.surrendered1 = True
        else:
            state.surrendered2 = True

        _mark_finished_current(state)

        if state.split and state.active_idx == 1 and not state.finished2:
            state.active_idx = 2
            await _save_state(state)
            bal = await _get_balance(interaction.user.id)
            footer = _options_text(state, resolved=False, balance=bal)
            return await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

        if _both_finished(state):
            await _save_state(state)
            return await _resolve_split_or_single(interaction, state)

        await _save_state(state)
        bal = await _get_balance(interaction.user.id)
        footer = _options_text(state, resolved=False, balance=bal)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

    @bot.tree.command(name="split", description="Split your initial pair into two hands", guilds=guilds)
    async def split_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await _load_state(interaction.user.id)
        if not _validate_state(interaction, state):
            return

        if state.split or state.active_idx != 1:
            return await interaction.followup.send("You can only split once, before playing Hand 1.")
        bal = await _get_balance(interaction.user.id)
        if not _can_split(state, bal):
            return await interaction.followup.send("You can only split identical ranks and you must have enough balance for a second bet.")

        # Take second bet
        await _set_balance(interaction.user.id, bal - state.bet)

        # Perform split
        c1, c2 = state.player[0], state.player[1]
        state.player = [c1, state.deck.pop()]
        state.hand2 = [c2, state.deck.pop()]
        state.bet2 = state.bet
        state.split = True
        state.active_idx = 1
        state.finished1 = False
        state.finished2 = False

        await _save_state(state)
        bal = await _get_balance(interaction.user.id)
        footer = _options_text(state, resolved=False, balance=bal)
        await interaction.followup.send(embed=_out_embed(interaction.user, state, reveal=False, footer=footer, resolved=False))

# ----------------------------- common validation -----------------------------
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
