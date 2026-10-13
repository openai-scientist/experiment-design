from experiment_design.science.evidence import supports_number


def test_numbers_do_not_match_digits_inside_other_numbers():
    assert supports_number(25, "There are 25 students.")
    assert not supports_number(25, "There are 125 students.")
    assert not supports_number(0.05, None)
    assert supports_number(0.05, "ICC 5e-2")
