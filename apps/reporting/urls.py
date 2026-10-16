from django.urls import path

from .views import ReportSummary

urlpatterns = [
    path('summary/', ReportSummary.as_view()),
]
