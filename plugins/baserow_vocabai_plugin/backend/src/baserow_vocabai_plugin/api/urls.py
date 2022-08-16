from django.urls import re_path

from .views import StartingView

app_name = "baserow_vocabai_plugin.api"

urlpatterns = [
    re_path(r"starting/$", StartingView.as_view(), name="starting"),
]
