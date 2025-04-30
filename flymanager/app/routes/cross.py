import datetime
from fuzzywuzzy import fuzz
from flask import Blueprint, render_template, request, redirect, session, jsonify, current_app, url_for
from flymanager.utils.mongo import (
    get_user_crosses, get_metadata, get_user_initials, get_all_genotypes, add_metadata, 
    add_to_cross, edit_cross, update_cross_vials, write_activity, get_flip_in, get_eclosion_in
)
from flymanager.app import db
from flymanager.utils.labels import generate_label_pdf
from flymanager.utils.scanner import get_available_ports
from flymanager.utils.genetics import cross_genotypes, qc_genotype
from flymanager.utils.utils import clean_tagify_data
from flymanager.app.routes.auth import login_required

bp = Blueprint('cross', __name__) # url_prefix defined in app/__init__

@bp.route('/cross_explorer', methods=['GET', 'POST'])
@login_required
def cross_explorer():
    username = session.get("username")
    crosses = get_user_crosses(username, db)

    # nested sort by TrayID and TrayPosition
    crosses = sorted(crosses, key=lambda x: (str(x['TrayID']), int(x['TrayPosition'] if x['TrayPosition']!='' else 0)))

    # add FlipIn and EclosesIn fields
    for cross in crosses:
        cross['FlipIn'] = get_flip_in(cross)

        day_value = cross['FlipIn'].split(' ')[0].split(',')[0].strip()
        day_value = int(day_value) if day_value != 'N/A' else -999

        # process the FlipIn field
        if day_value == -999:
            cross['FlipIn'] = 'No Flip'
            cross['FlipInColor'] = '#ffcce0'
        elif day_value < 0:
            cross['FlipIn'] = 'Overdue'
            cross['FlipInColor'] = '#f25567'
        elif day_value == 0:
            cross['FlipIn'] = 'Flip today'
            cross['FlipInColor'] = '#fca15b'
        else:
            cross['FlipIn'] = 'Flip in {} day{}'.format(day_value, 's' if day_value > 1 else '')
            cross['FlipInColor'] = '#66fa78'

        cross['EclosesIn'] = get_eclosion_in(cross)

    # Extract unique values for filtering
    unique_values = {
        'MaleGenotype': sorted(set(str(cross['MaleGenotype']) for cross in crosses)),
        'FemaleGenotype': sorted(set(str(cross['FemaleGenotype']) for cross in crosses)),
        'TrayID': sorted(set(str(cross['TrayID']) for cross in crosses)),
        'Status': sorted(set(str(cross['Status']) for cross in crosses)),
        'FoodType': sorted(set(str(cross['FoodType']) for cross in crosses)),
    }

    if request.method == 'GET':
        # Check if there are filters stored in session
        filter_state = session.get('filter_state', {})
        # remove the 'No longer maintained' crosses from the list
        crosses = [cross for cross in crosses if str(cross['Status']) != 'No longer maintained']
        return render_template("cross_explorer.html", username=username, crosses=crosses, unique_values=unique_values, filter_state=filter_state)
    elif request.method == 'POST':
        if 'clear_filters' in request.form:
            session.pop('filter_state', None)
            return redirect(url_for('cross.cross_explorer'))

        # Get filter values from request
        filter_MaleGenotype = str(request.form.get('filterMaleGenotype'))
        filter_FemaleGenotype = str(request.form.get('filterFemaleGenotype'))
        filter_tray_id = str(request.form.get('filterTrayID'))
        filter_status = str(request.form.get('filterStatus'))
        filter_food_type = str(request.form.get('filterFoodType'))
        search_query = request.form.get('searchQuery')

        # Store filter state in session
        filter_state = {
            'filterMaleGenotype': filter_MaleGenotype,
            'filterFemaleGenotype': filter_FemaleGenotype,
            'filterTrayID': filter_tray_id,
            'filterStatus': filter_status,
            'filterFoodType': filter_food_type,
            'searchQuery': search_query
        }
        session['filter_state'] = filter_state

        # Apply filters
        filtered_crosses = crosses
        if filter_MaleGenotype:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['MaleGenotype']) == filter_MaleGenotype]
        if filter_FemaleGenotype:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['FemaleGenotype']) == filter_FemaleGenotype]
        if filter_tray_id:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['TrayID']) == filter_tray_id]
        if filter_status:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['Status']) == filter_status]
        else:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['Status']) != 'No longer maintained']
        if filter_food_type:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['FoodType']) == filter_food_type]
        
        # Apply search
        if search_query:
            def match(cross):
                search_fields = [
                    cross['Name'],
                    cross['MaleGenotype'],
                    cross['FemaleGenotype'],
                    cross['TrayID'],
                    cross['TrayPosition'],
                    cross['Comments']
                ]
                # combine all fields into a single string
                search_string = ' '.join(str(field) for field in search_fields)
                # find if the search query is a substring of the search string
                return fuzz.partial_ratio(search_string, search_query) > 80
            
            filtered_crosses = [cross for cross in filtered_crosses if match(cross)]

        # Recalculate unique values
        unique_values = {
            'MaleGenotype': sorted(set(str(cross['MaleGenotype']) for cross in filtered_crosses)),
            'FemaleGenotype': sorted(set(str(cross['FemaleGenotype']) for cross in filtered_crosses)),
            'TrayID': sorted(set(str(cross['TrayID']) for cross in filtered_crosses)),
            'Status': sorted(set(str(cross['Status']) for cross in filtered_crosses)),
            'FoodType': sorted(set(str(cross['FoodType']) for cross in filtered_crosses))
        }

        return render_template("cross_explorer.html", username=username, crosses=filtered_crosses, unique_values=unique_values, filter_state=filter_state)

