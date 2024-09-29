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