import asyncio
import sqlite3
import re
import os
from datetime import datetime
import secrets

import discord
from discord.ext import commands


class Thread:
    statuses = {1: "open", 2: "closed", 3: "suspended"}

    __slots__ = [
        "bot",
        "id",
        "status",
        "recipient",
        "creator",
        "creator_mod",
        "closer",
        "channel_id",
        "created_at",
        "scheduled_close_at",
        "scheduled_close_id",
        "alert_id",
        "messages",
    ]

    @classmethod
    async def from_data(cls, bot, data, cursor):
        # id
        # status
        # is_legacy
        # user_id
        # user_name
        # channel_id
        # created_at
        # scheduled_close_at
        # scheduled_close_id
        # scheduled_close_name
        # alert_id

        self = cls()
        self.bot = bot
        self.id = data[0]
        self.status = self.statuses[data[1]]

        user_id = data[3]
        if user_id:
            self.recipient = bot.get_user(int(user_id))
            if self.recipient is None:
                try:
                    self.recipient = await bot.fetch_user(int(user_id))
                except discord.NotFound:
                    self.recipient = None
        else:
            self.recipient = None

        self.creator = self.recipient
        self.creator_mod = False
        self.closer = None

        self.channel_id = int(data[5])
        self.created_at = datetime.fromisoformat(data[6])
        self.scheduled_close_at = (
            datetime.fromisoformat(data[7]) if data[7] else datetime.utcnow()
        )
        self.scheduled_close_id = data[8]
        self.alert_id = data[9]

        self.messages = []

        if self.id:
            for i in cursor.execute(
                "SELECT * FROM 'thread_messages' WHERE thread_id == ?", (self.id,)
            ):
                message = await ThreadMessage.from_data(bot, i)
                if message.type_ == "command" and "close" in message.body:
                    self.closer = message.author
                elif message.type_ == "system" and message.body.startswith(
                    "Thread was opened by "
                ):
                    # user used the `newthread` command
                    mod = message.body[:21]  # gets name#discrim
                    for i in bot.users:
                        if str(i) == mod:
                            self.creator = i
                            self.creator_mod = True
                            break
                self.messages.append(message)
        return self

    def serialize(self):
        """Turns it into a document"""
        payload = {
            "open": self.status != "closed",
            "channel_id": str(self.channel_id),
            "guild_id": str(self.bot.guild_id),
            "created_at": str(self.created_at),
            "closed_at": str(self.scheduled_close_at),
            "closer": None,
            "recipient": {
                "id": str(self.recipient.id),
                "name": self.recipient.name,
                "discriminator": self.recipient.discriminator,
                "avatar_url": str(self.recipient.avatar_url),
                "mod": False,
            },
            "creator": {
                "id": str(self.creator.id),
                "name": self.creator.name,
                "discriminator": self.creator.discriminator,
                "avatar_url": str(self.creator.avatar_url),
                "mod": self.creator_mod,
            },
            "messages": [m.serialize() for m in self.messages if m.serialize()],
        }
        if self.closer:
            payload["closer"] = {
                "id": str(self.closer.id),
                "name": self.closer.name,
                "discriminator": self.closer.discriminator,
                "avatar_url": str(self.closer.avatar_url),
                "mod": True,
            }
        return payload


