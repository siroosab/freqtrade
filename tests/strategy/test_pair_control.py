from freqtrade.strategy.pair_control import pair_control_store


def test_pair_control_defaults_are_isolated():
    pair = "TEST/USDT:USDT"

    settings = pair_control_store.get(pair)

    assert settings["pre_trade"]["long_enabled"] is True
    assert settings["pre_trade"]["short_enabled"] is True
    assert settings["pre_trade"]["entry_signal"] == "all"
    assert settings["pre_trade"]["leverage"] == 1
    assert settings["risk"]["stoploss_enabled"] is False
    assert settings["risk"]["averaging_enabled"] is False
    assert settings["risk"]["take_profit_enabled"] is False
    assert settings["risk"]["inactivity_exit_enabled"] is False


def test_pair_control_updates_only_requested_sections():
    pair = "TEST-UPDATE/USDT:USDT"

    settings = pair_control_store.set(
        pair,
        {
            "pre_trade": {"long_enabled": False, "entry_tag": "ui-entry"},
            "risk": {"stoploss_percent": -0.05, "stoploss_enabled": True},
        },
    )

    assert settings["pre_trade"]["long_enabled"] is False
    assert settings["pre_trade"]["short_enabled"] is True
    assert settings["pre_trade"]["entry_tag"] == "ui-entry"
    assert settings["risk"]["stoploss_percent"] == -0.05
    assert pair_control_store.get("OTHER/USDT:USDT")["pre_trade"]["long_enabled"] is True
