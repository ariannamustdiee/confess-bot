import os
import sqlite3
import datetime

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = "confessioni.db"
TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            guild_id INTEGER PRIMARY KEY,
            confess_channel INTEGER,
            review_channel INTEGER,
            log_channel INTEGER,
            staff_role INTEGER,
            next_number INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS confessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            number INTEGER,
            user_id INTEGER,
            username TEXT,
            content TEXT,
            status TEXT DEFAULT 'pending',
            moderator_id INTEGER,
            moderator_username TEXT,
            created_at TEXT,
            decided_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def get_config(guild_id):
    conn = db()
    row = conn.execute("SELECT * FROM config WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.close()
    return row


def set_config(guild_id, **kwargs):
    conn = db()
    existing = conn.execute("SELECT guild_id FROM config WHERE guild_id = ?", (guild_id,)).fetchone()
    if existing:
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE config SET {cols} WHERE guild_id = ?", (*kwargs.values(), guild_id))
    else:
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        conn.execute(
            f"INSERT INTO config (guild_id, {cols}) VALUES (?, {placeholders})",
            (guild_id, *kwargs.values()),
        )
    conn.commit()
    conn.close()


def create_confession(guild_id, user_id, username, content):
    conn = db()
    cur = conn.execute(
        "INSERT INTO confessions (guild_id, user_id, username, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, username, content, datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def get_confession(cid):
    conn = db()
    row = conn.execute("SELECT * FROM confessions WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return row


def decide_confession(cid, status, moderator_id, moderator_username, number=None):
    conn = db()
    conn.execute(
        "UPDATE confessions SET status=?, moderator_id=?, moderator_username=?, decided_at=?, number=? WHERE id=?",
        (
            status,
            moderator_id,
            moderator_username,
            datetime.datetime.utcnow().isoformat(),
            number,
            cid,
        ),
    )
    conn.commit()
    conn.close()


def next_number(guild_id):
    conn = db()
    row = conn.execute("SELECT next_number FROM config WHERE guild_id = ?", (guild_id,)).fetchone()
    n = row["next_number"] if row else 1
    conn.execute(
        "UPDATE config SET next_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    conn.commit()
    conn.close()
    return n


init_db()


# ---------------------------------------------------------------------
# MODAL - form che l'utente compila per inviare la confessione
# ---------------------------------------------------------------------
class ConfessModal(discord.ui.Modal, title="Nuova Confessione"):
    content = discord.ui.TextInput(
        label="Scrivi la tua confessione",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cfg = get_config(interaction.guild_id)
        if not cfg or not cfg["review_channel"]:
            await interaction.response.send_message(
                "Il bot non è ancora configurato. Chiedi a uno staff di eseguire /setup.",
                ephemeral=True,
            )
            return

        review_channel = interaction.guild.get_channel(cfg["review_channel"])
        if review_channel is None:
            await interaction.response.send_message(
                "Canale di review non trovato, contatta lo staff.", ephemeral=True
            )
            return

        cid = create_confession(
            interaction.guild_id, interaction.user.id, str(interaction.user), str(self.content)
        )

        embed = discord.Embed(
            title=f"Nuova confessione da revisionare (ID interno #{cid})",
            description=str(self.content),
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(
            name="Autore (visibile solo qui allo staff)",
            value=f"{interaction.user.mention} ({interaction.user.id})",
            inline=False,
        )

        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label="Accetta", style=discord.ButtonStyle.success, custom_id=f"confess_accept_{cid}"
            )
        )
        view.add_item(
            discord.ui.Button(
                label="Rifiuta", style=discord.ButtonStyle.danger, custom_id=f"confess_reject_{cid}"
            )
        )

        await review_channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            "La tua confessione è stata inviata allo staff per la revisione. Grazie!", ephemeral=True
        )


# ---------------------------------------------------------------------
# COMANDI SLASH
# ---------------------------------------------------------------------
@bot.tree.command(name="confess", description="Invia una confessione anonima")
async def confess(interaction: discord.Interaction):
    await interaction.response.send_modal(ConfessModal())


@bot.tree.command(name="setup", description="Configura i canali del bot confessioni (solo staff)")
@app_commands.describe(
    canale_confessioni="Canale pubblico dove verranno postate le confessioni approvate",
    canale_review="Canale privato staff dove arrivano le confessioni da approvare",
    canale_log="Canale privato staff dove viene registrato chi ha approvato/rifiutato",
    ruolo_staff="Ruolo che può approvare/rifiutare le confessioni",
)
@app_commands.default_permissions(manage_guild=True)
async def setup(
    interaction: discord.Interaction,
    canale_confessioni: discord.TextChannel,
    canale_review: discord.TextChannel,
    canale_log: discord.TextChannel,
    ruolo_staff: discord.Role,
):
    set_config(
        interaction.guild_id,
        confess_channel=canale_confessioni.id,
        review_channel=canale_review.id,
        log_channel=canale_log.id,
        staff_role=ruolo_staff.id,
    )
    await interaction.response.send_message(
        "Configurazione salvata!\n"
        f"- Confessioni pubbliche: {canale_confessioni.mention}\n"
        f"- Review staff: {canale_review.mention}\n"
        f"- Log staff: {canale_log.mention}\n"
        f"- Ruolo staff: {ruolo_staff.mention}",
        ephemeral=True,
    )


# ---------------------------------------------------------------------
# GESTIONE BOTTONI ACCETTA / RIFIUTA
# ---------------------------------------------------------------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id", "")
    if not custom_id.startswith("confess_"):
        return

    action, cid_str = custom_id.rsplit("_", 1)
    cid = int(cid_str)

    confession = get_confession(cid)
    if confession is None:
        await interaction.response.send_message("Confessione non trovata nel database.", ephemeral=True)
        return
    if confession["status"] != "pending":
        await interaction.response.send_message("Questa confessione è già stata gestita.", ephemeral=True)
        return

    cfg = get_config(interaction.guild_id)
    member = interaction.user

    has_perm = member.guild_permissions.administrator or member.guild_permissions.manage_guild
    if cfg and cfg["staff_role"]:
        has_perm = has_perm or any(r.id == cfg["staff_role"] for r in member.roles)

    if not has_perm:
        await interaction.response.send_message(
            "Non hai il permesso di gestire le confessioni.", ephemeral=True
        )
        return

    original_embed = interaction.message.embeds[0]

    if action == "confess_accept":
        number = next_number(interaction.guild_id)
        decide_confession(cid, "approved", member.id, str(member), number)

        confess_channel = interaction.guild.get_channel(cfg["confess_channel"]) if cfg else None
        if confess_channel:
            public_embed = discord.Embed(
                title=f"Confessione #{number:03}",
                description=confession["content"],
                color=discord.Color.blurple(),
                timestamp=datetime.datetime.utcnow(),
            )
            await confess_channel.send(embed=public_embed)

        log_channel = interaction.guild.get_channel(cfg["log_channel"]) if cfg and cfg["log_channel"] else None
        if log_channel:
            log_embed = discord.Embed(title=f"Confessione #{number:03} approvata", color=discord.Color.green())
            log_embed.add_field(name="Contenuto", value=confession["content"], inline=False)
            log_embed.add_field(
                name="Autore", value=f"<@{confession['user_id']}> ({confession['user_id']})", inline=True
            )
            log_embed.add_field(name="Approvata da", value=f"{member.mention} ({member.id})", inline=True)
            await log_channel.send(embed=log_embed)

        original_embed.add_field(
            name="Stato", value=f"Approvata da {member.mention} — #{number:03}", inline=False
        )
        await interaction.response.edit_message(embed=original_embed, view=None)

    elif action == "confess_reject":
        decide_confession(cid, "rejected", member.id, str(member))

        log_channel = interaction.guild.get_channel(cfg["log_channel"]) if cfg and cfg["log_channel"] else None
        if log_channel:
            log_embed = discord.Embed(title="Confessione rifiutata", color=discord.Color.red())
            log_embed.add_field(name="Contenuto", value=confession["content"], inline=False)
            log_embed.add_field(
                name="Autore", value=f"<@{confession['user_id']}> ({confession['user_id']})", inline=True
            )
            log_embed.add_field(name="Rifiutata da", value=f"{member.mention} ({member.id})", inline=True)
            await log_channel.send(embed=log_embed)

        original_embed.add_field(name="Stato", value=f"Rifiutata da {member.mention}", inline=False)
        await interaction.response.edit_message(embed=original_embed, view=None)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Loggato come {bot.user} - comandi sincronizzati")


bot.run(TOKEN)
