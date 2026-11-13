"""HTTP API: route plan, map, and health."""

from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.services.plan import PlanError, build_plan
from fuel.services.stations import get_stations


class RouteView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        return self._respond(request.query_params)

    def post(self, request):
        return self._respond(request.data)

    def _respond(self, source):
        try:
            payload = build_plan(
                source.get("start"),
                source.get("finish"),
                _number(source.get("range_miles"), 500),
                _number(source.get("mpg"), 10),
                _number(source.get("corridor_miles"), 10),
            )
        except PlanError as exc:
            return Response({"detail": str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(payload)


class MapView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            payload = build_plan(
                request.query_params.get("start"),
                request.query_params.get("finish"),
                _number(request.query_params.get("range_miles"), 500),
                _number(request.query_params.get("mpg"), 10),
                _number(request.query_params.get("corridor_miles"), 10),
            )
        except PlanError as exc:
            return JsonResponse({"detail": str(exc)}, status=exc.status)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return render(request, "fuel/map.html", {"plan": payload})


def health(_request):
    stations = get_stations()
    redis_ok = _redis_ok()
    body = {"status": "ok" if stations and redis_ok else "unavailable", "stations": len(stations)}
    if settings.REDIS_URL:
        body["redis"] = "ok" if redis_ok else "down"
    status = 200 if stations and redis_ok else 503
    return JsonResponse(body, status=status)


def index(_request):
    return JsonResponse(
        {
            "service": "spotter-fuel-route",
            "route": "/api/v1/route",
            "map": "/api/v1/map",
            "health": "/api/v1/health",
        }
    )


def _redis_ok() -> bool:
    if not settings.REDIS_URL:
        return bool(settings.DEBUG)
    try:
        from django_redis import get_redis_connection

        return bool(get_redis_connection("default").ping())
    except Exception:
        return False


def _number(value, default: float) -> float:
    if value is None or value == "":
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("range_miles, mpg, and corridor_miles must be numbers") from exc
    if number <= 0:
        raise ValueError("range_miles, mpg, and corridor_miles must be positive")
    return number
