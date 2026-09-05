from prediction_consistency import reconcile


def _context():
    return {
        "home": {"recent_form": {
            "goals_for_avg": 1.8, "goals_against_avg": 1.0,
            "first_half": {"goals_for_avg": 0.8, "goals_against_avg": 0.4},
            "second_half": {"goals_for_avg": 1.0, "goals_against_avg": 0.6},
        }},
        "away": {"recent_form": {
            "goals_for_avg": 1.2, "goals_against_avg": 1.4,
            "first_half": {"goals_for_avg": 0.5, "goals_against_avg": 0.7},
            "second_half": {"goals_for_avg": 0.7, "goals_against_avg": 0.7},
        }},
        "data_availability": {
            "first_half_goals": True, "second_half_goals": True,
            "goal_timing": True,
        },
    }


def test_score_outputs_are_linked():
    result = reconcile({}, _context())
    assert result["predicted_score"]
    assert abs(sum(result["ms_probabilities"].values()) - 100) < 0.1
    assert abs(sum(result["ou_2_5"].values()) - 100) < 0.1
    assert abs(sum(result["btts_probabilities"].values()) - 100) < 0.1
    assert result["prediction_consistency"]["score_ft_linked"] is True


def test_ht_and_htft_are_mathematically_consistent():
    result = reconcile({}, _context())
    top_ht = max(result["first_half"], key=result["first_half"].get)
    top_htft = result["iyms"]["top"]
    assert top_htft.split("/")[0] == top_ht
    assert abs(sum(result["iyms"]["probabilities"].values()) - 100) < 0.2
    assert result["prediction_consistency"]["htft_linked"] is True


def test_missing_data_does_not_claim_high_quality_or_fabricate_joint_model():
    result = reconcile({}, {"data_availability": {}})
    assert result["data_quality"]["level"] == "low"
    assert result["prediction_consistency"]["score_ft_linked"] is False
    assert result["prediction_consistency"]["htft_linked"] is False
    assert result["prediction_warnings"]