@bp.route('/add_cross', methods=['GET', 'POST'])
@bp.route('/add_cross/<unique_id>', methods=['GET', 'POST'])
@login_required
def add_cross(unique_id=None):
    username = session.get("username")
    ports = get_available_ports()

    # get metadata lists
    food_types = get_metadata('food_types', db)
    
    genotypes = get_all_genotypes(username, db)

    # If a unique_id is provided, fetch the cross data
    cross_data = {}
    if unique_id:
        cross = db['crosses'].find_one({"UniqueID": unique_id})
        if cross:
            cross_data = {
                'maleUniqueID': cross['MaleUniqueID'],
                'femaleUniqueID': cross['FemaleUniqueID'],
                'maleGenotype': cross['MaleGenotype'],
                'femaleGenotype': cross['FemaleGenotype'],
                'status': cross['Status'],
                'foodType': cross['FoodType'],
                'name': cross['Name'],
                'vialLifetime': cross.get('VialLifetime'),
                'flipFrequency': cross.get('FlipFrequency'),
                'developmentalTime': cross.get('DevelopmentalTime'),
                'maxCrossLifetime': cross.get('MaxCrossLifetime'),
                'comments': cross['Comments']
            }
        else:
            return jsonify({"error": "Cross not found."}), 404

    if request.method == 'POST':
        # Collect form data
        male_genotype_input = clean_tagify_data(request.form.get('maleGenotype'))[0]
        female_genotype_input = clean_tagify_data(request.form.get('femaleGenotype'))[0]
        
        food_type_input = clean_tagify_data(request.form.get('foodType'))[0]
        if food_type_input not in food_types:
            add_metadata('food_types', food_type_input, db)

        new_cross_data = {
            'MaleUniqueID': request.form.get('maleUniqueID'),
            'FemaleUniqueID': request.form.get('femaleUniqueID'),
            'MaleGenotype': male_genotype_input,
            'FemaleGenotype': female_genotype_input,
            'TrayID': request.form.get('trayID'),
            'TrayPosition': request.form.get('trayPosition'),
            'Status': request.form.get('status'),
            'FoodType': food_type_input,
            'Name': request.form.get('name'),
            'VialLifetime': request.form.get('vialLifetime', 12),
            'FlipFrequency': request.form.get('flipFrequency', 2),
            'DevelopmentalTime': request.form.get('developmentalTime', 10),
            'MaxCrossLifetime': request.form.get('maxCrossLifetime',10),
            'Comments': request.form.get('comments')
        }

        # remove empty fields
        new_cross_data = {k: v for k, v in new_cross_data.items() if v}

        # Add cross to the user's sheet
        success, uid_or_message = add_to_cross(username, new_cross_data, db)

        # get the cross data
        cross = db['crosses'].find_one({"UniqueID": uid_or_message})

        # update the cross vials
        update_cross_vials(cross, username, db)

        if success:
            return redirect(url_for('cross.cross_explorer'))
        else:
            # Handle error (e.g., QC failure)
            return render_template('add_cross.html', error=uid_or_message, username=username,
                                   food_types=food_types, genotypes=genotypes, ports=ports, cross_data=cross_data)

    return render_template('add_cross.html', username=username,
                           food_types=food_types, genotypes=genotypes, ports=ports, cross_data=cross_data)

