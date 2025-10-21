import pandas as pd
from thefuzz import process

def fuzzy_match_codes(nse_file, icici_file, score_cutoff=90):
    """
    Performs fuzzy string matching on the 'Description' column of two CSV files
    to map codes from NSE and ICICI.

    Args:
        nse_file (str): Filepath for the NSE csv file.
        icici_file (str): Filepath for the ICICI csv file.
        score_cutoff (int): The minimum similarity score (0-100) to consider a match.
                            Defaults to 90.

    Returns:
        pandas.DataFrame: A DataFrame containing the matched data.
    """
    try:
        # Load the datasets from your local files
        nse_df = pd.read_csv(nse_file, header=None)
        icici_df = pd.read_csv(icici_file, header=None)

        # Assign column names for clarity
        nse_df.columns = ['Code_NSE', 'Description_NSE', 'col3', 'col4', 'col5']
        icici_df.columns = ['Code_ICICI', 'Description_ICICI', 'col3', 'col4', 'col5']

        # Create a list of choices from the ICICI descriptions for matching
        icici_descriptions = icici_df['Description_ICICI'].tolist()
        
        # Prepare a dictionary for quick lookup of ICICI codes
        icici_desc_to_code_map = pd.Series(icici_df.Code_ICICI.values, index=icici_df.Description_ICICI).to_dict()

        matches = []

        # Iterate through each row in the NSE dataframe to find the best match
        for index, row in nse_df.iterrows():
            nse_description = row['Description_NSE']
            nse_code = row['Code_NSE']
            
            # Use process.extractOne to find the best match above the score_cutoff
            # This returns a tuple like ('matched_string', score) or None
            best_match = process.extractOne(nse_description, icici_descriptions, score_cutoff=score_cutoff)
            
            if best_match:
                matched_desc_icici = best_match[0]
                match_score = best_match[1]
                
                # Get the corresponding ICICI code using the map
                icici_code = icici_desc_to_code_map.get(matched_desc_icici)
                
                matches.append({
                    'Description_NSE': nse_description,
                    'Description_ICICI': matched_desc_icici,
                    'Match_Score': match_score,
                    'Code_NSE': nse_code,
                    'Code_ICICI': icici_code
                })
        
        # Convert the list of matches into a DataFrame
        result_df = pd.DataFrame(matches)
        
        # Reorder columns for clarity
        if not result_df.empty:
            result_df = result_df[['Description_NSE', 'Description_ICICI', 'Match_Score', 'Code_NSE', 'Code_ICICI']]

        return result_df

    except FileNotFoundError:
        print(f"Error: Make sure '{nse_file}' and '{icici_file}' are in the correct directory.")
        return pd.DataFrame()
    except Exception as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

# --- Main execution ---
if __name__ == "__main__":
    # --- FILE PATHS ---
    # Option 1 (Recommended): Keep your CSV files in the same folder as this script.
    NSE_FILENAME = r'C:\Users\Dell\Downloads\EQUITY_L (1).csv'
    ICICI_FILENAME = r'C:\Users\Dell\Documents\icici stoc.csv'

    # Option 2: If using a full Windows path, you MUST use a raw string (r'...') 
    # or forward slashes ('/') to avoid the SyntaxError.
    #
    # WRONG WAY (causes the error): 'C:\Users\...'
    #
    # CORRECT WAY (Raw String):
    # NSE_FILENAME = r'C:\Users\Dell\Documents\nse.csv'
    # ICICI_FILENAME = r'C:\Users\Dell\Documents\icici stoc.csv'
    #
    # CORRECT WAY (Forward Slashes):
    # NSE_FILENAME = 'C:/Users/Dell/Documents/nse.csv'
    # ICICI_FILENAME = 'C:/Users/Dell/Documents/icici stoc.csv'

    OUTPUT_FILENAME = r'C:\Users\Dell\Documents\fuzzy_mapped_codes.csv'
    
    # Set the similarity threshold (e.g., 90 for high similarity)
    # You can lower this to be less strict, e.g., 85
    SIMILARITY_CUTOFF = 90
    
    mapped_df = fuzzy_match_codes(NSE_FILENAME, ICICI_FILENAME, score_cutoff=SIMILARITY_CUTOFF)

    if not mapped_df.empty:
        # Save the result to a new CSV file
        mapped_df.to_csv(OUTPUT_FILENAME, index=False)
        
        print(f"Successfully mapped the codes with a similarity score of {SIMILARITY_CUTOFF}% or higher.")
        print(f"Result saved to '{OUTPUT_FILENAME}'")
        print("\nFirst 20 rows of the mapped data:")
        print(mapped_df.head(20))
    else:
        print("No matches found or an error occurred during processing.")

