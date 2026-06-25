"""
Reusable Discord UI modals for ChibiBeasts.
Import QuantityModal wherever a quantity prompt is needed.
"""

import discord
from typing import Callable, Awaitable


class QuantityModal(discord.ui.Modal):
    """
    A simple popup that asks the player for a quantity.
    Pass an async callback that receives (interaction, quantity).
    The callback is responsible for deferring/responding to the interaction.
    """

    def __init__(
        self,
        title: str,
        item_name: str,
        max_quantity: int,
        callback: Callable[[discord.Interaction, int], Awaitable[None]],
    ):
        super().__init__(title=title)
        self._callback = callback
        self._max = max_quantity
        self.quantity_input = discord.ui.TextInput(
            label=f"Quantity (you have {max_quantity:,})",
            placeholder=f"1 – {max_quantity:,}",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.quantity_input.value.strip()
        try:
            qty = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                "✦ Please enter a whole number.", ephemeral=True
            )
        if qty < 1:
            return await interaction.response.send_message(
                "✦ Quantity must be at least 1.", ephemeral=True
            )
        if qty > self._max:
            return await interaction.response.send_message(
                f"✦ You only have **{self._max:,}** — can't sell more than that.",
                ephemeral=True
            )
        await self._callback(interaction, qty)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        import logging
        logging.getLogger("chibibeasts.modals").exception("QuantityModal error", exc_info=error)
        try:
            await interaction.response.send_message(
                "✦ Something went wrong — please try again.", ephemeral=True
            )
        except discord.HTTPException:
            pass
