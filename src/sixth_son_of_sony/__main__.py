"""Command-line entry point for the bot."""

import logging

from .bot import create_bot
from .config import load_settings


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    configure_logging()
    settings = load_settings()
    bot = create_bot(settings)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