@bp.route('/view_cross/<unique_id>', methods=['GET', 'POST'])
@login_required
def view_cross(unique_id):
    username = session.get("username")

    # Get metadata lists
    food_types = get_metadata('food_types', db)
    genotypes = get_all_genotypes(username, db)

    # If a unique_id is provided, fetch the cross data
    cross = db['crosses'].find_one({"UniqueID": unique_id})
    if cross:
        cross_data = {
            'uniqueID': cross['UniqueID'],
            'maleUniqueID': cross['MaleUniqueID'],
            'femaleUniqueID': cross['FemaleUniqueID'],
            'maleGenotype': cross['MaleGenotype'],
            'femaleGenotype': cross['FemaleGenotype'],
            'trayID': cross.get('TrayID', ''),
            'trayPosition': cross.get('TrayPosition', ''),
            'status': cross['Status'],
            'foodType': cross.get('FoodType', 'Molasses'),
            'name': cross['Name'],
            'comments': cross['Comments'],
            'vialLifetime': cross.get('VialLifetime'),
            'flipFrequency': cross.get('FlipFrequency'),
            'developmentalTime': cross.get('DevelopmentalTime'),
            'maxCrossLifetime': cross.get('MaxCrossLifetime'),
            'creationDate': cross.get('CreationDate', ''),
            'lastFlipDate': cross.get('LastFlipDate', ''),
            'currentlyAliveVials': cross.get('CurrentlyAliveVials', ''),
            'flipLog': cross.get('FlipLog', '').replace('; ', '\n'),
            'nextFlipDates': cross.get('NextFlipDates', '').replace('; ', '\n'),
            'nextEclosionDates': cross.get('NextEclosionDates', '').replace('; ', '\n'),
            'dataModifiedDate': cross.get('DataModifiedDate', ''),
            'modificationLog': cross.get('ModificationLog', '').replace('; ', '\n'),
        }
    else:
        return jsonify({"error": "Cross not found."}), 404

    # Predict offspring genotypes
    predicted_offspring = cross_genotypes(cross_data['maleGenotype'], cross_data['femaleGenotype'])

    if request.method == 'POST':
        # Handle form data
        male_genotype_input = clean_tagify_data(request.form.get('maleGenotype'))[0]
        female_genotype_input = clean_tagify_data(request.form.get('femaleGenotype'))[0]

        food_type_input = clean_tagify_data(request.form.get('foodType'))[0]
        if food_type_input not in food_types:
            add_metadata('food_types', food_type_input, db)
            
        # Collect form data
        updated_cross_data = {
            'MaleUniqueID': request.form.get('maleUniqueID'),
            'FemaleUniqueID': request.form.get('femaleUniqueID'),
            'MaleGenotype': male_genotype_input,
            'FemaleGenotype': female_genotype_input,
            'TrayID': request.form.get('trayID'),
            'TrayPosition': request.form.get('trayPosition'),
            'Status': request.form.get('status'),
            'FoodType': food_type_input,
            'Name': request.form.get('name'),
            'Comments': request.form.get('comments'),
            'VialLifetime': request.form.get('vialLifetime', 14),
            'FlipFrequency': request.form.get('flipFrequency', 7),
            'DevelopmentalTime': request.form.get('developmentalTime', 10),
            'MaxCrossLifetime': request.form.get('maxCrossLifetime',18),
        }

        # Remove empty fields
        updated_cross_data = {k: v for k, v in updated_cross_data.items() if v}

        # Check if the cross data has changed and only keep the changed fields
        changed_fields = {k: v for k, v in updated_cross_data.items() if v != cross.get(k, '')}

        # Edit cross in the user's collection
        success = edit_cross(username, unique_id, db, changed_fields)

        # Get the updated cross data
        cross = db['crosses'].find_one({"UniqueID": unique_id})

        # Update the cross vials
        update_cross_vials(cross, username, db)

        if success:
            return redirect(url_for('cross.cross_explorer'))
        else:
            # Handle error (e.g., QC failure)
            return render_template('cross_explorer.html', error="Failed to update cross", username=username)

    return render_template('view_cross.html', username=username, food_types=food_types, 
                           genotypes=genotypes, cross_data=cross_data, predicted_offspring=predicted_offspring)

@bp.route('/generate_cross_labels', methods=['POST'])
@login_required
def generate_cross_labels():
    selected_uids = request.form.get('selected_uids').split(',')
    blank_spaces = int(request.form.get('blank_spaces', 0))
    quantities = request.form.get('quantities').split(',')

    # get the user's initials
    username = session.get("username")
    user_initials = get_user_initials(username, db)

    # get the selected stocks
    crosses = get_user_crosses(username, db)
    selected_crosses = [cross for cross in crosses if str(cross['UniqueID']) in selected_uids]

    # Sort the selected stocks by TrayID and TrayPosition
    selected_crosses = sorted(selected_crosses, key=lambda x: (x['TrayID'], int(x['TrayPosition'] if x['TrayPosition']!='' else 0)))

    # duplicate the selected stocks based on the quantities
    selected_crosses = [cross for cross, quantity in zip(selected_crosses, quantities) for _ in range(int(quantity))]
    
    # generate the labels
    filename = datetime.datetime.now().strftime('%Y-%m-%d')
    generate_label_pdf(
        filename,
        user_initials, 
        selected_crosses,
        ['cross']*len(selected_crosses),
        blank_spaces, 
        len(selected_crosses)
    )
    
    pdf_file_path = '/static/generated_labels/{}.pdf'.format(filename)

    # write the activity to the user's activity sheet
    write_activity(username, 'Generated labels for {} crosses'.format(len(selected_crosses)), db)
    
    return redirect(pdf_file_path)