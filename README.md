# Superintendent research database
# V2, 2026-06-03

# Contributors: Sam Stemper, Marek Chadim, Ariel Gelrud, Cameron Greene, Seth Zimmerman

# Overview

Sam Stemper and the Broad Center at the Yale School of Management have compiled a panel dataset of superintendents in more than 10 thousand U.S. public school districts across 23 states. These files update and expand those used in Stemper (2022).

We plan to update these data once per year. When updating, we will maintain copies of all previous dataset editions on this site.

We plan to add additional states as those data become available.

We welcome contributions from users. If you would like to contribute superintendent data from other states or years, or if you notice an issue with the data or code, please contact us at broadcenter@yale.edu.

## Use

These data come from public records. You are welcome to use them. Please do so with the following citation:

Stemper, Sam and The Broad Center. Superintendent Research Dataset (v2, 2026-06-03), 2026.

## Contents

- `data/`: Raw and processed datasets.
- `scripts/`: R scripts for importing, cleaning, and merging data. Also contains `04_assign_global_ids.py` for cross-state person linking.
- `output/`: Secondary figures, tables, and a memo.
- `disambiguation/`: Python tool for determining whether same-named superintendents in different states are the same person. See `disambiguation/README.md` for details.

## The superintendent research dataset

The final dataset is `data/processed/combined_superintendents.csv`.

The dataset contains eight variables.
- `id`: uniquely identifies each row. The first two digits are a state code and the remaining digits are within-state line numbers.
- `state`: the state in which the district is located.
- `leaid`: the local education agency identification number, suitable for matching to the Common Core of Data.
- `name_raw`: name text as reported in the initial data source.
- `name_clean`: name data in standardized format.
- `year`: the fall of the academic year in which the superintendent data was recorded.
- `salary`: superintendent salary in dollars. Not available for all states.
- `super_id`: within-state superintendent panel identifier. Obtained by matching superintendents by name within states.

### Cross-state identifiers (optional)

Running `scripts/04_assign_global_ids.py` produces `data/processed/combined_superintendents_global.csv`, which adds one additional variable:

- `global_id`: a cross-state person identifier. Superintendents confirmed as the same person across states are assigned a single `global_id`, enabling cross-state career trajectory analysis. Where no cross-state match was confirmed, `global_id` is unique to that superintendent within their state (equivalent to `super_id`).

Cross-state matches are drawn from `disambiguation/output/critic_combined_passed.csv` — 264 confirmed same-person pairs identified from 1,512 candidates using an LLM-based pipeline with adversarial critic validation (99.0% accuracy, 97.4% precision, 100.0% recall on a 200-case manual test set).

## Code

To generate the dataset yourself from the raw data, open `us_superintendents_data.Rproj` and run `scripts/00_master.R`. By default, this will use processed data files included in the package; to re-generate these from scratch, set `read_PDFs <- 1` in `00_master.R`. This has a normal run time of 20–30 minutes.

To also generate cross-state global identifiers, set `assign_global_ids <- 1` in `00_master.R`. This requires Python and uses the pre-computed disambiguation results in `disambiguation/output/`.

The code proceeds as follows:
1. Download and organize files from state-level sources.
2. Import data into R.
3. Link districts to their LEAIDs using state district IDs or district names using annual crosswalks from the Urban Institute's Common Core of Data (CCD) data repository.
4. (Optional) Assign cross-state `global_id` by merging within-state `super_id`s using confirmed disambiguation matches.

## Data sources

All files are from state websites. Raw files extracted from these websites are stored in `data/raw/`. See "The Broad Center Data Extension Notes.xlsx" for details on data sourcing.

## Benchmarking and known issues

See `output/TBC_supt_memo_June_03_2026.pdf` for descriptive statistics, comparisons to other published work on superintendents, and a list of known issues.

## References

Stemper, Sam. "Doing more with less: School management and education production." (2022).
