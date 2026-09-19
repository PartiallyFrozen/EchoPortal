"""Forecast -> library key mapping for the Sky face."""
import weatherart


def test_condition_groups_cover_the_open_meteo_codes():
    assert weatherart.condition_group(0) == "clear"
    assert weatherart.condition_group(1) == "mostly_clear"
    assert weatherart.condition_group(3) == "overcast"
    assert weatherart.condition_group(48) == "fog"
    assert weatherart.condition_group(55) == "drizzle"
    assert weatherart.condition_group(65) == "heavy_rain"
    assert weatherart.condition_group(86) == "snow"
    assert weatherart.condition_group(99) == "thunderstorm"


def test_an_unknown_code_falls_back_rather_than_raising():
    assert weatherart.condition_group(4242) == "partly_cloudy"


def test_time_buckets_split_the_day_at_the_documented_hours():
    assert weatherart.time_bucket(0) == "dawn"
    assert weatherart.time_bucket(7) == "dawn"
    assert weatherart.time_bucket(8) == "morning"
    assert weatherart.time_bucket(11) == "morning"
    assert weatherart.time_bucket(12) == "afternoon"
    assert weatherart.time_bucket(16) == "afternoon"
    assert weatherart.time_bucket(17) == "golden"
    assert weatherart.time_bucket(19) == "golden"
    assert weatherart.time_bucket(20) == "night"
    assert weatherart.time_bucket(23) == "night"


def test_the_key_space_is_every_condition_times_every_time_of_day():
    keys = weatherart.all_keys()
    assert len(keys) == len(weatherart.CONDITION_GROUPS) * len(weatherart.TIMES) == 50
    assert len(set(keys)) == len(keys)
    assert "thunderstorm-night" in keys


def test_key_for_joins_the_condition_and_the_hour():
    assert weatherart.key_for(0, 13) == "clear-afternoon"
    assert weatherart.key_for(71, 21) == "snow-night"
    assert weatherart.key_for(61, 9) in weatherart.all_keys()


def test_every_key_builds_a_prompt_that_mentions_its_condition_and_time():
    for name, _codes, cdesc in weatherart.CONDITION_GROUPS:
        for tod, tdesc in weatherart.TIMES:
            prompt = weatherart.build_prompt("%s-%s" % (name, tod))
            assert cdesc in prompt
            assert tdesc in prompt
            assert "%s" not in prompt


def test_variant_zero_is_the_bare_name_and_the_rest_are_suffixed():
    assert weatherart.library_path("rain-dawn").endswith("rain-dawn.jpg")
    assert weatherart.library_path("rain-dawn", 0).endswith("rain-dawn.jpg")
    assert weatherart.library_path("rain-dawn", 2).endswith("rain-dawn-v2.jpg")


def test_library_variants_reports_only_files_that_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(weatherart, "LIBRARY_DIR", str(tmp_path))
    assert weatherart.library_variants("fog-night") == []
    (tmp_path / "fog-night.jpg").write_bytes(b"x")
    (tmp_path / "fog-night-v2.jpg").write_bytes(b"x")
    assert weatherart.library_variants("fog-night") == [0, 2]
