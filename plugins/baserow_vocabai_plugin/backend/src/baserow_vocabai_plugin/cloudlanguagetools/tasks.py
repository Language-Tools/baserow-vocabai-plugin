from baserow.config.celery import app

from baserow.contrib.database.table.models import Table
from baserow.contrib.database.rows.signals import before_rows_update, rows_updated
from baserow.contrib.database.table.signals import table_updated

from django.conf import settings
import redis
import json

from . import clt_interface
from .quotas import QuotaOverUsage
from ..fields.vocabai_models import CHOICE_PINYIN, CHOICE_JYUTPING

import os
import time
import requests
import pprint

import logging
logger = logging.getLogger(__name__)

EXPORT_SOFT_TIME_LIMIT = 60 * 60
EXPORT_TIME_LIMIT = EXPORT_SOFT_TIME_LIMIT + 60


TASK_ITERATION_SIZE_PLAN = [
    [5, 1],
    [5, 2],
    [5, 10],
    [5, 100],
    [5, 500],
    [5, 2000],
    [5, 200000]
]

def iterate_row_id_buckets(table_id):
    step_size_array = []
    for entry in TASK_ITERATION_SIZE_PLAN:
        count = entry[0]
        step_size = entry[1]
        for i in range(0, count):
            step_size_array.append(step_size)

    # first, collect all row IDs
    base_queryset = Table.objects
    table = base_queryset.select_related("database__workspace").get(id=table_id)
    # https://docs.djangoproject.com/en/4.0/ref/models/querysets/
    table_model = table.get_model()
    row_id_list = []
    for row in table_model.objects.all():
        row_id = row.id
        row_id_list.append(row_id)    

    while len(row_id_list) > 0:
        iteration_size = step_size_array[0]
        step_size_array = step_size_array[1:]
        iteration_row_id_list = row_id_list[0:iteration_size]
        row_id_list = row_id_list[iteration_size:]
        yield iteration_row_id_list


def process_row_id_bucket_iterate_rows(table_id, row_id_list):

    base_queryset = Table.objects
    table = base_queryset.select_related("database__workspace").get(id=table_id)
    # logger.info(f'table: {table}')

    table_model = table.get_model()

    row_list = []
    for row_id in row_id_list:
        row = table_model.objects.get(id=row_id)
        row_list.append(row)

    size_cutoff = 50

    if len(row_list) < size_cutoff:
        before_return = before_rows_update.send(
            None,
            rows=row_list,
            user=None,
            table=table,
            model=table_model,
            updated_field_ids=None,
        )

    for row in row_list:
        yield row

    if len(row_list) < size_cutoff:
        rows_updated.send(
            None,
            rows=row_list,
            user=None,
            table=table,
            model=table_model,
            before_return=before_return,
            updated_field_ids=None
        )
    else:
        # refresh whole table
        table_updated.send(None, table=table, user=None, force_table_refresh=True)


# translation 
# ===========

# noinspection PyUnusedLocal
@app.task(
    bind=True,
    soft_time_limit=EXPORT_SOFT_TIME_LIMIT,
    time_limit=EXPORT_TIME_LIMIT,
)
def run_clt_translation_all_rows(self, table_id, source_language, target_language, service, source_field_id, target_field_id, usage_user_id):
    # populating all rows is still a single celery task, but we break it up so that we can notify the user
    # about work in progress

    try:
        for row_id_list in iterate_row_id_buckets(table_id):
            for row in process_row_id_bucket_iterate_rows(table_id, row_id_list):
                text = getattr(row, source_field_id)
                if text != None and len(text) > 0:
                    translated_text = clt_interface.get_translation(text, source_language, target_language, service, usage_user_id)
                    setattr(row, target_field_id, translated_text)
                    row.save()
    except QuotaOverUsage:
        logger.exception(f'could not complete translation for user {usage_user_id}')



# transliteration
# ================

# noinspection PyUnusedLocal
@app.task(
    bind=True,
    soft_time_limit=EXPORT_SOFT_TIME_LIMIT,
    time_limit=EXPORT_TIME_LIMIT,
)
def run_clt_transliteration_all_rows(self, table_id, transliteration_id, source_field_id, target_field_id, usage_user_id):
    try:
        for row_id_list in iterate_row_id_buckets(table_id):
            for row in process_row_id_bucket_iterate_rows(table_id, row_id_list):
                text = getattr(row, source_field_id)
                if text != None and len(text) > 0:
                    result = clt_interface.get_transliteration(text, transliteration_id, usage_user_id)
                    setattr(row, target_field_id, result)
                    row.save()
    except QuotaOverUsage:
        logger.exception(f'could not complete transliteration for user {usage_user_id}')

