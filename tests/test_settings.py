from app.services.settings import all_settings, get_setting, get_setting_float, seed_defaults_from_env, set_setting


def test_get_setting_returns_default_when_unset(session, user):
    assert get_setting(session, user.id, "monthly_income") == "4500"
    assert get_setting_float(session, user.id, "budget_aviso_pct") == 70.0


def test_set_setting_overrides_default(session, user):
    set_setting(session, user.id, "monthly_income", "1350")
    assert get_setting(session, user.id, "monthly_income") == "1350"


def test_set_setting_twice_updates_same_row(session, user):
    set_setting(session, user.id, "monthly_income", "1000")
    set_setting(session, user.id, "monthly_income", "2000")
    assert get_setting(session, user.id, "monthly_income") == "2000"


def test_seed_defaults_from_env_does_not_override_existing(session, user):
    set_setting(session, user.id, "monthly_income", "1350")
    seed_defaults_from_env(session, user.id, monthly_income=4500)
    assert get_setting(session, user.id, "monthly_income") == "1350"


def test_all_settings_includes_every_known_key(session, user):
    result = all_settings(session, user.id)
    assert set(result.keys()) == {"monthly_income", "budget_aviso_pct", "budget_alerta_pct", "budget_excedido_pct"}