class ThreadMessage:
    types = {
        1: "system",
        2: "chat",
        3: "from_user",
        4: "to_user",
        5: "legacy",
        6: "command",
    }

    __slots__ = [
        "bot",
        "id",
        "type_",
        "author",
        "body",
        "attachments",
        "content",
        "is_anonymous",
        "dm_message_id",
        "created_at",
    ]

    @classmethod
    async def from_data(cls, bot, data):
        # id
        # thread_id
        # message_type
        # user_id
        # user_name
        # body
        # is_anonymous
        # dm_message_id
        # created_at

        self = cls()
        self.bot = bot
        self.id = data[1]
        self.type_ = self.types[data[2]]

        user_id = data[3]
        if user_id:
            self.author = bot.get_user(int(user_id))
            if self.author is None:
                try:
                    self.author = await bot.fetch_user(int(user_id))
                except discord.NotFound:
                    self.author = None
        else:
            self.author = None

        self.body = data[5]

        pattern = re.compile(r"http://[\d.]+:\d+/attachments/\d+/.*")
        self.attachments = pattern.findall(str(self.body))
        if self.attachments:
            index = self.body.find(self.attachments[0])
            self.content = self.body[:index]
        else:
            self.content = self.body

        self.is_anonymous = data[6]
        self.dm_message_id = data[7]
        self.created_at = datetime.fromisoformat(data[8])
        self.attachments = pattern.findall(str(self.body))
        return self

    def serialize(self):
        if self.type_ in ("from_user", "to_user"):
            return {
                "timestamp": str(self.created_at),
                "message_id": self.dm_message_id,
                "content": self.content,
                "author": {
                    "id": str(self.author.id),
                    "name": self.author.name,
                    "discriminator": self.author.discriminator,
                    "avatar_url": str(self.author.avatar_url),
                    "mod": self.type_ == "to_user",
                }
                if self.author
                else None,
                "attachments": self.attachments,
            }


class DragoryMigrate(commands.Cog):
    """
    Cog that migrates thread logs from [Dragory's](https://github.com/dragory/modmailbot) 
    modmail bot to this one.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def migratedb(self, ctx, url=None):
        """Migrates a database file to the mongo db."""
        try:
            url = url or ctx.message.attachments[0].url
        except IndexError:
            await ctx.send("Provide an sqlite file as the attachment.")

        async with self.bot.session.get(url) as resp:
            # TODO: use BytesIO or sth
            with open("dragorydb.sqlite", "wb+") as f:
                f.write(await resp.read())

        conn = sqlite3.connect("dragorydb.sqlite")
        c = conn.cursor()

        output = ""
        # Blocked Users

        for row in c.execute("SELECT * FROM 'blocked_users'"):
            # user_id
            # user_name
            # blocked_by
            # blocked_at

            user_id = row[0]

            cmd = self.bot.get_command('block')
            user = await self.bot.fetch_user(int(user_id))
            self.bot.loop.create_task(ctx.invoke(cmd, user=user))

        # Snippets
        for row in c.execute("SELECT * FROM 'snippets'"):
            # trigger	body	created_by	created_at
            name = row[0]
            value = row[1]

            if "snippets" not in self.bot.config.cache:
                self.bot.config["snippets"] = {}

            self.bot.config.snippets[name] = value
            output += f"Snippet {name} added: {value}\n"

        tasks = []

        prefix = os.getenv("LOG_URL_PREFIX", "/logs")
        if prefix == "NONE":
            prefix = ""

        async def convert_thread_log(row):
            thread = await Thread.from_data(self.bot, row, c)
            converted = thread.serialize()
            key = secrets.token_hex(6)
            converted["key"] = key
            converted["_id"] = key
            await self.bot.db.logs.insert_one(converted)
            log_url = f"{self.bot.config.log_url.strip('/')}{prefix}/{key}"
            output += f"Posted thread log: {log_url}"

        # Threads
        for row in c.execute("SELECT * FROM 'threads'"):
            tasks.append(convert_thread_log(row))

        with ctx.typing():
            await asyncio.gather(*tasks)
            # TODO: Create channels for non-closed threads

            await self.bot.config.update()

            async with self.bot.session.post(
                "https://hasteb.in/documents", data=output
            ) as resp:
                key = (await resp.json())["key"]

            await ctx.send(f"Done. Logs: https://hasteb.in/{key}")
            conn.close()
            os.remove("dragorydb.sqlite")


def setup(bot):
    bot.add_cog(DragoryMigrate(bot))
