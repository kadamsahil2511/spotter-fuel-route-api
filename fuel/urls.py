from django.urls import path

from fuel.views import MapView, RouteView, health, index

urlpatterns = [
    path("", index),
    path("api/v1/health", health),
    path("api/v1/route", RouteView.as_view()),
    path("api/v1/map", MapView.as_view()),
]
