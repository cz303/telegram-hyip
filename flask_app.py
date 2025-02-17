import datetime
import logging
import os
import pickle
import requests
from telegram.ext import messagequeue as mq
import sys
from pathlib import Path
import telegram
from flask import Flask, request, render_template, Response
from peewee import DoesNotExist, fn
from telegram.ext import ConversationHandler, MessageHandler, Filters
from telegram.utils.promise import Promise
from telegram.utils.request import Request as TelegramRequest
import bot_states
import command_handlers
import config
import error_handlers
import input_handlers
import mq_bot
from job_callbacks import reward_users, notify_inactive_users
from models import User, TopUp, Withdrawal
from flask_basicauth import BasicAuth
import json

app = Flask(__name__)
basic_auth = BasicAuth(app)

_ETH_WEI = 1000000000000000000
_DNSOMATIC = 'http://myip.dnsomatic.com'


def load_data():
    try:
        f = open('backup/conversations', 'rb')
        conv_handler.conversations = pickle.load(f)
        f.close()
    except FileNotFoundError:
        logging.error("Data file not found")
    except Exception:
        logging.error(sys.exc_info()[0])


def save_data():
    resolved = dict()
    for k, v in conv_handler.conversations.items():
        if isinstance(v, tuple) and len(v) is 2 and isinstance(v[1], Promise):
            try:
                new_state = v[1].result()
            except:
                new_state = v[0]
            resolved[k] = new_state
        else:
            resolved[k] = v
    try:
        f = open('backup/conversations', 'wb+')
        pickle.dump(resolved, f)
        f.close()
    except:
        logging.error(sys.exc_info()[0])
    print('=======================')
    print('CONVERSATION DATA SAVED')


def stop_updater():
    print('STOPPING BOT UPDATES')
    updater.stop()
    print('BOT UPDATES STOPPED')


dirname = os.path.dirname(__file__)
docs = os.path.join(dirname, 'docs')
partners = os.path.join(dirname, 'docs/partners')
transactions = os.path.join(dirname, 'docs/transactions')


def create_folder(path):
    if not os.path.exists(path):
        os.makedirs(path)


create_folder(docs)
create_folder(partners)
create_folder(transactions)

with open('config.json') as config_file:
    config_json = json.load(config_file)
    token = config_json['token']
    app.config['BASIC_AUTH_USERNAME'] = config_json['admin']['username']
    app.config['BASIC_AUTH_PASSWORD'] = config_json['admin']['password']
    try:
        blockcypher_key = config_json['blockcypher_key']
    except KeyError:
        blockcypher_key = ''

HOOKS_API = f'https://api.blockcypher.com/v1/eth/main/hooks?token={blockcypher_key}'


q = mq.MessageQueue(all_burst_limit=25, all_time_limit_ms=1017)
tel_request = TelegramRequest(con_pool_size=8)
mq_bot = mq_bot.MQBot(token=token, request=tel_request, mqueue=q)

updater = telegram.ext.updater.Updater(
    bot=mq_bot,
    request_kwargs={'read_timeout': 6, 'connect_timeout': 7},
)
dispatcher = updater.dispatcher

change_wallet_command_handler = command_handlers.change_wallet_initiation_handler()
withdraw_command_handler = command_handlers.withdraw_command_handler()
start_command_handler = command_handlers.start_command_handler()
transfer_balance_to_deposit_command_handler = command_handlers.transfer_balance_to_deposit()
transfer_balance_to_user_command_handler = command_handlers.transfer_balance_to_user()
demo_top_up_command_handler = command_handlers.demo_top_up()

main_handler = input_handlers.main_menu_input_handler()
change_wallet_handler = input_handlers.change_wallet_input_handler()
create_withdraw_input_handler = input_handlers.withdrawal_input_handler()
transfer_balance_to_deposit_input_handler = input_handlers.transfer_balance_to_deposit_input_handler()
transfer_balance_to_user_input_handler = input_handlers.transfer_balance_to_user_input_handler()
callback_query_handler = input_handlers.callback_query_handler()
demo_top_up_input_handler = input_handlers.demo_top_up()


conv_handler = ConversationHandler(
    entry_points=[
        start_command_handler,
        main_handler
    ],
    states={
        bot_states.MAIN: [
            start_command_handler,
            main_handler,
            change_wallet_command_handler,
            withdraw_command_handler,
            transfer_balance_to_deposit_command_handler,
            transfer_balance_to_user_command_handler,
            demo_top_up_command_handler
        ],
        bot_states.WALLET_CHANGE: [
            change_wallet_handler,
        ],
        bot_states.CREATE_WITHDRAWAL: [
            create_withdraw_input_handler,
        ],
        bot_states.TRANSFER_BALANCE_TO_DEPOSIT: [
            transfer_balance_to_deposit_input_handler,
        ],
        bot_states.TRANSFER_BALANCE_TO_USER: [
            transfer_balance_to_user_input_handler,
        ],
        bot_states.DEMO_TOP_UP: [
            demo_top_up_input_handler
        ]
    },
    fallbacks=[
    ],
    timed_out_behavior=[
        MessageHandler(
            Filters.text,
            error_handlers.timed_out_handler
        )
    ],
    run_async_timeout=1.0
)