# dictionary lookup
# =================

# noinspection PyUnusedLocal
@app.task(
    bind=True,
    soft_time_limit=EXPORT_SOFT_TIME_LIMIT,
    time_limit=EXPORT_TIME_LIMIT,
)
def run_clt_lookup_all_rows(self, table_id, lookup_id, source_field_id, target_field_id, usage_user_id):
    try:
        for row_id_list in iterate_row_id_buckets(table_id):
            for row in process_row_id_bucket_iterate_rows(table_id, row_id_list):
                text = getattr(row, source_field_id)
                if text != None and len(text) > 0:
                    result = clt_interface.get_dictionary_lookup(text, lookup_id, usage_user_id)
                    setattr(row, target_field_id, result)
                    row.save()        
    except QuotaOverUsage:
        logger.exception(f'could not complete dictionary lookup for user {usage_user_id}')


# chinese romanization
# ====================

# noinspection PyUnusedLocal
@app.task(
    bind=True,
    soft_time_limit=EXPORT_SOFT_TIME_LIMIT,
    time_limit=EXPORT_TIME_LIMIT,
)
def run_clt_chinese_romanization_all_rows(self, table_id, romanization_type, tone_numbers, spaces, source_field_id, target_field_id, usage_user_id):
    try:
        logger.debug(f'running run_clt_chinese_romanization_all_rows')
        for row_id_list in iterate_row_id_buckets(table_id):
            for row in process_row_id_bucket_iterate_rows(table_id, row_id_list):
                text = getattr(row, source_field_id)
                if text != None and len(text) > 0:
                    if romanization_type == CHOICE_PINYIN:
                        result = clt_interface.get_pinyin(text, tone_numbers, spaces)
                    elif romanization_type == CHOICE_JYUTPING:
                        result = clt_interface.get_jyutping(text, tone_numbers, spaces)
                    logger.debug(f'computed romanization: {pprint.pformat(result)}')
                    setattr(row, target_field_id, result)
                    row.save()        
    except QuotaOverUsage:
        logger.exception(f'could not complete chinese romanization for user {usage_user_id}')



# retrieving language data
# ========================

@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    logger.info('setup_periodic_tasks')
    
    # run every 30s (debug only)
    # period = 30
    period = 3600 * 3
    sender.add_periodic_task(period, refresh_cloudlanguagetools_language_data.s(), name='cloudlanguagetools language data')
    
    # run once at startup
    refresh_cloudlanguagetools_language_data.delay()

    sender.add_periodic_task(period, collect_user_data.s(), name='collect user data')

    # run once at startup
    collect_user_data.delay()


# we want to auto-retry on requests.exceptions.ReadTimeout
@app.task(autoretry_for=(requests.exceptions.ReadTimeout,), retry_kwargs={'max_retries': 5}, queue='cloudlanguagetools')
def refresh_cloudlanguagetools_language_data():
    logger.info('refresh_cloudlanguagetools_language_data')
    clt_interface.update_language_data()


# collecting user data
# ====================

from django.contrib.auth import get_user_model
User = get_user_model()

from ..fields.vocabai_models import VocabAiUsage, USAGE_PERIOD_MONTHLY, USAGE_PERIOD_DAILY
from baserow.core.models import WorkspaceUser
from baserow.contrib.database.models import Database
from baserow.contrib.database.table.models import Table

