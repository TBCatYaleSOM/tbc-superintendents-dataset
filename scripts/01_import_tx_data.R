# Close all connections and clear environment
closeAllConnections()
rm(list = ls())
source(here::here("scripts/00_setup.R"))

library(tidyverse)
library(here)
library(purrr)
library(readr)
library(dplyr)
library(stringr)
library(tigris)
library(readxl)
library(janitor)

####################################
# Process 1990-2010 data
####################################

tx_dir_path <- here("data", "raw", "tx")
files_9010 <- list.files(
  path = tx_dir_path,
  pattern = "^Supers (199\\d|20\\d{2})-\\d{4}\\.csv$",
  full.names = TRUE
)
stopifnot(length(files_9010) > 0)

tx_90_10 <- map_dfr(files_9010, read_csv, show_col_types = FALSE, .id = "file_id") |>
  mutate(source_file = basename(files_9010[as.integer(file_id)])) |>
  select(-file_id)

# Use state charter classification
#tx_90_10 <- tx_90_10 %>%
#  rename(charter_status = charter) %>%
#  mutate(charter_status = case_when(
#    charter_status == "OPEN ENROLLMENT CHARTER" ~ "All charter schools",
#    charter_status == "TRADITIONAL  ISD/CSD"    ~ "No charter schools",
#    TRUE ~ charter_status
#  ))

names(tx_90_10) <- tolower(names(tx_90_10))


tx_90_10_clean <- tx_90_10 %>%
  transmute(
    year        = as.integer(str_sub(as.character(year), 1, 4)),
    district_id = paste0("TX-",sprintf("%06d", as.integer(district))),
    leaid_name  = str_to_title(distname),
    salary      = suppressWarnings(as.integer(calc_full_fte_pay)),
    name_raw    = paste(str_to_title(fname), str_to_title(lname))
  ) 

####################################
# Process 2011-2017 data
####################################

tx_dir_path <- here("data", "raw", "tx")  
years <- 2011:2017

tx_11_17 <- purrr::map_dfr(years, function(y) {
  
  dir_y <- here("data", "raw", "tx", paste0("Nonteachers ", y, "-", y + 1))
  files_y <- list.files(dir_y, pattern = "\\.csv$", full.names = TRUE)
  stopifnot(length(files_y) > 0)
  
  purrr::map_dfr(
    files_y,
    ~ readr::read_csv(
      .x,
      col_types = readr::cols(.default = readr::col_character()),
      show_col_types = FALSE
    )
  ) %>%
    rename_with(tolower) %>%
    mutate(year = as.integer(y))
})

# Use state charter classification
#tx_11_17 <- tx_11_17 %>%
#  rename(charter = dist_charttypex) %>%
#  mutate(charter = case_when(
#    charter == "OPEN ENROLLMENT CHARTER" ~ "All charter schools",
#    charter == "TRADITIONAL  ISD/CSD"    ~ "No charter schools",
#    TRUE ~ charter
#  ))

tx_11_17_clean <- tx_11_17 %>%
  filter(
    !is.na(rolex),
    str_detect(rolex, regex("SUPERINTENDENT", ignore_case = TRUE))
  ) %>%
  transmute(
    year              = as.integer(year),
    district_id       = paste0("TX-",sprintf("%06d", as.integer(district))),
    leaid_name        = str_to_title(distname),
    salary            = as.integer(totalpay),
    name_raw          = paste(str_to_title(fname),str_to_title(lname))
  )

tx_11_17_clean <- tx_11_17_clean %>%
  mutate(
    salary            = na_if(salary, 0),
    name_raw          = if_else(name_raw == "Not Reported", NA_character_, name_raw),
  )

####################################
# Process 2018-2022 data
####################################

tx_dir_path <- here("data", "raw", "tx")  

