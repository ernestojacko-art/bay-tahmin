# BAY TAHMIN Football Intelligence Engine

## Data contract
The engine uses provider-observed football data only. Missing fields are reported as unavailable and never reconstructed as facts.

### Statistical models
- Elo / team strength
- Poisson / Dixon-Coles
- time-weighted form
- home/away split
- Monte Carlo simulation
- provider xG model when genuine xG is present
- fixture-statistics cross-signal
- market intelligence as a zero-weight cross-check

### First half and HT/FT
First-half probabilities are calculated separately from observed first-half performance when available. The engine exposes the full 3x3 HT×FT matrix and marks non-straight cells as surprise candidates when their probability/model-market divergence is meaningful.

### Squad/news boundary
5DollarFootballAPI provides fixtures, results, standings, odds, events and match statistics, but does not by itself establish a verified pre-match injury/probable-XI/news feed. The engine therefore exposes squad/news as unavailable unless a verified provider payload is actually supplied.

### Evaluation
Predictions can be persisted to Supabase `prediction_tracking`, resolved after real results, and summarized with accuracy and Brier score. No performance percentage is treated as a guarantee.