def subscribe_convertkit(user_record_list):
    convertkit_api_key = os.environ.get('CONVERTKIT_API_KEY', None)
    convertkit_api_secret = os.environ.get('CONVERTKIT_API_SECRET', None)
    convertkit_subscribe = os.environ.get('CONVERTKIT_SUBSCRIBE', 'NO') == 'YES'
    CONVERTKIT_THROTTLE_REQUESTS_SLEEP = 0.5
    CONVERTKIT_REQUEST_TIMEOUT=120
    CONVERTKIT_TAG_ID = 4024166 # vocabai_user

    if convertkit_api_key == None or convertkit_api_secret == None:
        logger.error(f'CONVERTKIT_API_KEY, CONVERTKIT_API_SECRET must be set in order to subscribe users to ConvertKit')
        return

    # ensure all users are subscribed to convertkit
    # =============================================
    try:
        # build list of emails already subscribed
        # =======================================
        url = f'https://api.convertkit.com/v3/tags/{CONVERTKIT_TAG_ID}/subscriptions?api_secret={convertkit_api_secret}'
        response = requests.get(url, timeout=CONVERTKIT_REQUEST_TIMEOUT)
        time.sleep(CONVERTKIT_THROTTLE_REQUESTS_SLEEP)
        response.raise_for_status()
        data = response.json()
        current_page = data['page']
        total_pages = data['total_pages']        
        email_set = set()
        for item in data['subscriptions']:
            email_set.add(item['subscriber']['email_address'])
        # go through all the pages
        while current_page < total_pages:
            next_page = current_page + 1
            url = f'https://api.convertkit.com/v3/tags/{CONVERTKIT_TAG_ID}/subscriptions?api_secret={convertkit_api_secret}&page={next_page}'
            response = requests.get(url, timeout=CONVERTKIT_REQUEST_TIMEOUT)
            response.raise_for_status()
            # throttle
            time.sleep(CONVERTKIT_THROTTLE_REQUESTS_SLEEP)
            data = response.json()
            current_page = data['page']
            for item in data['subscriptions']:
                email_set.add(item['subscriber']['email_address'])

        # now check each user
        # ===================
        for record in user_record_list:
            email = record['username']
            first_name = record['name']
            logger.info(f'processing user: email {email} first_name: {first_name}')
            if email not in email_set:
                # subscribe
                logger.info(f'need to subscribe {email} to convertkit')
                if convertkit_subscribe:
                    url = f'https://api.convertkit.com/v3/tags/{CONVERTKIT_TAG_ID}/subscribe'
                    response = requests.post(url, json={
                            "api_key": convertkit_api_key,
                            "email": email,
                            "first_name": first_name
                    }, timeout=CONVERTKIT_REQUEST_TIMEOUT)
                    response.raise_for_status()        
                    time.sleep(CONVERTKIT_THROTTLE_REQUESTS_SLEEP)
                else:
                    logger.info(f'would have subscribed {email} to convertkit')
    except Exception as e:
        logger.exception('could not perform convertkit subscriptions')
    

@app.task(queue='export')
def collect_user_data():
    logger.info('running task collect_user_data')

    user_list = User.objects.all()

    user_record_list = []

    for user in user_list:
        # user model: https://docs.djangoproject.com/en/4.1/ref/contrib/auth/
        # logger.info(f'user: {dir(user)}')
        username = user.username
        last_login = None
        if user.last_login != None:
            last_login = user.last_login.isoformat()
        date_joined = user.date_joined.strftime('%Y-%m-%d')
        logger.info(f'user: {user} first_name: {user.first_name}')

        # lookup usage records
        usage_list = VocabAiUsage.objects.filter(user=user)
        for usage in usage_list:
            logger.info(f'usage: {usage} characters: {usage.characters} period: {usage.period} period_time: {usage.period_time}')

        # collect number of groups, tables, rows
        # need to locate WorkspaceUser instances
        group_user_list = WorkspaceUser.objects.filter(user=user)
        group_count = 0
        database_count = 0
        table_count = 0
        row_count = 0
        for group_user in group_user_list:
            logger.info(f'group_user: {group_user} workspace: {group_user.workspace}')
            group_count += 1
            # find all the databases in that group
            database_list = Database.objects.filter(workspace=group_user.workspace)
            for database in database_list:
                logger.info(f'database: {database}')
                database_count += 1
                # find all of the tables in that database
                table_list = Table.objects.filter(database=database)
                for table in table_list:
                    table_count += 1
                    row_count += table.get_model(field_ids=[]).objects.count()

        logger.info(f'user stats: last_login: {last_login} databases: {database_count} tables: {table_count} rows: {row_count}')

        user_record_list.append({
            'username': username,
            'name': user.first_name,
            'last_login': last_login,
            'date_joined': date_joined,
            'table_count': table_count,
            'row_count': row_count
        })
    
    # make sure all users are subscribed to convertkit
    # ================================================
    subscribe_convertkit(user_record_list)






    

