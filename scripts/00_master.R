# The R version used is 4.1.0 (May 2021)
# master.R - Execute all scripts in one go

# 1. After installing the packages, comment out the lines below.
install.packages("here")
install.packages("readxl")
install.packages("writexl")
install.packages("tidyverse")
install.packages("pdftools")
install.packages("knitr")
install.packages("kableExtra")
install.packages("gender")
install.packages("wru")
install.packages("educationdata")
install.packages("stargazer")
install.packages("zoo")

# 2. (Optional) By default, these scripts use processed .Rda files. 
#    If you would like to generate the data "from scratch," change "read_PDFs <- 1" in the following line.
read_PDFs <- 1

# 3. (Optional) Assign cross-state global_ids using confirmed disambiguation matches.
#    Requires Python and produces combined_superintendents_global.csv.
#    Change to 1 to enable.
assign_global_ids <- 0

if (!require("here")) install.packages("here")
library(here)

cat("Working in project directory:", here::here(), "\n")

if(read_PDFs == 0){
scripts <- c(
  "scripts/00_setup.R",
  "scripts/00_import_urban_inst_data.R",
  "scripts/03_tables_figures.R"
)}

if (read_PDFs == 1){
scripts <- c(
  "scripts/00_setup.R",
  "scripts/00_import_urban_inst_data.R",
  "scripts/02_run_all.R",
  "scripts/03_tables_figures.R"
)}

for (script in scripts) {
  cat("Running:", script, "\n")
  source(here::here(script), echo = TRUE)
  if (assign_global_ids == 1 && script == "scripts/02_run_all.R") {
    cat("Running: scripts/04_assign_global_ids.py\n")
    exit_code <- system2(
      "python",
      args   = here::here("scripts/04_assign_global_ids.py"),
      stdout = "",
      stderr = ""
    )
    if (exit_code != 0) {
      stop("04_assign_global_ids.py failed with exit code ", exit_code,
           ". Check Python installation and that combined_superintendents.csv exists.")
    }
  }
}