# Overwrite source files to keep raw data files under 100 MB for git upload
tx_18 <- read.csv(file.path(tx_dir_path, "2018-19 NonTeachers.csv"), stringsAsFactors = FALSE)[, c("YEAR","DISTRICT","DISTNAME","FNAME","LNAME", "ROLEX","TOTALPAY")]
#write.csv(tx_18, "data/raw/tx/2018-19 NonTeachers.csv")
tx_19 <- read.csv(file.path(tx_dir_path, "2019-20 NonTeachers.csv"), stringsAsFactors = FALSE)[, c("YEAR","DISTRICT","DISTNAME","FNAME","LNAME", "ROLEX","TOTALPAY")]
#write.csv(tx_19, "data/raw/tx/2019-20 NonTeachers.csv")
tx_20 <- read.csv(file.path(tx_dir_path, "2020-21 NonTeachers.csv"), stringsAsFactors = FALSE)[, c("YEAR","DISTRICT","DISTNAME","FNAME","LNAME", "ROLEX","TOTALPAY")]
#write.csv(tx_20, "data/raw/tx/2020-21 NonTeachers.csv")
tx_21 <- read.csv(file.path(tx_dir_path, "2021-22 NonTeachers.csv"), stringsAsFactors = FALSE)[, c("YEAR","DISTRICT","DISTNAME","FNAME","LNAME", "ROLEX","TOTALPAY")]
#write.csv(tx_21, "data/raw/tx/2021-22 NonTeachers.csv")
tx_22 <- read.csv(file.path(tx_dir_path, "2022-23 NonTeachers.csv"), stringsAsFactors = FALSE)[, c("YEAR","DISTRICT","DISTNAME","FNAME","LNAME", "ROLEX","TOTALPAY")]
#write.csv(tx_22, "data/raw/tx/2022-23 NonTeachers.csv")
  
tx_18_22 <- rbind(tx_18, tx_19, tx_20, tx_21, tx_22)
names(tx_18_22) <- tolower(names(tx_18_22))
tx_18_22$year <- as.integer(substr(tx_18_22$year, 1, 4))

# Use state charter classification
#tx_18_22 <- tx_18_22 %>%
#  rename(charter = dist_charttypex) %>%
#  mutate(charter = case_when(
#    charter == "OPEN ENROLLMENT CHARTER" ~ "All charter schools",
#    charter == "TRADITIONAL  ISD/CSD"    ~ "No charter schools",
#    TRUE ~ charter
#  ))

tx_18_22_clean <- tx_18_22 %>%
  filter(
    !is.na(rolex),
    str_detect(rolex, regex("SUPERINTENDENT", ignore_case = TRUE))
  ) %>%
  transmute(
    year       = year,
    district_id = paste0("TX-",sprintf("%06d", as.integer(district))),
    distname   = distname,
    fname      = fname,
    lname      = lname,
    totalpay   = totalpay
  ) %>%
  mutate(
    # title-case names and build name_raw
    fname    = str_to_title(fname),
    lname    = str_to_title(lname),
    name_raw = paste(fname, lname),
    
    # salary
    salary   = suppressWarnings(as.integer(totalpay)),
    salary   = na_if(salary, 0L),
    
    # handle "Not Reported"
    name_raw = if_else(name_raw == "Not Reported", NA_character_, name_raw),
    
    # standardize district name
    leaid_name = str_to_title(distname)
  ) %>%
  select(
    year,
    district_id,
    leaid_name,
    salary,
    name_raw #, 
    #charter
  )

####################################
# Process 2023-2024 data
####################################

clean_super_name <- function(x) {
  x %>%
    str_squish() %>%                            # trim + collapse whitespace
    str_remove(regex("^(DR|MR|MRS|MS)\\.?\\s+", ignore_case = TRUE)) %>%  # drop leading titles
    str_to_title()
}
tx_dir_path <- here::here("data", "raw", "tx")

tx_2023_basic <- read_excel(
  path  = file.path(tx_dir_path, "TSD-2024-final.xlsx"),
  sheet = "Index of Districts and Charters",
  skip  = 1
) %>%
  dplyr::select(
    leaid_name = 1,
    district_id = 3,
    name_raw = 6
  )
tx_2023_basic$district_id <- paste0("TX-", str_replace_all(tx_2023_basic$district_id, "\\D", ""))
tx_2023_basic$year = 2023
tx_2023_basic$salary = NA
tx_2023_basic <- tx_2023_basic %>%
  mutate(name_raw = clean_super_name(name_raw))

tx_2024_basic <- read_excel(
  path  = file.path(tx_dir_path, "TSD-2025-final.xlsx"),
  sheet = "Index of Districts and Charters",
  skip  = 1
) %>%
  dplyr::select(
    leaid_name = 1,
    district_id = 3,
    name_raw = 6
  )
tx_2024_basic$district_id <- paste0("TX-", str_replace_all(tx_2024_basic$district_id, "\\D", ""))
tx_2024_basic$year = 2024
tx_2024_basic$salary = NA
tx_2024_basic <- tx_2024_basic %>%
  mutate(name_raw = clean_super_name(name_raw))

