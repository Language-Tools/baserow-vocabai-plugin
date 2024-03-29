from django.db import models
from django.core.exceptions import ValidationError
from baserow.contrib.database.fields.field_cache import FieldCache

from rest_framework import serializers

from baserow.contrib.database.fields.registries import FieldType
from baserow.contrib.database.fields.models import Field, TextField
from baserow.contrib.database.views.handler import ViewHandler

from baserow.contrib.database.fields.dependencies.models import FieldDependency
from baserow.contrib.database.table.models import TableModelQuerySet

from baserow.core.models import WORKSPACE_USER_PERMISSION_ADMIN, WorkspaceUser

from baserow.contrib.database.fields.field_filters import (
    contains_filter,
    contains_word_filter,
)
from baserow.contrib.database.formula import BaserowFormulaType, BaserowFormulaTextType

from .vocabai_models import TranslationField, TransliterationField, LanguageField, DictionaryLookupField, ChineseRomanizationField, CHOICE_PINYIN, CHOICE_JYUTPING

from ..cloudlanguagetools.tasks import run_clt_translation_all_rows, run_clt_transliteration_all_rows, run_clt_lookup_all_rows, run_clt_chinese_romanization_all_rows
from ..cloudlanguagetools import clt_interface

import logging
import pprint
logger = logging.getLogger(__name__)

# see https://community.baserow.io/t/anonymous-api-access-or-universal-token/788/18 for background
# the idea suggested by Nigel was to enhance Update Collector to run the lambdas at the end, at the right time
# however this will require a baserow update, and it's more complicated to do when this code is running as a baserow plugin.
USE_ENHANCED_UPDATE_COLLECTOR = False

class LanguageTextField(models.TextField):
    pass

class LanguageFieldType(FieldType):
    type = "language_text"
    model_class = LanguageField
    allowed_fields = ["language"]
    serializer_field_names = ["language"]

    def get_serializer_field(self, instance, **kwargs):
        required = kwargs.get("required", False)
        return serializers.CharField(
            **{
                "required": required,
                "allow_null": not required,
                "allow_blank": not required,
                "default": None,
                **kwargs,
            }
        )

    def get_model_field(self, instance, **kwargs):
        return LanguageTextField(
            default='', blank=True, null=True, **kwargs
        )


class TranslationTextField(models.TextField):
    requires_refresh_after_update = True


class TransformationFieldType(FieldType):
    def get_field_dependencies(self, field_instance: Field, field_lookup_cache: FieldCache):
        logger.debug(f'get_field_dependencies')
        if field_instance.source_field != None:
            logger.debug(f'we depend on field {field_instance.source_field}')
            return [
                FieldDependency(
                    dependency=field_instance.source_field,
                    dependant=field_instance
                )
            ]     
        return []    

    def after_create(self, field, model, user, connection, before, field_kwargs):
        self.update_all_rows(field)

    def after_update(
        self,
        from_field,
        to_field,
        from_model,
        to_model,
        user,
        connection,
        altered_column,
        before,
        to_field_kwargs
    ):
        self.update_all_rows(to_field)        

    def get_transformed_value(self, field, source_value, usage_user_id):
        if source_value == None or len(source_value) == 0:
            return ''
        transformed_value = self.transform_value(field, source_value, usage_user_id)
        return transformed_value

    def get_usage_user_id(self, field):
        """get the user_id that this usage will be associated with"""

        # find the admin in the group
        workspace = field.table.database.workspace

        admin_users = WorkspaceUser.objects.filter(workspace_id=workspace.id, permissions=WORKSPACE_USER_PERMISSION_ADMIN)
        for admin_user in admin_users:
            logger.info(f'admin_user: {admin_user.user}')
            return admin_user.user.id

        logger.error(f'admin user not found in group {group} workspace.id: {workspace.id}')
        return None

    def process_transformation(self, field, starting_row):
        source_internal_field_name = f'field_{field.source_field.id}'
        target_internal_field_name = f'field_{field.id}'

        if isinstance(starting_row, TableModelQuerySet):
            # if starting_row is TableModelQuerySet (when creating multiple rows in a batch), we want to iterate over its TableModel objects
            row_list = starting_row
        elif isinstance(starting_row, list):
            # if we have a list, it's a list of TableModels, iterate over them
            row_list = starting_row            
        else:
            # we got a single TableModel, transform it into a list of one element
            row_list = [starting_row]

        rows_to_bulk_update = []
        for row in row_list:
            source_value = getattr(row, source_internal_field_name)
            transformed_value = self.get_transformed_value(field, source_value, self.get_usage_user_id(field))
            setattr(row, target_internal_field_name, transformed_value)
            rows_to_bulk_update.append(row)

        model = field.table.get_model()
        model.objects.bulk_update(rows_to_bulk_update, fields=[field.db_column])


    def row_of_dependency_deleted(
        self,
        field,
        starting_row,
        update_collector,
        field_cache,
        via_path_to_starting_table):
        # don't do anything
        pass

    def row_of_dependency_moved(
        self,
        field,
        starting_row,
        update_collector,
        field_cache,
        via_path_to_starting_table):    
        # don't do anything
        pass

    # Lets this field type work with the contains view filter
    def contains_query(self, *args):
        return contains_filter(*args)

    # Lets this field type work with the contains word view filter
    def contains_word_query(self, *args):
        return contains_word_filter(*args)

    # Lets this field type be referenced (and treated like it is text) by the formula
    # field:
    def to_baserow_formula_type(self, field) -> BaserowFormulaType:
        return BaserowFormulaTextType(nullable=True)

    def from_baserow_formula_type(
            self, formula_type: BaserowFormulaTextType
    ) -> TextField:
        # Pretend to be a text field from the formula systems perspective
        return TextField()    

