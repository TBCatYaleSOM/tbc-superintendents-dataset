# Close all connections and clear environment
closeAllConnections()
rm(list = ls())

source(here::here("scripts/00_setup.R"))
library(dplyr)
library(stringr)
library(stringdist)

# Define Illinois data directory
il_dir_path <- here("data", "raw", "il")

# Define function to clean column names
clean_cols <- function(df){
  # Get the varnames
  var_names <- colnames(df)
  
  # Clean the varnames
  for (i in 1:length(var_names)){
    var_names[i] <- str_replace_all(tolower(var_names[i]), "\\.", "_")
    var_names[i] <- str_replace_all(var_names[i], "[\r\n]" , "")
    var_names[i] <- str_replace_all(tolower(var_names[i]), " ", "_")
  }
  
  # Replace the names with the clean names
  colnames(df) <- var_names
  
  return(df)
}

# Import Illinois raw data
files <- list.files(il_dir_path, pattern = "\\.xls$", full.names = TRUE)

all_dirs <- data.frame()
for(f in files){
  print(f)
  df <- try(read_xls(f, sheet = "Public"))
  if (inherits(df, "try-error")){
    df <- try(read_xls(f, sheet = "Public Dist & Sch"))
    if (inherits(df, "try-error")){
      df <- read_xls(f, sheet = "1 Public Dist & Sch")
    }
  }
  
  df <- clean_cols(df)
  
  df$file <- f
  
  # Standardize a few variables I want
  cols <- colnames(df)
  if("cat" %in% cols){
    df$category <- df$cat
  }
  
  if("facility_name" %in% cols){
    df$facilityname <- df$facility_name
  }
  
  reg_cols <- c("region-2_county-3_district-4","region-2county-3district-4","region2county3district4")
  for(r in reg_cols){
    if(r %in% cols){
      df$state_id <- df[[r]]
    }
  }
  
  all_dirs <- bind_rows(all_dirs, df)
  df <- NULL
}

# Subset to just district (not school) entries
all_dirs_dist <- all_dirs %>% filter(parse_number(category)==2) %>% 
  select(administrator, nces_id, state_id, file, facilityname)
all_dirs_dist$state_id <- parse_number(all_dirs_dist$state_id)
table(all_dirs_dist$file)

# Add years
all_dirs_dist$year <- parse_number(str_sub(basename(all_dirs_dist$file), 1, 4))
all_dirs_dist$year <- ifelse(is.na(all_dirs_dist$year), 
                             parse_number(str_sub(all_dirs_dist$file, nchar(all_dirs_dist$file)-5, nchar(all_dirs_dist$file)-4)) + 1999, 
                             all_dirs_dist$year)
all_dirs_dist$year <- as.numeric(all_dirs_dist$year)
table(all_dirs_dist$year, useNA = "always")

all_supers <- all_dirs_dist
all_supers <- all_supers %>%
  rename(
    name_raw   = administrator,
    leaid_name = facilityname,
    leaid      = nces_id
  ) %>%
  select(-state_id, -file) %>%
  relocate(year, leaid, leaid_name, name_raw)

all_supers <- all_supers %>%
  mutate(
    leaid_name = str_squish(leaid_name),
    leaid_name = str_to_title(leaid_name)
  )