tx_23_24_clean <- bind_rows(tx_2023_basic, tx_2024_basic)

####################################
# Combine all years
####################################

tx_raw <- bind_rows(
  tx_90_10_clean,
  tx_11_17_clean,
  tx_18_22_clean,
  tx_23_24_clean
)

tx_raw <- tx_raw %>%
  mutate(name_raw = if_else(name_raw == "Reported Not", NA_character_, name_raw))

#write.csv(tx_raw, "tx_raw.csv"))

####################################
# Merge Urban Institute data
####################################

dist_chars_path <- here("data", "raw", "urban_inst")
tx_distids <- data.frame()
years <- 1990:2024

# Loop through years to load and process data
for(y in years){
  print(y)
  
  # Load Rda file
  load(file.path(dist_chars_path, paste0("chars_", y, ".Rda")))
  df <- get(paste0("chars_", y))
  
  # Process the data
  temp <- df %>% 
    filter(fips == "Texas") %>% 
    select(year, leaid, state_leaid, nces_lea_name = lea_name, agency_charter_indicator, enrollment) %>% 
    mutate(leaid = if(is.character(leaid)) {
      parse_number(leaid)
    } else {
      as.numeric(leaid)  # If already numeric, just ensure it's numeric
    }, 
    state_leaid_n = as.numeric(str_remove_all(state_leaid, "TX-")))
  
  tx_distids <- bind_rows(tx_distids, temp)
  
  # Remove the loaded object
  rm(list = paste0("chars_", y))
}

# Merge with `tx_raw`
tx_raw <- tx_raw %>%
  mutate(
    state_leaid_n = as.integer(str_replace_all(district_id, "\\D", ""))  # strips TX- and leading zeros
  )
tx_lea <- left_join(tx_raw, tx_distids, by = c("state_leaid_n","year"))
tx_lea <- tx_lea %>% rename(charter = agency_charter_indicator)

mean(!is.na(tx_lea$charter))
mean(!is.na(tx_lea$leaid))
tx_lea %>%
  filter(!is.na(leaid)) %>%
  summarize(present = mean(!is.na(charter)))

tx_distids %>%
  summarize(present = mean(!is.na(agency_charter_indicator)))

tx_distids %>%
  group_by(year) %>%
  summarize(
    n = n(),
    present = mean(!is.na(agency_charter_indicator))
  ) %>%
  arrange(year)

# Add state and ID fields
tx_lea <- tx_lea %>% distinct(leaid, year, name_raw, .keep_all = TRUE)
tx_lea <- tx_lea %>% arrange(leaid, year)
tx_lea <- tx_lea %>%
  mutate(state = "tx",
         id = paste0("tx", str_pad(1:nrow(tx_lea), width = 5, side = "left", pad = "0")),
         name_clean = clean_names(name_raw))
tx_lea <- tx_lea |>
  dplyr::select(
    id,
    state,
    leaid,
    leaid_name = nces_lea_name,
    name_raw,
    name_clean,
    year,
    charter,
    salary,
  )
tx_lea <- tx_lea |>
  mutate(leaid_name = str_to_title(str_squish(leaid_name)))

# Create table with relevant columns
all_supers <- tx_lea %>% select(id, state, leaid, leaid_name, name_raw, name_clean, year, charter, salary)
all_supers$leaid_name <- str_to_title(all_supers$leaid_name)

# Delete rows where LEAID is missing (i.e., charter/non-standard districts) or name_clean
all_supers <- all_supers[!is.na(all_supers$leaid), ]
all_supers <- all_supers[!is.na(all_supers$name_clean), ]


# Deduplicate 240 pairs by keeping the observation with the highest or non-missing salary
all_supers <- all_supers %>%
  arrange(year, leaid, desc(!is.na(salary)), desc(salary)) %>%
  group_by(year, leaid) %>%
  slice(1) %>%
  ungroup()



# Save the processed data
save(all_supers, file = file.path(clean_path, "all_supers_tx.Rda"))

# data checks 
data_checks(all_supers)

duplicates <- all_supers %>%
  group_by(year, leaid) %>%
  filter(n() > 1) %>%
  arrange(year, leaid)
#write.csv(all_supers, "all_supers_tx.csv")
#write.csv(duplicates, "duplicates.csv")