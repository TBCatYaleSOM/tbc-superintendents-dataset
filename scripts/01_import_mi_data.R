# Close all connections and clear environment
closeAllConnections()
rm(list = ls())

source(here::here("scripts/00_setup.R"))

library(foreign)

# Define Michigan data directory
mi_dir_path <- here("data", "raw", "mi")

files <- list.files(mi_dir_path, pattern = "*.DBF", full.names = T)

all <- data.frame()
for(f in files){
  raw <- read.dbf(f) %>% select(NCES, FNAME, LNAME, COMNAME) %>% 
    filter(is.na(NCES)==0) %>% 
    mutate(file = f)
  all <- bind_rows(raw, all)
  
}

#Map each file to a school year
filemap <- data.frame(file = files, 
                      short = basename(files))
filemap$date <- str_sub(filemap$short, 6, 15)
filemap$date <- gsub('[[:punct:] ]+','/',filemap$date) %>% as.Date()
filemap$year <- ifelse(lubridate::month(filemap$date) > 7,  
                       lubridate::year(filemap$date), 
                       lubridate::year(filemap$date) - 1)

all <- left_join(all, filemap)


#Take earliest file for each district and school year
all <- all %>% arrange(NCES, date) %>% 
  group_by(NCES, year) %>% mutate(n = row_number()) %>% 
  filter(n==1) %>% select(-n)

all$state <- "mi"
all$name_raw <- paste(all$FNAME, all$LNAME)
all$name_clean <- clean_names(all$name_raw)
all$leaid <- as.numeric(as.character(all$NCES))
all$leaid_name <- (as.character(all$COMNAME))
all <- all %>% distinct(leaid, leaid_name, year, name_clean, .keep_all = TRUE)
all <- all %>% arrange(leaid, year)
all$id <- paste0("mi",str_pad(1:nrow(all), width = 5, side = "left", pad = "0"))


# Map district IDs to LEAIDs
# Initialize an empty data frame
#years <- c(2011, 2014)
#mi_distids <- data.frame()

# Loop through years to load and process data
#for(y in years){
  #print(y)
  
  # Load Rda file
  #load(file.path(dist_chars_path, paste0("chars_", y, ".Rda")))
  #df <- get(paste0("chars_", y))
  
  # Process the data
  #temp <- df %>% 
    #filter(fips == "Michigan") %>% 
    #select(year, leaid, state_leaid, nces_lea_name = lea_name, agency_charter_indicator, enrollment) %>% 
    # mutate(leaid = as.character(leaid))
  
  #  mi_distids <- bind_rows(mi_distids, temp)
  
  # Remove the loaded object
  #  rm(list = paste0("chars_", y))
  #}

# Clean state_ids
#mi_distids$state_id <- str_remove_all(mi_distids$state_leaid, "MI-")

# Restrict to "D" districts
#mi_distids <- mi_distids %>% filter(str_sub(state_id,1,1)=="D")

# Convert to numeric
#mi_distids$state_id <- as.numeric(str_sub(mi_distids$state_id, 2, 999))

#head(all)
#head(mi_distids)
#mi_distids$leaid <- as.numeric(mi_distids$leaid)
#mi_combined_lea <- inner_join(all, mi_distids, by = c("year","leaid"))

#all <- mi_combined_lea
#all <- all %>% rename(charter = agency_charter_indicator)
#head(all)


all$charter <- NA

#Create table with names, district IDs, and years
all_supers <- all %>% ungroup() %>% 
  select(id, state, leaid, leaid_name = COMNAME, name_raw, name_clean, year, leaid, charter)
all_supers$leaid_name <- str_to_title(all_supers$leaid_name)

write.csv(all_supers, "mi.csv", row.names = FALSE)

# Save the processed data
save(all_supers, file = file.path(clean_path, "all_supers_mi.Rda"))

# data checks
data_checks(all_supers)