dispatcher.add_handler(conv_handler)
dispatcher.add_handler(callback_query_handler)
dispatcher.add_error_handler(error_handlers.error_callback)

j = updater.job_queue
j.run_daily(reward_users, time=datetime.time(hour=3))
j.run_daily(notify_inactive_users, days=(5,), time=datetime.time(hour=14))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

PRIVATE_SSH = '../keys/private.key'
CERT_SSH = '../keys/cert.pem'

if Path(PRIVATE_SSH).is_file() and Path(CERT_SSH).is_file():
    updater.start_webhook(
        listen='0.0.0.0',
        port=8443,
        url_path=token,
        key=PRIVATE_SSH,
        cert=CERT_SSH,
        webhook_url=f'https://hyipcrew.herokuapp.com/{token}'
    )
    print('Webhook updater started')
else:
    updater.start_polling()
    print('Polling updater started')

load_data()


@app.route('/confirmed_transaction', methods=['POST'])
def top_up_balance():
    data = request.get_json()

    if data['outputs'][0]['addresses'][0].lower() != config.project_eth_address()[2:]:
        return Response(
            response='Success',
            status=200,
            mimetype='application/json'
        )

    amount = data['total'] / _ETH_WEI
    if not amount:
        return Response(
            response='Success',
            status=200,
            mimetype='application/json'
        )
    wallet = data['inputs'][0]['addresses'][0].lower()
    wallet = f'0x{wallet}'

    try:
        user = User.get(wallet=wallet)
        top_up = TopUp.create(
            user=user,
            amount=amount,
            from_wallet=wallet
        )
    except DoesNotExist:
        top_up = TopUp.create(
            amount=amount,
            received=False,
            from_wallet=wallet
        )

    return Response(
        response='Success',
        status=200,
        mimetype='application/json'
    )


class ValidationError(Exception):
    pass


@app.route('/user_deposit')
@basic_auth.required
def user_deposit():
    return render_template(
        'user_deposit.html'
    )


@app.route('/increase_user_deposit', methods=['POST'])
@basic_auth.required
def increase_user_deposit():
    json = request.get_json(silent=True)
    user_id = json['user_id']
    amount = json['amount']
    try:
        user_id = int(user_id)
    except TypeError:
        return Response(
            response='Неправильный id пользователя',
            status=400,
            mimetype='application/json'
        )
    try:
        user = User.get(User.chat_id == user_id)
    except DoesNotExist as e:
        return Response(
            response='Нет такого юзера',
            status=400,
            mimetype='application/json'
        )

    try:
        amount = float(amount)
    except ValueError:
        return Response(
            response='Не похоже на дробное число',
            status=400,
            mimetype='application/json'
        )

    TopUp.create(
        amount=amount,
        user=user
    )
    return Response(
        response='Успешно',
        status=200,
        mimetype='application/json'
    )


@app.route('/approve_withdrawal', methods=['POST'])
@basic_auth.required
def approve_withdrawal():
    id = request.get_json(silent=True)['id']
    withdrawal = Withdrawal.get(id=id)
    if withdrawal.approved:
        return Response(
            response='Вывод уже был подтвержден',
            status=400,
            mimetype='application/json'
        )
    withdrawal.approved = True
    withdrawal.save()
    return Response(
        response='Успешно',
        status=200,
        mimetype='application/json'
    )


@app.route('/withdrawals')
@basic_auth.required
def withdrawals():
    withdrawals = Withdrawal.select(Withdrawal, User) \
        .where(Withdrawal.approved == False).order_by(Withdrawal.created_at).join(User)

    total_sum = Withdrawal.select(fn.COALESCE(fn.SUM(Withdrawal.amount), 0).alias('total_sum')) \
        .where(Withdrawal.approved == False).execute()

    return render_template(
        'withdrawals.html',
        total_sum=total_sum[0].total_sum,
        withdrawals=withdrawals,
    )


@app.route('/lost_top_ups')
@basic_auth.required
def lost_top_ups():
    lost_top_ups = TopUp.select().where(TopUp.received == False)

    return render_template(
        'lost_top_ups.html',
        lost_top_ups=lost_top_ups
    )


@app.route('/delete_top_up', methods=['DELETE'])
@basic_auth.required
def top_up_delete():
    json_request = request.get_json(silent=True)
    id = json_request['id']
    top_up = TopUp.get(id=id)
    top_up.delete_instance()

    return Response(
        response='Успешно',
        status=200,
        mimetype='application/json'
    )


