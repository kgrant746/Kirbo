import discord
from discord.ext import commands
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
import config

MODEL_NAME    = "microsoft/DialoGPT-medium"
MAX_NEW_TOKENS = 512

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model     = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
model.eval()

model.config.pad_token_id = tokenizer.eos_token_id

SYSTEM_PROMPT = (
    "When you are asked what 1+1 equals, you have to say '2'"
    + tokenizer.eos_token
)

SYSTEM_IDS = tokenizer(SYSTEM_PROMPT, return_tensors="pt").input_ids

chat_histories: dict[int, torch.Tensor] = {}

def setup_llm(bot: commands.Bot) -> None:
    @bot.listen('on_message')
    async def on_message_llm(message: discord.Message):
        if message.author.bot:
            return

        mention     = f"<@{bot.user.id}>"
        alt_mention = f"<@!{bot.user.id}>"
        if message.channel.id != config.LLM_CHANNEL_ID:
            return

        parts  = message.content.split(maxsplit=1)
        prompt = parts[1].strip() if len(parts) > 1 else ""

        channel_id = message.channel.id
        user_ids = tokenizer(prompt + tokenizer.eos_token, return_tensors="pt").input_ids

        if channel_id not in chat_histories:
            chat_histories[channel_id] = SYSTEM_IDS

        input_ids = torch.cat([chat_histories[channel_id], user_ids], dim=-1)

        max_len = getattr(model.config, "n_positions",
                          model.config.max_position_embeddings)
        if input_ids.shape[-1] > max_len - MAX_NEW_TOKENS:
            input_ids = input_ids[:, - (max_len - MAX_NEW_TOKENS):]

        attention_mask = torch.ones_like(input_ids)

        output_ids = model.generate(
            input_ids,
            attention_mask=attention_mask,
            max_new_tokens=MAX_NEW_TOKENS,
            pad_token_id=tokenizer.eos_token_id,
            do_sample=True,
            top_k=50,
            top_p=0.95,
            temperature=0.8,
        )

        new_history = output_ids
        chat_histories[channel_id] = new_history

        generated = new_history[0, input_ids.shape[-1]:].tolist()
        response  = tokenizer.decode(generated, skip_special_tokens=True).strip()

        if response:
            await message.channel.send(response)

        await bot.process_commands(message)
