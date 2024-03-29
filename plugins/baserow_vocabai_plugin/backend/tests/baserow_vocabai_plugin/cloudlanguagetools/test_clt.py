import pytest
import json
import os
import importlib
import pprint
import json
import logging
from django.shortcuts import reverse
from rest_framework.status import HTTP_200_OK

from baserow_vocabai_plugin.cloudlanguagetools import clt_interface, quotas
import cloudlanguagetools.languages

logger = logging.getLogger(__name__)


# tests which need to be run with CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES=no

def use_clt_real_services():
    os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] = 'no'
    importlib.reload(cloudlanguagetools.servicemanager)
    clt_interface.reload_manager()    

def use_clt_test_services():
    os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] = 'yes'
    importlib.reload(cloudlanguagetools.servicemanager)
    clt_interface.reload_manager()        

@pytest.mark.django_db
def test_language_data(api_client, data_fixture):
    use_clt_test_services()
    assert os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] == 'yes'

    user, token = data_fixture.create_user_and_token()

    # update language data first
    clt_interface.update_language_data()

    # make sure language list is available
    response = api_client.get(
        reverse("api:baserow_vocabai_plugin:language-list"),
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.status_code == HTTP_200_OK

    # verify some things
    language_list = response.data
    assert 'fr' in language_list
    assert language_list['fr'] == 'French'


@pytest.mark.django_db
def test_quotas(api_client, data_fixture):
    use_clt_test_services()
    assert os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] == 'yes'

    user, token = data_fixture.create_user_and_token()

    # update language data first
    clt_interface.update_language_data()

    # verify initial usage record
    usage_record = quotas.get_usage_record(user.id)
    assert usage_record.monthly_usage_record.characters == 0
    assert usage_record.daily_usage_record.characters == 0
    
    # transformations on free and premium services should be allowed
    translation_result_str = clt_interface.get_translation('yoyo', 'fr', 'en', 'TestServiceA', user.id)
    # usage should still be zero
    quotas.get_usage_record(user.id).monthly_usage_record.characters == 0
    quotas.get_usage_record(user.id).daily_usage_record.characters == 0
    # run transformation on premium service
    translation_result_str = clt_interface.get_translation('yoyo', 'fr', 'en', 'TestServiceB', user.id)
    quotas.get_usage_record(user.id).monthly_usage_record.characters == 4
    quotas.get_usage_record(user.id).daily_usage_record.characters == 4   

    # log some usage, exceeding the daily quota
    quotas.get_usage_record(user.id).update_usage(quotas.FREE_ACCOUNT_DAILY_MAX_CHARACTERS - 3)

    # the free option should go through
    translation_result_str = clt_interface.get_translation('yoyo2', 'fr', 'en', 'TestServiceA', user.id)

    # the premium option should be blocked, should raise QuotaOverUsage
    with pytest.raises(quotas.QuotaOverUsage) as quota_exception:
        translation_result_str = clt_interface.get_translation('yoyo3', 'fr', 'en', 'TestServiceB', user.id)