class TranslationFieldType(TransformationFieldType):
    type = "translation"
    model_class = TranslationField
    allowed_fields = [
        'source_field_id',
        'target_language',
        'service'
    ]
    serializer_field_names = [
        'source_field_id',
        'target_language',
        'service'
    ]
    serializer_field_overrides = {
        "source_field_id": serializers.IntegerField(
            required=False,
            allow_null=True,
            source="source_field.id",
            help_text="The id of the field to translate",
        ),
        "target_language": serializers.CharField(
            required=True,
            allow_null=False,
            allow_blank=False
        ),
        'service': serializers.CharField(
            required=True,
            allow_null=False,
            allow_blank=False
        )
    }

    can_be_primary_field = False

    def prepare_value_for_db(self, instance, value):
        return value

    def get_serializer_field(self, instance, **kwargs):
        return serializers.CharField(
            **{
                "required": False,
                "allow_null": True,
                "allow_blank": True,
                **kwargs,
            }        
        )

    def get_model_field(self, instance, **kwargs):
        return TranslationTextField(
            default=None,
            blank=True, 
            null=True, 
            **kwargs
        )


    def transform_value(self, field, source_value, usage_user_id):
        source_language = field.source_field.language  
        target_language = field.target_language
        translation_service = field.service
        if source_value == None or len(source_value) == 0:
            return ''
        translated_text = clt_interface.get_translation(source_value, source_language, target_language, translation_service, usage_user_id)
        return translated_text

    def row_of_dependency_updated(
        self,
        field,
        starting_row,
        update_collector,
        field_cache: "FieldCache",
        via_path_to_starting_table,
    ):

        if USE_ENHANCED_UPDATE_COLLECTOR:
            # as per nigel, it's preferrable to use the update collector to do the update at the end
            def translate_rows(rows):
                source_language = field.source_field.language  
                target_language = field.target_language
                translation_service = field.service          
                source_internal_field_name = f'field_{field.source_field.id}'
                target_internal_field_name = f'field_{field.id}'
                for row in rows:
                    text = getattr(row, source_internal_field_name)
                    if text != None:
                        translated_text = clt_interface.get_translation(text, source_language, target_language, translation_service)
                        setattr(row, target_internal_field_name, translated_text)

            update_collector.add_field_with_pending_update_function(
                field,
                update_function=translate_rows,
                via_path_to_starting_table=via_path_to_starting_table,
            )       
        else:
            self.process_transformation(field, starting_row)

        ViewHandler().field_value_updated(field)     

        super().row_of_dependency_updated(
            field,
            starting_row,
            update_collector,
            field_cache,
            via_path_to_starting_table,
        )        


    def update_all_rows(self, field):
        logger.info(f'update_all_rows')
        source_field_language = field.source_field.language
        target_language = field.target_language
        translation_service = field.service          
        source_field_id = f'field_{field.source_field.id}'
        target_field_id = f'field_{field.id}'

        table_id = field.table.id

        logger.info(f'after_update table_id: {table_id} source_field_id: {source_field_id} target_field_id: {target_field_id}')

        run_clt_translation_all_rows.delay(table_id, 
                                           source_field_language, 
                                           target_language,
                                           translation_service,
                                           source_field_id, 
                                           target_field_id,
                                           self.get_usage_user_id(field))


