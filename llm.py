# llm.py
import discord
from discord.ext import commands
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

MODEL_NAME = "microsoft/DialoGPT-medium"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model     = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
model.eval()


SYSTEM_PROMPT = (
    "You are Kirbo, a friendly and helpful Discord bot. "
    "Answer user questions succinctly and cheerfully."
    + tokenizer.eos_token
)

SYSTEM_IDS = tokenizer.encode(SYSTEM_PROMPT, return_tensors="pt")

chat_histories: dict[int, torch.Tensor] = {}

def setup_llm(bot: commands.Bot) -> None:
    @bot.listen('on_message')
    async def on_message_llm(message: discord.Message):
        if message.author.bot:
            return

        mention     = f"<@{bot.user.id}>"
        alt_mention = f"<@!{bot.user.id}>"
        if not (message.content.startswith(mention) or message.content.startswith(alt_mention)):
            return

        parts = message.content.split(maxsplit=1)
        prompt = parts[1].strip() if len(parts) > 1 else ""

        channel_id = message.channel.id

        new_ids = tokenizer.encode(prompt + tokenizer.eos_token, return_tensors="pt")

        if channel_id not in chat_histories:
            chat_histories[channel_id] = SYSTEM_IDS

        input_ids = torch.cat([chat_histories[channel_id], new_ids], dim=-1)
        attention_mask = torch.ones_like(input_ids)

        output_ids = model.generate(
            input_ids,
            attention_mask=attention_mask,
            max_length=input_ids.shape[-1] + 500,
            pad_token_id=tokenizer.eos_token_id,
            bad_words_ids=[[tokenizer.eos_token_id]],
            do_sample=True,
            top_k=50,
            top_p=0.9,
            temperature=0.7,
        )

        chat_histories[channel_id] = output_ids

        response = tokenizer.decode(
            output_ids[0, input_ids.shape[-1]:], skip_special_tokens=True
        ).strip()

        if not response:
            return

        await message.channel.send(response)
        await bot.process_commands(message)