@pytest.mark.django_db(transaction=True)
def test_add_language_field(api_client, data_fixture):
    use_clt_test_services()

    # CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES=yes pytest baserow_vocabai_plugin/cloudlanguagetools/test_clt.py -k test_add_language_field
    assert os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] == 'yes'

    user, token = data_fixture.create_user_and_token()

    # update language data first
    clt_interface.update_language_data()    

    response = api_client.get(reverse('api:baserow_vocabai_plugin:translation-options'),HTTP_AUTHORIZATION=f"JWT {token}",)
    assert response.status_code == HTTP_200_OK
    pprint.pprint(response.data)    

    # create database
    # ===============

    database = data_fixture.create_database_application(user=user)
    pprint.pprint(database)

    # create table 
    # ============

    url = reverse(
        "api:database:tables:async_create", kwargs={"database_id": database.id}
    )
    response = api_client.post(
        url, {"name": "test_table_1"}, format="json", HTTP_AUTHORIZATION=f"JWT {token}"
    )
    assert response.status_code == HTTP_200_OK
    json_response = response.json()
    table_id = json_response['id']
    pprint.pprint(json_response)

    # list fields in the table
    # ========================

    url = reverse(
        "api:database:fields:list", kwargs={"table_id": table_id}
    )    
    response = api_client.get(
        url, HTTP_AUTHORIZATION=f"JWT {token}"
    )
    assert response.status_code == HTTP_200_OK
    json_response = response.json()
    pprint.pprint(json_response)

    # create french language field
    # ============================

    response = api_client.post(
        reverse("api:database:fields:list", kwargs={"table_id": table_id}),
        {"name": "french", "type": "language_text", "language": "fr"},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    print('response from creating french language field:')
    pprint.pprint(response_json)
    french_field_id = response_json['id']
    assert response.status_code == HTTP_200_OK

    # create english translation field
    # ================================

    response = api_client.post(
        reverse("api:database:fields:list", kwargs={"table_id": table_id}),
        {"name": "english_trans", "type": "translation", "source_field_id": french_field_id, 'target_language': 'en', 'service': 'TestServiceA'},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    english_trans_field_id = response_json['id']
    # pprint.pprint(response_json)
    assert response.status_code == HTTP_200_OK    


    # enter some data in the french field
    # ===================================

    response = api_client.post(
        reverse("api:database:rows:list", kwargs={"table_id": table_id}),
        {f"field_{french_field_id}": "bonjour"},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    assert response.status_code == HTTP_200_OK    

    pprint.pprint(response_row)

    assert response_row[f'field_{french_field_id}'] == 'bonjour'
    english_field_data = json.loads(response_row[f'field_{english_trans_field_id}'])
    assert english_field_data == {
        "text": "bonjour", "from_language_key": "fr", "to_language_key": 'en'
    }



@pytest.mark.django_db(transaction=True)
def test_pinyin(api_client, data_fixture):
    use_clt_real_services()

    # CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES=no pytest baserow_vocabai_plugin/cloudlanguagetools/test_clt.py -k test_pinyin -s -rPP --log-cli-level=DEBUG
    # CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES=no pytest baserow_vocabai_plugin/cloudlanguagetools/test_clt.py -k test_pinyin
    assert os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] == 'no'

    logger.info(f'starting test_pinyin')
    user, token = data_fixture.create_user_and_token()

    ROMANIZATION_FORMAT_REVISION = 3

    # update language data first
    # clt_interface.update_language_data()

    # create database and table
    # =========================
    
    logger.info(f'creating database and table')

    database = data_fixture.create_database_application(user=user)

    url = reverse("api:database:tables:async_create", kwargs={"database_id": database.id})
    response = api_client.post(url, {"name": "test_table_1"}, format="json", HTTP_AUTHORIZATION=f"JWT {token}")
    assert response.status_code == HTTP_200_OK
    json_response = response.json()
    table_id = json_response['id']

    # add chinese field
    # =================
    # cloudlanguagetools.languages.Language.zh_cn

    logger.info(f'adding chinese language field')

    response = api_client.post(
        reverse("api:database:fields:list", kwargs={"table_id": table_id}),
        {"name": "chinese", "type": "language_text", "language": cloudlanguagetools.languages.Language.zh_cn.name},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    pprint.pprint(response_json)
    chinese_field_id = response_json['id']
    assert response.status_code == HTTP_200_OK    

    # add pinyin field
    # ================

    logger.info(f'adding pinyin field')

    response = api_client.post(
        reverse("api:database:fields:list", kwargs={"table_id": table_id}),
        {
            "name": "pinyin", 
            "type": "chinese_romanization", 
            "transformation": "pinyin",
            "source_field_id": chinese_field_id, 
            'tone_numbers': False,
            'spaces': False,
        },
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    pprint.pprint(response_json)
    assert response.status_code == HTTP_200_OK
    pinyin_field_id = response_json['id']

    # enter some data in the chinese field
    # ====================================

    logger.info(f'writing data to row')

    response = api_client.post(
        reverse("api:database:rows:list", kwargs={"table_id": table_id}),
        {f"field_{chinese_field_id}": "了"},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    print('response_row after writing some data:')
    # pprint.pprint(response_row) 
    table_row_id = response_row['id']
    assert response.status_code == HTTP_200_OK

    # retrieve the row
    # ================

    logger.info('retrieving row')
    response = api_client.get(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    assert response.status_code == HTTP_200_OK
    pprint.pprint(response_row) 

    expected_output = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'le',
        'solution_overrides': [0],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }

    assert response_row[f'field_{pinyin_field_id}'] == expected_output


    # modify the pinyin field
    # =======================

    logger.info('updating row, the pinyin field')
    field_value = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'le',
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }
    response = api_client.patch(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        {f"field_{pinyin_field_id}": field_value},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    logger.info(pprint.pformat(response_row))
    assert response.status_code == HTTP_200_OK


    expected_output = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'liǎo',
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }
    assert response_row[f'field_{pinyin_field_id}'] == expected_output

    # retrieve the row again, make sure the id on the pinyin field is correct
    # =======================================================================

    logger.info('retrieving row')
    response = api_client.get(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    assert response.status_code == HTTP_200_OK
    # pprint.pprint(response_row) 

    expected_field_value = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'liǎo',
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }

    assert response_row[f'field_{pinyin_field_id}'] == expected_field_value


    # set a rendered_solution_override
    # ================================

    logger.info('updating row, the pinyin field')
    field_value = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'le',
        'rendered_solution_override': 'liao 3', # doesn't have to be valid pinyin
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }
    response = api_client.patch(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        {f"field_{pinyin_field_id}": field_value},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    logger.info(pprint.pformat(response_row))
    assert response.status_code == HTTP_200_OK


    expected_output = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'liao 3',
        'rendered_solution_override': 'liao 3',
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }
    assert response_row[f'field_{pinyin_field_id}'] == expected_output

    # remove the rendered_solution_override
    # ================================

    logger.info('updating row, the pinyin field')
    field_value = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'le',
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }
    response = api_client.patch(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        {f"field_{pinyin_field_id}": field_value},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    logger.info(pprint.pformat(response_row))
    assert response.status_code == HTTP_200_OK


    expected_output = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'liǎo',
        'solution_overrides': [1],
        'word_list': ['了'],
        'solutions': [['le', 'liǎo', 'liào']]
    }
    assert response_row[f'field_{pinyin_field_id}'] == expected_output

    # modify pinyin derived field, use tone numbers
    # =============================================

    logger.info(f'modifying pinyin field to use tone numbers')

    response = api_client.patch(
        # reverse("api:database:fields:item", kwargs={"id": pinyin_field_id,"table_id": table_id}),
        f'/api/database/fields/{pinyin_field_id}/',
        {
            "name": "pinyin", 
            "type": "chinese_romanization", 
            "transformation": "pinyin",
            "source_field_id": chinese_field_id, 
            'tone_numbers': True,
            'spaces': False,
        },
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    # logger.debug(f'after switching to tone numbers: {pprint.pformat(response_json)}')
    assert response.status_code == HTTP_200_OK

    # retrieve row again, we should see tone numbers in the pinyin field
    # ==================================================================

    logger.info('retrieving row to make sure the pinyin field now has tone numbers')
    response = api_client.get(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    assert response.status_code == HTTP_200_OK
    logger.debug(f'response after switching to tone numbers: {pprint.pformat(response_row)}')
    
    expected_output = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'le5',
        'solution_overrides': [0],
        'word_list': ['了'],
        'solutions': [['le5', 'liao3', 'liao4']]
    }    
    assert response_row[f'field_{pinyin_field_id}'] == expected_output

    # change chinese text, make sure pinyin gets updated
    # ==================================================

    new_chinese = '没有'
    logger.info(f'changing chinese field to {new_chinese}')

    response = api_client.patch(
        reverse("api:database:rows:item", kwargs={"table_id": table_id, 'row_id': table_row_id}),
        {f"field_{chinese_field_id}": new_chinese},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_row = response.json()
    assert response.status_code == HTTP_200_OK
    pprint.pprint(response_row)

    expected_pinyin = {
        'format_revision': ROMANIZATION_FORMAT_REVISION,
        'rendered_solution': 'mei2you3',
        'solution_overrides': [0],
        'word_list': ['没有'],
        'solutions': [['mei2you3']]
    }
    assert response_row[f'field_{pinyin_field_id}'] == expected_pinyin


@pytest.mark.django_db(transaction=True)
def test_create_multiple_rows(api_client, data_fixture):
    use_clt_test_services()

    # CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES=yes pytest baserow_vocabai_plugin/cloudlanguagetools/test_clt.py -k test_create_multiple_rows
    assert os.environ['CLOUDLANGUAGETOOLS_CORE_TEST_SERVICES'] == 'yes'

    user, token = data_fixture.create_user_and_token()

    # update language data first
    clt_interface.update_language_data()    

    response = api_client.get(reverse('api:baserow_vocabai_plugin:translation-options'),HTTP_AUTHORIZATION=f"JWT {token}",)
    assert response.status_code == HTTP_200_OK
    pprint.pprint(response.data)    

    # create database
    # ===============

    database = data_fixture.create_database_application(user=user)
    pprint.pprint(database)

    # create table 
    # ============

    url = reverse(
        "api:database:tables:async_create", kwargs={"database_id": database.id}
    )
    response = api_client.post(
        url, {"name": "test_table_1"}, format="json", HTTP_AUTHORIZATION=f"JWT {token}"
    )
    assert response.status_code == HTTP_200_OK
    json_response = response.json()
    table_id = json_response['id']
    pprint.pprint(json_response)


    # create french language field
    # ============================

    response = api_client.post(
        reverse("api:database:fields:list", kwargs={"table_id": table_id}),
        {"name": "french", "type": "language_text", "language": "fr"},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    print('response from creating french language field:')
    pprint.pprint(response_json)
    french_field_id = response_json['id']
    assert response.status_code == HTTP_200_OK

    # create english translation field
    # ================================

    response = api_client.post(
        reverse("api:database:fields:list", kwargs={"table_id": table_id}),
        {"name": "english_trans", "type": "translation", "source_field_id": french_field_id, 'target_language': 'en', 'service': 'TestServiceA'},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_json = response.json()
    english_trans_field_id = response_json['id']
    # pprint.pprint(response_json)
    assert response.status_code == HTTP_200_OK    


    # batch create multiple rows
    # ==========================
    url = f'/api/database/rows/table/{table_id}/batch/'
    rows = [
        {f"field_{french_field_id}": "Bonjour"},
        {f"field_{french_field_id}": "Au Revoir"},
    ]
    response = api_client.post(
        url,
        {'items': rows},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_data = response.json()
    assert response.status_code == HTTP_200_OK, response.content
    print(f'*** response from batch create: {response_data}')

    english_trans_field_row_0 = response_data['items'][0][f'field_{english_trans_field_id}']
    english_trans_field_row_1 = response_data['items'][1][f'field_{english_trans_field_id}']
    assert json.loads(english_trans_field_row_0) == {
        "text": "Bonjour", "from_language_key": "fr", "to_language_key": 'en'
    }
    assert json.loads(english_trans_field_row_1) == {
        "text": "Au Revoir", "from_language_key": "fr", "to_language_key": 'en'
    }

    row_id_0 = response_data['items'][0]['id']
    row_id_1 = response_data['items'][1]['id']

    # now, update those rows
    # ======================
    url = f'/api/database/rows/table/{table_id}/batch/'
    rows = [
        {'id': row_id_0, f"field_{french_field_id}": "Merci"},
        {'id': row_id_1, f"field_{french_field_id}": "Non"},
    ]
    response = api_client.patch(
        url,
        {'items': rows},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    response_data = response.json()
    assert response.status_code == HTTP_200_OK, response.content
    print(f'*** response from batch update: {response_data}')

    english_trans_field_row_0 = response_data['items'][0][f'field_{english_trans_field_id}']
    english_trans_field_row_1 = response_data['items'][1][f'field_{english_trans_field_id}']
    assert json.loads(english_trans_field_row_0) == {
        "text": "Merci", "from_language_key": "fr", "to_language_key": 'en'
    }
    assert json.loads(english_trans_field_row_1) == {
        "text": "Non", "from_language_key": "fr", "to_language_key": 'en'
    }    