class TransliterationFieldType(TransformationFieldType):
    type = "transliteration"
    model_class = TransliterationField
    allowed_fields = [
        'source_field_id',
        'transliteration_id'
    ]
    serializer_field_names = [
        'source_field_id',
        'transliteration_id'
    ]
    serializer_field_overrides = {
        "source_field_id": serializers.IntegerField(
            required=False,
            allow_null=True,
            source="source_field.id",
            help_text="The id of the field to translate",
        ),
        "transliteration_id": serializers.CharField(
            required=True,
            allow_null=False,
            allow_blank=False
        ),
    }

    can_be_primary_field = False

    def prepare_value_for_db(self, instance, value):
        return value

    def get_serializer_field(self, instance, **kwargs):
        return serializers.CharField(
            **{
                "required": False,
                "allow_null": True,
                "allow_blank": True,
                **kwargs,
            }        
        )

    def get_model_field(self, instance, **kwargs):
        return TranslationTextField(
            default=None,
            blank=True, 
            null=True, 
            **kwargs
        )

    def transform_value(self, field, source_value, usage_user_id):
        transliteration_id = field.transliteration_id
        transliterated_text = clt_interface.get_transliteration(source_value, transliteration_id, usage_user_id)
        return transliterated_text

    def row_of_dependency_updated(
        self,
        field,
        starting_row,
        update_collector,
        field_cache: "FieldCache",
        via_path_to_starting_table,
    ):


        if USE_ENHANCED_UPDATE_COLLECTOR:

            def transliterate_rows(rows):
                transliteration_id = field.transliteration_id
                source_internal_field_name = f'field_{field.source_field.id}'
                target_internal_field_name = f'field_{field.id}'
                for row in rows:
                    text = getattr(row, source_internal_field_name)
                    if text != None:
                        transliterated_text = clt_interface.get_transliteration(text, transliteration_id)
                        setattr(row, target_internal_field_name, transliterated_text)

            update_collector.add_field_with_pending_update_function(
                field,
                update_function=transliterate_rows,
                via_path_to_starting_table=via_path_to_starting_table,
            )       

        else:
            self.process_transformation(field, starting_row)

        ViewHandler().field_value_updated(field)     

        super().row_of_dependency_updated(
            field,
            starting_row,
            update_collector,
            field_cache,
            via_path_to_starting_table,
        )        


    def update_all_rows(self, field):
        logger.info(f'update_all_rows')
        transliteration_id = field.transliteration_id
        source_field_id = f'field_{field.source_field.id}'
        target_field_id = f'field_{field.id}'

        table_id = field.table.id


        run_clt_transliteration_all_rows.delay(table_id, 
                                                transliteration_id,
                                                source_field_id, 
                                                target_field_id,
                                                self.get_usage_user_id(field))



class DictionaryLookupFieldType(TransformationFieldType):
    type = "dictionary_lookup"
    model_class = DictionaryLookupField
    allowed_fields = [
        'source_field_id',
        'lookup_id'
    ]
    serializer_field_names = [
        'source_field_id',
        'lookup_id'
    ]
    serializer_field_overrides = {
        "source_field_id": serializers.IntegerField(
            required=False,
            allow_null=True,
            source="source_field.id",
            help_text="The id of the field for which to do dictionary lookup",
        ),
        "lookup_id": serializers.CharField(
            required=True,
            allow_null=False,
            allow_blank=False
        ),
    }

    can_be_primary_field = False

    def prepare_value_for_db(self, instance, value):
        return value

    def get_serializer_field(self, instance, **kwargs):
        return serializers.CharField(
            **{
                "required": False,
                "allow_null": True,
                "allow_blank": True,
                **kwargs,
            }        
        )

    def get_model_field(self, instance, **kwargs):
        return TranslationTextField(
            default=None,
            blank=True, 
            null=True, 
            **kwargs
        )


    def transform_value(self, field, source_value, usage_user_id):
        lookup_id = field.lookup_id
        lookup_result = clt_interface.get_dictionary_lookup(source_value, lookup_id, usage_user_id)
        return lookup_result

    def row_of_dependency_updated(
        self,
        field,
        starting_row,
        update_collector,
        field_cache,
        via_path_to_starting_table,
    ):

        if USE_ENHANCED_UPDATE_COLLECTOR:
            def perform_dictionary_lookup_rows(rows):
                lookup_id = field.lookup_id
                source_internal_field_name = f'field_{field.source_field.id}'
                target_internal_field_name = f'field_{field.id}'
                for row in rows:
                    text = getattr(row, source_internal_field_name)
                    if text != None:
                        lookup_result = clt_interface.get_dictionary_lookup(text, lookup_id)
                        setattr(row, target_internal_field_name, lookup_result)

            update_collector.add_field_with_pending_update_function(
                field,
                update_function=perform_dictionary_lookup_rows,
                via_path_to_starting_table=via_path_to_starting_table,
            )       
        else:
            self.process_transformation(field, starting_row)

        ViewHandler().field_value_updated(field)     

        super().row_of_dependency_updated(
            field,
            starting_row,
            update_collector,
            field_cache,
            via_path_to_starting_table,
        )        


    def update_all_rows(self, field):
        logger.info(f'update_all_rows')
        lookup_id = field.lookup_id
        source_field_id = f'field_{field.source_field.id}'
        target_field_id = f'field_{field.id}'

        table_id = field.table.id

        run_clt_lookup_all_rows.delay(table_id, 
                                        lookup_id, 
                                        source_field_id, 
                                        target_field_id,
                                        self.get_usage_user_id(field))