## ---- 1) key builder: keep numbers; drop boilerplate words; keep place stem + first numeric token ----
il_place_key <- function(x, n_words = 2) {
  stopifnot(n_words %in% c(1, 2))
  
  stop_tokens <- c(
    # community / consolidated / generic school-system tokens
    "comm","com","con","cons","consol","consolidated",
    "c","u","h","s","sd","cusd","ccsd","hsd","usd",
    "sch","school","schools","dist","district","unit",
    "elem","elementary","grade","high","hs","twp","township",
    "pub","public","co","county","dept","of"
  )
  
  # normalize but KEEP digits and hyphens; remove weird symbols († etc.)
  y <- tolower(x)
  y <- gsub("[^a-z0-9#-]+", " ", y)
  y <- gsub("\\s+", " ", trimws(y))
  
  toks_list <- strsplit(y, " ", fixed = TRUE)
  
  out <- vapply(toks_list, function(tt) {
    tt <- tt[nzchar(tt)]
    if (length(tt) == 0) return(NA_character_)
    
    # drop leading boilerplate tokens (handles "school district 46", "sch district 45 dupage county", etc.)
    i <- 1L
    while (i <= length(tt) && tt[i] %in% stop_tokens) i <- i + 1L
    if (i > length(tt)) return(NA_character_)
    
    # stem word 1
    stem <- tt[i]
    
    # optional stem word 2: next non-stop, non-numeric token
    if (n_words == 2L) {
      j <- i + 1L
      while (j <= length(tt) && tt[j] %in% stop_tokens) j <- j + 1L
      if (j <= length(tt) && !grepl("[0-9]", tt[j])) {
        stem <- paste(stem, tt[j])
      }
    }
    
    # numeric-ish token anywhere: keeps "301", "#3", "u-46", "73-5", "72c"
    k <- which(grepl("[0-9]", tt))[1]
    numtok <- if (!is.na(k)) tt[k] else NA_character_
    
    if (!is.na(numtok)) paste(stem, numtok) else stem
  }, character(1))
  
  out
}

## ---- 2) fill missing leaid ONLY when key -> leaid is unique (within state) ----
# build key (temporary)
leaid_name_key <- il_place_key(all_supers$leaid_name, n_words = 2)
key_state <- paste(tolower(all_supers$state), leaid_name_key, sep = "|")

# mapping from key_state to leaid, but only if unique
nonmiss <- !is.na(all_supers$leaid) & !is.na(key_state)
u_map <- tapply(all_supers$leaid[nonmiss], key_state[nonmiss], function(v) {
  u <- unique(v)
  if (length(u) == 1L) u else NA_integer_
})

# fill
fill_val <- u_map[key_state]
idx_fill <- is.na(all_supers$leaid) & !is.na(fill_val)
all_supers$leaid[idx_fill] <- as.integer(fill_val[idx_fill])

# cleanup
rm(leaid_name_key, key_state, nonmiss, u_map, fill_val, idx_fill)


all_supers <- all_supers %>%
  mutate(
    leaid_name_key = il_place_key(leaid_name, n_words = 2),
    leaid_name_key = str_squish(str_to_lower(leaid_name_key)),
    leaid = suppressWarnings(as.integer(leaid))
  ) %>%
  group_by(leaid_name_key) %>%
  mutate(
    leaid = if_else(
      is.na(leaid),
      first(leaid[!is.na(leaid)]),
      leaid
    )
  ) %>%
  ungroup() %>%
  select(-leaid_name_key)

# For years 2012 to 2020, IL directories report NCES IDs
#table(is.na(all_dirs_dist$nces_id), all_dirs_dist$year, useNA = "always")

# Map district IDs to LEAIDs for 2003 to 2011
# Initialize an empty data frame
#il_distids <- data.frame()
#years_leaids <- 2002:2011

# Loop through years to load and process data
#for(y in years_leaids){
#  print(y)
  
  # Load Rda file
#  load(file.path(dist_chars_path, paste0("chars_", y, ".Rda")))
#  df <- get(paste0("chars_", y))
  
  # Process the data
#  temp <- df %>% 
#    filter(fips == "Illinois") %>% 
#    select(year, leaid, state_leaid, nces_lea_name = lea_name, agency_charter_indicator, enrollment) %>% 
#    mutate(leaid = parse_number(leaid))
  
#  il_distids <- bind_rows(il_distids, temp)
  
  # Remove the loaded object
#  rm(list = paste0("chars_", y))
#}

# Create new state_id variable that drops "IL-" from the front of state_leaid and drops the last part of the string after the final "-"
#il_distids <- il_distids %>%
#  mutate(state_id = sub("^IL-", "", state_leaid),        # Remove "IL-" from the start
#         state_id = sub("-[^-]+$", "", state_id),         # Remove the last part after the final "-"
#         state_id = gsub("-", "", state_id),              # Remove all "-" characters
#         state_id = sub("[a-zA-Z]$", "", state_id))       # Remove trailing letter (if any)

#il_distids$state_id <- as.numeric(il_distids$state_id)

