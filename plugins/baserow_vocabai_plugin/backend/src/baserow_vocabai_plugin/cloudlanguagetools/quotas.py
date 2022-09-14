import datetime
import logging

from ..fields.vocabai_models import VocabAiUsage, USAGE_PERIOD_MONTHLY, USAGE_PERIOD_DAILY

from django.contrib.auth import get_user_model
User = get_user_model()

logger = logging.getLogger(__name__)

class UsageRecord():
    def __init__(self, monthly_usage_record, daily_usage_record):
        self.monthly_usage_record = monthly_usage_record
        self.daily_usage_record = daily_usage_record        

    def check_quota_available(self):
        pass

    def update_usage(self, character_cost):
        self.daily_usage_record.characters = self.daily_usage_record.characters + character_cost
        self.monthly_usage_record.characters = self.monthly_usage_record.characters + character_cost

        self.daily_usage_record.save()
        self.monthly_usage_record.save()

        self.log_usage()

    def log_usage(self):
        user = self.daily_usage_record.user
        daily = self.daily_usage_record
        monthly = self.monthly_usage_record
        logger.info(f'usage for {user}, daily/{daily.period_time}: {daily.characters} characters, monthly/{monthly.period_time}: {monthly.characters} characters')

def get_usage_record(usage_user_id):
    # locate user
    user_records = User.objects.filter(id=usage_user_id)
    if len(user_records) != 1:
        logger.error(f'found {len(user_records)} records for user_id: {usage_user_id}')
    user = user_records[0]

    period_time_monthly = int(datetime.datetime.today().strftime('%Y%m'))
    period_time_daily = int(datetime.datetime.today().strftime('%Y%m%d'))        

    return UsageRecord(get_usage_entry(user, USAGE_PERIOD_MONTHLY, period_time_monthly), 
                       get_usage_entry(user, USAGE_PERIOD_DAILY, period_time_daily))


def get_usage_entry(user, period, period_time):
    monthly_usage_records = VocabAiUsage.objects.filter(user=user, period=period, period_time=period_time)
    if len(monthly_usage_records) == 0:
        # create record
        usage = VocabAiUsage(user=user, period=period, period_time=period_time, characters=0)
        usage.save()
    else:
        usage = monthly_usage_records[0]

    return usage