class ChineseRomanizationFieldType(TransformationFieldType):
    type = "chinese_romanization"
    model_class = ChineseRomanizationField
    allowed_fields = [
        'source_field_id',
        'correction_table_id',
        'transformation',
        'tone_numbers',
        'spaces'
    ]

    serializer_field_names = [
        'source_field_id',
        'correction_table_id',
        'transformation',
        'tone_numbers',
        'spaces'        
    ]
    serializer_field_overrides = {
        "source_field_id": serializers.IntegerField(
            required=False,
            allow_null=True,
            source="source_field.id",
            help_text="The id of the field to transliterate",
        ),
        "correction_table_id": serializers.IntegerField(
            required=False,
            allow_null=True,
            source="correction_table.id",
            help_text="The id of the table which contains pinyin/jyutping corrections",
        ),
        "transformation": serializers.CharField(
            required=True,
            allow_null=False,
            allow_blank=False
        ),        
        "tone_numbers": serializers.BooleanField(
            required=True,
            allow_null=False,
        ),
        "spaces": serializers.BooleanField(
            required=True,
            allow_null=False,
        ),        
    }    

    can_be_primary_field = False

    def prepare_value_for_db(self, instance, value):
        logger.info(f'prepare_value_for_db, value: {value}')
        value = clt_interface.update_rendered_solution(value)
        return value

    # def get_serializer_field(self, instance, **kwargs):
    #     return serializers.JSONField(**kwargs)

    # def get_model_field(self, instance, **kwargs):
    #     return models.JSONField(null=True, blank=True, default={}, **kwargs)

    def get_serializer_field(self, instance, **kwargs):
        logger.info('get_serializer_field')
        return serializers.JSONField(
            default={},
            required=False,
            allow_null=True,
            **kwargs)

    def get_model_field(self, instance, **kwargs):
        # needs to return a Django Model Field like models.TextField or models.CharField etc.
        logger.info('get_model_field')
        return models.JSONField(            
            default={},
            blank=True, 
            null=True, 
            **kwargs)

    def transform_value(self, field, source_value, usage_user_id):
        logger.info('transform_field')
        romanization_choices = []
        if field.transformation == CHOICE_PINYIN:
            result =  clt_interface.get_pinyin(source_value, field.tone_numbers, field.spaces)
        elif field.transformation == CHOICE_JYUTPING:
            result =  clt_interface.get_jyutping(source_value, field.tone_numbers, field.spaces)
        return result

    def get_export_value(
        self, value, field_object, rich_value = False
    ):
        if value != None and value != '':
            return value['rendered_solution']
        return value

    def row_of_dependency_updated(
        self,
        field,
        starting_row,
        update_collector,
        field_cache: "FieldCache",
        via_path_to_starting_table,
    ):
        logger.info('row_of_dependency_updated')

        self.process_transformation(field, starting_row)

        ViewHandler().field_value_updated(field)     

        super().row_of_dependency_updated(
            field,
            starting_row,
            update_collector,
            field_cache,
            via_path_to_starting_table,
        )        


    def update_all_rows(self, field):
        logger.info(f'update_all_rows')
        source_field_id = f'field_{field.source_field.id}'
        target_field_id = f'field_{field.id}'

        table_id = field.table.id

        run_clt_chinese_romanization_all_rows.delay(table_id, 
                                                    field.transformation,
                                                    field.tone_numbers,
                                                    field.spaces,
                                                    source_field_id, 
                                                    target_field_id,
                                                    self.get_usage_user_id(field))