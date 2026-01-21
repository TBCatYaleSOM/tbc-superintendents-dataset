# Close all connections and clear environment
closeAllConnections()
rm(list = ls())

source(here::here("scripts/00_setup.R"))

# Define California data directory
ca_dir_path <- here("data", "raw", "ca")

# Import CA raw data
files <- list.files(ca_dir_path, pattern = "*.xls", full.names = TRUE)

ca_raw <- data.frame()
for(f in files){
  print(f)
  df <- read_excel(path = f) %>% select(C, D, DISTRICT, SUPFNAME, SUPLNAME)
  colnames(df) <- c("c","d","dist_name","first", "last")
  
  # Convert c and d to numeric explicitly
  df$c <- as.numeric(df$c)
  df$d <- as.numeric(df$d)
  
  df <- df %>% distinct()
  df$year <- as.numeric(str_sub(basename(f), 1, 2)) + 2000
  
  ca_raw <- bind_rows(df, ca_raw)
}

ca_raw$dist_raw <- as.numeric(paste0(ca_raw$c, ca_raw$d))


print(table(ca_raw$year, useNA = "always"))

#Years are one higher than they should be, based on some newsworthy entries/exits
#https://www.pressdemocrat.com/article/news/bellevue-union-school-district-names-new-superintendent/
#http://www.precinctreporter.com/2020/10/08/riverside-county-supt-dr-judy-white-announces-retirement/
#https://www.desertsun.com/story/news/education/2020/12/15/sandra-lyon-retire-palm-springs-school-superintendent-june/3917688001/
ca_raw$year <- ca_raw$year - 1

# Map district IDs to LEAIDs
# Initialize an empty data frame
ca_distids <- data.frame()
years <- 2008:2022

# Loop through years to load and process data
for(y in years){
  print(y)
  
  # Load Rda file
  load(file.path(dist_chars_path, paste0("chars_", y, ".Rda")))
  df <- get(paste0("chars_", y))
  
  # Process the data
  temp <- df %>% 
    filter(fips == "California") %>% 
    select(year, leaid, state_leaid, nces_lea_name = lea_name, agency_charter_indicator, enrollment) %>% 
    mutate(leaid = parse_number(leaid), 
           state_leaid_n = as.numeric(str_remove_all(str_remove_all(tolower(state_leaid), "ca"),"-")))
  
  ca_distids <- bind_rows(ca_distids, temp)
  
  # Remove the loaded object
  rm(list = paste0("chars_", y))
}

# A small number of instances have incorrect district codes which result in non-unique district codes within a year
ca_raw$dist_raw <- ifelse(ca_raw$year==2009 & ca_raw$dist_name=="Big Pine Unified",
                          604950, ca_raw$dist_raw)
ca_raw$dist_raw <- ifelse(ca_raw$year==2009 & ca_raw$dist_name=="Owens Valley Unified",
                          629190, ca_raw$dist_raw)
ca_raw$dist_raw <- ifelse(ca_raw$year==2009 & ca_raw$dist_name=="Hamilton Unified",
                          601339, ca_raw$dist_raw)
ca_raw$dist_raw <- ifelse(ca_raw$year==2009 & ca_raw$dist_name=="Needles Unified",
                          626760, ca_raw$dist_raw)

# Merge with `ca_raw`
all_ca_lea <- inner_join(ca_raw, ca_distids, by = c("dist_raw" = "state_leaid_n", "year"))

# Check the districts that don't merge
non_merged <- anti_join(ca_raw, ca_distids, by = c("dist_raw" = "state_leaid_n", "year"))

# Non-merged tend to be "SBE" districts and first years following mergers 
# (e.g. Upper Lake Unified in 2015, Bonsall Unified in 2013)

all_ca_lea$state <- "ca"
all_ca_lea$name_raw <- paste(all_ca_lea$first, all_ca_lea$last)
all_ca_lea <- all_ca_lea %>%
  mutate(name_raw = if_else(name_raw == "NA NA", NA_character_, name_raw))
all_ca_lea$name_clean <- clean_names(all_ca_lea$name_raw)
all_ca_lea <- all_ca_lea %>% distinct(leaid, year, name_raw, .keep_all = TRUE)
all_ca_lea <- all_ca_lea %>% arrange(leaid, year)
all_ca_lea$id <- paste0("ca",str_pad(1:nrow(all_ca_lea), width = 5, side = "left", pad = "0"))
all_ca_lea <- all_ca_lea %>% rename(charter = agency_charter_indicator)
all_ca_lea <- all_ca_lea[!is.na(all_ca_lea$name_raw), ]

#Create table with names, district IDs, and years (for School Boards project)
all_supers <- all_ca_lea %>% select(id, state, leaid, leaid_name = nces_lea_name, name_raw, name_clean, year, leaid, charter)
all_supers$leaid_name <- str_to_title(all_supers$leaid_name)

print(table(non_merged$year, useNA = "always"))

save(all_supers, file = file.path(clean_path, "all_supers_ca.Rda"))
#write.csv(all_supers, file = file.path(clean_path, "all_supers_ca.csv"), row.names = FALSE)

# data checks 
data_checks(all_supers)