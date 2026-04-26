import pytest

from alerts import Alert, AlertDirection, check_price_alert, filter_triggered_alerts


class TestCheckPriceAlert:
    def test_above_alert_triggered(self):
        alert = Alert(stock_code="2330", price=600, direction=AlertDirection.ABOVE)
        assert check_price_alert(alert, current_price=610)

    def test_above_alert_not_triggered(self):
        alert = Alert(stock_code="2330", price=600, direction=AlertDirection.ABOVE)
        assert not check_price_alert(alert, current_price=590)

    def test_above_alert_triggered_exactly_at_price(self):
        alert = Alert(stock_code="2330", price=600, direction=AlertDirection.ABOVE)
        assert check_price_alert(alert, current_price=600)

    def test_below_alert_triggered(self):
        alert = Alert(stock_code="2330", price=500, direction=AlertDirection.BELOW)
        assert check_price_alert(alert, current_price=490)

    def test_below_alert_not_triggered(self):
        alert = Alert(stock_code="2330", price=500, direction=AlertDirection.BELOW)
        assert not check_price_alert(alert, current_price=510)

    def test_below_alert_triggered_exactly_at_price(self):
        alert = Alert(stock_code="2330", price=500, direction=AlertDirection.BELOW)
        assert check_price_alert(alert, current_price=500)

    def test_disabled_alert_never_triggers(self):
        alert = Alert(stock_code="2330", price=500, direction=AlertDirection.BELOW, enabled=False)
        assert not check_price_alert(alert, current_price=400)


class TestFilterTriggeredAlerts:
    def test_returns_only_triggered(self):
        alerts = [
            Alert("2330", 600, AlertDirection.ABOVE),
            Alert("2317", 100, AlertDirection.BELOW),
            Alert("0050", 150, AlertDirection.ABOVE),
        ]
        prices = {"2330": 610, "2317": 110, "0050": 155}
        triggered = filter_triggered_alerts(alerts, prices)
        codes = [a.stock_code for a in triggered]
        assert "2330" in codes
        assert "0050" in codes
        assert "2317" not in codes

    def test_empty_alerts(self):
        assert filter_triggered_alerts([], {}) == []

    def test_missing_price_skipped(self):
        alerts = [Alert("2330", 600, AlertDirection.ABOVE)]
        triggered = filter_triggered_alerts(alerts, {})
        assert triggered == []

    def test_all_disabled(self):
        alerts = [
            Alert("2330", 600, AlertDirection.ABOVE, enabled=False),
        ]
        triggered = filter_triggered_alerts(alerts, {"2330": 999})
        assert triggered == []
