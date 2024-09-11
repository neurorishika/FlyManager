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

def hex_to_rgb(hex):
    """
    Convert a hex color code to RGB
    """
    hex = hex.lstrip("#")
    return tuple(int(hex[i:i+2], 16)/255 for i in (0, 2, 4))

def clean_tagify_data(form_data):
    if form_data == '':
        return form_data
    form_data_elements = form_data.split('"')[1::2]
    # remove all elements that are "value"
    form_data_elements = [element for element in form_data_elements if element != 'value']
    return form_data_elements