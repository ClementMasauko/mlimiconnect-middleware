import json
from urllib.error import HTTPError
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


class FakeWeatherResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({
            "timezone": "Africa/Blantyre",
            "current": {"time": "2026-08-30T12:00", "temperature_2m": 27.4, "relative_humidity_2m": 58, "precipitation": 0, "weather_code": 2, "wind_speed_10m": 11.2, "soil_temperature_0cm": 30.1, "soil_moisture_0_to_1cm": 0.2},
            "daily": {"time": ["2026-08-30"], "weather_code": [61], "temperature_2m_max": [29], "temperature_2m_min": [17], "precipitation_sum": [2.4], "precipitation_probability_max": [70], "et0_fao_evapotranspiration": [4.1]},
        }).encode("utf-8")


class FakeMetResponse(FakeWeatherResponse):
    def read(self):
        return json.dumps({"properties": {"timeseries": [{"time": "2026-09-01T08:00:00Z", "data": {"instant": {"details": {"air_temperature": 24.5, "relative_humidity": 50, "wind_speed": 4}}, "next_1_hours": {"summary": {"symbol_code": "partlycloudy_day"}, "details": {"precipitation_amount": 0.2, "probability_of_precipitation": 20}}}}]}}).encode("utf-8")


@override_settings(
    WEATHER_PROVIDER="open_meteo",
    WEATHER_API_URL="https://api.open-meteo.com/v1/forecast",
    WEATHER_TIMEOUT_SECONDS=2,
    WEATHER_CACHE_SECONDS=1200,
    WEATHER_STALE_SECONDS=21600,
)
class WeatherTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @patch("core.weather.urlopen", return_value=FakeWeatherResponse())
    def test_live_weather_is_transformed_attributed_and_cached(self, mocked_open):
        first = self.client.get("/api/advisory/weather/?district=Lilongwe")
        second = self.client.get("/api/advisory/weather/?district=Lilongwe")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["source"], "Open-Meteo")
        self.assertEqual(first.data["current"]["condition"], "Partly cloudy")
        self.assertEqual(first.data["forecast"][0]["rain_probability_percent"], 70)
        self.assertFalse(first.data["cached"])
        self.assertTrue(second.data["cached"])
        self.assertEqual(mocked_open.call_count, 1)
        requested_url = mocked_open.call_args.args[0].full_url
        self.assertIn("latitude=-13.96", requested_url)
        self.assertNotIn("key=", requested_url)

    def test_unknown_district_is_rejected(self):
        response = self.client.get("/api/advisory/weather/?district=Unknown")
        self.assertEqual(response.status_code, 400)

    @override_settings(WEATHER_FALLBACK_API_URL="https://api.met.no/weatherapi/locationforecast/2.0/compact", WEATHER_USER_AGENT="MlimiConnect/1.0 https://mlimiconnect.netlify.app/contact")
    @patch("core.weather.urlopen", side_effect=[HTTPError("https://api.open-meteo.com", 429, "Too Many Requests", {}, None), FakeMetResponse()])
    def test_rate_limit_uses_attributed_met_norway_fallback(self, mocked_open):
        response = self.client.get("/api/advisory/weather/?district=Lilongwe")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["source"], "MET Norway")
        self.assertEqual(response.data["current"]["temperature_c"], 24.5)
        self.assertEqual(response.data["current"]["wind_speed_kmh"], 14.4)
        self.assertEqual(mocked_open.call_count, 2)
        fallback_request = mocked_open.call_args_list[1].args[0]
        self.assertIn("lat=-13.9600", fallback_request.full_url)
        self.assertIn("MlimiConnect", fallback_request.headers["User-agent"])

    @patch("core.weather.urlopen", return_value=FakeWeatherResponse())
    def test_ussd_weather_uses_same_provider(self, _mocked_open):
        with override_settings(USSD_SERVICE_KEY="ussd-test", USSD_ALLOWED_IPS=["127.0.0.1"]):
            response = self.client.get("/api/ussd/services/weather/?district=Blantyre", HTTP_X_USSD_SERVICE_KEY="ussd-test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["source"], "Open-Meteo")
        self.assertIn("Rain chance", response.data["summary"])