@app.route('/user_lookup')
@basic_auth.required
def user_lookup():
    ITEMS_PER_PAGE = 15
    page = request.args.get('page')
    id = request.args.get('id')
    username = request.args.get('username')
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1

    users = User.select()
    users_count = users.count()
    if id:
        try:
            id = int(id)
            users = users.where(User.chat_id == id)
        except ValueError:
            pass
    if username:
        users = users.where(User.username.contains(username))

    users = users.order_by(-User.created_at).paginate(page, ITEMS_PER_PAGE)

    if (page + 1) * ITEMS_PER_PAGE - users_count > ITEMS_PER_PAGE:
        next_link = None
    else:
        next_link = f'/user_lookup?page={page + 1}'

    if page - 1 <= 0:
        prev_link = None
    else:
        prev_link = f'/user_lookup?page={page - 1}'

    return render_template(
        'user_lookup.html',
        users=users,
        users_count=users_count,
        next_link=next_link,
        prev_link=prev_link
    )


@app.route('/received_top_up', methods=['POST'])
@basic_auth.required
def top_up_received():
    json_request = request.get_json(silent=True)
    id = json_request['id']
    user_id = json_request['user_id']
    try:
        user_id = int(user_id)
    except TypeError:
        return Response(
            response='Неверный id пользователя',
            status=400,
            mimetype='application/json'
        )
    top_up = TopUp.get(id=id)
    if top_up.received:
        return Response(
            response='Пополнение уже зачислено',
            status=400,
            mimetype='application/json'
        )
    try:
        user = User.get(User.chat_id == user_id)
    except DoesNotExist as e:
        return Response(
            response='Нет такого пользователя',
            status=400,
            mimetype='application/json'
        )

    top_up.user = user
    top_up.received = True
    top_up.save()

    return Response(
        response='Успешно',
        status=200,
        mimetype='application/json'
    )


@app.route('/')
@basic_auth.required
def statistics():
    def get_chart_data_for_transactions(transactions, columns):
        statistics_data = {}
        for transaction in transactions:
            date = transaction.created_at.strftime("%d %B")
            if date not in statistics_data:
                statistics_data[date] = 0
            statistics_data[date] += float(transaction.amount)

        chart_data = [columns]
        for day in statistics_data.keys():
            chart_data.append([day, statistics_data[day]])

        return chart_data

    now = datetime.datetime.now()
    month_ago = now - datetime.timedelta(days=30)
    withdrawals = Withdrawal.select() \
        .where(Withdrawal.created_at < now) \
        .where(Withdrawal.created_at > month_ago).order_by(Withdrawal.created_at)

    top_ups = TopUp.select() \
        .where(TopUp.created_at < now) \
        .where(TopUp.created_at > month_ago).order_by(TopUp.created_at)

    withdrawal_data = get_chart_data_for_transactions(
        withdrawals,
        [
            'Day',
            'Withdrawals'
        ]
    )

    top_up_data = get_chart_data_for_transactions(
        top_ups,
        [
            'Day',
            'TopUps'
        ]
    )

    registrations = User.select().where(User.created_at < now) \
        .where(User.created_at > month_ago).order_by(User.created_at)

    registration_temp = {}
    for registration in registrations:
        date = registration.created_at.strftime("%d %B")
        if date not in registration_temp:
            registration_temp[date] = 0
        registration_temp[date] += 1

    registration_data = [
        [
            'Day',
            'Registrations'
        ]
    ]

    for day in registration_temp.keys():
        registration_data.append([day, registration_temp[day]])

    return render_template(
        'statistics.html',
        withdrawal_data=withdrawal_data,
        top_up_data=top_up_data,
        registration_data=registration_data
    )


def transaction_hook_exists(json_data):
    for hook in json_data:
        if hook['address'] == config.project_eth_address()[2:]:
            print('Webhook exists')
            return True
    return False


def blockcypher_webhook():
    if not blockcypher_key:
        print('Running without blockcypher webhook. Transactions will not be recorded!')
        return True
    hooks = requests.get(HOOKS_API).json()
    if transaction_hook_exists(hooks):
        return True

    print('Webhook doesn\'t exists')

    f = requests.request('GET', _DNSOMATIC)
    server_ip = f.text

    response = requests.post(
        HOOKS_API,
        json={
            "event": "confirmed-tx",
            "address": config.project_eth_address()[2:],
            "url": f"http://{server_ip}:8000/confirmed_transaction"
        }
    )

    if 'id' in response.json().keys():
        print('Hook created successfully')
        return True
    else:
        print('Hook error')
        return False


if __name__ == "__main__":
    if blockcypher_webhook():
        app.run(threaded=True, host='0.0.0.0', port=8000)
        stop_updater()
        save_data()
        sys.exit(0)
    else:
        print('Terminating the app')
