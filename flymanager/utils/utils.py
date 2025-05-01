# Description: Utility functions for the FlyManager application
import datetime

# take an console input from the user and validate it against a list of valid options
def validated_input(prompt, valid_options, default=None, show_options=True):
    """
    Ask the user for input, validate it against a list of valid options
    and return the response. If the user enters an invalid option, they
    will be prompted to try again.
    """
    while True:
        if show_options:
            response = input(f"{prompt} ({'/'.join(valid_options)}) ({default}): ").lower().strip()
        else:
            response = input(f"{prompt} ({default}): ").lower().strip()
        if response == "" and default is not None:
            return default
        elif response in valid_options:
            return response
        else:
            print(f"Invalid option '{response}'. Please try again.")

# convert a hex color code to RGB
def hex_to_rgb(hex):
    """
    Convert a hex color code to RGB
    """
    hex = hex.lstrip("#")
    return tuple(int(hex[i:i+2], 16)/255 for i in (0, 2, 4))

# clean the data from the tagify input
def clean_tagify_data(form_data):
    if form_data == '':
        return form_data
    form_data_elements = form_data.split('"')[1::2]
    # remove all elements that are "value"
    form_data_elements = [element for element in form_data_elements if element != 'value']
    return form_data_elements

# check if each one has a vial name and a date in the correct format: (VialID, YYYY-MM-DD HH:MM)
def clean_log_entry(entry):
    """ 
    Clean the log entry to get the vial and the date.
    """
    if entry.count(",") != 1:
        vial = None
        try:
            date = datetime.datetime.strptime(entry.strip(), "%Y-%m-%d %H:%M")
        except:
            date = datetime.datetime.strptime(entry.strip(), "%Y-%m-%d %H:%M:%S")
        return vial, date
    else:
        vial = entry.split(", ")[0].strip()
        try:
            date = datetime.datetime.strptime(entry.split(",")[1].strip(), "%Y-%m-%d %H:%M")
        except:
            date = datetime.datetime.strptime(entry.split(",")[1].strip(), "%Y-%m-%d %H:%M:%S")
        return vial, date

# Helper function to map day strings to numbers
def day_str_to_num(day_str):
    day_map = {
        "Mo": 0,  # Monday
        "Tu": 1,  # Tuesday
        "We": 2,  # Wednesday
        "Th": 3,  # Thursday
        "Fr": 4,  # Friday
        "Sa": 5,  # Saturday
        "Su": 6   # Sunday
    }
    return day_map.get(day_str)

def increment_replicate_id(replicate_id):
    """
    Increments the replicate ID by following alphabetical order (a, b, ..., z, aa, ab, ..., az, ba, ..., zz, aaa, etc.).
    
    Parameters:
    replicate_id: str
        The current replicate ID.
    
    Returns:
    str
        The next replicate ID in sequence.
    """

    # If replicate_id is empty, return 'a'
    if replicate_id == "":
        return "a"
    
    def incr_chr(c):
        """
        Helper function that increments a single character, handling wraparound from 'z' to 'a'.
        
        Returns:
        (carry, next_char) where:
        - carry: 1 if wrapping from 'z' to 'a', otherwise 0.
        - next_char: the next character in sequence.
        """
        if c == 'z':
            return 1, 'a'  # wrap from 'z' to 'a' with carry 1
        else:
            return 0, chr(ord(c) + 1)  # normal increment with no carry
    
    # Convert the replicate_id into a list of characters
    lst = list(replicate_id)
    result = []
    
    # Loop through the list from the rightmost character (the least significant)
    while lst:
        carry, next_ = incr_chr(lst.pop())  # increment the last letter in the list
        result.append(next_)                # add incremented character to the result

        if not carry:                       # if no carry, we are done
            break
        if not lst:                         # if the list is empty but we still have a carry, prepend 'a'
            result.append('a')
    
    result += lst[::-1]                     # append the remaining characters (if any) in reverse order
    return ''.join(result[::-1])            # convert list back to string in reverse order

# Helper to parse flip day value
def parse_flip_day(flip_in_str):
    try:
        day_value_str = flip_in_str.split(' ')[0].split(',')[0].strip()
        return int(day_value_str) if day_value_str != 'N/A' else -999
    except:
        return -999

# Helper to parse date string irrespective of format
def get_datetime_from_str(date_str):
    """
    Convert a date string to a datetime object.
    Handles multiple formats: YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY, DD/MM/YYYY.
    """
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y"
    ]
    
    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Date string '{date_str}' does not match any expected format.")