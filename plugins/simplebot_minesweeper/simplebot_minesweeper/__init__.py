# -*- coding: utf-8 -*-
import os
import re
import time

from .db import DBManager
from .game import Board
from deltabot.hookspec import deltabot_hookimpl
# typing:
from deltabot import DeltaBot
from deltabot.bot import Replies
from deltabot.commands import IncomingCommand
from deltachat import Chat, Contact, Message


version = '1.0.0'
nick_re = re.compile(r'[-a-zA-Z0-9_]{1,16}$')
db: DBManager
dbot: DeltaBot


# ======== Hooks ===============

@deltabot_hookimpl
def deltabot_init(bot: DeltaBot) -> None:
    global db, dbot
    dbot = bot
    db = get_db(bot)

    bot.filters.register(name=__name__, func=filter_messages)

    dbot.commands.register('/mines_play', cmd_play)
    dbot.commands.register('/mines_repeat', cmd_repeat)
    dbot.commands.register('/mines_nick', cmd_nick)
    dbot.commands.register('/mines_top', cmd_top)


@deltabot_hookimpl
def deltabot_member_removed(chat: Chat, contact: Contact) -> None:
    game = db.get_game_by_gid(chat.id)
    if game:
        me = dbot.self_contact
        if contact.addr in (me.addr, game['addr']):
            db.delete_game(game['addr'])
            if contact != me:
                chat.remove_contact(me)


# ======== Filters ===============

def filter_messages(message: Message, replies: Replies) -> None:
    """Process move coordinates in Minesweeper game groups.
    """
    if len(message.text) != 2 or not message.text.isalnum() or message.text.isalpha() or message.text.isdigit():
        return

    game = db.get_game_by_gid(message.chat.id)
    if game is None or game['board'] is None:
        return

    try:
        b = Board(game['board'])
        b.move(message.text)
        db.set_board(game['addr'], b.export())
        replies.add(text=run_turn(message.chat.id))
    except ValueError as err:
        dbot.logger.exception(err)
        replies.add(text='❌ Invalid move!')


# ======== Commands ===============

def cmd_play(command: IncomingCommand, replies: Replies) -> None:
    """Start a new Minesweeper game.

    Example: `/mines_play`
    """
    player = command.message.get_sender_contact()
    if not db.get_nick(player.addr):
        text = "You need to set a nick before start playing,"
        text += " send /mines_nick Your Nick"
        replies.add(text=text)
        return
    game = db.get_game_by_addr(player.addr)

    if game is None:  # create a new chat
        chat = command.bot.create_group('💣 Minesweeper', [player.addr])
        db.add_game(player.addr, chat.id, Board().export())
        text = 'Hello {}, in this group you can play Minesweeper.\n\n'.format(
            player.name)
        replies.add(text=text + run_turn(chat.id), chat=chat)
    else:
        db.set_board(game['addr'], Board().export())
        if command.message.chat.id == game['gid']:
            chat = command.message.chat
        else:
            chat = command.bot.get_chat(game['gid'])
        replies.add(
            text='Game started!\n\n' + run_turn(game['gid']), chat=chat)


def cmd_repeat(command: IncomingCommand, replies: Replies) -> None:
    """Send Minesweeper game board again.

    Example: `/mines_repeat`
    """
    game = db.get_game_by_addr(command.message.get_sender_contact().addr)
    if game:
        if command.message.chat.id == game['gid']:
            chat = command.message.chat
        else:
            chat = command.bot.get_chat(game['gid'])
        replies.add(text=run_turn(game['gid']), chat=chat)
    else:
        replies.add(
            text="No active game, send /mines_play to start playing.")


def cmd_nick(command: IncomingCommand, replies: Replies) -> None:
    """Set your nick shown in Minesweeper scoreboard or show your current nick if no new nick is provided.

    Example: `/mines_nick Dark Warrior`
    """
    addr = command.message.get_sender_contact().addr
    if command.payload:
        new_nick = ' '.join(command.args)
        if not nick_re.match(new_nick):
            replies.add(text='** Invalid nick, only letters, numbers, "-" and "_" are allowed, and nick should be less than 16 characters')
        elif db.get_addr(new_nick):
            replies.add(text='** Nick already taken, try again')
        else:
            db.set_nick(addr, new_nick)
            replies.add(text='** Nick: {}'.format(new_nick))
    else:
        replies.add(text='** Nick: {}'.format(db.get_nick(addr)))


def cmd_top(command: IncomingCommand, replies: Replies) -> None:
    """Send Minesweeper scoreboard.

    Example: `/mines_top`
    """
    limit = 15
    text = '🏆 Minesweeper Scoreboard\n\n'
    game = db.get_game_by_addr(command.message.get_sender_contact().addr)
    if not game:
        games = db.get_games(limit)
    else:
        games = db.get_games()
    if not games:
        text += '(Empty list)'
    for n, g in enumerate(games[:limit], 1):
        text += '#{} {} {}\n'.format(
            n, db.get_nick(g['addr']), g['score'])
    if game:
        player_pos = games.index(game)
        if player_pos >= limit:
            text += '\n'
            if player_pos > limit:
                pgame = games[player_pos-1]
                text += '#{} {} {}\n'.format(
                    player_pos, db.get_nick(pgame['addr']), pgame['score'])
            text += '#{} {} {}\n'.format(
                player_pos+1, db.get_nick(game['addr']), game['score'])
            if player_pos < len(games)-1:
                ngame = games[player_pos+1]
                text += '#{} {} {}\n'.format(
                    player_pos+2, db.get_nick(ngame['addr']), ngame['score'])
    replies.add(text=text)


# ======== Utilities ===============

def run_turn(gid: int) -> str:
    g = db.get_game_by_gid(gid)
    assert g is not None
    if not g['board']:
        return "No active game, send /mines_play to start playing."
    b = Board(g['board'])
    result = b.result()
    if result == 1:
        now = time.time()
        score = b.get_score(now)
        text = '🏆 Game over. You Win!!!\n'
        if score <= g['score']:
            score = g['score'] + 1
        db.set_game(g['addr'], None, score)
        text += 'New High Score: {}\n📊 /mines_top'.format(score)
    elif result == -1:
        db.set_board(g['addr'], None)
        text = '☠️ Game over. You died.\n📊 /mines_top'
    else:
        return str(b)
    text += '\n\n{}\n▶️ Play again? /mines_play'.format(b.reveal(result))
    return text


def get_db(bot: DeltaBot) -> DBManager:
    path = os.path.join(os.path.dirname(bot.account.db_path), __name__)
    if not os.path.exists(path):
        os.makedirs(path)
    return DBManager(os.path.join(path, 'sqlite.db'))
