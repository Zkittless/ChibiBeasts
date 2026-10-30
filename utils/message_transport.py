"""Bot-authenticated handles for long-lived public interaction messages."""
import discord


def _snowflake(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def durable_message(message, *, channel=None):
    """Return an edit/delete handle that does not retain an interaction token.

    Keep the original message for attachment metadata. Public interaction
    responses can be addressed by channel/message ID using the bot's existing
    connection. Ephemeral responses still require their interaction endpoint.
    No channel fetch or network request is made here.
    """
    if not isinstance(message, (discord.WebhookMessage, discord.InteractionMessage)):
        return message
    if getattr(getattr(message, 'flags', None), 'ephemeral', False):
        return message
    message_id = getattr(message, 'id', None)
    if not _snowflake(message_id):
        return message
    original_channel = getattr(message, 'channel', None)
    original_id = getattr(original_channel, 'id', None)
    for candidate in (channel, original_channel):
        if candidate is None:
            continue
        if _snowflake(original_id) and getattr(candidate, 'id', None) != original_id:
            continue
        factory = getattr(candidate, 'get_partial_message', None)
        http = getattr(getattr(candidate, '_state', None), 'http', None)
        if callable(factory) and all(callable(getattr(http, method, None))
                                     for method in ('edit_message', 'delete_message')):
            return factory(message_id)
    return message
