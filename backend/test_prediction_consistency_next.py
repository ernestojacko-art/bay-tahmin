from prediction_consistency import reconcile

def context():
    return {"home":{"recent_form":{"sample":10,"goals_for_avg":1.8,"goals_against_avg":1.0,"first_half":{"goals_for_avg":0.8,"goals_against_avg":0.4}}},"away":{"recent_form":{"sample":10,"goals_for_avg":1.2,"goals_against_avg":1.4,"first_half":{"goals_for_avg":0.5,"goals_against_avg":0.7}}},"data_availability":{"first_half_goals":True,"second_half_goals":True,"goal_timing":True}}

def test_score_outputs_are_linked():
 r=reconcile({},context());assert r["predicted_score"];assert abs(sum(r["ms_probabilities"].values())-100)<.1;assert abs(sum(r["ou_2_5"].values())-100)<.1;assert abs(sum(r["btts_probabilities"].values())-100)<.1;assert r["prediction_consistency"]["score_ft_linked"]

def test_recommended_htft_matches_top_ht_marginal():
 r=reconcile({},context());top_ht=max(r["first_half"],key=r["first_half"].get);assert r["iyms"]["top"].split("/")[0]==top_ht;assert abs(sum(r["iyms"]["probabilities"].values())-100)<.2

def test_missing_data_is_not_claimed_high_quality():
 r=reconcile({}, {"data_availability":{}});assert r["data_quality"]["level"]=="low";assert not r["prediction_consistency"]["score_ft_linked"];assert not r["prediction_consistency"]["htft_linked"];assert r["prediction_warnings"]