# Some state id values have >1 leaid associated with them (e.g. 10011720)
# Take the dist_id with the highest enrollment
# First confirm that the largest district is always substantially larger than the next largest
#enr_check <- il_distids %>% group_by(year, state_id) %>% 
#  filter(enrollment > 0) %>% 
#  mutate(rank_enr = rank(-enrollment, ties.method = "first")) %>% 
#  pivot_wider(id_cols = c(year, state_id), 
#              values_from = enrollment, 
#              names_from = rank_enr, 
#              names_prefix = "enr") %>% 
#  filter(is.na(enr2)==0)
#quantile(enr_check$enr1/enr_check$enr2, seq(0,1,0.01), na.rm = T)

#il_distids <- il_distids %>% group_by(year, state_id) %>% 
#  filter(enrollment > 0) %>% 
#  mutate(rank_enr = rank(-enrollment, ties.method = "first")) %>% 
#  filter(rank_enr==1) %>% 
#  select(year, leaid, state_id, nces_lea_name, agency_charter_indicator)

#all_dirs_dist_02_11 <- all_dirs_dist %>% filter(year %in% 2002:2011) %>% 
#  select(-nces_id) %>% 
#  inner_join(., il_distids, by = c("state_id", "year"))

# Check that state_id and year are unique
#check_n <- all_dirs_dist_02_11 %>% group_by(state_id, year) %>% summarize(n = n())
#table(check_n$n)

# Inspect unmatched 
#unmatched <- all_dirs_dist %>% filter(year %in% 2002:2011) %>% 
#  select(-nces_id) %>% 
#  anti_join(., il_distids, by = c("state_id", "year"))
#table(unmatched$year)

#all_dirs_dist_12_20 <- all_dirs_dist %>% filter(year %in% 2012:2023) %>% 
#  mutate(leaid = as.numeric(nces_id)) %>% 
#  filter(is.na(leaid)==0)

#all_dirs_leas <- bind_rows(all_dirs_dist_02_11, all_dirs_dist_12_20)
#all_dirs_leas$name_raw <- all_dirs_leas$administrator
all_supers$name_clean <- clean_names(all_supers$name_raw)
all_supers$state <- "IL"
#all_dirs_leas <- all_dirs_leas %>% distinct(leaid, year, name_clean, agency_charter_indicator, .keep_all = TRUE)
all_supers <- all_supers %>% arrange(leaid, year)
all_supers$id <- paste0("il",str_pad(1:nrow(all_supers), width = 5, side = "left", pad = "0"))
#all_dirs_leas <- all_dirs_leas %>% rename(charter = agency_charter_indicator)

write.csv(all_supers, "il.csv", row.names = FALSE)

# Create table with names, district IDs, and years
#all_supers <- all_supers %>% select(id, state, leaid, leaid_name = nces_lea_name, name_raw, name_clean, year, leaid, charter)
#all_supers$leaid_name <- str_to_title(all_supers$leaid_name)

      x <- all_supers
      x$year  <- as.integer(as.character(x$year))
      x$leaid <- as.character(x$leaid)   # keep as character for exact matching
      
      # rows that are in a duplicated YEAR-LEAID key (either copy)
      dup_row <- duplicated(x[c("year","leaid")]) | duplicated(x[c("year","leaid")], fromLast = TRUE)
      
      # how many rows are involved in duplicated keys?
      sum(dup_row, na.rm = TRUE)
      
      # how many duplicated *keys* (groups) exist?
      key <- paste(x$year, x$leaid, sep = "___")
      tab <- table(key[!is.na(x$year) & !is.na(x$leaid)])
      sum(tab > 1)
      
      # show ALL rows for duplicated keys (this is what you want to inspect)
      x_dups <- x[dup_row & !is.na(x$year) & !is.na(x$leaid), ]
      x_dups <- x_dups[order(x_dups$year, x_dups$leaid, x_dups$leaid_name, x_dups$name_raw), ]
      

all_supers <- all_supers[!is.na(all_supers$leaid), ]
#write.csv(x_dups, "il_dups.csv", row.names = FALSE)


# Save the processed data
save(all_supers, file = file.path(clean_path, "all_supers_il.Rda"))

# data checks
data_checks(all_supers)


